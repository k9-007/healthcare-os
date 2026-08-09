from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Patient, ScheduledCall, utcnow
from ..schemas import SchedulePatchIn, ScheduledCallOut
from ..services import scheduler
from ..services.careplus import materialize_schedule

router = APIRouter(tags=["schedule"])


@router.get("/schedule/upcoming", response_model=list[ScheduledCallOut])
def global_queue(limit: int = 50, db: Session = Depends(get_db)):
    """Global upcoming-calls queue for the dashboard."""
    rows = db.scalars(
        select(ScheduledCall)
        .where(ScheduledCall.status.in_(["pending", "no_answer", "failed", "placed"]))
        .order_by(ScheduledCall.due_at)
        .limit(min(limit, 200))
    ).all()
    return [_with_name(db, sc) for sc in rows]


@router.get("/patients/{patient_id}/schedule", response_model=list[ScheduledCallOut])
def patient_schedule(patient_id: int, db: Session = Depends(get_db)):
    if not db.get(Patient, patient_id):
        raise HTTPException(404, "patient not found")
    rows = db.scalars(
        select(ScheduledCall)
        .where(ScheduledCall.patient_id == patient_id)
        .order_by(ScheduledCall.due_at.desc())
        .limit(100)
    ).all()
    return [_with_name(db, sc) for sc in rows]


@router.post("/patients/{patient_id}/schedule/rematerialize")
def rematerialize(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    if not patient.care_plan:
        raise HTTPException(404, "no care plan to materialize")
    slots = materialize_schedule(db, patient.care_plan)
    db.commit()
    return {"materialized": slots}


@router.post("/schedule/run-now")
async def run_now():
    """Force a scheduler tick immediately (demo control)."""
    return await scheduler.tick()


@router.post("/schedule/{scheduled_call_id}/simulate", response_model=ScheduledCallOut)
async def simulate_slot(scheduled_call_id: int, db: Session = Depends(get_db)):
    """Fire one specific slot right now regardless of its due time (demo control)."""
    sc = db.get(ScheduledCall, scheduled_call_id)
    if not sc:
        raise HTTPException(404, "scheduled call not found")
    if sc.status not in {"pending", "no_answer", "failed"}:
        raise HTTPException(409, f"slot is '{sc.status}' — only pending/no_answer/failed slots can fire")
    patient = db.get(Patient, sc.patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    from ..services.scheduler import _place_scheduled_call
    await _place_scheduled_call(db, sc, patient)
    db.commit()
    db.refresh(sc)
    return _with_name(db, sc)


@router.patch("/schedule/{scheduled_call_id}", response_model=ScheduledCallOut)
def patch_slot(scheduled_call_id: int, payload: SchedulePatchIn, db: Session = Depends(get_db)):
    """Snooze / skip / reschedule one slot."""
    sc = db.get(ScheduledCall, scheduled_call_id)
    if not sc:
        raise HTTPException(404, "scheduled call not found")
    if sc.status in {"completed", "placed"}:
        raise HTTPException(409, f"cannot modify a '{sc.status}' slot")

    now = utcnow().replace(tzinfo=None)
    if payload.action == "skip":
        sc.status = "skipped"
        sc.last_error = "skipped by care team"
    elif payload.action == "snooze":
        minutes = payload.minutes or 30
        sc.next_attempt_at = now + timedelta(minutes=minutes)
        if sc.due_at <= now:
            sc.due_at = sc.next_attempt_at
    elif payload.action == "reschedule":
        if not payload.due_at:
            raise HTTPException(422, "reschedule requires due_at")
        new_due = payload.due_at.replace(tzinfo=None)
        sc.due_at = new_due
        sc.next_attempt_at = new_due
        sc.status = "pending"
    db.commit()
    db.refresh(sc)
    return _with_name(db, sc)


def _with_name(db: Session, sc: ScheduledCall) -> ScheduledCallOut:
    out = ScheduledCallOut.model_validate(sc)
    patient = db.get(Patient, sc.patient_id)
    out.patient_name = patient.name if patient else None
    out.language = patient.preferred_language if patient else None
    return out
