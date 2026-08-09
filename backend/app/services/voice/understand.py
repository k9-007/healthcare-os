"""Per-turn understanding, on a phone-call time budget.

One `sarvam-105b-conversations` call (~0.8s) classifies the reply to the
question we just asked. A deterministic red-flag keyword scan runs *first* and
can only ever escalate: patient safety must not depend on an LLM being up,
fast, or correct.
"""

import logging
from dataclasses import dataclass, field

from ..careplus import (
    NEGATIVE_MED_PATTERNS,
    POSITIVE_MED_PATTERNS,
    SYMPTOM_KEYWORDS,
    _is_urgent,
)
from ..sarvam import SarvamUnavailable, extract_json, sarvam

logger = logging.getLogger("voice.understand")

SYSTEM = (
    "You are the understanding layer of a nurse's follow-up phone call. "
    "Classify the patient's spoken reply to the question that was just asked. "
    "Use only what the patient said; never invent clinical detail. "
    "Reply with pure JSON and nothing else."
)

SCHEMA = (
    '{"answered":true|false,'
    '"yes_no":"yes"|"no"|"unclear",'
    '"answer":"one short sentence in English summarising their reply",'
    '"symptoms":["..."],'
    '"pain_score":null or 0-10,'
    '"urgency":"low"|"medium"|"high"}'
)


@dataclass
class Understanding:
    answered: bool = False
    yes_no: str = "unclear"  # yes | no | unclear
    answer: str = ""
    symptoms: list[str] = field(default_factory=list)
    pain_score: int | None = None
    urgency: str = "low"
    source: str = "llm"  # llm | keywords

    @property
    def is_emergency(self) -> bool:
        return self.urgency == "high"


async def understand(question_en: str, transcript: str, *, expects_yes_no: bool) -> Understanding:
    """Classify one patient reply. Never raises — a failed turn degrades to keywords."""
    text = (transcript or "").strip()
    if not text:
        return Understanding(answered=False, source="keywords")

    result: Understanding | None = None
    try:
        raw = await sarvam.chat_fast(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": (
                    f"QUESTION ASKED: {question_en}\n"
                    f"PATIENT SAID: {text}\n\n"
                    f"Respond with exactly this JSON shape:\n{SCHEMA}"
                )},
            ],
            temperature=0.1, max_tokens=220,
        )
        result = _shape(extract_json(raw))
    except (SarvamUnavailable, Exception) as e:  # noqa: BLE001 — the call must continue
        logger.warning("fast understanding failed (%s); using keywords", e)

    if result is None:
        result = _keywords(text, expects_yes_no)
    elif expects_yes_no and result.yes_no == "unclear":
        result.yes_no = _keywords(text, True).yes_no

    # Safety net: keyword red flags override anything the model concluded.
    if _is_urgent(text):
        result.urgency = "high"
    return result


def _shape(data: object) -> Understanding:
    if not isinstance(data, dict):
        raise ValueError("understanding response was not an object")
    yes_no = str(data.get("yes_no") or "unclear").strip().lower()
    if yes_no not in {"yes", "no", "unclear"}:
        yes_no = "unclear"
    urgency = str(data.get("urgency") or "low").strip().lower()
    if urgency not in {"low", "medium", "high"}:
        urgency = "low"

    raw_symptoms = data.get("symptoms") or []
    if isinstance(raw_symptoms, str):
        raw_symptoms = [s for s in raw_symptoms.split(",")]
    symptoms = [str(s).strip() for s in raw_symptoms if str(s).strip()][:6]
    symptoms = [s for s in symptoms if s.lower() not in {"none", "no", "n/a", "nil"}]

    pain = data.get("pain_score")
    pain_score = None
    if isinstance(pain, (int, float)):
        pain_score = max(0, min(10, int(pain)))
    elif isinstance(pain, str) and pain.strip().isdigit():
        pain_score = max(0, min(10, int(pain.strip())))

    return Understanding(
        answered=bool(data.get("answered", True)),
        yes_no=yes_no,
        answer=str(data.get("answer") or "").strip()[:400],
        symptoms=symptoms,
        pain_score=pain_score,
        urgency=urgency,
    )


def _keywords(text: str, expects_yes_no: bool) -> Understanding:
    low = text.lower()
    out = Understanding(answered=True, answer=text[:400], source="keywords")
    if expects_yes_no:
        if any(p in low for p in NEGATIVE_MED_PATTERNS):
            out.yes_no = "no"
        elif any(p in low for p in POSITIVE_MED_PATTERNS):
            out.yes_no = "yes"
    out.symptoms = [n for n, kws in SYMPTOM_KEYWORDS.items() if any(k in low for k in kws)]
    if _is_urgent(text):
        out.urgency = "high"
    return out
