"""End-to-end prescription pipeline orchestration."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Document
from ..sarvam import SarvamUnavailable, sarvam
from .catalog import build_catalog, frequency_to_schedule, match_medicine, normalize_name
from .interpret import heuristic_interpret, interpret_raw_text
from .preprocess import preprocess_image_file
from .schemas import MatchedMedicine, PrescriptionParseResult, RawInterpretation

logger = logging.getLogger("prescription.pipeline")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
ALLOWED_EXTS = IMAGE_EXTS | {".pdf"}


async def process_prescription_bytes(
    content: bytes,
    *,
    filename: str = "prescription.jpg",
    language: str = "en-IN",
    patient_id: int | None = None,
    persist_document: bool = True,
    db: Session | None = None,
    vision_text: str | None = None,
    use_llm: bool = True,
) -> PrescriptionParseResult:
    """Accept upload bytes and run the full pipeline.

    `vision_text` lets tests (and offline demos) skip the live Sarvam Vision call.
    """
    settings = get_settings()
    ext = Path(filename).suffix.lower() or ".jpg"
    if ext not in ALLOWED_EXTS:
        return PrescriptionParseResult(
            status="failed",
            error=f"unsupported file type '{ext}' — allowed: {sorted(ALLOWED_EXTS)}",
        )
    if not content:
        return PrescriptionParseResult(status="failed", error="uploaded file is empty")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        return PrescriptionParseResult(
            status="failed",
            error=f"file exceeds {settings.max_upload_mb} MB limit",
        )

    safe_name = f"rx_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    dest = settings.uploads_dir / safe_name
    dest.write_bytes(content)
    rel_path = f"uploads/{safe_name}"

    doc_id: int | None = None
    if persist_document and db is not None:
        doc = Document(
            patient_id=patient_id,
            title=Path(filename).stem or "Prescription",
            type="prescription",
            file_path=rel_path,
            status="extracting",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id

    try:
        result = await process_prescription_file(
            dest,
            language=language,
            db=db,
            vision_text=vision_text,
            use_llm=use_llm,
        )
    except Exception as e:
        logger.exception("prescription pipeline crashed")
        result = PrescriptionParseResult(status="failed", error=f"{type(e).__name__}: {e}")

    result.document_id = doc_id
    result.file_path = rel_path

    if persist_document and db is not None and doc_id is not None:
        doc = db.get(Document, doc_id)
        if doc:
            raw = result.raw_interpretation.raw_text
            doc.extracted_md = raw or doc.extracted_md
            if result.status == "failed":
                doc.status = "failed"
                doc.error = result.error or "prescription parse failed"
            else:
                doc.status = "ready"
                doc.error = ""
            db.commit()

    return result


async def process_prescription_file(
    path: Path,
    *,
    language: str = "en-IN",
    db: Session | None = None,
    vision_text: str | None = None,
    use_llm: bool = True,
) -> PrescriptionParseResult:
    warnings: list[str] = []

    vision_path, prep_meta = preprocess_image_file(path)
    if prep_meta.get("skipped"):
        warnings.append(f"preprocess skipped: {prep_meta.get('reason', 'unknown')}")

    # --- Handwriting / text detection via Sarvam Vision (or injected text) ---
    raw_text = (vision_text or "").strip()
    if not raw_text:
        if not sarvam.settings.sarvam_configured:
            return PrescriptionParseResult(
                status="failed",
                preprocessing=prep_meta,
                warnings=warnings,
                error="SARVAM_API_KEY is not configured — cannot run vision extraction. "
                      "Set the key in backend/.env, or pass pre-extracted text in tests.",
            )
        try:
            raw_text = await sarvam.vision_extract(str(vision_path), language)
        except SarvamUnavailable as e:
            return PrescriptionParseResult(
                status="failed",
                preprocessing=prep_meta,
                warnings=warnings,
                error=f"vision extraction failed: {e}",
            )

    if not raw_text.strip():
        return PrescriptionParseResult(
            status="failed",
            preprocessing=prep_meta,
            raw_interpretation=RawInterpretation(raw_text=""),
            warnings=warnings,
            error="vision returned empty text — image may be blank or unreadable",
        )

    # --- Raw interpretation ---
    raw = await interpret_raw_text(raw_text, use_llm=use_llm)
    if not raw.medicines:
        # Second chance: pure heuristic if LLM returned nothing useful
        alt = heuristic_interpret(raw_text)
        if alt.medicines:
            warnings.append("LLM found no medicines; used heuristic line parser")
            raw = alt
        else:
            warnings.append("no medicine lines detected in raw interpretation")

    # --- Medicine name matching ---
    catalog = build_catalog(db)
    matched: list[MatchedMedicine] = []
    unmatched: list[str] = []

    for line in raw.medicines:
        hit = match_medicine(line.raw_name, catalog)
        schedule = frequency_to_schedule(line.frequency, line.instructions)
        if hit.matched and hit.entry is not None:
            conf = min(1.0, 0.55 * hit.score + 0.25 + (0.1 if line.dose else 0) + (0.1 if line.frequency else 0))
            matched.append(
                MatchedMedicine(
                    name=hit.entry.canonical,
                    matched_name=hit.entry.canonical,
                    generic_name=hit.entry.generic,
                    brand_name=(
                        hit.entry.alias
                        if hit.entry.alias.lower() != hit.entry.canonical.lower()
                        and normalize_name(hit.entry.alias) == normalize_name(line.raw_name)
                        else ""
                    ),
                    dose=line.dose,
                    frequency=line.frequency,
                    schedule=schedule,
                    duration=line.duration,
                    instructions=line.instructions,
                    confidence=round(conf, 3),
                    match_score=round(hit.score, 3),
                    raw_name=line.raw_name,
                    matched=True,
                )
            )
        else:
            conf = 0.35 + (0.1 if line.dose else 0) + (0.1 if line.frequency else 0)
            unmatched.append(line.raw_line or line.raw_name)
            matched.append(
                MatchedMedicine(
                    name=line.raw_name,
                    matched_name="",
                    generic_name="",
                    brand_name="",
                    dose=line.dose,
                    frequency=line.frequency,
                    schedule=schedule,
                    duration=line.duration,
                    instructions=line.instructions,
                    confidence=round(min(conf, 0.55), 3),
                    match_score=round(hit.score, 3),
                    raw_name=line.raw_name,
                    matched=False,
                )
            )

    status = "ok"
    if not matched:
        status = "partial" if raw_text.strip() else "failed"
    elif unmatched and len(unmatched) == len(matched):
        status = "partial"
    elif unmatched:
        status = "partial"

    return PrescriptionParseResult(
        status=status,
        preprocessing=prep_meta,
        raw_interpretation=raw,
        medicines=matched,
        unmatched=unmatched,
        warnings=warnings,
    )
