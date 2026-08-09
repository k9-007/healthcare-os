from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    CallLog, CarePlan, CareEvent, Escalation, ExtractedResponse, Patient, ScheduledCall, utcnow,
)
from ..schemas import AnalyticsSummaryOut, EscalationOut
from ..services.careplus import adherence_for_patient

router = APIRouter(tags=["analytics"])


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def summary(db: Session = Depends(get_db)):
    total_patients = db.scalar(select(func.count(Patient.id))) or 0
    active_plans = db.scalar(
        select(func.count(CarePlan.id)).where(CarePlan.status == "active")
    ) or 0

    # adherence across all took_medicine responses
    med_rows = db.scalars(
        select(ExtractedResponse.value).where(ExtractedResponse.key == "took_medicine")
    ).all()
    known = [v for v in med_rows if v in {"true", "false"}]
    taken = sum(1 for v in known if v == "true")
    skipped_slots = db.scalar(
        select(func.count(ScheduledCall.id)).where(
            ScheduledCall.kind == "medicine", ScheduledCall.status == "skipped"
        )
    ) or 0
    denom = len(known) + skipped_slots
    adherence_pct = round(100.0 * taken / denom, 1) if denom else 100.0

    missed_doses = db.scalar(
        select(func.count(CareEvent.id)).where(CareEvent.type == "missed_dose")
    ) or 0

    open_escalations = db.scalar(
        select(func.count(Escalation.id)).where(Escalation.status == "open")
    ) or 0

    # at-risk = open escalation OR adherence < 70
    at_risk = 0
    for pid in db.scalars(select(Patient.id)).all():
        p_esc = db.scalar(
            select(func.count(Escalation.id)).where(
                Escalation.patient_id == pid, Escalation.status == "open"
            )
        ) or 0
        adh = adherence_for_patient(db, pid)
        if p_esc > 0 or (adh is not None and adh < 70):
            at_risk += 1

    # follow-up completion
    fu_total = db.scalar(
        select(func.count(ScheduledCall.id)).where(
            ScheduledCall.kind == "followup",
            ScheduledCall.status.in_(["completed", "skipped", "failed", "no_answer"]),
        )
    ) or 0
    fu_done = db.scalar(
        select(func.count(ScheduledCall.id)).where(
            ScheduledCall.kind == "followup", ScheduledCall.status == "completed"
        )
    ) or 0
    followup_pct = round(100.0 * fu_done / fu_total, 1) if fu_total else 100.0

    # call success rate
    calls_total = db.scalar(select(func.count(CallLog.id))) or 0
    calls_done = db.scalar(
        select(func.count(CallLog.id)).where(CallLog.status == "completed")
    ) or 0
    call_success = round(100.0 * calls_done / calls_total, 1) if calls_total else 100.0

    # 7-day adherence trend from daily took_medicine responses
    trend = []
    today = utcnow().date()
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        day_rows = db.execute(
            select(ExtractedResponse.value)
            .join(CallLog, ExtractedResponse.call_log_id == CallLog.id)
            .where(
                ExtractedResponse.key == "took_medicine",
                func.date(CallLog.created_at) == day.isoformat(),
            )
        ).scalars().all()
        day_known = [v for v in day_rows if v in {"true", "false"}]
        pct = round(100.0 * sum(1 for v in day_known if v == "true") / len(day_known), 1) if day_known else None
        trend.append({"date": day.isoformat(), "adherence_pct": pct, "responses": len(day_known)})

    recent = db.scalars(
        select(Escalation).order_by(Escalation.created_at.desc()).limit(10)
    ).all()
    recent_out = []
    for e in recent:
        item = EscalationOut.model_validate(e)
        p = db.get(Patient, e.patient_id)
        item.patient_name = p.name if p else None
        recent_out.append(item)

    return AnalyticsSummaryOut(
        total_patients=total_patients,
        active_care_plans=active_plans,
        adherence_pct=adherence_pct,
        missed_doses=missed_doses,
        patients_at_risk=at_risk,
        open_escalations=open_escalations,
        followup_completion_pct=followup_pct,
        call_success_rate_pct=call_success,
        total_calls=calls_total,
        adherence_trend=trend,
        recent_escalations=recent_out,
    )


@router.get("/escalations", response_model=list[EscalationOut])
def list_escalations(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Escalation).order_by(Escalation.created_at.desc())
    if status:
        if status not in {"open", "ack", "closed"}:
            raise HTTPException(422, "status must be open|ack|closed")
        q = q.where(Escalation.status == status)
    out = []
    for e in db.scalars(q.limit(100)).all():
        item = EscalationOut.model_validate(e)
        p = db.get(Patient, e.patient_id)
        item.patient_name = p.name if p else None
        out.append(item)
    return out


@router.patch("/escalations/{escalation_id}", response_model=EscalationOut)
def update_escalation(escalation_id: int, status: str, db: Session = Depends(get_db)):
    if status not in {"open", "ack", "closed"}:
        raise HTTPException(422, "status must be open|ack|closed")
    esc = db.get(Escalation, escalation_id)
    if not esc:
        raise HTTPException(404, "escalation not found")
    esc.status = status
    db.commit()
    db.refresh(esc)
    item = EscalationOut.model_validate(esc)
    p = db.get(Patient, esc.patient_id)
    item.patient_name = p.name if p else None
    return item
