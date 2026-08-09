import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import CallLog
from ..services import careplus, telephony

logger = logging.getLogger("twilio")
router = APIRouter(prefix="/twilio", tags=["twilio"])


@router.post("/voice/{call_id}")
def voice(call_id: int, db: Session = Depends(get_db)):
    """Twilio fetches TwiML when the patient answers: play TTS, record reply."""
    call = db.get(CallLog, call_id)
    if not call:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>Call not found.</Say></Response>',
            media_type="application/xml",
        )
    return Response(content=telephony.twiml_for_call(call), media_type="application/xml")


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
