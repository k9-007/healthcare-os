from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CareEvent, CarePlan, FollowUpQuestion, Medicine, Patient
from ..schemas import CarePlanIn, CarePlanOut
from ..services.careplus import materialize_schedule

router = APIRouter(prefix="/patients/{patient_id}/care-plan", tags=["care-plans"])


@router.post("", response_model=CarePlanOut)
def upsert_care_plan(patient_id: int, payload: CarePlanIn, db: Session = Depends(get_db)):
    """Create/replace the patient's care plan and auto-materialize the schedule.

    The dashboard is the single source of truth — every save regenerates
    future pending ScheduledCall slots (placed/completed history is untouched).
    """
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")

    plan = patient.care_plan
    is_new = plan is None
    if plan is None:
        plan = CarePlan(patient_id=patient_id)
        db.add(plan)

    plan.status = payload.status
    plan.start_date = payload.start_date or plan.start_date or date.today()
    plan.call_window = payload.call_window
    plan.max_retries = payload.max_retries
    plan.retry_backoff = payload.retry_backoff
    db.flush()

    # replace medicines & questions with the submitted config
    for med in list(plan.medicines):
        db.delete(med)
    for q in list(plan.questions):
        db.delete(q)
    db.flush()
    for m in payload.medicines:
        db.add(Medicine(care_plan_id=plan.id, **m.model_dump()))
    for q in payload.questions:
        db.add(FollowUpQuestion(care_plan_id=plan.id, **q.model_dump()))
    db.flush()
    db.refresh(plan)

    slots = materialize_schedule(db, plan)

    if is_new and payload.medicines:
        db.add(CareEvent(
            patient_id=patient_id, type="med_started", severity="info",
            title="Care plan activated",
            detail=f"{len(payload.medicines)} medicine(s), {len(payload.questions)} follow-up question(s); "
                   f"{slots} call slot(s) scheduled.",
        ))
    db.commit()
    db.refresh(plan)
    return plan


@router.get("", response_model=CarePlanOut)
def get_care_plan(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    if not patient.care_plan:
        raise HTTPException(404, "no care plan for this patient yet")
    return patient.care_plan
