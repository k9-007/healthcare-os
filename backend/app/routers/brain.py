from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import BrainAnswerOut, BrainAskIn
from ..services import brain

router = APIRouter(prefix="/brain", tags=["brain"])


@router.post("/ask", response_model=BrainAnswerOut)
async def ask(payload: BrainAskIn, db: Session = Depends(get_db)):
    """Cited Q&A over ingested documents — PageIndex tree search + cite-or-refuse."""
    return await brain.ask(db, payload.question.strip(), payload.patient_id)
