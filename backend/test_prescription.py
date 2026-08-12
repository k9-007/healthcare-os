"""Focused tests for prescription matching + structured output.

Vision is mocked via `vision_text=` so no SARVAM_API_KEY is required.
Run:  cd backend && python test_prescription.py
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# Allow `python test_prescription.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.prescription.catalog import (  # noqa: E402
    build_catalog,
    frequency_to_schedule,
    match_medicine,
    normalize_name,
)
from app.services.prescription.interpret import heuristic_interpret  # noqa: E402
from app.services.prescription.pipeline import process_prescription_bytes  # noqa: E402
from app.services.prescription.preprocess import preprocess_image_bytes  # noqa: E402

failures = 0


def check(cond: bool, msg: str) -> None:
    global failures
    failures += 0 if cond else 1
    print(f"{'ok  ' if cond else 'FAIL'} {msg}")


# --- normalize / match ---
check(normalize_name("Tab. Metformin") == "metformin", "normalize strips Tab.")
check(normalize_name("Pan-D 40mg") == "pan d", "normalize Pan-D")

catalog = build_catalog()
hit = match_medicine("Metformn", catalog)
check(hit.matched and hit.entry is not None, "fuzzy Metformn matches")
check(hit.entry.canonical == "Metformin" if hit.entry else False, "Metformn → Metformin")
check(hit.score >= 0.62, f"Metformn score high enough ({hit.score:.2f})")

dolo = match_medicine("Dolo", catalog)
check(dolo.matched and dolo.entry and dolo.entry.canonical == "Paracetamol", "Dolo → Paracetamol")

pan = match_medicine("Pan D", catalog)
check(pan.matched and pan.entry and pan.entry.canonical == "Pantoprazole", "Pan D → Pantoprazole")

miss = match_medicine("XylophoneZ9", catalog)
check(not miss.matched, "nonsense name unmatched")

# --- frequency → schedule ---
check(frequency_to_schedule("BD") == "08:00,20:00", "BD schedule")
check(frequency_to_schedule("TDS") == "08:00,14:00,20:00", "TDS schedule")
check(frequency_to_schedule("1-0-1") == "08:00,20:00", "1-0-1 schedule")
check(frequency_to_schedule("HS") == "21:00", "HS schedule")
check(frequency_to_schedule("SOS", "if fever") == "09:00", "SOS schedule")

# --- heuristic raw interpretation ---
SAMPLE_MD = """# Discharge medications

- Tab Metformn 500mg BD x 30 days after food
- Amlodipine 5 mg OD morning
- Dolo 650mg SOS if fever
Diet: low sugar
"""
raw = heuristic_interpret(SAMPLE_MD)
check(len(raw.medicines) >= 3, f"heuristic found ≥3 meds (got {len(raw.medicines)})")
names = [m.raw_name.lower() for m in raw.medicines]
check(any("metform" in n for n in names), "heuristic kept Metformn")
check(any(m.dose.lower().replace(" ", "") in {"500mg", "5mg", "650mg"} for m in raw.medicines), "heuristic doses")

# --- preprocess (tiny JPEG via Pillow if present) ---
try:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), color=(200, 200, 200)).save(buf, format="JPEG")
    processed, meta = preprocess_image_bytes(buf.getvalue(), filename="tiny.jpg")
    check(meta.get("resized") == "up", f"tiny image upscaled ({meta})")
    check(len(processed) > 0, "preprocess produced bytes")
except ImportError:
    print("skip preprocess (Pillow not installed)")

# --- end-to-end with mocked vision ---
async def _e2e() -> None:
    result = await process_prescription_bytes(
        b"not-a-real-image-but-vision-is-mocked",
        filename="rx.txt",  # will fail ext check
        persist_document=False,
        vision_text=SAMPLE_MD,
    )
    check(result.status == "failed", "rejects .txt upload")

    # Use a valid image extension; vision_text skips Sarvam
    jpeg = b"\xff\xd8\xff\xd9"  # minimal JPEG marker pair — preprocess may skip
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (640, 480), color=(255, 255, 255)).save(buf, format="JPEG")
        jpeg = buf.getvalue()
    except ImportError:
        pass

    result = await process_prescription_bytes(
        jpeg,
        filename="discharge.jpg",
        persist_document=False,
        vision_text=SAMPLE_MD,
        use_llm=False,
    )
    check(result.status in {"ok", "partial"}, f"e2e status {result.status}")
    check(len(result.medicines) >= 3, f"e2e medicines ≥3 (got {len(result.medicines)})")
    check(result.raw_interpretation.raw_text.strip() == SAMPLE_MD.strip(), "raw interpretation preserved")
    matched_names = {m.name for m in result.medicines if m.matched}
    check("Metformin" in matched_names, f"e2e matched Metformin ({matched_names})")
    check("Paracetamol" in matched_names or "Amlodipine" in matched_names, "e2e matched another catalog drug")
    met = next(m for m in result.medicines if m.name == "Metformin")
    check(met.schedule == "08:00,20:00", f"Metformin BD → {met.schedule}")
    check(met.dose.replace(" ", "").lower() == "500mg", f"Metformin dose {met.dose}")
    check(0.0 <= met.confidence <= 1.0, "confidence in range")
    # Structured JSON shape smoke
    payload = result.model_dump()
    check("raw_interpretation" in payload and "medicines" in payload, "structured JSON keys")
    check(isinstance(payload["medicines"][0]["confidence"], float), "confidence is float")


asyncio.run(_e2e())

print()
if failures:
    print(f"{failures} failure(s)")
    sys.exit(1)
print("all prescription tests passed")
