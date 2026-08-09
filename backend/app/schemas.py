import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Patients ----------

class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    age: int = Field(default=0, ge=0, le=120)
    sex: str = "F"
    phone: str = Field(min_length=5, max_length=32)
    preferred_language: str = "hi-IN"
    timezone: str = "Asia/Kolkata"
    diagnosis: str = ""
    family_contact: str = ""
    notes: str = ""

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-()]", "", v)
        if not re.match(r"^\+?\d{5,15}$", cleaned):
            raise ValueError("phone must be 5-15 digits, optionally prefixed with +")
        # Stored in E.164 so a number is dialable the moment it is saved —
        # Twilio rejects anything else with error 21211.
        from .services.telephony import to_e164

        return to_e164(cleaned)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"unknown IANA timezone: {v}")
        return v

    @field_validator("sex")
    @classmethod
    def _sex(cls, v: str) -> str:
        if v not in {"F", "M"}:
            raise ValueError("sex must be F or M")
        return v


class PatientUpdate(BaseModel):
    name: str | None = None
    age: int | None = None
    sex: str | None = None
    phone: str | None = None
    preferred_language: str | None = None
    timezone: str | None = None
    diagnosis: str | None = None
    family_contact: str | None = None
    notes: str | None = None


class PatientOut(ORMModel):
    id: int
    name: str
    age: int
    sex: str
    phone: str
    preferred_language: str
    timezone: str
    diagnosis: str
    family_contact: str
    notes: str
    created_at: datetime


class PatientSummaryOut(PatientOut):
    adherence_pct: float | None = None
    open_escalations: int = 0
    risk: str = "low"  # low|medium|high
    status: str = "active"  # active|recovered
    next_call_at: datetime | None = None


# ---------- Care plan ----------

class MedicineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dose: str = ""
    schedule: str = "08:00"
    window_minutes: int = Field(default=30, ge=0, le=720)
    instructions: str = ""
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, v: str) -> str:
        times = [t.strip() for t in v.split(",") if t.strip()]
        if not times:
            raise ValueError("schedule must contain at least one HH:MM time")
        for t in times:
            if not TIME_RE.match(t):
                raise ValueError(f"invalid time '{t}' — use 24h HH:MM")
        return ",".join(sorted(set(times)))


class FollowUpQuestionIn(BaseModel):
    text: str = Field(min_length=1)
    type: str = "boolean"
    options: str = ""
    ask_after_days: int = Field(default=1, ge=0, le=365)
    at_time: str = "10:00"

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in {"boolean", "number", "enum", "short"}:
            raise ValueError("type must be boolean|number|enum|short")
        return v

    @field_validator("at_time")
    @classmethod
    def _at(cls, v: str) -> str:
        if not TIME_RE.match(v):
            raise ValueError("at_time must be 24h HH:MM")
        return v


class CarePlanIn(BaseModel):
    status: str = "active"
    start_date: date | None = None
    call_window: str = "08:00-20:00"
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_backoff: str = "15,60,240"
    medicines: list[MedicineIn] = []
    questions: list[FollowUpQuestionIn] = []

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in {"active", "paused", "done"}:
            raise ValueError("status must be active|paused|done")
        return v

    @field_validator("call_window")
    @classmethod
    def _window(cls, v: str) -> str:
        if not WINDOW_RE.match(v):
            raise ValueError("call_window must be HH:MM-HH:MM")
        return v

    @field_validator("retry_backoff")
    @classmethod
    def _backoff(cls, v: str) -> str:
        try:
            mins = [int(x) for x in v.split(",") if x.strip()]
        except ValueError:
            raise ValueError("retry_backoff must be csv of minutes, e.g. 15,60,240")
        if not mins or any(m < 0 for m in mins):
            raise ValueError("retry_backoff minutes must be non-negative")
        return ",".join(str(m) for m in mins)


class MedicineOut(ORMModel):
    id: int
    name: str
    dose: str
    schedule: str
    window_minutes: int
    instructions: str
    start_date: date | None
    end_date: date | None


class FollowUpQuestionOut(ORMModel):
    id: int
    text: str
    type: str
    options: str
    ask_after_days: int
    at_time: str


class CarePlanOut(ORMModel):
    id: int
    patient_id: int
    status: str
    start_date: date
    call_window: str
    max_retries: int
    retry_backoff: str
    created_at: datetime
    medicines: list[MedicineOut] = []
    questions: list[FollowUpQuestionOut] = []


# ---------- Documents / Brain ----------

class DocumentOut(ORMModel):
    id: int
    patient_id: int | None
    title: str
    type: str
    status: str
    error: str
    created_at: datetime
    chunk_count: int = 0
    pages: int = 0
    size_kb: int = 0
    excerpt: str = ""
    extracted_md: str | None = None


class BrainAskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    patient_id: int | None = None


class CitationOut(BaseModel):
    document_id: int
    document_title: str
    page: int
    section: str = ""
    snippet: str = ""


class BrainAnswerOut(BaseModel):
    answer: str
    refused: bool = False
    citations: list[CitationOut] = []
    confidence: float = 0.0
    engine: str = "sarvam-105b"  # or "fallback-keyword"


# ---------- Calls ----------

class ExtractedResponseOut(ORMModel):
    id: int
    question_id: int | None
    medicine_id: int | None
    key: str
    value: str
    value_type: str


class CallTurnOut(ORMModel):
    id: int
    turn_index: int
    role: str
    step_key: str
    text: str
    text_english: str
    audio_path: str
    language: str
    stt_confidence: float
    latency_ms: int
    barge_in: bool
    started_at: datetime


class CallLogOut(ORMModel):
    id: int
    patient_id: int
    direction: str
    mode: str
    status: str
    kind: str
    script_text: str
    script_text_translated: str
    tts_audio_path: str
    recording_path: str
    transcript: str
    transcript_english: str
    detected_language: str
    language_confidence: float
    error_message: str = ""
    created_at: datetime
    responses: list[ExtractedResponseOut] = []
    turns: list[CallTurnOut] = []


class CallCreateOut(BaseModel):
    call: CallLogOut
    tts_audio_url: str | None = None
    escalation_id: int | None = None
    stream_url: str | None = None


class ReplyProcessOut(BaseModel):
    call: CallLogOut
    escalation_id: int | None = None
    events_created: int = 0


class DoctorReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


# ---------- Schedule ----------

class CallTargetOut(ORMModel):
    id: int
    ref_type: str
    ref_id: int
    label: str


class ScheduledCallOut(ORMModel):
    id: int
    care_plan_id: int | None
    patient_id: int
    kind: str
    due_at: datetime
    slot_key: str
    status: str
    attempts: int
    next_attempt_at: datetime
    call_log_id: int | None
    last_error: str
    targets: list[CallTargetOut] = []
    patient_name: str | None = None
    language: str | None = None


class SchedulePatchIn(BaseModel):
    action: str  # snooze|skip|reschedule
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    due_at: datetime | None = None

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v not in {"snooze", "skip", "reschedule"}:
            raise ValueError("action must be snooze|skip|reschedule")
        return v


# ---------- Timeline / analytics ----------

class CareEventOut(ORMModel):
    id: int
    patient_id: int
    ts: datetime
    type: str
    title: str
    detail: str
    severity: str


class EscalationOut(ORMModel):
    id: int
    patient_id: int
    call_log_id: int | None
    reason: str
    urgency: str
    status: str
    created_at: datetime
    patient_name: str | None = None


class AnalyticsSummaryOut(BaseModel):
    total_patients: int
    active_care_plans: int
    adherence_pct: float
    missed_doses: int
    patients_at_risk: int
    open_escalations: int
    followup_completion_pct: float
    call_success_rate_pct: float
    total_calls: int
    adherence_trend: list[dict] = []
    recent_escalations: list[EscalationOut] = []
