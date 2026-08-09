from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CareEvent, Escalation, Patient, ScheduledCall, utcnow
from ..schemas import CareEventOut, PatientCreate, PatientOut, PatientSummaryOut, PatientUpdate
from ..services.careplus import adherence_for_patient

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("", response_model=PatientOut, status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.flush()
    db.add(CareEvent(
        patient_id=patient.id, type="discharge", severity="info",
        title="Patient discharged & enrolled",
        detail=f"{patient.name} enrolled in HealthcareOS follow-up. Diagnosis: {patient.diagnosis or 'n/a'}.",
    ))
    db.commit()
    db.refresh(patient)
    return patient


@router.get("", response_model=list[PatientSummaryOut])
def list_patients(db: Session = Depends(get_db)):
    patients = db.scalars(select(Patient).order_by(Patient.created_at.desc())).all()
    out = []
    for p in patients:
        out.append(_summarize(db, p))
    return out


@router.get("/{patient_id}", response_model=PatientSummaryOut)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    return _summarize(db, patient)


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: int, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "phone" in updates:
        updates["phone"] = PatientCreate.model_validate({**_as_create(patient), "phone": updates["phone"]}).phone
    if "timezone" in updates:
        PatientCreate.model_validate({**_as_create(patient), "timezone": updates["timezone"]})
    for k, v in updates.items():
        setattr(patient, k, v)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/{patient_id}/timeline", response_model=list[CareEventOut])
def timeline(patient_id: int, db: Session = Depends(get_db)):
    if not db.get(Patient, patient_id):
        raise HTTPException(404, "patient not found")
    return db.scalars(
        select(CareEvent).where(CareEvent.patient_id == patient_id).order_by(CareEvent.ts.asc())
    ).all()


@router.post("/{patient_id}/recovered", response_model=CareEventOut)
def mark_recovered(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")
    if patient.care_plan:
        patient.care_plan.status = "done"
    event = CareEvent(
        patient_id=patient_id, type="recovered", severity="info",
        title="Recovery complete", detail="Care journey closed by the care team.",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _summarize(db: Session, p: Patient) -> PatientSummaryOut:
    adherence = adherence_for_patient(db, p.id)
    open_esc = len(db.scalars(
        select(Escalation.id).where(Escalation.patient_id == p.id, Escalation.status == "open")
    ).all())
    next_call = db.scalar(
        select(ScheduledCall.due_at)
        .where(ScheduledCall.patient_id == p.id, ScheduledCall.status == "pending",
               ScheduledCall.due_at >= utcnow().replace(tzinfo=None))
        .order_by(ScheduledCall.due_at)
        .limit(1)
    )
    risk = "low"
    if open_esc > 0:
        risk = "high"
    elif adherence is not None and adherence < 70:
        risk = "medium"
    status = "recovered" if p.care_plan and p.care_plan.status == "done" else "active"
    base = PatientOut.model_validate(p).model_dump()
    return PatientSummaryOut(
        **base, adherence_pct=adherence, open_escalations=open_esc, risk=risk,
        status=status, next_call_at=next_call,
    )


def _as_create(p: Patient) -> dict:
    return {
        "name": p.name, "phone": p.phone, "preferred_language": p.preferred_language,
        "timezone": p.timezone, "diagnosis": p.diagnosis,
        "family_contact": p.family_contact, "notes": p.notes,
    }
