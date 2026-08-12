"""Turn vision markdown into a RawInterpretation (medicine lines).

Prefers Sarvam chat_json when configured; falls back to a deterministic
regex/heuristic parser so matching + CarePlan mapping still work offline.
"""

from __future__ import annotations

import logging
import re

from ..sarvam import SarvamUnavailable, sarvam
from .schemas import RawInterpretation, RawMedicineLine

logger = logging.getLogger("prescription.interpret")

_DOSE = re.compile(
    r"(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|iu|units?|%))",
    re.I,
)
_FREQ = re.compile(
    r"\b(?P<freq>qid|tds|tid|bd|bid|od|qd|hs|sos|prn|"
    r"once(?:\s+daily)?|twice(?:\s+daily)?|thrice(?:\s+daily)?|"
    r"\d\s*[-/]\s*\d\s*[-/]\s*\d|"
    r"morning(?:\s+and\s+evening)?|night|daily)\b",
    re.I,
)
_DURATION = re.compile(
    r"(?:x|for|×)\s*(?P<dur>\d+\s*(?:day|days|week|weeks|month|months|wk|wks))",
    re.I,
)
_INSTR = re.compile(
    r"\b(?P<instr>after\s+food|before\s+food|with\s+food|empty\s+stomach|"
    r"only\s+if\s+pain|if\s+fever|with\s+water|at\s+bedtime)\b",
    re.I,
)
_MED_LINE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])?\s*(?:tab\.?|cap\.?|tablet|capsule)?\s*"
    r"(?P<name>[A-Za-z][A-Za-z0-9+./\-]{1,40}(?:\s+[A-Za-z][A-Za-z0-9+./\-]{0,20}){0,3})",
    re.I,
)

_SYSTEM = """You extract medicines from an Indian prescription / discharge medication list.
Return ONLY JSON with this shape:
{
  "patient_name_guess": "",
  "doctor_name_guess": "",
  "notes": ["optional free-text notes"],
  "medicines": [
    {
      "raw_name": "drug name as written",
      "raw_line": "full line",
      "dose": "500mg",
      "frequency": "BD",
      "duration": "5 days",
      "instructions": "after food"
    }
  ]
}
Rules:
- Prefer brand/generic names exactly as written (including misspellings).
- frequency may be OD/BD/TDS/QID/HS/SOS/1-0-1/etc.
- Skip headers, diagnoses, diet, and non-drug lines.
- If nothing looks like a medicine, return medicines: [].
"""


async def interpret_raw_text(raw_text: str, *, use_llm: bool = True) -> RawInterpretation:
    """LLM interpret when Sarvam is up; else heuristic parse."""
    text = (raw_text or "").strip()
    base = RawInterpretation(raw_text=text)
    if not text:
        return base

    if use_llm and sarvam.settings.sarvam_configured:
        try:
            data = await sarvam.chat_json(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": text[:8000]},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            if isinstance(data, dict):
                return _from_llm_dict(text, data)
        except SarvamUnavailable as e:
            logger.warning("prescription LLM interpret unavailable: %s — using heuristic", e)
        except Exception as e:
            logger.warning("prescription LLM interpret failed: %s — using heuristic", e)

    return heuristic_interpret(text)


def heuristic_interpret(raw_text: str) -> RawInterpretation:
    """Offline parser for markdown / OCR lines — used by tests and fallback."""
    medicines: list[RawMedicineLine] = []
    notes: list[str] = []
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        lower = line.lower()
        if any(k in lower for k in ("diagnosis", "diet", "warning", "follow-up", "follow up", "patient detail")):
            notes.append(line)
            continue
        med = _parse_line(line)
        if med:
            medicines.append(med)
        elif len(line) > 8:
            notes.append(line)
    return RawInterpretation(raw_text=raw_text, medicines=medicines, notes=notes[:20])


def _parse_line(line: str) -> RawMedicineLine | None:
    m = _MED_LINE.match(line)
    if not m:
        return None
    name = m.group("name").strip(" .-")
    # Reject lines that are clearly not drugs
    if name.lower() in {"name", "medications", "medication", "discharge", "summary"}:
        return None
    if len(name) < 2:
        return None

    dose = ""
    dm = _DOSE.search(line)
    if dm:
        dose = re.sub(r"\s+", "", dm.group("dose"))

    frequency = ""
    fm = _FREQ.search(line)
    if fm:
        frequency = fm.group("freq").upper() if len(fm.group("freq")) <= 4 else fm.group("freq").lower()

    duration = ""
    dur = _DURATION.search(line)
    if dur:
        duration = dur.group("dur").strip()

    instructions = ""
    im = _INSTR.search(line)
    if im:
        instructions = im.group("instr").strip().lower()

    # If we found neither dose nor frequency, require a medicine-ish cue
    if not dose and not frequency:
        if not re.search(r"\b(tab|cap|mg|mcg|syrup|inj)\b", line, re.I):
            return None

    return RawMedicineLine(
        raw_name=name,
        raw_line=line,
        dose=dose,
        frequency=frequency,
        duration=duration,
        instructions=instructions,
    )


def _from_llm_dict(raw_text: str, data: dict) -> RawInterpretation:
    meds: list[RawMedicineLine] = []
    for item in data.get("medicines") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("raw_name") or item.get("name") or "").strip()
        if not name:
            continue
        meds.append(
            RawMedicineLine(
                raw_name=name,
                raw_line=str(item.get("raw_line") or ""),
                dose=str(item.get("dose") or ""),
                frequency=str(item.get("frequency") or ""),
                duration=str(item.get("duration") or ""),
                instructions=str(item.get("instructions") or ""),
            )
        )
    notes = [str(n) for n in (data.get("notes") or []) if str(n).strip()]
    return RawInterpretation(
        raw_text=raw_text,
        medicines=meds,
        notes=notes[:20],
        patient_name_guess=str(data.get("patient_name_guess") or ""),
        doctor_name_guess=str(data.get("doctor_name_guess") or ""),
    )
