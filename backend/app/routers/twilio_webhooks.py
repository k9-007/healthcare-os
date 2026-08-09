import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import CallLog, Patient
from ..services import careplus, telephony
from ..services.voice import agent

logger = logging.getLogger("twilio")
router = APIRouter(prefix="/twilio", tags=["twilio"])


def _xml(content: str) -> Response:
    return Response(content=content, media_type="application/xml")


NOT_FOUND_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<Response><Say>Sorry, this call could not be set up.</Say></Response>"
)


def _prepare_in_background(call_id: int) -> None:
    """Render the dialogue while Twilio dials, instead of before we answer it.

    Twilio abandons a call if the TwiML webhook takes longer than ~15s, and
    preparing a plan costs translate plus a TTS render per line. The agent
    rebuilds the plan itself if the stream somehow wins the race.
    """
    task = asyncio.create_task(agent.prepare_call(call_id))
    _prep_tasks.add(task)
    task.add_done_callback(_prep_tasks.discard)


_prep_tasks: set[asyncio.Task] = set()


# Declared before /voice/{call_id} so "demo" is never parsed as a call id.
@router.api_route("/voice/demo", methods=["GET", "POST"])
async def voice_demo(
    patient_id: int | None = None,
    lang: str | None = None,
    db: Session = Depends(get_db),
):
    """Self-contained TwiML entrypoint for the Twilio Console's "Make a test call".

    The console dials using its own session, so this path needs neither an
    Account SID nor working REST auth on our side — only a publicly reachable
    URL. It creates the CallLog and its care-plan targets on the fly, then
    connects the media stream, which makes it the fastest way to prove a real
    conversation (and Indian call termination) end to end.
    """
    patient = (
        db.get(Patient, patient_id) if patient_id
        else db.scalars(select(Patient).order_by(Patient.id)).first()
    )
    if not patient:
        logger.error("demo call requested but no patient exists — is the database seeded?")
        return _xml(NOT_FOUND_TWIML)

    # `lang` applies to this call only; it must not rewrite the patient's record.
    call = await careplus.create_care_call(db, patient, language=lang, with_script=False)
    call.mode = "twilio"
    call.status = "ringing"
    db.commit()

    logger.info(
        "console test call → call_id=%s patient=%s lang=%s stream=%s",
        call.id, patient.name, call.detected_language, telephony.stream_ws_url(call.id),
    )
    _prepare_in_background(call.id)
    return _xml(telephony.streaming_twiml(call.id))


@router.api_route("/voice/{call_id}", methods=["GET", "POST"])
async def voice(call_id: int, db: Session = Depends(get_db)):
    """TwiML served when the patient answers.

    VOICE_MODE=stream hands the audio to the conversational agent over a media
    stream; "classic" keeps the original play-then-record flow as a fallback for
    when a WebSocket cannot be established.
    """
    call = db.get(CallLog, call_id)
    if not call:
        return _xml(NOT_FOUND_TWIML)
    settings = get_settings()
    if settings.voice_mode == "stream" and settings.twilio_trial_account:
        logger.warning(
            "call %s: TWILIO_TRIAL_ACCOUNT is set — serving <Play> TwiML because "
            "trial accounts strip <Stream>. Upgrade the account for a real conversation.",
            call_id,
        )
        return _xml(telephony.twiml_for_call(call))
    if settings.voice_mode == "stream":
        _prepare_in_background(call_id)
        return _xml(telephony.streaming_twiml(call_id))
    return _xml(telephony.twiml_for_call(call))


@router.post("/recording/{call_id}")
async def recording(call_id: int, request: Request, db: Session = Depends(get_db)):
    """Recording webhook → download audio → STT → analytics → persist."""
    call = db.get(CallLog, call_id)
    empty = Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response/>', media_type="application/xml"
    )
    if not call:
        return empty

    form = await request.form()
    recording_url = str(form.get("RecordingUrl") or "")
    if not recording_url:
        logger.warning("recording webhook without RecordingUrl for call %s", call_id)
        return empty

    settings = get_settings()
    audio_bytes = b""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Twilio serves recordings with basic auth; .wav suffix selects format
            resp = await client.get(
                recording_url + ".wav",
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                follow_redirects=True,
            )
            resp.raise_for_status()
            audio_bytes = resp.content
    except Exception as e:  # noqa: BLE001 — webhook must always return valid TwiML
        logger.error("failed to download recording for call %s: %s", call_id, e)
        return empty

    rec_name = f"rec_{call.id}_{int(time.time())}.wav"
    (settings.recordings_dir / rec_name).write_bytes(audio_bytes)
    call.recording_path = f"recordings/{rec_name}"

    try:
        await careplus.process_reply(db, call, audio_bytes=audio_bytes, filename=rec_name)
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("reply processing failed for call %s: %s", call_id, e)
        db.rollback()
    return empty


@router.post("/status/{call_id}")
async def status(call_id: int, request: Request, db: Session = Depends(get_db)):
    call = db.get(CallLog, call_id)
    if call:
        form = await request.form()
        tw_status = str(form.get("CallStatus") or "")
        mapping = {
            "queued": "queued", "initiated": "queued", "ringing": "ringing",
            "in-progress": "ringing", "answered": "ringing",
            "completed": call.status if call.status == "completed" else "completed",
            "busy": "failed", "no-answer": "failed", "failed": "failed", "canceled": "failed",
        }
        if tw_status in mapping and call.status != "completed":
            call.status = mapping[tw_status]
            db.commit()
    return {"ok": True}
