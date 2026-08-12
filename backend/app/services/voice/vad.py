"""Silero VAD endpointing.

The ONNX graph is driven directly through onnxruntime rather than the
`silero_vad` torch wrapper: the model is 2 MB, inference is ~0.1 ms per window
on CPU, and keeping torch off the call path removes a heavyweight import from
request handling.

Silero's 8 kHz mode consumes 256-sample windows, so inbound 20 ms telephony
frames (160 samples) are re-blocked internally.
"""

import logging
import os
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .audio import (
    PCM_BYTES_PER_FRAME,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    SILENCE_FRAME,
    duration_ms,
)

logger = logging.getLogger("voice.vad")

WINDOW_SAMPLES = 256  # Silero's required window at 8 kHz
# The graph expects the previous 32 samples prepended to every window; without
# them its internal STFT is framed against silence and the probabilities are
# erratic (loud speech scoring 0.05).
CONTEXT_SAMPLES = 32
_session = None


def _get_session():
    """Lazily build the shared ORT session (single-threaded: it is called from
    the event loop, and per-frame work is far cheaper than a thread hop)."""
    global _session
    if _session is None:
        import onnxruntime as ort
        import silero_vad

        path = os.path.join(os.path.dirname(silero_vad.__file__), "data", "silero_vad.onnx")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        _session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        logger.info("Silero VAD loaded from %s", path)
    return _session


def warmup() -> bool:
    """Pay the model-load cost at boot instead of during the first call.

    A 20 ms frame is 160 samples — short of the 256-sample window — so a single
    push returns the carried-forward probability and never touches the session.
    Feed enough frames to force a real inference, or the load lands on the first
    live frame instead and stalls the event loop for ~0.9 s while the greeting
    is playing.
    """
    endpointer = Endpointer()
    for _ in range(-(-WINDOW_SAMPLES // SAMPLES_PER_FRAME)):
        endpointer.push(SILENCE_FRAME)
    if _session is None:
        logger.error("Silero VAD warmup ran without building a session")
        return False
    return True


class Event(Enum):
    SPEECH_START = "speech_start"
    UTTERANCE_END = "utterance_end"


@dataclass
class Stats:
    """Per-utterance VAD confidence, for diagnosing what the line actually sent."""

    frames: int = 0
    speech_frames: int = 0
    peak_prob: float = 0.0
    _prob_sum: float = 0.0

    @property
    def mean_prob(self) -> float:
        return self._prob_sum / self.frames if self.frames else 0.0

    @property
    def speech_ratio(self) -> float:
        return self.speech_frames / self.frames if self.frames else 0.0

    def observe(self, prob: float, is_speech: bool) -> None:
        self.frames += 1
        self.speech_frames += int(is_speech)
        self.peak_prob = max(self.peak_prob, prob)
        self._prob_sum += prob

    def __str__(self) -> str:
        return (
            f"peak={self.peak_prob:.2f} mean={self.mean_prob:.2f} "
            f"speech={self.speech_ratio:.0%}"
        )


class Endpointer:
    """Turns a stream of 20 ms PCM frames into utterance boundaries.

    Emits SPEECH_START once sustained speech is detected and UTTERANCE_END when
    the trailing silence (or the max-length guard) closes the turn. The captured
    audio includes a short pre-roll so the first phoneme is never clipped.
    """

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        start_ms: int = 150,
        silence_ms: int = 600,
        max_utterance_ms: int = 15000,
        preroll_ms: int = 400,
        gap_tolerance_ms: int = 120,
    ) -> None:
        self.threshold = threshold
        # Hysteresis: it takes a confident frame to start counting speech, but
        # only a weak one to keep counting. Silero's 8 kHz probabilities dip
        # hard between syllables, and a single threshold turns one sentence into
        # a dozen fragments.
        self.continue_threshold = threshold * 0.6
        self.gap_tolerance_ms = gap_tolerance_ms
        self.start_ms = start_ms
        self.silence_ms = silence_ms
        self.max_utterance_ms = max_utterance_ms
        self._preroll = deque(maxlen=max(1, preroll_ms // 20))
        self._residual = b""
        self._last_prob = 0.0
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.last_stats = Stats()
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_run_ms = 0
        self.silence_run_ms = 0
        self._gap_ms = 0
        self._utterance = bytearray()
        self._preroll.clear()
        self._residual = b""
        self._last_prob = 0.0
        self._context[:] = 0.0
        self._state[:] = 0.0
        self.stats = Stats()

    @property
    def utterance(self) -> bytes:
        return bytes(self._utterance)

    def utterance_ms(self) -> int:
        return duration_ms(bytes(self._utterance))

    def push(self, frame: bytes) -> Event | None:
        """Feed one 20 ms PCM frame. Returns a boundary event, if any."""
        prob = self._probability(frame)
        frame_ms = duration_ms(frame)
        # Once a run is under way (provisionally or confirmed), the weaker
        # threshold applies — see `continue_threshold`.
        started = self.in_speech or self.speech_run_ms > 0
        is_speech = prob >= (self.continue_threshold if started else self.threshold)
        self.stats.observe(prob, is_speech)

        if self.in_speech:
            self._utterance.extend(frame)
            if is_speech:
                self.speech_run_ms += frame_ms
                self.silence_run_ms = 0
            else:
                self.silence_run_ms += frame_ms
                if self.silence_run_ms >= self.silence_ms:
                    return Event.UTTERANCE_END
            if self.utterance_ms() >= self.max_utterance_ms:
                return Event.UTTERANCE_END
            return None

        self._preroll.append(frame)
        if is_speech:
            self.speech_run_ms += frame_ms
            self._gap_ms = 0
            if self.speech_run_ms >= self.start_ms:
                self.in_speech = True
                self.silence_run_ms = 0
                self._utterance = bytearray(b"".join(self._preroll))
                self._preroll.clear()
                return Event.SPEECH_START
        elif self.speech_run_ms:
            # A brief dip between syllables should not restart the count.
            self._gap_ms += frame_ms
            if self._gap_ms > self.gap_tolerance_ms:
                self.speech_run_ms = 0
                self._gap_ms = 0
        return None

    def take_utterance(self, trim_trailing_silence: bool = True) -> bytes:
        """Return the captured utterance and arm the endpointer for the next turn."""
        pcm = bytes(self._utterance)
        if trim_trailing_silence and self.silence_run_ms > 200:
            drop = (self.silence_run_ms - 200) // 20 * PCM_BYTES_PER_FRAME
            if 0 < drop < len(pcm):
                pcm = pcm[: len(pcm) - drop]
        self.last_stats = self.stats
        self.reset()
        return pcm

    def _probability(self, frame: bytes) -> float:
        """Run every complete 256-sample window in this frame; report the max.

        Max (not mean) keeps onset latency low — a window of speech at the end
        of a frame should trip the detector immediately.
        """
        self._residual += frame
        usable = len(self._residual) // (WINDOW_SAMPLES * 2) * (WINDOW_SAMPLES * 2)
        if not usable:
            # A 20 ms frame is 160 samples, so roughly every third frame cannot
            # complete a window. Carrying the last verdict forward keeps the
            # speech/silence run counters continuous; reporting silence here
            # would reset them and no utterance would ever start.
            return self._last_prob
        block, self._residual = self._residual[:usable], self._residual[usable:]
        samples = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0

        session = _get_session()
        best = 0.0
        for i in range(0, len(samples), WINDOW_SAMPLES):
            window = samples[i : i + WINDOW_SAMPLES].reshape(1, -1)
            with_context = np.concatenate([self._context, window], axis=1)
            out, self._state = session.run(
                None, {"input": with_context, "state": self._state, "sr": self._sr}
            )
            self._context = with_context[:, -CONTEXT_SAMPLES:]
            best = max(best, float(out[0][0]))
        self._last_prob = best
        return best
