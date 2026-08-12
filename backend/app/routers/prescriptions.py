"""Prescription image upload → structured medicines JSON."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Patient
from ..schemas import PrescriptionParseOut
from ..services.prescription.pipeline import ALLOWED_EXTS, process_prescription_bytes

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


@router.post("/parse", response_model=PrescriptionParseOut)
async def parse_prescription(
    file: UploadFile = File(...),
    patient_id: int | None = Form(None),
    language: str = Form("en-IN"),
    persist: bool = Form(True),
    db: Session = Depends(get_db),
):
    """Upload a prescription / discharge meds image (or PDF) and get Structured JSON.

    Stages: preprocess → Sarvam vision → raw interpretation → medicine matching.
    Matched medicines use CarePlan-compatible `schedule` (HH:MM csv) so the
    Care plan builder can accept them without remapping.
    """
    if patient_id is not None and db.get(Patient, patient_id) is None:
        raise HTTPException(404, "patient not found")

    ext = Path(file.filename or "rx.jpg").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(422, f"unsupported file type '{ext}' — allowed: {sorted(ALLOWED_EXTS)}")

    content = await file.read()
    if not content:
        raise HTTPException(422, "uploaded file is empty")

    result = await process_prescription_bytes(
        content,
        filename=file.filename or f"prescription{ext}",
        language=language,
        patient_id=patient_id,
        persist_document=persist,
        db=db,
    )
    if result.status == "failed" and result.error and "SARVAM_API_KEY" in result.error:
        raise HTTPException(503, result.error)
    if result.status == "failed" and result.error and "vision extraction failed" in result.error:
        raise HTTPException(502, result.error)
    if result.status == "failed":
        raise HTTPException(422, result.error or "prescription parse failed")
    return result
