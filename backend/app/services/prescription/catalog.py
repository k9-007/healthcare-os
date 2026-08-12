"""Local medicine catalog + fuzzy brand/generic matching.

No external drug API is wired in this repo — we ship a compact India-focused
catalog and optionally enrich it from formulary documents / existing care-plan
medicines in the DB. Matching is stdlib difflib so tests need no extras.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

# brand → generic (common Indian RX / OTC shorthand seen on discharge slips)
_BUILTIN: list[tuple[str, str, str]] = [
    # (canonical_name, generic_name, brand_or_alias)
    ("Metformin", "Metformin", "Glycomet"),
    ("Metformin", "Metformin", "Glucophage"),
    ("Metformin", "Metformin", "Metformin"),
    ("Amlodipine", "Amlodipine", "Amlong"),
    ("Amlodipine", "Amlodipine", "Amlodipine"),
    ("Amlodipine", "Amlodipine", "Amtas"),
    ("Paracetamol", "Paracetamol", "Dolo"),
    ("Paracetamol", "Paracetamol", "Crocin"),
    ("Paracetamol", "Paracetamol", "Calpol"),
    ("Paracetamol", "Paracetamol", "Paracetamol"),
    ("Paracetamol", "Paracetamol", "Acetaminophen"),
    ("Pantoprazole", "Pantoprazole", "Pan"),
    ("Pantoprazole", "Pantoprazole", "Pan-D"),
    ("Pantoprazole", "Pantoprazole", "Pantop"),
    ("Pantoprazole", "Pantoprazole", "Pantoprazole"),
    ("Omeprazole", "Omeprazole", "Omez"),
    ("Omeprazole", "Omeprazole", "Omeprazole"),
    ("Atorvastatin", "Atorvastatin", "Atorva"),
    ("Atorvastatin", "Atorvastatin", "Lipitor"),
    ("Atorvastatin", "Atorvastatin", "Atorvastatin"),
    ("Telmisartan", "Telmisartan", "Telma"),
    ("Telmisartan", "Telmisartan", "Telmisartan"),
    ("Losartan", "Losartan", "Losar"),
    ("Losartan", "Losartan", "Losartan"),
    ("Aspirin", "Aspirin", "Ecosprin"),
    ("Aspirin", "Aspirin", "Aspirin"),
    ("Clopidogrel", "Clopidogrel", "Clopilet"),
    ("Clopidogrel", "Clopidogrel", "Clopidogrel"),
    ("Thyroxine", "Levothyroxine", "Thyronorm"),
    ("Thyroxine", "Levothyroxine", "Eltroxin"),
    ("Thyroxine", "Levothyroxine", "Thyroxine"),
    ("Calcium", "Calcium carbonate", "Shelcal"),
    ("Calcium", "Calcium carbonate", "Calcium"),
    ("Vitamin D3", "Cholecalciferol", "Uprise-D3"),
    ("Vitamin D3", "Cholecalciferol", "Vitamin D3"),
    ("Vitamin B12", "Methylcobalamin", "Neurobion"),
    ("Vitamin B12", "Methylcobalamin", "B12"),
    ("Amoxicillin-Clavulanate", "Amoxicillin + Clavulanic acid", "Augmentin"),
    ("Amoxicillin", "Amoxicillin", "Amoxil"),
    ("Azithromycin", "Azithromycin", "Azithral"),
    ("Azithromycin", "Azithromycin", "Azithromycin"),
    ("Cefixime", "Cefixime", "Taxim-O"),
    ("Cefixime", "Cefixime", "Cefixime"),
    ("Ciprofloxacin", "Ciprofloxacin", "Ciplox"),
    ("Ciprofloxacin", "Ciprofloxacin", "Ciprofloxacin"),
    ("Ibuprofen", "Ibuprofen", "Brufen"),
    ("Ibuprofen", "Ibuprofen", "Ibuprofen"),
    ("Diclofenac", "Diclofenac", "Voveran"),
    ("Diclofenac", "Diclofenac", "Diclofenac"),
    ("Cetirizine", "Cetirizine", "Okacet"),
    ("Cetirizine", "Cetirizine", "Cetirizine"),
    ("Montelukast", "Montelukast", "Montair"),
    ("Montelukast", "Montelukast", "Montelukast"),
    ("Salbutamol", "Salbutamol", "Asthalin"),
    ("Salbutamol", "Salbutamol", "Salbutamol"),
    ("Insulin", "Insulin", "Insulin"),
    ("Glimepiride", "Glimepiride", "Amaryl"),
    ("Glimepiride", "Glimepiride", "Glimepiride"),
    ("Sitagliptin", "Sitagliptin", "Januvia"),
    ("Sitagliptin", "Sitagliptin", "Sitagliptin"),
    ("ORS", "Oral rehydration salts", "ORS"),
    ("Ondansetron", "Ondansetron", "Emeset"),
    ("Ondansetron", "Ondansetron", "Ondansetron"),
    ("Domperidone", "Domperidone", "Domstal"),
    ("Domperidone", "Domperidone", "Domperidone"),
    ("Ranitidine", "Ranitidine", "Rantac"),
    ("Furosemide", "Furosemide", "Lasix"),
    ("Furosemide", "Furosemide", "Furosemide"),
    ("Spironolactone", "Spironolactone", "Aldactone"),
    ("Spironolactone", "Spironolactone", "Spironolactone"),
    ("Gabapentin", "Gabapentin", "Gabapin"),
    ("Gabapentin", "Gabapentin", "Gabapentin"),
    ("Pregabalin", "Pregabalin", "Pregaba"),
    ("Pregabalin", "Pregabalin", "Pregabalin"),
]

_NOISE = re.compile(
    r"\b(tab\.?|tabs\.?|tablet|tablets|cap\.?|caps\.?|capsule|syrup|inj\.?|"
    r"injection|sachet|drops?|ointment|cream|susp\.?|suspension)\b",
    re.I,
)
_DOSE_TOKEN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|iu|units?|%)?\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9+]+")


@dataclass(frozen=True)
class CatalogEntry:
    canonical: str
    generic: str
    alias: str  # searchable brand / alias / spelling


@dataclass(frozen=True)
class MatchResult:
    entry: CatalogEntry | None
    score: float
    matched: bool


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = _NOISE.sub(" ", text)
    text = _DOSE_TOKEN.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def builtin_catalog() -> list[CatalogEntry]:
    seen: set[tuple[str, str, str]] = set()
    out: list[CatalogEntry] = []
    for canonical, generic, alias in _BUILTIN:
        key = (canonical.lower(), generic.lower(), alias.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(CatalogEntry(canonical=canonical, generic=generic, alias=alias))
    return out


def catalog_from_db(db: Session | None) -> list[CatalogEntry]:
    """Pull names already used on care plans + formulary document titles/chunks."""
    if db is None:
        return []
    from ...models import Document, Medicine

    entries: list[CatalogEntry] = []
    for name, in db.execute(select(Medicine.name).distinct()).all():
        if not name or not str(name).strip():
            continue
        n = str(name).strip()
        entries.append(CatalogEntry(canonical=n, generic=n, alias=n))

    for doc in db.scalars(
        select(Document).where(Document.type == "formulary", Document.status == "ready")
    ).all():
        text = doc.extracted_md or ""
        for line in text.splitlines():
            line = line.strip(" -*\t")
            if not line or line.startswith("#"):
                continue
            # First token-ish phrase before em-dash / dose
            piece = re.split(r"[—\-–|,;(]", line, maxsplit=1)[0].strip()
            piece = _NOISE.sub(" ", piece).strip()
            if 2 <= len(piece) <= 60 and re.search(r"[A-Za-z]", piece):
                entries.append(CatalogEntry(canonical=piece, generic=piece, alias=piece))
    return entries


def build_catalog(db: Session | None = None, extra: Iterable[str] | None = None) -> list[CatalogEntry]:
    entries = list(builtin_catalog())
    entries.extend(catalog_from_db(db))
    for name in extra or []:
        n = (name or "").strip()
        if n:
            entries.append(CatalogEntry(canonical=n, generic=n, alias=n))
    return entries


def match_medicine(raw_name: str, catalog: list[CatalogEntry], *, min_score: float = 0.62) -> MatchResult:
    """Fuzzy-match a handwritten/OCR medicine name against the catalog."""
    needle = normalize_name(raw_name)
    if not needle or not catalog:
        return MatchResult(entry=None, score=0.0, matched=False)

    best: CatalogEntry | None = None
    best_score = 0.0
    best_alias_score = -1.0
    for entry in catalog:
        alias_score = _similarity(needle, normalize_name(entry.alias))
        score = max(
            alias_score,
            _similarity(needle, normalize_name(entry.canonical)),
            _similarity(needle, normalize_name(entry.generic)),
        )
        # Tie-break on alias score so "Dolo" keeps the Dolo row, while
        # "Metformn" prefers the Metformin alias over Glycomet/Glucophage.
        better = score > best_score + 1e-9
        tied = abs(score - best_score) <= 1e-9 and score > 0
        if better or (tied and alias_score > best_alias_score):
            best_score = score
            best_alias_score = alias_score
            best = entry

    if best is None or best_score < min_score:
        return MatchResult(entry=None, score=best_score, matched=False)
    return MatchResult(entry=best, score=best_score, matched=True)


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    # Prefix / containment boosts for "Metformn" vs "Metformin", "Pan D" vs "Pan-D"
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) >= 3:
            return max(0.85, len(shorter) / max(1, len(longer)))
    ratio = SequenceMatcher(None, a, b).ratio()
    # Token-set style: compare sorted unique tokens
    ta, tb = set(a.split()), set(b.split())
    if ta and tb:
        inter = len(ta & tb)
        union = len(ta | tb)
        token = inter / union
        ratio = max(ratio, token)
    return ratio


# Frequency / timing → CarePlan schedule (HH:MM csv)
_FREQ_SCHEDULE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(qid|4\s*(times|/|\-)?\s*day|four times)\b", re.I), "08:00,12:00,16:00,20:00"),
    (re.compile(r"\b(tds|tid|3\s*(times|/|\-)?\s*day|thrice|three times)\b", re.I), "08:00,14:00,20:00"),
    (re.compile(r"\b(bd|bid|2\s*(times|/|\-)?\s*day|twice|two times)\b", re.I), "08:00,20:00"),
    (re.compile(r"\b(hs|night|bedtime|nocte)\b", re.I), "21:00"),
    (re.compile(r"\b(morning\s*(and|&)\s*evening|am\s*(and|&|/)\s*pm)\b", re.I), "08:00,20:00"),
    (re.compile(r"\b(od|qd|once|daily|everyday|1\s*(time|/|\-)?\s*day)\b", re.I), "08:00"),
    (re.compile(r"\b(sos|prn|if needed|as needed)\b", re.I), "09:00"),
]


def frequency_to_schedule(frequency: str, instructions: str = "") -> str:
    blob = f"{frequency} {instructions}".strip()
    if not blob:
        return "08:00"
    for pat, schedule in _FREQ_SCHEDULE:
        if pat.search(blob):
            return schedule
    # Pattern like 1-0-1 / 1-1-1
    m = re.search(r"\b([01])\s*[-/]\s*([01])\s*[-/]\s*([01])\b", blob)
    if m:
        times = []
        if m.group(1) == "1":
            times.append("08:00")
        if m.group(2) == "1":
            times.append("14:00")
        if m.group(3) == "1":
            times.append("20:00")
        return ",".join(times) if times else "08:00"
    return "08:00"
