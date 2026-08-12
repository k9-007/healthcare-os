"""VoiceAgent — the conversation.

One instance per call, driven entirely by the inbound 20 ms audio frames. The
frame loop is the clock: every state transition (playback finished, patient
started talking, patient stopped talking, nobody said anything for 7 seconds)
is evaluated once per frame, which gives 20 ms resolution without timers racing
each other.

Latency, per turn:
    VAD end-of-speech 600ms + STT ~860ms + understanding ~800ms + cached TTS 0ms
    ≈ 2.3s for a scripted question, versus ~3.9s if TTS were synthesized live.
"""

import asyncio
import contextlib
import logging
import time
from enum import Enum

from ...config import get_settings
from ...db import SessionLocal
from ...models import CallLog
from ..sarvam import SarvamUnavailable, sarvam
from . import dialogue, meta, persist, prewarm, stream_stt
from .audio import SILENCE_FRAME
from .meta import Intent
from .audio import duration_ms, peak, pcm_to_wav, rms
from .dialogue import Plan, Step
from .transport import BaseTransport
from .understand import Understanding, understand
from .vad import Endpointer, Event, Stats

logger = logging.getLogger("voice.agent")

# How many times the nurse will explain herself on one question before giving
# up on clarifying and letting the reprompt ladder move the call along.
MAX_META_REPLIES = 3


class State(str, Enum):
    SPEAKING = "speaking"
    LISTENING = "listening"
    CAPTURING = "capturing"
    THINKING = "thinking"
    DONE = "done"


class VoiceAgent:
    def __init__(self, call_id: int, transport: BaseTransport) -> None:
        self.call_id = call_id
        self.transport = transport
        self.state = State.SPEAKING
        self.settings = get_settings()

        self.endpointer = Endpointer(
            threshold=self.settings.vad_speech_threshold,
            start_ms=self.settings.vad_start_ms,
            silence_ms=self.settings.vad_silence_ms,
            max_utterance_ms=self.settings.vad_max_utterance_ms,
        )
        self.db = SessionLocal()
        self.call: CallLog | None = None
        self.plan: Plan | None = None

        self.step_index = 0
        self.turn_index = 0
        self.reprompts = 0
        self.no_answer_streak = 0
        self.escalated = False
        # Meta-intent handling never advances the script, so it needs its own
        # bound: a patient stuck on "who is this?" must not loop forever.
        self.meta_replies = 0
        self._ack_turn = 0
        self._stt: stream_stt.SarvamStream | None = None
        self._listening_since = 0.0
        self._turn_task: asyncio.Task | None = None
        self._barge_in_pending = False
        self._playing = False

    # ---------- lifecycle ----------

    async def run(self) -> None:
        try:
            await self._prepare()
            await self._speak_step(self._current_step())
            await self._loop()
        except asyncio.CancelledError:
            logger.info("call %s agent cancelled", self.call_id)
            raise
        except Exception:
            logger.exception("call %s agent failed", self.call_id)
        finally:
            await self._shutdown()

    async def _prepare(self) -> None:
        self.call = self.db.get(CallLog, self.call_id)
        if not self.call:
            raise ValueError(f"call {self.call_id} not found")
        self.call.status = "ringing"
        self.db.commit()

        self.plan = dialogue.take_plan(self.call_id)
        if self.plan is None:
            # No head start (e.g. a stream that reconnected) — build it now. The
            # patient hears silence for this stretch, so it is worth avoiding.
            logger.warning("call %s had no prepared plan; building inline", self.call_id)
            self.plan = await dialogue.build_plan(self.db, self.call, do_prewarm=False)
        logger.info(
            "call %s conversation ready: %d steps in %s",
            self.call_id, len(self.plan.steps), self.plan.language,
        )
        if self.settings.stt_streaming and self.settings.sarvam_api_key:
            session = stream_stt.SarvamStream(
                self.settings.sarvam_api_key, self.plan.language
            )
            self._stt = session if await session.connect() else None

    async def _shutdown(self) -> None:
        if self._stt is not None:
            await self._stt.close()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        try:
            if self.call:
                self.call = self.db.get(CallLog, self.call_id)
                persist.finalize(self.db, self.call)
                self.db.commit()
        except Exception:
            logger.exception("call %s finalization failed", self.call_id)
            self.db.rollback()
        finally:
            self.db.close()
        with contextlib.suppress(Exception):
            await self.transport.hangup()

    # ---------- the frame loop ----------

    async def _loop(self) -> None:
        async for frame in self.transport.frames():
            if self.state is State.DONE:
                return
            event = self.endpointer.push(frame)

            # Only the patient's own turn goes to the streaming transcriber:
            # feeding it the silence under our own questions invites finals
            # that belong to no turn at all.
            if self._stt is not None and self.state in (State.LISTENING, State.CAPTURING):
                await self._stt.feed(frame)

            if self.state is State.SPEAKING:
                await self._on_frame_speaking(event)
            elif self.state is State.LISTENING:
                await self._on_frame_listening(event)
            elif self.state is State.CAPTURING:
                await self._on_frame_capturing(event)
            # THINKING: frames are drained but ignored — we are not listening
            # for an answer to a question we have not finished processing.

    async def _on_frame_speaking(self, event: Event | None) -> None:
        if self.transport.playback_done.is_set():
            self._enter_listening()
            return
        # Barge-in needs a longer, more confident run of speech than normal
        # endpointing: line echo of our own voice must not cut us off. And there
        # is nothing to interrupt until audio is actually going out.
        if not self._playing:
            return
        if self.endpointer.in_speech and self.endpointer.speech_run_ms >= self.settings.vad_barge_in_ms:
            logger.info("call %s barge-in after %dms", self.call_id, self.endpointer.speech_run_ms)
            self._barge_in_pending = True
            await self.transport.clear()
            self.state = State.CAPTURING

    async def _on_frame_listening(self, event: Event | None) -> None:
        if event is Event.SPEECH_START:
            self.state = State.CAPTURING
            return
        elapsed = (time.monotonic() - self._listening_since) * 1000
        if elapsed >= self.settings.voice_no_speech_ms:
            await self._on_silence()

    async def _on_frame_capturing(self, event: Event | None) -> None:
        if event is not Event.UTTERANCE_END:
            return
        pcm = self.endpointer.take_utterance()
        stats = self.endpointer.last_stats
        self.state = State.THINKING
        barge_in, self._barge_in_pending = self._barge_in_pending, False
        self._turn_task = asyncio.create_task(self._handle_utterance(pcm, barge_in, stats))

    def _enter_listening(self) -> None:
        self.state = State.LISTENING
        self._playing = False
        self.endpointer.reset()
        self._listening_since = time.monotonic()
        if self._stt is not None:
            self._stt.begin_turn()

    # ---------- turns ----------

    def _current_step(self) -> Step | None:
        assert self.plan is not None
        if self.step_index >= len(self.plan.steps):
            return None
        return self.plan.steps[self.step_index]

    async def _speak_step(self, step: Step | None, *, lead: str = "") -> None:
        if step is None:
            await self._close(lead=lead)
            return
        await self._speak(step.spoken, step_key=step.key, lead=lead)
        if step.kind == "greeting":
            # The greeting is a statement, not a question — roll straight on.
            self.step_index += 1
            next_step = self._current_step()
            if next_step is not None:
                await self._speak(next_step.spoken, step_key=next_step.key)

    async def _speak(
        self, text: str, *, step_key: str = "", record: bool = True, lead: str = ""
    ) -> None:
        """Render (or fetch cached) audio and start real-time playback.

        `lead` is a short line spoken immediately before `text` — an
        acknowledgement, or the answer to a question the patient just asked.
        The two are spliced into a single playback rather than queued as two:
        a separate `play()` can land after the sender has already drained and
        reported the line finished, which would hand the turn back mid-sentence.
        """
        assert self.plan is not None
        # Claim the speaking state before synthesis: the frame loop runs
        # concurrently and would otherwise treat the not-yet-started line as
        # already finished and start listening for an answer.
        self.state = State.SPEAKING
        self._playing = False
        self.endpointer.reset()
        self.transport.begin_playback()

        started = time.monotonic()
        segments = [s for s in (lead, text) if s.strip()]
        rendered = [await prewarm.synthesize_cached(s, self.plan.language) for s in segments]
        latency = int((time.monotonic() - started) * 1000)
        usable = [p for p in rendered if p]
        # A breath between the acknowledgement and the question; spliced with
        # no gap they run together into one hurried word.
        pcm = (SILENCE_FRAME * 6).join(usable) if usable else None
        if pcm is None:
            logger.error("call %s could not synthesize %r — skipping line", self.call_id, text[:60])
            self.transport.playback_done.set()
            self._enter_listening()
            return
        text = " ".join(segments)

        self._playing = True
        await self.transport.play(pcm)
        if record:
            self.turn_index += 1
            persist.add_turn(
                self.db, self.call, index=self.turn_index, role="nurse",
                step_key=step_key, text=text, language=self.plan.language,
                latency_ms=latency,
            )
            self.db.commit()
        logger.info(
            "call %s nurse[%s]: %r (%dms tts, %dms audio)",
            self.call_id, step_key or "-", text[:70], latency, duration_ms(pcm),
        )

    async def _handle_utterance(self, pcm: bytes, barge_in: bool, stats: Stats) -> None:
        """STT → understanding → persistence → the next thing we say."""
        assert self.plan is not None
        step = self._current_step()
        started = time.monotonic()
        logger.info(
            "call %s utterance captured: %dms rms=%.0f peak=%.0f vad[%s]%s",
            self.call_id, duration_ms(pcm), rms(pcm), peak(pcm), stats,
            " barge-in" if barge_in else "",
        )
        try:
            transcript, language, confidence = await self._transcribe(pcm)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("call %s transcription failed", self.call_id)
            transcript, language, confidence = "", self.plan.language, 0.0

        self.turn_index += 1
        persist.add_turn(
            self.db, self.call, index=self.turn_index, role="patient",
            step_key=step.key if step else "",
            text=transcript, audio_path=persist.save_audio(self.call_id, "patient", pcm),
            language=language or self.plan.language, confidence=confidence,
            latency_ms=int((time.monotonic() - started) * 1000), barge_in=barge_in,
        )
        self.db.commit()
        logger.info(
            "call %s patient[%s]: %r (%dms utterance, %dms stt)",
            self.call_id, step.key if step else "-", transcript[:70],
            duration_ms(pcm), int((time.monotonic() - started) * 1000),
        )

        if not transcript.strip():
            await self._on_unclear()
            return

        # "Wrong number" and "stop calling me" are answered locally: they end
        # the call, so spending an LLM round trip to confirm them is latency
        # the patient pays for nothing.
        ending = meta.terminal_intent(meta.detect(transcript))
        if ending is not None:
            await self._on_terminal_intent(ending, transcript)
            return

        expects_yes_no = bool(step and step.ref_type == "medicine")
        u = await understand(
            step.text_en if step else "How are you feeling?",
            transcript,
            expects_yes_no=expects_yes_no,
        )

        if u.urgency == "high":
            await self._on_emergency(step, u)
            return

        ending = meta.terminal_intent(u.meta_intents)
        if ending is not None:
            await self._on_terminal_intent(ending, transcript)
            return

        answered = u.has_clinical_answer(expects_yes_no=expects_yes_no)
        if u.meta_intents and not answered:
            # The patient asked us something instead of answering. Reply, and
            # leave the script exactly where it was — no result is recorded for
            # a question that has not been answered yet.
            await self._on_meta(u.meta_intents, step)
            return

        if step is not None and not u.answered and self.reprompts < 1:
            await self._on_unclear()
            return

        if step is not None:
            events, escalation = persist.record_answer(self.db, self.call, step, u)
            self.db.commit()
            if escalation:
                self.escalated = True

        self.reprompts = 0
        self.no_answer_streak = 0
        self.meta_replies = 0
        # An answer plus a question ("yes I took it — who is this?") gets both:
        # the answer is recorded and the question is answered on the way to the
        # next step, without re-asking what was already answered.
        lead = self._meta_lead(u.meta_intents) or self._acknowledgement(step, u)
        self.step_index += 1
        await self._speak_step(self._current_step(), lead=lead)

    async def _transcribe(self, pcm: bytes) -> tuple[str, str, float]:
        assert self.plan is not None
        if duration_ms(pcm) < 200:
            return "", "", 0.0
        level = rms(pcm)
        if level < self.settings.vad_min_utterance_rms:
            # Sarvam answers near-silence with a confident invented sentence,
            # which the understanding step then treats as a real reply.
            logger.info(
                "call %s discarding %dms of near-silence (rms=%.0f)",
                self.call_id, duration_ms(pcm), level,
            )
            return "", "", 0.0
        if self._stt is not None:
            streamed = await self._stt.take()
            if streamed:
                return streamed, self.plan.language, 1.0
            logger.info("call %s no streamed transcript; using batch", self.call_id)
        try:
            return await sarvam.stt(
                pcm_to_wav(pcm), "turn.wav", self.plan.language, timeout=15.0, retries=2,
            )
        except SarvamUnavailable as e:
            logger.warning("call %s STT unavailable: %s", self.call_id, e)
            return "", "", 0.0

    # ---------- meta-intents ----------

    def _meta_lead(self, intents: list[Intent]) -> str:
        """The one line that answers what the patient just asked.

        One line, not one per intent: "who are you" and "why are you calling"
        arrive together and have a single natural answer, and a nurse who
        replies with a paragraph before re-asking has lost the patient again.
        """
        assert self.plan is not None
        if Intent.WHO_ARE_YOU in intents and Intent.WHY_CALLING in intents:
            return self.plan.phrase("meta_identity_purpose")
        for intent, key in (
            (Intent.WHO_ARE_YOU, "meta_identity"),
            (Intent.WHY_CALLING, "meta_purpose"),
            (Intent.LANGUAGE, "meta_language"),
            (Intent.CONFUSED, "meta_confused"),
            (Intent.REPEAT, "meta_repeat"),
            (Intent.GREETING, "meta_greeting"),
        ):
            if intent in intents:
                return self.plan.phrase(key)
        return ""

    async def _on_meta(self, intents: list[Intent], step: Step | None) -> None:
        """Answer the patient's question, then ask ours again. Step unchanged."""
        assert self.plan is not None
        self.meta_replies += 1
        logger.info(
            "call %s meta-intent %s — holding step %s (reply %d)",
            self.call_id, [i.value for i in intents],
            step.key if step else "-", self.meta_replies,
        )
        lead = self._meta_lead(intents)
        if step is None:
            await self._close(lead=lead)
            return
        if self.meta_replies > MAX_META_REPLIES:
            # Explaining is not working. Fall back to the reprompt ladder,
            # which eventually moves on rather than looping here forever.
            logger.info("call %s: too many clarifications, resuming script", self.call_id)
            self.meta_replies = 0
            await self._on_unclear()
            return
        await self._speak(step.spoken, step_key="clarify", lead=lead)

    async def _on_terminal_intent(self, intent: Intent, transcript: str) -> None:
        """Honour "wrong number" / "call me later" / "never call again" and stop."""
        assert self.plan is not None
        key = {
            Intent.STOP: "meta_stop",
            Intent.WRONG_PERSON: "meta_wrong_person",
            Intent.BUSY: "meta_busy",
        }[intent]
        logger.info("call %s ending on meta-intent %s", self.call_id, intent.value)
        try:
            persist.note_interruption(self.db, self.call, intent.value, transcript)
            self.db.commit()
        except Exception:
            logger.exception("call %s could not record %s", self.call_id, intent.value)
            self.db.rollback()
        # Nothing clinical is spoken here: a stranger who says "wrong number"
        # must not learn the patient's medicines or condition.
        await self._speak(self.plan.phrase(key), step_key=f"meta:{intent.value}")
        await self.transport.playback_done.wait()
        self.state = State.DONE

    def _acknowledgement(self, step: Step | None, u: Understanding) -> str:
        """A short human reaction to what was just said, chosen by the result.

        Alternating between two variants is what stops four medicine questions
        in a row from sounding like a form being read out.
        """
        assert self.plan is not None
        self._ack_turn += 1
        alt = self._ack_turn % 2 == 0
        if u.symptoms or (u.pain_score or 0) > 0:
            return self.plan.phrase("ack_symptom")
        if step is not None and step.ref_type == "medicine":
            if u.yes_no == "yes":
                return self.plan.phrase("ack_med_yes_alt" if alt else "ack_med_yes")
            if u.yes_no == "no":
                return self.plan.phrase("ack_med_no_alt" if alt else "ack_med_no")
        if step is not None and step.ref_type == "wellbeing" and u.urgency == "low":
            return self.plan.phrase("ack_wellbeing_good")
        return self.plan.phrase("ack_neutral")

    # ---------- exception paths ----------

    async def _on_unclear(self) -> None:
        assert self.plan is not None
        self.reprompts += 1
        if self.reprompts > 2:
            self.step_index += 1
            self.reprompts = 0
            await self._speak_step(self._current_step())
            return
        await self._speak(self.plan.phrase("reprompt"), step_key="reprompt")

    async def _on_silence(self) -> None:
        """Nobody said anything for the no-speech window."""
        assert self.plan is not None
        self.no_answer_streak += 1
        if self.no_answer_streak >= 2:
            logger.info("call %s: no reply twice, closing", self.call_id)
            await self._speak(self.plan.phrase("no_answer"), step_key="no_answer")
            await self.transport.playback_done.wait()
            self.state = State.DONE
            return
        await self._speak(self.plan.phrase("reprompt"), step_key="reprompt")

    async def _on_emergency(self, step: Step | None, u: Understanding) -> None:
        """Red flag: abandon the script, give emergency guidance, escalate now."""
        assert self.plan is not None
        logger.warning("call %s RED FLAG: %s", self.call_id, u.answer[:120])
        if step is not None:
            _, escalation = persist.record_answer(self.db, self.call, step, u)
            self.escalated = self.escalated or escalation is not None
        else:
            persist.record_answer(
                self.db, self.call,
                Step(key="wellbeing", text_en="How are you feeling?", ref_type="wellbeing"), u,
            )
        self.db.commit()
        await self._speak(self.plan.phrase("emergency"), step_key="emergency")
        await self.transport.playback_done.wait()
        self.state = State.DONE

    async def _close(self, *, lead: str = "") -> None:
        assert self.plan is not None
        await self._speak(self.plan.phrase("closing"), step_key="closing", lead=lead)
        await self.transport.playback_done.wait()
        self.state = State.DONE


async def run_agent(call_id: int, transport: BaseTransport) -> None:
    agent = VoiceAgent(call_id, transport)
    await agent.run()


async def prepare_call(call_id: int) -> Plan | None:
    """Build and pre-render a call's dialogue before the phone starts ringing.

    Called from the TwiML webhook (and the scheduler) so the greeting can play
    the moment the media stream opens rather than after a translate+TTS round trip.
    """
    db = SessionLocal()
    try:
        call = db.get(CallLog, call_id)
        if not call:
            return None
        plan = await dialogue.build_plan(db, call, do_prewarm=True)
        dialogue.stash_plan(call_id, plan)
        return plan
    except Exception:
        logger.exception("failed preparing call %s", call_id)
        return None
    finally:
        db.close()


def mark_stream_call_sid(call_id: int, call_sid: str) -> None:
    """Record the call identifier announced on the media stream.

    Streamed calls are not placed through our REST client, so this is the only
    place we learn their carrier-side id.
    """
    if not call_sid:
        return
    db = SessionLocal()
    try:
        call = db.get(CallLog, call_id)
        if call and not call.twilio_sid:
            call.twilio_sid = call_sid
            db.commit()
    finally:
        db.close()
