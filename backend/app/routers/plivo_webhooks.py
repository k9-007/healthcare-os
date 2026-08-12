"""Plivo Voice webhooks — a care call as a turn-by-turn conversation.

    /plivo/voice/{call_id}         answer_url → greeting + the first question
    /plivo/turn/{call_id}/{index}  one patient reply → understood → next question
    /plivo/hangup/{call_id}        final call status

The dialogue plan, the understanding layer and the care-record writes are the
same ones the streaming agent uses; only turn-taking differs. Plivo ends a turn
on a silence window instead of VAD, so the patient cannot interrupt mid-sentence,
but every reply is still transcribed, understood and recorded as it happens
rather than as one blob after the call.
"""

import asyncio
import contextlib
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import CallLog, CallTurn
from ..services import telephony
from ..services.sarvam import SarvamUnavailable, sarvam
from ..services.voice import dialogue, persist, prewarm
from ..services.voice.audio import clip_wav
from ..services.voice.dialogue import PHRASES, Plan, Step
from ..services.voice.understand import understand

logger = logging.getLogger("plivo")
router = APIRouter(prefix="/plivo", tags=["plivo"])

# Sarvam's synchronous STT hard-rejects audio over 30 s.
STT_CLIP_MS = 29_000

# A plan lives for the length of its call: rebuilding one per turn would spend a
# translate round-trip per line while the patient waits on the phone.
_plans: dict[int, Plan] = {}

NOT_FOUND_XML = telephony.plivo_response(
    telephony.speak_element("Sorry, this call could not be set up.")
)


def _xml(content: str) -> Response:
    return Response(content=content, media_type="application/xml")


@router.api_route("/voice/{call_id}", methods=["GET", "POST"])
async def voice(call_id: int, request: Request, db: Session = Depends(get_db)):
    """XML served when the patient answers.

    VOICE_MODE=stream → bidirectional `<Stream>` into VoiceAgent + Silero VAD.
    VOICE_MODE=classic → `<Play>` + `<Record>` turn loop (no barge-in).
    """
    call = db.get(CallLog, call_id)
    if not call:
        logger.error("answer webhook for unknown call %s", call_id)
        return _xml(NOT_FOUND_XML)

    # The CallUUID exists only once the call is up; it is what the Plivo console
    # and the recordings API key everything on, so it replaces the RequestUUID.
    call_uuid = str((await _params(request)).get("CallUUID") or "")
    if call_uuid:
        call.twilio_sid = call_uuid
    if call.status in ("completed", "failed"):
        # A retried answer webhook can arrive after the hangup already landed.
        # Resurrecting the status (or speaking) at that point corrupts the
        # record of a call that is over.
        db.commit()
        logger.info("call %s answer webhook after the call ended (%s) — ignoring", call_id, call.status)
        return _xml(telephony.plivo_response())
    call.status = "ringing"
    db.commit()

    settings = get_settings()
    if settings.voice_mode == "stream":
        # Dialogue is prepared while the phone rings; the agent picks it up the
        # moment the WebSocket opens. Silero VAD owns turn-taking from there.
        await _ensure_plan_ready(db, call)
        ws_url = f"{settings.public_ws_base_url}/ws/voice/plivo/{call_id}"
        status_url = f"{settings.public_base_url}/plivo/stream-status/{call_id}"
        logger.info(
            "call %s answered (CallUUID=%s): streaming VAD → %s",
            call_id, call_uuid or "?", ws_url,
        )
        return _xml(telephony.plivo_response(
            telephony.stream_element(ws_url, status_callback_url=status_url)
        ))

    plan = await _plan_for(db, call)
    logger.info(
        "call %s answered (CallUUID=%s): %d steps in %s (classic Record)",
        call_id, call_uuid or "?", len(plan.steps), plan.language,
    )

    # Plivo re-fetches the answer URL whenever a delivery times out or fails.
    # Restarting the script would greet the patient mid-conversation, so pick
    # up at the question that is already in play instead.
    resume = _asked_step_index(db, call, plan)
    if resume is not None:
        logger.info("call %s answer webhook repeated — resuming at %s", call_id, plan.steps[resume].key)
        return _xml(await _ask(db, call, plan, resume))

    # The greeting is a statement, not a question — it rolls straight into the
    # first question so the patient is only ever asked to speak once per turn.
    greeting = [plan.steps[0].spoken] if plan.steps and plan.steps[0].kind == "greeting" else []
    return _xml(await _ask(db, call, plan, _next_question(plan, -1), prefix_lines=greeting))


@router.api_route("/stream-status/{call_id}", methods=["GET", "POST"])
async def stream_status(call_id: int, request: Request):
    """Plivo stream lifecycle callbacks (started / stopped / failed)."""
    params = await _params(request)
    logger.info(
        "call %s stream status: Event=%s StreamID=%s Reason=%s Duration=%s",
        call_id,
        params.get("Event") or params.get("event") or "?",
        params.get("StreamID") or params.get("streamId") or "?",
        params.get("StatusReason") or "",
        params.get("Duration") or "",
    )
    return {"ok": True}


@router.api_route("/turn/{call_id}/{index}", methods=["GET", "POST"])
async def turn(call_id: int, index: int, request: Request, db: Session = Depends(get_db)):
    """One patient turn: transcribe the reply, understand it, ask what's next."""
    call = db.get(CallLog, call_id)
    if not call:
        return _xml(NOT_FOUND_XML)
    plan = await _plan_for(db, call)
    if not 0 <= index < len(plan.steps):
        return _xml(await _close(db, call, plan))
    step = plan.steps[index]

    params = await _params(request)
    transcript, language, confidence, audio_path = await _capture(call, params, plan)
    persist.add_turn(
        db, call, index=_next_turn_index(db, call), role="patient", step_key=step.key,
        text=transcript, audio_path=audio_path,
        language=language or plan.language, confidence=confidence,
    )
    if audio_path and not call.recording_path:
        call.recording_path = audio_path
    db.commit()
    logger.info("call %s patient[%s]: %r", call_id, step.key, transcript[:70])

    if call.status in ("completed", "failed"):
        # The hangup webhook beat this one. The reply is still real clinical
        # data — understand and record it — but nobody is on the line to hear
        # another question.
        if transcript.strip():
            u = await understand(step.text_en, transcript, expects_yes_no=step.ref_type == "medicine")
            persist.record_answer(db, call, step, u)
            if call.status == "failed":
                # "failed" only meant "hung up before any reply" — no longer true.
                persist.finalize(db, call)
            db.commit()
        return _xml(telephony.plivo_response())

    if not transcript.strip():
        # Two silent turns means nobody is answering — an empty line or a
        # voicemail should not be walked through the whole questionnaire.
        if _silent_streak(db, call) >= 2:
            return _xml(await _no_answer(db, call, plan))
        return _xml(await _retry_or_advance(db, call, plan, index))

    u = await understand(step.text_en, transcript, expects_yes_no=step.ref_type == "medicine")

    if u.urgency == "high":
        logger.warning("call %s RED FLAG: %s", call_id, u.answer[:120])
        persist.record_answer(db, call, step, u)
        db.commit()
        return _xml(await _emergency(db, call, plan))

    if not u.answered:
        return _xml(await _retry_or_advance(db, call, plan, index))

    persist.record_answer(db, call, step, u)
    db.commit()
    return _xml(await _ask(db, call, plan, _next_question(plan, index)))


@router.api_route("/hangup/{call_id}", methods=["GET", "POST"])
async def hangup(call_id: int, request: Request, db: Session = Depends(get_db)):
    """Plivo's final word on the call. Also the only signal we get when the
    patient hangs up mid-conversation, which still leaves usable answers."""
    call = db.get(CallLog, call_id)
    if not call:
        return {"ok": True}
    plivo_status = str((await _params(request)).get("CallStatus") or "")
    if call.status != "completed":
        if _has_reply(db, call):
            persist.finalize(db, call)
        else:
            call.status = "failed"
        db.commit()
    _plans.pop(call_id, None)
    logger.info("call %s hangup: CallStatus=%s → %s", call_id, plivo_status, call.status)
    return {"ok": True}


# ---------------- turns ----------------


async def _ask(
    db: Session,
    call: CallLog,
    plan: Plan,
    index: int | None,
    *,
    prefix_lines: list[str] | None = None,
) -> str:
    """XML that speaks the given lines, asks step `index`, then records the reply."""
    if index is None:
        return await _close(db, call, plan)

    step = plan.steps[index]
    elements: list[str] = []
    for line in [*(prefix_lines or []), step.spoken]:
        elements.append(await _line(line, plan))
        _log_nurse(db, call, line, step.key)
    db.commit()

    action = f"{get_settings().public_base_url}/plivo/turn/{call.id}/{index}"
    elements.append(telephony.record_element(action))
    logger.info("call %s nurse[%s]: %r", call.id, step.key, step.spoken[:70])
    return telephony.plivo_response(*elements)


async def _retry_or_advance(db: Session, call: CallLog, plan: Plan, index: int) -> str:
    """Ask again if the reply was unusable, but never more than twice per step —
    a patient who cannot be understood must not be trapped on one question.

    The attempt count comes from the turns already written for this step, not
    from the webhook URL, so the loop is bounded even if the carrier drops our
    query string or the backend restarts mid-call.
    """
    if _attempts_on_step(db, call, plan.steps[index].key) < 2:
        return await _ask(db, call, plan, index, prefix_lines=[plan.phrase("reprompt")])
    return await _ask(db, call, plan, _next_question(plan, index))


async def _no_answer(db: Session, call: CallLog, plan: Plan) -> str:
    """Nobody is speaking: say we will call back, and let the call end."""
    line = plan.phrase("no_answer")
    element = await _line(line, plan)
    _log_nurse(db, call, line, "no_answer")
    persist.finalize(db, call)
    db.commit()
    _plans.pop(call.id, None)
    logger.info("call %s: no usable reply, closing", call.id)
    return telephony.plivo_response(element)


async def _emergency(db: Session, call: CallLog, plan: Plan) -> str:
    """Red flag: abandon the script, give emergency guidance, end the call."""
    line = plan.phrase("emergency")
    element = await _line(line, plan)
    _log_nurse(db, call, line, "emergency")
    persist.finalize(db, call)
    db.commit()
    _plans.pop(call.id, None)
    return telephony.plivo_response(element)


async def _close(db: Session, call: CallLog, plan: Plan) -> str:
    line = plan.phrase("closing")
    element = await _line(line, plan)
    _log_nurse(db, call, line, "closing")
    persist.finalize(db, call)
    db.commit()
    _plans.pop(call.id, None)
    logger.info("call %s conversation complete", call.id)
    return telephony.plivo_response(element)


def _next_question(plan: Plan, after_index: int) -> int | None:
    for i in range(after_index + 1, len(plan.steps)):
        if plan.steps[i].kind == "question":
            return i
    return None


def _asked_step_index(db: Session, call: CallLog, plan: Plan) -> int | None:
    """The plan index of the question already in play, if any was asked.

    Nurse turns are the durable record of where the conversation is, so a
    repeated answer webhook (or one served after a restart) lands back on the
    open question instead of starting the script over. Phrase turns like
    "reprompt" are skipped — the question they re-asked is further back.
    """
    by_key = {s.key: i for i, s in enumerate(plan.steps) if s.kind == "question"}
    asked = db.scalars(
        select(CallTurn.step_key)
        .where(CallTurn.call_log_id == call.id, CallTurn.role == "nurse")
        .order_by(CallTurn.turn_index.desc())
    ).all()
    for key in asked:
        if key in by_key:
            return by_key[key]
    return None


async def _line(text: str, plan: Plan) -> str:
    """One spoken line: pre-rendered Sarvam audio when we have it, else <Speak>.

    Every scripted line is normally already in the prewarm cache (rendered while
    the phone was ringing), so this costs nothing mid-call.
    """
    if not prewarm.is_cached(text, plan.language):
        await prewarm.synthesize_cached(text, plan.language)
    if prewarm.is_cached(text, plan.language):
        name = prewarm.cached_path(text, plan.language).name
        return telephony.play_element(telephony.data_url(f"tts_cache/{name}"))
    logger.warning("no audio for %r (%s) — falling back to <Speak>", text[:40], plan.language)
    return telephony.speak_element(text)


def _log_nurse(db: Session, call: CallLog, text: str, step_key: str) -> None:
    persist.add_turn(
        db, call, index=_next_turn_index(db, call), role="nurse",
        step_key=step_key, text=text, language=call.detected_language,
    )


def _next_turn_index(db: Session, call: CallLog) -> int:
    highest = db.scalar(
        select(func.max(CallTurn.turn_index)).where(CallTurn.call_log_id == call.id)
    )
    return (highest or 0) + 1


def _attempts_on_step(db: Session, call: CallLog, step_key: str) -> int:
    """How many times the patient has already been recorded answering this step."""
    return db.scalar(
        select(func.count(CallTurn.id)).where(
            CallTurn.call_log_id == call.id, CallTurn.role == "patient",
            CallTurn.step_key == step_key,
        )
    ) or 0


def _silent_streak(db: Session, call: CallLog) -> int:
    """Consecutive empty patient turns at the end of the call so far."""
    recent = db.scalars(
        select(CallTurn).where(
            CallTurn.call_log_id == call.id, CallTurn.role == "patient"
        ).order_by(CallTurn.turn_index.desc()).limit(4)
    ).all()
    streak = 0
    for t in recent:
        if t.text.strip():
            break
        streak += 1
    return streak


def _has_reply(db: Session, call: CallLog) -> bool:
    return db.scalar(
        select(CallTurn.id).where(
            CallTurn.call_log_id == call.id, CallTurn.role == "patient",
            func.trim(CallTurn.text) != "",
        ).limit(1)
    ) is not None


# ---------------- plan + audio plumbing ----------------


async def _ensure_plan_ready(db: Session, call: CallLog) -> None:
    """Join (or start) ring-time prep so VoiceAgent finds a prewarmed plan.

    Must not call `_plan_for` here — that pops the stash into the classic-mode
    cache and starves the streaming agent of its prepared audio.
    """
    prep = telephony.preparation_task(call.id)
    if prep is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(prep), timeout=15.0)
        return
    from ..services.voice import agent as voice_agent
    try:
        await asyncio.wait_for(voice_agent.prepare_call(call.id), timeout=20.0)
    except Exception:  # noqa: BLE001 — agent will build inline if needed
        logger.exception("call %s: stream plan prep failed; agent will build inline", call.id)


async def _plan_for(db: Session, call: CallLog) -> Plan:
    """The call's dialogue, built once and reused for every turn."""
    plan = _plans.get(call.id) or dialogue.take_plan(call.id)
    if plan is None and (prep := telephony.preparation_task(call.id)) is not None:
        # The patient answered before the ring-time preparation finished —
        # join it rather than rebuilding the plan (translate + TTS) inline.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(prep), timeout=15.0)
        plan = dialogue.take_plan(call.id)
    if plan is None:
        try:
            plan = await dialogue.build_plan(db, call, do_prewarm=True)
        except Exception:  # noqa: BLE001 — a live call must still say something
            logger.exception("call %s: plan build failed; falling back to the script", call.id)
            plan = _script_plan(call)
    _plans[call.id] = plan
    return plan


def _script_plan(call: CallLog) -> Plan:
    """Degraded single-question plan from the script the call was created with."""
    text = call.script_text or "Hello, this is your care assistant from the hospital."
    return Plan(
        language=call.detected_language or (call.patient.preferred_language if call.patient else "en-IN"),
        steps=[Step(
            key="wellbeing", text_en=text, text=call.script_text_translated or text,
            ref_type="wellbeing",
        )],
        phrases=dict(PHRASES),
    )


async def _capture(
    call: CallLog, params: dict, plan: Plan
) -> tuple[str, str, float, str]:
    """Download the turn's recording and transcribe it → (text, language, confidence, path)."""
    record_url = str(params.get("RecordUrl") or "")
    if not record_url:
        logger.info("call %s: turn ended with no recording", call.id)
        return "", "", 0.0, ""

    try:
        audio = await _download(record_url)
    except Exception as e:  # noqa: BLE001 — a lost recording must not end the call
        logger.error("call %s: could not download %s: %s", call.id, record_url, e)
        return "", "", 0.0, ""

    name = f"turn_{call.id}_patient_{int(time.time() * 1000)}.wav"
    (get_settings().recordings_dir / name).write_bytes(audio)
    path = f"recordings/{name}"

    try:
        audio = clip_wav(audio, STT_CLIP_MS)
    except Exception:  # noqa: BLE001 — an unparseable blob goes to STT as-is
        pass
    try:
        # The patient is holding the line through this call: a tight timeout and
        # a single retry beat a perfect transcript that arrives after they gave
        # up and hung up.
        transcript, language, confidence = await sarvam.stt(
            audio, name, plan.language, timeout=12.0, retries=2
        )
    except SarvamUnavailable as e:
        logger.warning("call %s: STT unavailable: %s", call.id, e)
        return "", "", 0.0, path
    return transcript, language, confidence, path


async def _download(record_url: str, attempts: int = 3) -> bytes:
    """Fetch a Plivo recording. The action webhook can beat the file into
    storage, so a 404 is retried rather than treated as a lost turn."""
    settings = get_settings()
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(1, attempts + 1):
            try:
                resp = await client.get(record_url, follow_redirects=True)
                if resp.status_code in (401, 403):
                    resp = await client.get(
                        record_url,
                        auth=(settings.plivo_auth_id, settings.plivo_auth_token),
                        follow_redirects=True,
                    )
                resp.raise_for_status()
                return resp.content
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < attempts:
                    await asyncio.sleep(0.5)
    raise last or RuntimeError("recording download failed")


async def _params(request: Request) -> dict:
    """Plivo posts form-encoded webhooks, but retries GET when configured that way."""
    if request.method == "GET":
        return dict(request.query_params)
    return dict(await request.form())
