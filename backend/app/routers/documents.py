import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import DocChunk, Document
from ..schemas import DocumentOut
from ..services import brain
from ..services.sarvam import SarvamUnavailable, sarvam

logger = logging.getLogger("documents")
router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".md", ".txt"}
DOC_TYPES = {"guideline", "sop", "discharge", "lab", "formulary", "prescription"}


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    type: str = Form("guideline"),
    patient_id: int | None = Form(None),
    language: str = Form("en-IN"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if type not in DOC_TYPES:
        raise HTTPException(422, f"type must be one of {sorted(DOC_TYPES)}")

    ext = Path(file.filename or "upload.bin").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(422, f"unsupported file type '{ext}' — allowed: {sorted(ALLOWED_EXTS)}")

    content = await file.read()
    if not content:
        raise HTTPException(422, "uploaded file is empty")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB limit")

    safe_name = f"doc_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    dest = settings.uploads_dir / safe_name
    dest.write_bytes(content)

    doc = Document(
        patient_id=patient_id,
        title=title.strip() or (file.filename or safe_name),
        type=type,
        file_path=f"uploads/{safe_name}",
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    if ext in {".md", ".txt"}:
        # text uploads index synchronously — no Vision round-trip needed
        doc.extracted_md = content.decode("utf-8", errors="replace")
        doc.status = "ready"
        brain.index_document(db, doc)
        db.commit()
        db.refresh(doc)
    else:
        doc.status = "extracting"
        db.commit()
        background.add_task(_extract_in_background, doc.id, str(dest), language)

    return _to_out(db, doc)


def _extract_in_background(doc_id: int, path: str, language: str) -> None:
    """Vision job pipeline runs outside the request; document status tracks it."""
    import asyncio

    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if not doc:
            return
        try:
            md = asyncio.run(sarvam.vision_extract(path, language))
            doc.extracted_md = md
            doc.status = "ready"
            doc.error = ""
            brain.index_document(db, doc)
        except SarvamUnavailable as e:
            doc.status = "failed"
            doc.error = f"{e} — you can re-upload the content as .md/.txt instead."
            logger.error("vision extraction failed for doc %s: %s", doc_id, e)
        db.commit()
    finally:
        db.close()


@router.get("", response_model=list[DocumentOut])
def list_documents(patient_id: int | None = None, db: Session = Depends(get_db)):
    q = select(Document).order_by(Document.created_at.desc())
    if patient_id is not None:
        q = q.where((Document.patient_id == patient_id) | (Document.patient_id.is_(None)))
    return [_to_out(db, d, include_md=False) for d in db.scalars(q).all()]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    return _to_out(db, doc, include_md=True)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    db.delete(doc)
    db.commit()


def _to_out(db: Session, doc: Document, include_md: bool = False) -> DocumentOut:
    count = db.scalar(select(func.count(DocChunk.id)).where(DocChunk.document_id == doc.id)) or 0
    out = DocumentOut.model_validate(doc)
    out.chunk_count = count
    out.pages = db.scalar(
        select(func.max(DocChunk.page)).where(DocChunk.document_id == doc.id)
    ) or (1 if doc.extracted_md else 0)
    if doc.file_path:
        file = get_settings().data_path / doc.file_path
        if file.exists():
            out.size_kb = max(1, file.stat().st_size // 1024)
    if not out.size_kb and doc.extracted_md:
        out.size_kb = max(1, len(doc.extracted_md.encode()) // 1024)
    if doc.extracted_md:
        out.excerpt = doc.extracted_md.strip()[:220]
    if not include_md:
        out.extracted_md = None
    return out
