"""WebSocket endpoints carrying live call audio.

    /ws/voice/twilio/{call_id}   Twilio Media Streams (μ-law 8 kHz base64)
    /ws/voice/browser/{call_id}  Operator console mic (PCM16 8 kHz binary)

Both hand the socket to the same `VoiceAgent`; only the framing differs. The
browser route exists so the entire conversation — VAD, STT, understanding,
escalation — is demoable and testable without a carrier in the loop.
"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import CallLog, Patient
from ..services import careplus
from ..services.voice import agent as voice_agent
from ..services.voice.agent import VoiceAgent, mark_stream_call_sid
from ..services.voice.transport import BrowserTransport, TwilioTransport

logger = logging.getLogger("voice.ws")
router = APIRouter(tags=["voice"])

STREAM_START_TIMEOUT = 10.0


def _call_exists(call_id: int) -> bool:
    db = SessionLocal()
    try:
        return db.get(CallLog, call_id) is not None
    finally:
        db.close()


@router.api_route("/voice/stream-demo", methods=["GET", "POST"])
async def stream_demo(
    patient_id: int | None = None,
    lang: str | None = None,
    db: Session = Depends(get_db),
):
    """Create a call for the streaming agent and hand back the socket to use.

    Exercises the full conversation — VAD, STT, understanding, escalation —
    without a carrier in the loop; `test_voice_stream.py` drives it.
    """
    patient = (
        db.get(Patient, patient_id) if patient_id
        else db.scalars(select(Patient).order_by(Patient.id)).first()
    )
    if not patient:
        return {"error": "no patient exists — is the database seeded?"}

    # `lang` applies to this call only; it must not rewrite the patient's record.
    call = await careplus.create_care_call(db, patient, language=lang, with_script=False)
    call.status = "ringing"
    db.commit()

    # Render the dialogue now so the greeting plays the moment the socket opens
    # instead of after a translate plus a TTS render per line.
    task = asyncio.create_task(voice_agent.prepare_call(call.id))
    _prep_tasks.add(task)
    task.add_done_callback(_prep_tasks.discard)

    ws_url = f"{get_settings().public_ws_base_url}/ws/voice/twilio/{call.id}"
    logger.info("stream demo → call_id=%s patient=%s lang=%s", call.id, patient.name, lang or "-")
    return {"call_id": call.id, "ws_url": ws_url, "language": call.detected_language}


_prep_tasks: set[asyncio.Task] = set()


@router.websocket("/ws/voice/twilio/{call_id}")
async def twilio_stream(websocket: WebSocket, call_id: int):
    await websocket.accept()
    if not _call_exists(call_id):
        logger.error("media stream for unknown call %s — closing", call_id)
        await websocket.close(code=1008)
        return

    transport = TwilioTransport(websocket)
    transport.start()
    logger.info("call %s media stream connected", call_id)
    try:
        # Twilio sends `start` (with the CallSid) before any audio; the agent
        # cannot emit media until it knows the streamSid.
        await asyncio.wait_for(transport.ready.wait(), timeout=STREAM_START_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("call %s: no Twilio start event within %.0fs", call_id, STREAM_START_TIMEOUT)
        await transport.stop()
        return

    mark_stream_call_sid(call_id, transport.call_sid)
    await _run(call_id, transport)


@router.websocket("/ws/voice/browser/{call_id}")
async def browser_stream(websocket: WebSocket, call_id: int):
    await websocket.accept()
    if not _call_exists(call_id):
        await websocket.close(code=1008)
        return

    transport = BrowserTransport(websocket)
    transport.start()
    logger.info("call %s browser stream connected", call_id)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(transport.ready.wait(), timeout=STREAM_START_TIMEOUT)
    await _run(call_id, transport)


async def _run(call_id: int, transport) -> None:
    agent = VoiceAgent(call_id, transport)
    try:
        await agent.run()
    except WebSocketDisconnect:
        logger.info("call %s stream disconnected", call_id)
    except Exception:
        logger.exception("call %s stream handler failed", call_id)
    finally:
        await transport.stop()
        logger.info(
            "call %s stream closed (in=%d frames, out=%d frames)",
            call_id, transport.frames_in, transport.frames_out,
        )
