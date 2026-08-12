"""Writing a live conversation into the care record.

The batch `<Record>` flow analysed one blob of audio after the call ended. A
conversation produces facts turn by turn, so they are written turn by turn —
which is also what lets the UI show a call unfolding instead of a result.
"""

import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    CallLog, CallTurn, CareEvent, Escalation, ExtractedResponse,
    FollowUpQuestion, Medicine, ScheduledCall,
)
from .audio import pcm_to_wav
from .dialogue import Step
from .understand import Understanding

logger = logging.getLogger("voice.persist")


def save_audio(call_id: int, role: str, pcm: bytes) -> str:
    """Persist per-turn audio so the conversation is replayable in the UI."""
    if not pcm:
        return ""
    name = f"turn_{call_id}_{role}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.wav"
    (get_settings().recordings_dir / name).write_bytes(pcm_to_wav(pcm))
    return f"recordings/{name}"


def add_turn(
    db: Session,
    call: CallLog,
    *,
    index: int,
    role: str,
    text: str,
    step_key: str = "",
    text_english: str = "",
    audio_path: str = "",
    language: str = "",
    confidence: float = 0.0,
    latency_ms: int = 0,
    barge_in: bool = False,
) -> CallTurn:
    turn = CallTurn(
        call_log_id=call.id, turn_index=index, role=role, step_key=step_key,
        text=text, text_english=text_english or ("" if role == "patient" else text),
        audio_path=audio_path, language=language, stt_confidence=confidence,
        latency_ms=latency_ms, barge_in=barge_in,
    )
    db.add(turn)
    db.flush()
    return turn


INTERRUPTION_NOTES = {
    "stop": ("Patient asked not to be called again", "critical"),
    "wrong_person": ("Wrong number — this phone does not reach the patient", "warn"),
    "busy": ("Patient was busy and asked to be called later", "info"),
}


def note_interruption(db: Session, call: CallLog, intent: str, transcript: str) -> None:
    """Record a call the patient ended, as an event a human will actually see.

    There is no consent or do-not-call table in this schema, so "never call me
    again" cannot be enforced on the next scheduled slot. Raising a critical
    care event is the strongest durable action available: the care team sees
    it on the timeline and can stop the plan. This is a real gap, not a fix.
    """
    title, severity = INTERRUPTION_NOTES.get(intent, (f"Call interrupted: {intent}", "info"))
    db.add(CareEvent(
        patient_id=call.patient_id, type="alert" if severity == "critical" else "call",
        severity=severity, title=title, detail=(transcript or "")[:300],
    ))
    db.flush()


def record_answer(db: Session, call: CallLog, step: Step, u: Understanding) -> tuple[int, Escalation | None]:
    """Turn one understood reply into extracted responses, events and escalations."""
    events = 0
    patient_id = call.patient_id

    if step.ref_type == "medicine" and step.ref_id:
        value = {"yes": "true", "no": "false"}.get(u.yes_no, "unknown")
        db.add(ExtractedResponse(
            call_log_id=call.id, medicine_id=step.ref_id,
            key="took_medicine", value=value, value_type="boolean",
        ))
        if value == "false":
            med = db.get(Medicine, step.ref_id)
            db.add(CareEvent(
                patient_id=patient_id, type="missed_dose", severity="warn",
                title=f"Missed dose: {med.name if med else step.text_en}",
                detail=u.answer or "Patient said they had not taken it.",
            ))
            events += 1

    elif step.ref_type == "followup" and step.ref_id:
        q = db.get(FollowUpQuestion, step.ref_id)
        value = u.answer or {"yes": "yes", "no": "no"}.get(u.yes_no, "")
        if value:
            db.add(ExtractedResponse(
                call_log_id=call.id, question_id=step.ref_id,
                key=(q.text[:120] if q else step.text_en[:120]),
                value=value[:2000], value_type=(q.type if q else "text"),
            ))

    for symptom in u.symptoms:
        db.add(ExtractedResponse(
            call_log_id=call.id, key="symptom", value=symptom[:200], value_type="text"
        ))
        db.add(CareEvent(
            patient_id=patient_id, type="symptom", severity="info",
            title=f"Symptom reported: {symptom}", detail=u.answer[:300],
        ))
        events += 1

    if u.pain_score is not None:
        db.add(ExtractedResponse(
            call_log_id=call.id, key="pain_score",
            value=str(u.pain_score), value_type="number",
        ))

    if u.urgency != "low":
        db.add(ExtractedResponse(
            call_log_id=call.id, key="urgency", value=u.urgency, value_type="enum"
        ))

    escalation = None
    if u.urgency == "high" and not _has_open_escalation(db, call.id):
        escalation = Escalation(
            patient_id=patient_id, call_log_id=call.id,
            reason=u.answer or "Urgent symptoms reported during a care call",
            urgency="high", status="open",
        )
        db.add(escalation)
        db.add(CareEvent(
            patient_id=patient_id, type="alert", severity="critical",
            title="URGENT: escalation raised mid-call",
            detail=u.answer[:300] or "Red-flag symptoms detected during the conversation.",
        ))
        events += 1

    db.flush()
    return events, escalation


def _has_open_escalation(db: Session, call_id: int) -> bool:
    return db.scalar(
        select(Escalation.id).where(
            Escalation.call_log_id == call_id, Escalation.status == "open"
        ).limit(1)
    ) is not None


def finalize(db: Session, call: CallLog, *, status: str = "completed") -> None:
    """Roll the turns up into the call record the rest of the app already reads."""
    turns = db.scalars(
        select(CallTurn).where(CallTurn.call_log_id == call.id).order_by(CallTurn.turn_index)
    ).all()
    patient_turns = [t for t in turns if t.role == "patient" and t.text.strip()]

    call.transcript = "\n".join(t.text.strip() for t in patient_turns)
    call.transcript_english = "\n".join(
        (t.text_english or t.text).strip() for t in patient_turns
    )
    if patient_turns:
        call.detected_language = patient_turns[-1].language or call.detected_language
        call.language_confidence = max((t.stt_confidence for t in patient_turns), default=0.0)
    call.status = status

    sc = db.scalar(select(ScheduledCall).where(ScheduledCall.call_log_id == call.id))
    if sc:
        sc.status = "completed" if patient_turns else "no_answer"

    # The rest of the app reads a single urgency row per call; turns only write
    # one when something was actually wrong, so record the benign case here.
    has_urgency = db.scalar(
        select(ExtractedResponse.id).where(
            ExtractedResponse.call_log_id == call.id, ExtractedResponse.key == "urgency"
        ).limit(1)
    )
    if not has_urgency:
        db.add(ExtractedResponse(
            call_log_id=call.id, key="urgency", value="low", value_type="enum"
        ))

    db.add(CareEvent(
        patient_id=call.patient_id, type="call",
        severity="info" if patient_turns else "warn",
        title=(
            f"{call.kind.capitalize()} conversation completed "
            f"({len(patient_turns)} patient replies)"
            if patient_turns else "Care call ended with no reply"
        ),
        detail=(call.transcript_english or call.transcript)[:400],
    ))
    db.flush()
