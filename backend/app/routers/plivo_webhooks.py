"""Plivo Voice webhooks for Patient Care+ outbound IVR calls."""

import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import CallLog
from ..services import careplus, telephony

logger = logging.getLogger("plivo")
router = APIRouter(prefix="/plivo", tags=["plivo"])

EMPTY_XML = Response(
    content='<?xml version="1.0" encoding="UTF-8"?><Response/>',
    media_type="application/xml",
)


@router.api_route("/voice/{call_id}", methods=["GET", "POST"])
def voice(call_id: int, db: Session = Depends(get_db)):
    """Plivo fetches XML when the patient answers: play TTS, record reply."""
    call = db.get(CallLog, call_id)
    if not call:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Speak>Call not found.</Speak></Response>',
            media_type="application/xml",
        )
    call.status = "ringing"
    db.commit()
    return Response(content=telephony.plivo_xml_for_call(call), media_type="application/xml")


@router.api_route("/recording/{call_id}", methods=["GET", "POST"])
async def recording(call_id: int, request: Request, db: Session = Depends(get_db)):
    """Recording action webhook → download audio → STT → analytics → persist."""
    call = db.get(CallLog, call_id)
    if not call:
        return EMPTY_XML

    form = await request.form()
    recording_url = str(form.get("RecordUrl") or form.get("record_url") or "")
    if not recording_url:
        logger.warning("recording webhook without RecordUrl for call %s form=%s", call_id, dict(form))
        return EMPTY_XML

    settings = get_settings()
    audio_bytes = b""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(recording_url, follow_redirects=True)
            if resp.status_code in (401, 403):
                resp = await client.get(
                    recording_url,
                    auth=(settings.plivo_auth_id, settings.plivo_auth_token),
                    follow_redirects=True,
                )
            resp.raise_for_status()
            audio_bytes = resp.content
    except Exception as e:  # noqa: BLE001 — webhook must always return valid XML
        logger.error("failed to download recording for call %s: %s", call_id, e)
        return EMPTY_XML

    if not audio_bytes:
        logger.warning("empty recording for call %s", call_id)
        return EMPTY_XML

    rec_name = f"rec_{call.id}_{int(time.time())}.wav"
    (settings.recordings_dir / rec_name).write_bytes(audio_bytes)
    call.recording_path = f"recordings/{rec_name}"

    try:
        await careplus.process_reply(db, call, audio_bytes=audio_bytes, filename=rec_name)
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("reply processing failed for call %s: %s", call_id, e)
        db.rollback()
    return EMPTY_XML


@router.api_route("/hangup/{call_id}", methods=["GET", "POST"])
async def hangup(call_id: int, request: Request, db: Session = Depends(get_db)):
    """Hangup / status callback — update CallLog status from Plivo CallStatus."""
    call = db.get(CallLog, call_id)
    if call:
        form = await request.form()
        pl_status = str(form.get("CallStatus") or form.get("HangupCause") or "").lower()
        mapping = {
            "queued": "queued",
            "ringing": "ringing",
            "in-progress": "ringing",
            "answered": "ringing",
            "completed": call.status if call.status == "completed" else "completed",
            "busy": "failed",
            "no-answer": "failed",
            "noanswer": "failed",
            "failed": "failed",
            "canceled": "failed",
            "cancelled": "failed",
            "timeout": "failed",
            "rejected": "failed",
        }
        # HangupCause values when CallStatus missing
        if pl_status not in mapping and pl_status:
            if pl_status in ("normal hangup", "normal_hangup", "endapp"):
                mapped = "completed" if call.status != "completed" else call.status
            else:
                mapped = mapping.get(pl_status)
            if mapped and call.status != "completed":
                call.status = mapped
                db.commit()
        elif pl_status in mapping and call.status != "completed":
            call.status = mapping[pl_status]
            db.commit()
    return {"ok": True}
