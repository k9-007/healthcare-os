"""Call transports.

A transport hides where the audio comes from so `VoiceAgent` is identical for a
real phone call and a browser call:

  PlivoTransport   — Plivo Audio Streaming (μ-law 8 kHz, playAudio / clearAudio)
  TwilioTransport  — Twilio Media Streams (μ-law 8 kHz, base64, 20 ms frames)
  BrowserTransport — the operator console mic (raw PCM16 8 kHz binary frames)

Both pace outbound audio in real time. That is what makes barge-in possible: if
we dumped a whole sentence into the socket at once, the far end would already
have buffered it and there would be nothing left to cancel.
"""

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .audio import (
    FRAME_MS,
    PCM_BYTES_PER_FRAME,
    frame_pcm,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
)

logger = logging.getLogger("voice.transport")

# How far ahead of real time we allow the far end to buffer. Enough to ride out
# network jitter, short enough that a barge-in cancels almost everything unsaid.
LEAD_MS = 200
INBOUND_MAX_FRAMES = 250  # 5s; a backlog this deep means the agent has stalled


class BaseTransport:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.inbound: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=INBOUND_MAX_FRAMES)
        self.closed = asyncio.Event()
        self.playback_done = asyncio.Event()
        self.playback_done.set()
        self._outbound: list[bytes] = []
        self._generation = 0  # bumped on clear() to abandon in-flight playback
        self._sender: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._mark_guard: asyncio.Task | None = None
        self._inbound_residual = b""
        self.frames_in = 0
        self.frames_out = 0
        self.frames_dropped = 0

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop(), name="voice-reader")

    async def stop(self) -> None:
        self.closed.set()
        for task in (self._sender, self._reader, self._mark_guard):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        with contextlib.suppress(Exception):
            if self.ws.client_state == WebSocketState.CONNECTED:
                await self.ws.close()

    # ---------- inbound ----------

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield 20 ms PCM16 8 kHz frames until the call ends."""
        while True:
            frame = await self.inbound.get()
            if frame is None:
                return
            yield frame

    def _offer_pcm(self, pcm: bytes) -> None:
        """Re-block arbitrary-length PCM into exact 20 ms frames.

        Carriers are free to batch or split media packets — Plivo's chunk size
        is not part of its contract — but the endpointer counts pre-roll, gap
        tolerance and trailing-silence trim in whole 20 ms frames. Feeding it a
        100 ms packet as one "frame" silently stretches the pre-roll window to
        two seconds and coarsens every threshold by 5x. Any partial tail is
        carried into the next packet rather than zero-padded, which would inject
        a click into the middle of the utterance.
        """
        if not pcm:
            return
        buf = self._inbound_residual + pcm
        whole = len(buf) // PCM_BYTES_PER_FRAME * PCM_BYTES_PER_FRAME
        for i in range(0, whole, PCM_BYTES_PER_FRAME):
            self._offer(buf[i : i + PCM_BYTES_PER_FRAME])
        self._inbound_residual = buf[whole:]

    def _offer(self, pcm_frame: bytes) -> None:
        self.frames_in += 1
        try:
            self.inbound.put_nowait(pcm_frame)
        except asyncio.QueueFull:
            # Never let a slow turn stall the socket reader; the oldest audio is
            # the least useful, so drop it and keep the stream live.
            self.frames_dropped += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self.inbound.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.inbound.put_nowait(pcm_frame)

    def quality_summary(self) -> str:
        """Transport-specific line diagnostics for the end-of-call log."""
        return ""

    def _end_input(self) -> None:
        self.closed.set()
        with contextlib.suppress(asyncio.QueueFull):
            self.inbound.put_nowait(None)

    async def _read_loop(self) -> None:
        try:
            while not self.closed.is_set():
                await self._read_one()
        except (WebSocketDisconnect, RuntimeError):
            logger.info("transport socket closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("transport read loop failed")
        finally:
            self._end_input()

    async def _read_one(self) -> None:
        raise NotImplementedError

    # ---------- outbound ----------

    def begin_playback(self) -> None:
        """Mark playback as pending before the audio exists.

        Synthesis can take a second; without this the agent's frame loop would
        see `playback_done` still set and conclude the line had already finished.
        """
        self.playback_done.clear()

    async def play(self, pcm: bytes) -> None:
        """Queue audio for real-time playback. Returns immediately;
        `playback_done` fires when the far end has finished playing it."""
        if not pcm or self.closed.is_set():
            self.playback_done.set()
            return
        self._outbound.extend(frame_pcm(pcm))
        self.playback_done.clear()
        if self._sender is None or self._sender.done():
            self._sender = asyncio.create_task(self._send_loop(), name="voice-sender")

    async def clear(self) -> None:
        """Barge-in: drop everything not yet spoken, here and at the far end."""
        self._generation += 1
        self._outbound.clear()
        if self._sender and not self._sender.done():
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender
        self._sender = None
        with contextlib.suppress(Exception):
            await self._send_clear()
        self.playback_done.set()

    @property
    def speaking(self) -> bool:
        return not self.playback_done.is_set()

    async def _send_loop(self) -> None:
        generation = self._generation
        start = time.monotonic()
        sent = 0
        try:
            while self._outbound and not self.closed.is_set():
                if generation != self._generation:
                    return
                target = start + (sent * FRAME_MS - LEAD_MS) / 1000.0
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                frame = self._outbound.pop(0)
                await self._send_frame(frame)
                self.frames_out += 1
                sent += 1
            # Wait out the audio still buffered at the far end. Twilio replaces
            # this with a real mark echo; see TwilioTransport.
            await asyncio.sleep(LEAD_MS / 1000.0)
            await self._on_drained()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("transport send loop failed")
            self.playback_done.set()

    async def _on_drained(self) -> None:
        self.playback_done.set()

    def _arm_mark_guard(self, seconds: float = 5.0) -> None:
        """Fail open if the far end never echoes our playback mark — a stuck
        `speaking` state would silently end the conversation."""
        generation = self._generation

        async def guard() -> None:
            await asyncio.sleep(seconds)
            if generation == self._generation and not self.playback_done.is_set():
                logger.warning("no playback mark echoed within %.1fs; continuing", seconds)
                self.playback_done.set()

        self._mark_guard = asyncio.create_task(guard(), name="voice-mark-guard")

    async def _send_frame(self, pcm_frame: bytes) -> None:
        raise NotImplementedError

    async def _send_clear(self) -> None:
        return None

    async def hangup(self) -> None:
        await self.stop()


class PlivoTransport(BaseTransport):
    """Plivo Audio Streaming: μ-law 8 kHz base64, bidirectional.

    Inbound:  start / media / playedStream / clearedAudio
    Outbound: playAudio / checkpoint / clearAudio

    Playback completion uses `checkpoint` → `playedStream` (Plivo's equivalent
    of Twilio's mark echo). Barge-in uses `clearAudio`.
    See https://plivo.com/docs/voice-agents/audio-streaming/
    """

    CONTENT_TYPE = "audio/x-mulaw"

    def __init__(self, ws: WebSocket) -> None:
        super().__init__(ws)
        self.stream_id = ""
        self.call_uuid = ""
        self._mark_seq = 0
        self._pending_checkpoint = ""
        self.ready = asyncio.Event()
        self._last_seq = 0
        self.off_track_packets = 0
        self.sequence_gaps = 0
        self.duplicate_packets = 0
        self.chunk_sizes: set[int] = set()

    async def _read_one(self) -> None:
        raw = await self.ws.receive_text()
        msg = json.loads(raw)
        event = msg.get("event")
        if event == "media":
            media = msg.get("media") or {}
            # The stream is requested as inbound-only, but never trust that: an
            # outbound frame is our own TTS coming back, and feeding it to the
            # endpointer makes the agent barge in on itself and transcribe its
            # own voice as the patient's answer.
            track = (media.get("track") or "inbound").lower()
            if track != "inbound":
                self.off_track_packets += 1
                return
            self._note_sequence(msg.get("sequenceNumber"))
            payload = media.get("payload")
            if payload:
                pcm = ulaw_to_pcm16(base64.b64decode(payload))
                self.chunk_sizes.add(len(pcm))
                self._offer_pcm(pcm)
        elif event == "start":
            start = msg.get("start") or {}
            self.stream_id = (
                start.get("streamId") or msg.get("streamId") or self.stream_id
            )
            self.call_uuid = start.get("callId") or self.call_uuid
            logger.info(
                "plivo stream started id=%s call=%s tracks=%s format=%s",
                self.stream_id, self.call_uuid,
                start.get("tracks") or start.get("track") or "?",
                start.get("mediaFormat") or "?",
            )
            self.ready.set()
        elif event == "playedStream":
            name = msg.get("name") or ""
            if name and name == self._pending_checkpoint:
                self.playback_done.set()
        elif event == "clearedAudio":
            # Far end confirmed barge-in; playback_done was already set in clear().
            pass
        elif event == "dtmf":
            digit = (msg.get("dtmf") or {}).get("digit") or ""
            logger.info("plivo dtmf digit=%s on stream %s", digit, self.stream_id)

    def _note_sequence(self, raw: object) -> None:
        """Track packet loss and reordering — both show up as clipped audio."""
        try:
            seq = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if self._last_seq:
            if seq <= self._last_seq:
                self.duplicate_packets += 1
            elif seq > self._last_seq + 1:
                self.sequence_gaps += 1
        self._last_seq = max(seq, self._last_seq)

    def quality_summary(self) -> str:
        return (
            f"chunks={sorted(self.chunk_sizes)}B off_track={self.off_track_packets} "
            f"gaps={self.sequence_gaps} dupes={self.duplicate_packets}"
        )

    async def _send_frame(self, pcm_frame: bytes) -> None:
        await self.ws.send_text(json.dumps({
            "event": "playAudio",
            "media": {
                "contentType": self.CONTENT_TYPE,
                "sampleRate": 8000,
                "payload": base64.b64encode(pcm16_to_ulaw(pcm_frame)).decode(),
            },
        }))

    async def _on_drained(self) -> None:
        # Don't set playback_done here — wait for Plivo to echo playedStream.
        self._mark_seq += 1
        self._pending_checkpoint = f"m{self._mark_seq}"
        with contextlib.suppress(Exception):
            await self.ws.send_text(json.dumps({
                "event": "checkpoint",
                "streamId": self.stream_id,
                "name": self._pending_checkpoint,
            }))
        self._arm_mark_guard()

    async def _send_clear(self) -> None:
        self._pending_checkpoint = ""
        if not self.stream_id:
            return
        await self.ws.send_text(json.dumps({
            "event": "clearAudio",
            "streamId": self.stream_id,
        }))

    async def hangup(self) -> None:
        await asyncio.sleep(0.4)
        await self.stop()


class TwilioTransport(BaseTransport):
    """Twilio Media Streams: μ-law 8 kHz base64 payloads, both directions.

    Playback completion uses Twilio `mark` events — the only trustworthy signal
    that the patient actually heard the whole line, since Twilio buffers.
    """

    def __init__(self, ws: WebSocket) -> None:
        super().__init__(ws)
        self.stream_sid = ""
        self.call_sid = ""
        self._mark_seq = 0
        self._pending_mark = ""
        self.ready = asyncio.Event()

    async def _read_one(self) -> None:
        raw = await self.ws.receive_text()
        msg = json.loads(raw)
        event = msg.get("event")
        if event == "media":
            media = msg.get("media", {})
            if (media.get("track") or "inbound").lower() != "inbound":
                self.frames_dropped += 1
                return
            payload = media.get("payload")
            if payload:
                self._offer_pcm(ulaw_to_pcm16(base64.b64decode(payload)))
        elif event == "start":
            start = msg.get("start", {})
            self.stream_sid = start.get("streamSid") or msg.get("streamSid") or ""
            self.call_sid = start.get("callSid") or ""
            logger.info("twilio stream started sid=%s call=%s", self.stream_sid, self.call_sid)
            self.ready.set()
        elif event == "mark":
            name = msg.get("mark", {}).get("name") or ""
            if name == self._pending_mark:
                self.playback_done.set()
        elif event == "stop":
            logger.info("twilio stream stopped sid=%s", self.stream_sid)
            self._end_input()

    async def _send_frame(self, pcm_frame: bytes) -> None:
        await self.ws.send_text(json.dumps({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": base64.b64encode(pcm16_to_ulaw(pcm_frame)).decode()},
        }))

    async def _on_drained(self) -> None:
        # Don't set playback_done here — wait for Twilio to echo the mark, which
        # is the only signal that the patient actually heard the whole line.
        self._mark_seq += 1
        self._pending_mark = f"m{self._mark_seq}"
        with contextlib.suppress(Exception):
            await self.ws.send_text(json.dumps({
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": self._pending_mark},
            }))
        self._arm_mark_guard()

    async def _send_clear(self) -> None:
        self._pending_mark = ""
        await self.ws.send_text(json.dumps({"event": "clear", "streamSid": self.stream_sid}))

    async def hangup(self) -> None:
        # Let the closing line finish arriving before tearing the socket down.
        await asyncio.sleep(0.4)
        await self.stop()


class BrowserTransport(BaseTransport):
    """Operator-console calls: binary PCM16 8 kHz frames in, same out.

    Used for demos and for testing the whole agent without a carrier. The client
    echoes `{"event":"mark"}` when its playback buffer drains, mirroring Twilio.
    """

    def __init__(self, ws: WebSocket) -> None:
        super().__init__(ws)
        self.ready = asyncio.Event()

    async def _read_one(self) -> None:
        msg = await self.ws.receive()
        if msg.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(msg.get("code", 1000))
        data = msg.get("bytes")
        if data:
            self._offer_pcm(data)
            return
        text = msg.get("text")
        if not text:
            return
        event = json.loads(text)
        kind = event.get("event")
        if kind == "start":
            self.ready.set()
        elif kind == "mark":
            self.playback_done.set()
        elif kind == "stop":
            self._end_input()

    async def _send_frame(self, pcm_frame: bytes) -> None:
        await self.ws.send_bytes(pcm_frame)

    async def _on_drained(self) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send_text(json.dumps({"event": "expect_mark"}))
        self._arm_mark_guard(seconds=3.0)

    async def _send_clear(self) -> None:
        await self.ws.send_text(json.dumps({"event": "clear"}))

    async def send_event(self, payload: dict) -> None:
        """Push agent state (transcript turns, status) to the console UI."""
        if self.closed.is_set():
            return
        with contextlib.suppress(Exception):
            await self.ws.send_text(json.dumps(payload))


class NullTransport(BaseTransport):
    """In-process transport for tests: audio in from a list, audio out to a buffer."""

    def __init__(self) -> None:  # noqa: D107 - no websocket involved
        super().__init__(ws=None)  # type: ignore[arg-type]
        self.written = bytearray()
        self.cleared = 0

    def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.closed.set()
        if self._sender and not self._sender.done():
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender

    async def _send_frame(self, pcm_frame: bytes) -> None:
        self.written.extend(pcm_frame)

    async def _send_clear(self) -> None:
        self.cleared += 1

    def feed(self, pcm: bytes) -> None:
        self._offer_pcm(pcm)
