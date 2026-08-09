import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import CallLog, CareEvent, Patient, ScheduledCall
from ..schemas import CallCreateOut, CallLogOut, DoctorReplyIn, ReplyProcessOut
from ..services import careplus, spoken, telephony

router = APIRouter(tags=["calls"])

MAX_AUDIO_MB = 20
AUDIO_EXTS = {".wav", ".mp3", ".webm", ".ogg", ".m4a"}


@router.post("/patients/{patient_id}/call", response_model=CallCreateOut, status_code=201)
async def trigger_call(patient_id: int, db: Session = Depends(get_db)):
    """Manually place a care call now (demo hero button).

    Uses the care plan's medicines as targets if a plan exists, else a generic
    check-in. Script → translate → TTS → place (plivo | simulation).
    """
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")

    call = await careplus.create_care_call(db, patient)
    sc = db.scalar(select(ScheduledCall).where(ScheduledCall.call_log_id == call.id))
    targets = list(sc.targets) if sc else []
    # Release the SQLite write lock before the carrier round-trip; place_call
    # blocks on the network and nothing else can write until it returns.
    db.commit()

    try:
        telephony.place_call(call, patient.phone)
    except telephony.TelephonyError as e:
        # A call that never reached the patient must read as failed everywhere,
        # not sit forever on "waiting for the patient's reply".
        db.add(CareEvent(
            patient_id=patient.id, type="call", severity="warning",
            title="Care call could not be placed", detail=str(e)[:400],
        ))
        db.commit()
        raise HTTPException(502, str(e))

    db.add(CareEvent(
        patient_id=patient.id, type="call", severity="info",
        title=f"Care call placed ({call.mode})",
        detail="; ".join(t.label for t in targets)[:400] or "General check-in",
    ))
    db.commit()
    db.refresh(call)
    return CallCreateOut(
        call=CallLogOut.model_validate(call),
        tts_audio_url=_audio_url(call),
        stream_url=_stream_url(call),
    )


@router.get("/calls/{call_id}", response_model=CallLogOut)
def get_call(call_id: int, db: Session = Depends(get_db)):
    call = db.get(CallLog, call_id)
    if not call:
        raise HTTPException(404, "call not found")
    return call


@router.get("/patients/{patient_id}/calls", response_model=list[CallLogOut])
def list_calls(patient_id: int, db: Session = Depends(get_db)):
    if not db.get(Patient, patient_id):
        raise HTTPException(404, "patient not found")
    return db.scalars(
        select(CallLog).where(CallLog.patient_id == patient_id).order_by(CallLog.created_at.desc())
    ).all()


@router.post("/calls/{call_id}/simulate-reply", response_model=ReplyProcessOut)
async def simulate_reply(
    call_id: int,
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Simulation mode: browser mic recording (or typed text) as the patient's reply."""
    call = db.get(CallLog, call_id)
    if not call:
        raise HTTPException(404, "call not found")
    if call.status == "completed":
        raise HTTPException(409, "this call already has a processed reply")

    audio_bytes: bytes | None = None
    filename = "reply.wav"
    if audio is not None:
        filename = audio.filename or "reply.wav"
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext and ext not in AUDIO_EXTS:
            raise HTTPException(422, f"unsupported audio type '{ext}' — allowed: {sorted(AUDIO_EXTS)}")
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(422, "audio file is empty")
        if len(audio_bytes) > MAX_AUDIO_MB * 1024 * 1024:
            raise HTTPException(413, f"audio exceeds {MAX_AUDIO_MB} MB")
        settings = get_settings()
        rec_name = f"rec_{call.id}_{int(time.time())}{ext or '.wav'}"
        (settings.recordings_dir / rec_name).write_bytes(audio_bytes)
        call.recording_path = f"recordings/{rec_name}"
    elif not (text and text.strip()):
        raise HTTPException(422, "provide either an audio file or a text reply")

    escalation, events = await careplus.process_reply(
        db, call, audio_bytes=audio_bytes, filename=filename, transcript_text=text,
    )
    db.commit()
    db.refresh(call)
    return ReplyProcessOut(
        call=CallLogOut.model_validate(call),
        escalation_id=escalation.id if escalation else None,
        events_created=events,
    )


@router.post("/patients/{patient_id}/reply", response_model=CallCreateOut, status_code=201)
async def doctor_reply(patient_id: int, payload: DoctorReplyIn, db: Session = Depends(get_db)):
    """Closed loop: the doctor's reply becomes an automatic callback call
    in the patient's language (translate → TTS → place)."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")

    # The doctor types clinically ("Dolo 650mg SOS"); the patient hears it, so
    # the spoken copy is expanded to words while the Care Graph keeps the
    # original wording.
    message = payload.message.strip()
    script_en = (
        f"Hello {spoken.speakable(patient.name)}, this is a message from your doctor. "
        f"{spoken.speakable(message)} Take care."
    )
    script_local = await careplus.localize_script(script_en, patient.preferred_language)
    audio_path = await careplus.synthesize(script_local, patient.preferred_language)

    call = CallLog(
        patient_id=patient.id, direction="outbound", kind="callback",
        script_text=script_en, script_text_translated=script_local,
        tts_audio_path=audio_path, status="queued",
    )
    db.add(call)
    db.flush()

    try:
        telephony.place_call(call, patient.phone)
    except telephony.TelephonyError as e:
        db.commit()
        raise HTTPException(502, f"telephony failed: {e}")

    # a callback closes the loop — reflect it on the Care Graph
    db.add(CareEvent(
        patient_id=patient.id, type="advice", severity="info",
        title="Doctor's advice sent as callback",
        detail=message[:400],
    ))
    # acknowledge open escalations once the doctor has responded
    for esc in patient.escalations:
        if esc.status == "open":
            esc.status = "ack"
    db.commit()
    db.refresh(call)
    return CallCreateOut(
        call=CallLogOut.model_validate(call),
        tts_audio_url=_audio_url(call),
        stream_url=_stream_url(call),
    )


def _audio_url(call: CallLog) -> str | None:
    if not call.tts_audio_path:
        return None
    return f"{get_settings().public_base_url}/data/{call.tts_audio_path}"


def _stream_url(call: CallLog) -> str | None:
    """WebSocket the operator console connects to in order to hold the
    conversation in the browser — the same agent a phone call gets."""
    if get_settings().voice_mode != "stream":
        return None
    return f"{get_settings().public_ws_base_url}/ws/voice/browser/{call.id}"
