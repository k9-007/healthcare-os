"""Pre-synthesized speech cache.

A care call is mostly scripted: when the scheduler materializes a slot it
already knows the exact medicines and follow-up questions. Rendering that audio
ahead of time turns the common turn from ~3.9s of dead air into ~1.7s, because
TTS (the slowest stage, 1.3s for one sentence) drops out of the live path
entirely.

Cache key is a hash of (text, language, speaker); files live under
DATA_DIR/tts_cache as 8 kHz WAVs and are reused across calls and patients —
"Have you taken your Metformin?" is synthesized once, ever.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from ...config import get_settings
from ..sarvam import DEFAULT_SPEAKER, SarvamUnavailable, sarvam
from .audio import wav_to_pcm8k

logger = logging.getLogger("voice.prewarm")

_locks: dict[str, asyncio.Lock] = {}


def cache_dir() -> Path:
    d = get_settings().data_path / "tts_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(text: str, language: str, speaker: str = DEFAULT_SPEAKER) -> str:
    norm = " ".join(text.split()).lower()
    return hashlib.sha256(f"{norm}|{language}|{speaker}".encode()).hexdigest()[:24]


def cached_path(text: str, language: str, speaker: str = DEFAULT_SPEAKER) -> Path:
    return cache_dir() / f"{cache_key(text, language, speaker)}.wav"


def is_cached(text: str, language: str, speaker: str = DEFAULT_SPEAKER) -> bool:
    p = cached_path(text, language, speaker)
    return p.exists() and p.stat().st_size > 44


async def synthesize_cached(
    text: str, language: str, speaker: str = DEFAULT_SPEAKER
) -> bytes | None:
    """8 kHz PCM for `text`, from cache when possible. None if TTS is down.

    Concurrent requests for the same line wait on one synthesis rather than
    each paying the Sarvam round-trip.
    """
    text = " ".join(text.split())
    if not text:
        return None
    path = cached_path(text, language, speaker)
    if path.exists() and path.stat().st_size > 44:
        return wav_to_pcm8k(path.read_bytes())

    key = path.name
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if path.exists() and path.stat().st_size > 44:
            return wav_to_pcm8k(path.read_bytes())
        try:
            wav = await sarvam.tts_telephony(text, language, speaker)
        except (SarvamUnavailable, Exception) as e:  # noqa: BLE001 — never break a live call
            logger.warning("TTS failed for %r (%s): %s", text[:40], language, e)
            return None
        tmp = path.with_suffix(".part")
        tmp.write_bytes(wav)
        tmp.replace(path)
        logger.info("synthesized %d bytes for %r (%s)", len(wav), text[:40], language)
        return wav_to_pcm8k(wav)


async def prewarm(lines: list[str], language: str, speaker: str = DEFAULT_SPEAKER) -> int:
    """Render any not-yet-cached lines. Returns how many were synthesized."""
    todo = [t for t in dict.fromkeys(lines) if t.strip() and not is_cached(t, language, speaker)]
    if not todo:
        return 0
    results = await asyncio.gather(
        *(synthesize_cached(t, language, speaker) for t in todo), return_exceptions=True
    )
    done = sum(1 for r in results if isinstance(r, bytes))
    logger.info("prewarmed %d/%d lines for %s", done, len(todo), language)
    return done
