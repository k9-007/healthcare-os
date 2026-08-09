from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    age: Mapped[int] = mapped_column(Integer, default=0)
    sex: Mapped[str] = mapped_column(String(1), default="F")  # F|M
    phone: Mapped[str] = mapped_column(String(32))
    preferred_language: Mapped[str] = mapped_column(String(16), default="hi-IN")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    diagnosis: Mapped[str] = mapped_column(String(500), default="")
    family_contact: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    care_plan: Mapped["CarePlan | None"] = relationship(
        back_populates="patient", uselist=False, cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(back_populates="patient")
    call_logs: Mapped[list["CallLog"]] = relationship(back_populates="patient")
    events: Mapped[list["CareEvent"]] = relationship(back_populates="patient")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="patient")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(32), default="guideline")  # guideline|sop|discharge|lab|formulary
    file_path: Mapped[str] = mapped_column(String(500), default="")
    extracted_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|extracting|ready|failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped["Patient | None"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=1)
    section: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="chunks")


class CarePlan(Base):
    __tablename__ = "care_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|done
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    call_window: Mapped[str] = mapped_column(String(16), default="08:00-20:00")
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff: Mapped[str] = mapped_column(String(64), default="15,60,240")  # minutes csv
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped["Patient"] = relationship(back_populates="care_plan")
    medicines: Mapped[list["Medicine"]] = relationship(back_populates="care_plan", cascade="all, delete-orphan")
    questions: Mapped[list["FollowUpQuestion"]] = relationship(
        back_populates="care_plan", cascade="all, delete-orphan"
    )
    scheduled_calls: Mapped[list["ScheduledCall"]] = relationship(back_populates="care_plan")


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(primary_key=True)
    care_plan_id: Mapped[int] = mapped_column(ForeignKey("care_plans.id"))
    name: Mapped[str] = mapped_column(String(200))
    dose: Mapped[str] = mapped_column(String(100), default="")
    schedule: Mapped[str] = mapped_column(String(200), default="08:00")  # csv of HH:MM local times
    window_minutes: Mapped[int] = mapped_column(Integer, default=30)
    instructions: Mapped[str] = mapped_column(String(300), default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    care_plan: Mapped["CarePlan"] = relationship(back_populates="medicines")


class FollowUpQuestion(Base):
    __tablename__ = "followup_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    care_plan_id: Mapped[int] = mapped_column(ForeignKey("care_plans.id"))
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16), default="boolean")  # boolean|number|enum|short
    options: Mapped[str] = mapped_column(String(300), default="")  # csv for enum
    ask_after_days: Mapped[int] = mapped_column(Integer, default=1)
    at_time: Mapped[str] = mapped_column(String(8), default="10:00")

    care_plan: Mapped["CarePlan"] = relationship(back_populates="questions")


class ScheduledCall(Base):
    __tablename__ = "scheduled_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    care_plan_id: Mapped[int | None] = mapped_column(ForeignKey("care_plans.id"), nullable=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    kind: Mapped[str] = mapped_column(String(16))  # medicine|followup|callback
    due_at: Mapped[datetime] = mapped_column(DateTime)  # UTC
    slot_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending|placed|completed|failed|skipped|no_answer
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    call_log_id: Mapped[int | None] = mapped_column(ForeignKey("call_logs.id"), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    care_plan: Mapped["CarePlan | None"] = relationship(back_populates="scheduled_calls")
    targets: Mapped[list["CallTarget"]] = relationship(
        back_populates="scheduled_call", cascade="all, delete-orphan"
    )
    call_log: Mapped["CallLog | None"] = relationship(foreign_keys=[call_log_id])


class CallTarget(Base):
    __tablename__ = "call_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_call_id: Mapped[int] = mapped_column(ForeignKey("scheduled_calls.id"))
    ref_type: Mapped[str] = mapped_column(String(16))  # medicine|followup
    ref_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(400))

    scheduled_call: Mapped["ScheduledCall"] = relationship(back_populates="targets")


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    direction: Mapped[str] = mapped_column(String(16), default="outbound")
    mode: Mapped[str] = mapped_column(String(16), default="simulation")  # plivo|simulation
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|ringing|completed|failed
    kind: Mapped[str] = mapped_column(String(16), default="medicine")  # medicine|followup|callback|manual
    script_text: Mapped[str] = mapped_column(Text, default="")
    script_text_translated: Mapped[str] = mapped_column(Text, default="")
    tts_audio_path: Mapped[str] = mapped_column(String(500), default="")
    recording_path: Mapped[str] = mapped_column(String(500), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    transcript_english: Mapped[str] = mapped_column(Text, default="")
    detected_language: Mapped[str] = mapped_column(String(16), default="")
    language_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # Provider call id — Plivo CallUUID (column name kept for SQLite compat)
    twilio_sid: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped["Patient"] = relationship(back_populates="call_logs")
    responses: Mapped[list["ExtractedResponse"]] = relationship(
        back_populates="call_log", cascade="all, delete-orphan"
    )


class ExtractedResponse(Base):
    __tablename__ = "extracted_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_log_id: Mapped[int] = mapped_column(ForeignKey("call_logs.id"))
    question_id: Mapped[int | None] = mapped_column(ForeignKey("followup_questions.id"), nullable=True)
    medicine_id: Mapped[int | None] = mapped_column(ForeignKey("medicines.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[str] = mapped_column(Text, default="")
    value_type: Mapped[str] = mapped_column(String(16), default="text")  # boolean|number|enum|text

    call_log: Mapped["CallLog"] = relationship(back_populates="responses")


class CareEvent(Base):
    __tablename__ = "care_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    type: Mapped[str] = mapped_column(String(24))
    # discharge|med_started|call|missed_dose|symptom|alert|advice|recovered
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info|warn|critical

    patient: Mapped["Patient"] = relationship(back_populates="events")


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    call_log_id: Mapped[int | None] = mapped_column(ForeignKey("call_logs.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|ack|closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped["Patient"] = relationship(back_populates="escalations")
