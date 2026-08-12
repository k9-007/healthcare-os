"""Telephony audio primitives.

Everything on the call path is mono 16-bit PCM at 8 kHz — the native rate of
both Twilio Media Streams and Sarvam TTS (`speech_sample_rate: 8000`), so no
resampling happens per frame. μ-law only exists at the Twilio boundary.
"""

import audioop
import io
import wave
from collections.abc import Iterator

SAMPLE_RATE = 8000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 160
PCM_BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # 320
SILENCE_FRAME = b"\x00" * PCM_BYTES_PER_FRAME


def ulaw_to_pcm16(payload: bytes) -> bytes:
    return audioop.ulaw2lin(payload, 2)


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    return audioop.lin2ulaw(pcm, 2)


def frame_pcm(pcm: bytes, frame_bytes: int = PCM_BYTES_PER_FRAME) -> Iterator[bytes]:
    """Split PCM into fixed-size frames, zero-padding the final partial frame."""
    for i in range(0, len(pcm), frame_bytes):
        chunk = pcm[i : i + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk += b"\x00" * (frame_bytes - len(chunk))
        yield chunk


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm8k(blob: bytes) -> bytes:
    """Decode any mono/stereo PCM WAV to 8 kHz mono 16-bit.

    Sarvam returns 8 kHz when asked, but the pre-synthesis cache may hold audio
    rendered at another rate, and test fixtures are usually 16 kHz.
    """
    with wave.open(io.BytesIO(blob), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        pcm = w.readframes(w.getnframes())
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels > 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, SAMPLE_RATE, None)
    return pcm


def duration_ms(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> int:
    return int(len(pcm) / 2 / sample_rate * 1000)


def clip_wav(blob: bytes, max_ms: int) -> bytes:
    """The first `max_ms` of a WAV clip, re-encoded as 8 kHz mono.

    Sarvam's synchronous STT rejects audio longer than 30 s, so an over-long
    carrier recording must be cut, not sent whole — the useful speech is at the
    start, the tail is the silence that failed to end the turn.
    """
    pcm = wav_to_pcm8k(blob)
    if duration_ms(pcm) <= max_ms:
        return blob
    return pcm_to_wav(pcm[: max_ms * SAMPLE_RATE * 2 // 1000])


def rms(pcm: bytes) -> float:
    return float(audioop.rms(pcm, 2)) if pcm else 0.0


def peak(pcm: bytes) -> float:
    return float(audioop.max(pcm, 2)) if pcm else 0.0
