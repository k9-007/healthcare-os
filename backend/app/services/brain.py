"""Brain — PageIndex-style reasoning retrieval + cite-or-refuse answers.

Documents are parsed into a hierarchical section tree (heading/page aware) and
stored as DocChunk nodes. Questions are answered in two LLM stages:

  1. tree search — the LLM sees a compact table of contents and *reasons*
     about which nodes to open (no vectors, no similarity guessing),
  2. grounded answer — sarvam LLM answers ONLY from the opened nodes under a
     strict cite-or-refuse system prompt.

If the Sarvam API is unavailable, a deterministic keyword-scoring fallback
keeps the endpoint functional (clearly labelled `engine="fallback-keyword"`).
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DocChunk, Document
from ..schemas import BrainAnswerOut, CitationOut
from .sarvam import SarvamUnavailable, sarvam

logger = logging.getLogger("brain")

MAX_CHUNK_CHARS = 1600
MAX_SELECTED_NODES = 6

CITE_OR_REFUSE_SYSTEM = """You are Brain, a clinical knowledge assistant for a hospital.
You are given numbered SOURCE NODES extracted from the hospital's own verified documents.
Rules — these are absolute:
1. Answer ONLY using facts present in the source nodes. Never use outside knowledge for clinical claims.
2. Cite every factual sentence with the node marker, e.g. [2].
3. If the sources do not contain enough information to answer safely, REFUSE: set "refused" true and explain briefly what is missing. Never guess. An unsupported medical answer is worse than no answer.
4. Be concise and clinically precise.
5. Structure "answer" as markdown built for fast clinical scanning:
   - Line 1: the direct answer in one sentence, with key values/doses in **bold** (e.g. "**HbA1C is 5.3%** — within the normal range [1].").
   - Then up to 5 short bullets ("- ") with supporting detail, each ending with its citation marker.
   - If there are warnings, contraindications or red flags, add a final bullet starting with "⚠️ ".
   - No headings, no tables, no nested lists. Bullets under 20 words each.
Respond with pure JSON: {"answer": str, "refused": bool, "used_nodes": [int], "confidence": float between 0 and 1}"""


# ---------------- indexing ----------------

def index_document(db: Session, doc: Document) -> int:
    """Parse extracted markdown into page/section-aware tree nodes (DocChunks)."""
    db.query(DocChunk).filter(DocChunk.document_id == doc.id).delete()
    text = doc.extracted_md or ""
    if not text.strip():
        return 0

    chunks = _build_nodes(text)
    for i, (page, section, body) in enumerate(chunks):
        db.add(DocChunk(document_id=doc.id, ordinal=i, page=page, section=section[:300], text=body))
    db.flush()
    return len(chunks)


def _build_nodes(md: str) -> list[tuple[int, str, str]]:
    """Split markdown into (page, section, text) nodes.

    Page tracking: honours explicit page markers Vision emits (e.g. '<!-- page: 3 -->'
    or '--- Page 3 ---'); otherwise estimates ~2800 chars per page.
    """
    page_marker = re.compile(r"(?:<!--\s*page[:\s]+(\d+)\s*-->|^-{2,}\s*page\s+(\d+)\s*-{2,}$)", re.I | re.M)
    heading = re.compile(r"^(#{1,4})\s+(.+)$", re.M)

    nodes: list[tuple[int, str, str]] = []
    current_section = "Document"
    current_page = 1
    buffer: list[str] = []
    chars_seen = 0

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            # split oversized sections so each node stays reasoning-sized
            for j in range(0, len(body), MAX_CHUNK_CHARS):
                nodes.append((current_page, current_section, body[j : j + MAX_CHUNK_CHARS]))
        buffer.clear()

    for line in md.splitlines():
        pm = page_marker.search(line)
        if pm:
            flush()
            current_page = int(pm.group(1) or pm.group(2))
            continue
        hm = heading.match(line)
        if hm:
            flush()
            current_section = hm.group(2).strip()
            continue
        buffer.append(line)
        chars_seen += len(line)
        if chars_seen > 2800:  # heuristic page advance when no explicit markers
            chars_seen = 0
            if not page_marker.search(md[:100]):
                current_page += 1
    flush()
    return nodes or [(1, "Document", md[:MAX_CHUNK_CHARS])]


# ---------------- asking ----------------

async def ask(db: Session, question: str, patient_id: int | None = None) -> BrainAnswerOut:
    q = select(DocChunk, Document).join(Document, DocChunk.document_id == Document.id)
    q = q.where(Document.status == "ready")
    if patient_id is not None:
        q = q.where((Document.patient_id == patient_id) | (Document.patient_id.is_(None)))
    rows = db.execute(q).all()

    if not rows:
        return BrainAnswerOut(
            answer="No documents have been ingested yet — upload clinical documents to Brain first.",
            refused=True, confidence=0.0, engine="none",
        )

    try:
        return await _ask_llm(question, rows)
    except SarvamUnavailable as e:
        logger.warning("Brain LLM path unavailable (%s); using keyword fallback", e)
        return _ask_fallback(question, rows)


async def _ask_llm(question: str, rows) -> BrainAnswerOut:
    # Stage 1 — tree search: reason over the table of contents.
    toc_lines = []
    for i, (chunk, doc) in enumerate(rows):
        preview = " ".join(chunk.text.split())[:140]
        toc_lines.append(f"[{i}] doc='{doc.title}' page={chunk.page} section='{chunk.section}' :: {preview}")
    toc = "\n".join(toc_lines[:400])  # bound prompt size

    selection = await sarvam.chat_json(
        [
            {"role": "system", "content": (
                "You navigate a hospital document tree like a clinician flipping to the right section. "
                "Given a question and a table of contents of nodes, pick the node indices most likely to "
                f"contain the answer (at most {MAX_SELECTED_NODES}). "
                'Respond with pure JSON and nothing else: {"nodes": [int]}'
            )},
            {"role": "user", "content": f"Question: {question}\n\nTable of contents:\n{toc}"},
        ],
        temperature=0.1,
        max_tokens=4096,  # reasoning model — leave room for reasoning + JSON
    )
    picked = [i for i in (selection.get("nodes") or []) if isinstance(i, int) and 0 <= i < len(rows)]
    picked = picked[:MAX_SELECTED_NODES]
    if not picked:
        return BrainAnswerOut(
            answer="I could not find any section of the ingested documents relevant to this question, so I won't guess.",
            refused=True, confidence=0.1, engine="sarvam-105b",
        )

    # Stage 2 — grounded, cite-or-refuse answer over the opened nodes.
    numbered = []
    for n, idx in enumerate(picked, start=1):
        chunk, doc = rows[idx]
        numbered.append(f"[{n}] (doc: {doc.title}, page {chunk.page}, section: {chunk.section})\n{chunk.text}")
    sources = "\n\n".join(numbered)

    result = await sarvam.chat_json(
        [
            {"role": "system", "content": CITE_OR_REFUSE_SYSTEM},
            {"role": "user", "content": f"SOURCE NODES:\n{sources}\n\nQUESTION: {question}"},
        ],
        temperature=0.2, max_tokens=4096,  # reasoning model — reasoning + cited answer
    )

    refused = bool(result.get("refused"))
    used = [n for n in (result.get("used_nodes") or []) if isinstance(n, int) and 1 <= n <= len(picked)]
    if not used and not refused:
        used = list(range(1, len(picked) + 1))
    citations = []
    for n in used:
        chunk, doc = rows[picked[n - 1]]
        citations.append(CitationOut(
            document_id=doc.id, document_title=doc.title, page=chunk.page,
            section=chunk.section, snippet=" ".join(chunk.text.split())[:220],
        ))
    conf = result.get("confidence")
    confidence = float(conf) if isinstance(conf, (int, float)) else (0.2 if refused else 0.75)
    return BrainAnswerOut(
        answer=str(result.get("answer") or ""), refused=refused,
        citations=citations, confidence=max(0.0, min(1.0, confidence)), engine="sarvam-105b",
    )


def _ask_fallback(question: str, rows) -> BrainAnswerOut:
    """Deterministic keyword-overlap retrieval; refuses when nothing matches."""
    q_tokens = _tokens(question)
    scored = []
    for chunk, doc in rows:
        overlap = len(q_tokens & _tokens(chunk.section + " " + chunk.text))
        if overlap:
            scored.append((overlap, chunk, doc))
    scored.sort(key=lambda t: -t[0])
    top = scored[:3]
    if not top or top[0][0] < 2:
        return BrainAnswerOut(
            answer="I couldn't find supporting evidence for this in the ingested documents, so I won't guess. "
                   "(Note: the Sarvam LLM is currently unreachable — this is the offline retrieval fallback.)",
            refused=True, confidence=0.0, engine="fallback-keyword",
        )
    citations = [
        CitationOut(document_id=doc.id, document_title=doc.title, page=chunk.page,
                    section=chunk.section, snippet=" ".join(chunk.text.split())[:220])
        for _, chunk, doc in top
    ]
    best = top[0][1]
    answer = (
        f"Closest match: **{citations[0].document_title}**, page {best.page} — {best.section} [1]\n"
        f"- {' '.join(best.text.split())[:400]} [1]\n"
        "- ⚠️ Sarvam LLM is unreachable right now — this is offline keyword retrieval; verify against the cited source."
    )
    max_possible = max(len(q_tokens), 1)
    return BrainAnswerOut(
        answer=answer, refused=False, citations=citations,
        confidence=min(0.6, top[0][0] / max_possible), engine="fallback-keyword",
    )


STOPWORDS = frozenset(
    "the a an is are was were be been of for in on at to from with and or if this that "
    "what which who whom how why when where do does did has have had any my our your".split()
)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}
