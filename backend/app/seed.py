"""Demo seed data — two multilingual patients, care plans, and a discharge
summary indexed into Brain, so the app demos end-to-end from first boot."""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CareEvent, CarePlan, Document, FollowUpQuestion, Medicine, Patient
from .services import brain
from .services.careplus import materialize_schedule

logger = logging.getLogger("seed")

DISCHARGE_MD = """# Discharge Summary — Mrs. Anita Sharma

## Patient Details
Name: Anita Sharma. Age: 58. Diagnosis: Type 2 Diabetes Mellitus with hypertension.
Admitted for glycemic control. Discharged in stable condition.

## Medications on Discharge
- Metformin 500 mg — twice daily (morning and evening), to be taken after food.
- Amlodipine 5 mg — once daily in the morning.
Do not skip doses. If a dose is missed, take it as soon as remembered unless the next dose is near.

## Diet & Lifestyle
Low-sugar, low-salt diet. 30 minutes of walking daily. Monitor blood glucose twice a week.

## Warning Signs — Seek Immediate Care
Severe chest pain, breathlessness, fainting, blood sugar below 70 mg/dL with sweating or confusion.
In any of these situations the patient must contact the hospital emergency line immediately.

## Follow-up
Review in OPD after 14 days with fasting blood sugar and HbA1c reports.
"""

GUIDELINE_MD = """# Hospital SOP — Post-Discharge Diabetes Care

## Medication Adherence Protocol
Patients discharged on Metformin must be monitored for adherence for the first 30 days.
Two or more missed doses in a week requires a doctor review and a caregiver notification.

## Hypoglycemia Management
If a patient reports dizziness, sweating or confusion, check for hypoglycemia.
Advise 15 g fast-acting glucose and re-check in 15 minutes. Escalate if symptoms persist.

## Escalation Criteria
Chest pain, breathlessness, or fainting reported on any follow-up call is a HIGH urgency
escalation: alert the duty nurse immediately and advise emergency care.
"""


def seed_if_empty(db: Session) -> bool:
    if db.scalar(select(Patient.id).limit(1)) is not None:
        return False
    logger.info("seeding demo data")

    sharma = Patient(
        name="Anita Sharma", age=58, sex="F", phone="+916355351675", preferred_language="hi-IN",
        timezone="Asia/Kolkata", diagnosis="Type 2 Diabetes + Hypertension",
        family_contact="+919876500000 (son, Rohit)",
        notes="58F, discharged after glycemic control admission.",
    )
    murugan = Patient(
        name="Murugan Velu", age=64, sex="M", phone="+919812345678", preferred_language="ta-IN",
        timezone="Asia/Kolkata", diagnosis="Post-operative knee replacement",
        family_contact="+919812300000 (daughter, Priya)",
        notes="64M, TKR surgery, mobility exercises prescribed.",
    )
    db.add_all([sharma, murugan])
    db.flush()

    for p, dx in ((sharma, "diabetes management"), (murugan, "knee replacement recovery")):
        db.add(CareEvent(
            patient_id=p.id, type="discharge", severity="info",
            title="Patient discharged & enrolled",
            detail=f"{p.name} enrolled in HealthcareOS follow-up for {dx}.",
        ))

    plan1 = CarePlan(patient_id=sharma.id, status="active", start_date=date.today(),
                     call_window="08:00-21:00")
    plan2 = CarePlan(patient_id=murugan.id, status="active", start_date=date.today(),
                     call_window="09:00-20:00")
    db.add_all([plan1, plan2])
    db.flush()

    db.add_all([
        Medicine(care_plan_id=plan1.id, name="Metformin", dose="500mg",
                 schedule="08:00,20:00", instructions="after food",
                 start_date=date.today(), end_date=date.today() + timedelta(days=30)),
        Medicine(care_plan_id=plan1.id, name="Amlodipine", dose="5mg",
                 schedule="08:00", instructions="",
                 start_date=date.today(), end_date=date.today() + timedelta(days=30)),
        Medicine(care_plan_id=plan2.id, name="Paracetamol", dose="650mg",
                 schedule="09:00,21:00", instructions="only if pain",
                 start_date=date.today(), end_date=date.today() + timedelta(days=14)),
    ])
    db.add_all([
        FollowUpQuestion(care_plan_id=plan1.id, text="Are you checking your blood sugar twice a week?",
                         type="boolean", ask_after_days=2, at_time="10:00"),
        FollowUpQuestion(care_plan_id=plan1.id, text="On a scale of 0 to 10, how is your energy level?",
                         type="number", ask_after_days=3, at_time="10:00"),
        FollowUpQuestion(care_plan_id=plan2.id, text="Can you walk without support?",
                         type="boolean", ask_after_days=2, at_time="11:00"),
        FollowUpQuestion(care_plan_id=plan2.id, text="Rate your knee pain from 0 to 10.",
                         type="number", ask_after_days=1, at_time="11:00"),
    ])
    db.add_all([
        CareEvent(patient_id=sharma.id, type="med_started", severity="info",
                  title="Care plan activated", detail="Metformin 500mg + Amlodipine 5mg; 2 follow-up questions."),
        CareEvent(patient_id=murugan.id, type="med_started", severity="info",
                  title="Care plan activated", detail="Paracetamol 650mg PRN; 2 follow-up questions."),
    ])
    db.flush()

    doc1 = Document(patient_id=sharma.id, title="Discharge Summary — Anita Sharma",
                    type="discharge", extracted_md=DISCHARGE_MD, status="ready")
    doc2 = Document(patient_id=None, title="SOP — Post-Discharge Diabetes Care",
                    type="sop", extracted_md=GUIDELINE_MD, status="ready")
    db.add_all([doc1, doc2])
    db.flush()
    brain.index_document(db, doc1)
    brain.index_document(db, doc2)

    materialize_schedule(db, plan1)
    materialize_schedule(db, plan2)

    db.commit()
    logger.info("seed complete: 2 patients, 2 plans, 2 documents")
    return True
