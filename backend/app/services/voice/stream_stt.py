"""Streaming transcription over Sarvam's speech-to-text WebSocket.

Measured against the batch endpoint on real recorded turns from call 157/158,
the final transcript lands 570–1137 ms earlier — often before the patient has
even stopped speaking, because Sarvam transcribes continuously and finalizes on
its own end-of-speech signal instead of waiting for a file to be uploaded.

Turn-taking is deliberately *not* handed over to Sarvam's VAD. Silero already
decides when the patient has finished, that behaviour is tuned and working, and
Sarvam's server-side VAD clips leading words often enough that trusting it with
a yes/no answer would be a bad trade. This class is only a faster replacement
for the transcription round trip: frames go in, finished text comes out, and
anything unexpected falls back to the batch POST.
"""

import asyncio
import base64
import contextlib
import json
import logging

import websockets

logger = logging.getLogger("voice.stt")

WS_URL = "wss://api.sarvam.ai/speech-to-text/ws"

# How long to wait past our own end-of-speech for Sarvam to finalize before
# giving up and using the batch endpoint. Finals normally arrive before this
# point; the budget only exists so a stalled socket cannot stall the call.
FINAL_GRACE_S = 0.5


class SarvamStream:
    """One WebSocket per call, collecting finished transcripts per turn."""

    def __init__(self, api_key: str, language: str, sample_rate: int = 8000) -> None:
        self._key = api_key
        self._language = language
        self._rate = sample_rate
        self._ws: websockets.ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._parts: list[str] = []
        self._arrived = asyncio.Event()
        self.alive = False

    @property
    def _params(self) -> dict[str, str]:
        return {
            "model": "saaras:v3",
            "language-code": self._language,
            "sample_rate": str(self._rate),
            "input_audio_codec": "pcm_s16le",
            "mode": "codemix",
            "high_vad_sensitivity": "true",
            "flush_signal": "true",
            "vad_signals": "true",
        }

    async def connect(self) -> bool:
        query = "&".join(f"{k}={v}" for k, v in self._params.items())
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    f"{WS_URL}?{query}",
                    additional_headers={"Api-Subscription-Key": self._key},
                    ping_interval=20,
                ),
                timeout=5.0,
            )
        except Exception as e:
            logger.warning("streaming STT unavailable (%s); using batch", e)
            return False
        self._task = asyncio.create_task(self._receive())
        self.alive = True
        logger.info("streaming STT session open (%s)", self._language)
        return True

    async def _receive(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "error":
                    logger.warning("streaming STT error: %s", msg.get("data"))
                    continue
                text = ((msg.get("data") or {}).get("transcript") or "").strip()
                if text:
                    self._parts.append(text)
                    self._arrived.set()
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("streaming STT receive loop failed")
        finally:
            # No reconnect: the batch endpoint is a correct, already-tested
            # fallback, and a half-working socket mid-call is worse than none.
            self.alive = False

    async def feed(self, pcm: bytes) -> None:
        if not self.alive or self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({
                "audio": {
                    "data": base64.b64encode(pcm).decode("ascii"),
                    "encoding": "audio/wav",
                    "sample_rate": self._rate,
                }
            }))
        except Exception:
            logger.warning("streaming STT send failed; falling back to batch")
            self.alive = False

    def begin_turn(self) -> None:
        """Drop anything Sarvam said before the patient started this answer."""
        self._parts.clear()
        self._arrived.clear()

    async def take(self) -> str:
        """The transcript for the turn just ended, or "" to use batch instead."""
        if not self.alive:
            return ""
        if not self._parts:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), timeout=FINAL_GRACE_S)
        return " ".join(self._parts).strip()

    async def close(self) -> None:
        self.alive = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
