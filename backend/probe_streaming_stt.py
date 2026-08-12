"""Feasibility probe for Sarvam streaming STT — python probe_streaming_stt.py

Streams a real recorded utterance at wall-clock pace and measures the gap
between the last audio chunk and the final transcript. That gap is the only
number that matters: it is what streaming would replace the current batch
round trip with. Compared against the batch path on the same audio.
"""

import asyncio
import json
import sys
import time
import wave

import websockets

from app.config import get_settings
from app.services.sarvam import sarvam

SETTINGS = get_settings()
WS_URL = "wss://api.sarvam.ai/speech-to-text/ws"
PARAMS = {
    "model": "saaras:v3",
    "language-code": "hi-IN",
    "sample_rate": "8000",
    "input_audio_codec": "pcm_s16le",
    "mode": "codemix",
    "high_vad_sensitivity": "true",
    "flush_signal": "true",
    "vad_signals": "true",
}


def load(path: str) -> tuple[bytes, int]:
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


async def probe(path: str) -> None:
    pcm, rate = load(path)
    duration = len(pcm) / 2 / rate
    print(f"\n{path.split('/')[-1]}  ({duration:.2f}s @ {rate}Hz)")

    # --- batch, as shipped ---
    with open(path, "rb") as f:
        blob = f.read()
    t0 = time.monotonic()
    try:
        text, lang, conf = await sarvam.stt(blob, "utterance.wav", language_hint="hi-IN")
        batch_ms = int((time.monotonic() - t0) * 1000)
        print(f"  batch     {batch_ms:5d}ms after end-of-speech  {text!r}")
    except Exception as e:
        batch_ms = -1
        print(f"  batch     FAILED: {type(e).__name__}: {e}")

    # --- streaming ---
    query = "&".join(f"{k}={v}" for k, v in PARAMS.items())
    headers = {"Api-Subscription-Key": SETTINGS.sarvam_api_key}
    try:
        ws = await websockets.connect(f"{WS_URL}?{query}", additional_headers=headers)
    except Exception as e:
        print(f"  streaming CONNECT FAILED: {type(e).__name__}: {e}")
        return

    finals: list[str] = []
    partials = 0
    end_of_audio = 0.0
    first_partial: float | None = None
    final_at: float | None = None

    async def receive():
        nonlocal partials, first_partial, final_at
        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type") or msg.get("event") or "?"
                data = msg.get("data") or {}
                text = (data.get("transcript") or "").strip()
                now = time.monotonic()
                if kind in {"partial_transcript", "partial"} or (text and not data.get("is_final", True)):
                    partials += 1
                    if first_partial is None:
                        first_partial = now
                elif text:
                    finals.append(text)
                    final_at = now
                elif kind not in {"ping", "pong"}:
                    print(f"    <- {kind}: {json.dumps(msg)[:160]}")
        except websockets.ConnectionClosed:
            pass

    task = asyncio.create_task(receive())
    # 20 ms frames, paced in real time — exactly what PlivoTransport produces.
    frame = int(rate * 0.02) * 2
    started = time.monotonic()
    import base64
    for i in range(0, len(pcm), frame):
        chunk = pcm[i : i + frame]
        await ws.send(json.dumps({
            "audio": {
                "data": base64.b64encode(chunk).decode("ascii"),
                "encoding": "audio/wav",
                "sample_rate": rate,
            }
        }))
        target = started + (i + frame) / 2 / rate
        await asyncio.sleep(max(0.0, target - time.monotonic()))
    end_of_audio = time.monotonic()
    await ws.send(json.dumps({"type": "flush"}))

    try:
        await asyncio.wait_for(task, timeout=6.0)
    except asyncio.TimeoutError:
        pass
    await ws.close()

    if finals:
        gap = int((final_at - end_of_audio) * 1000)
        lead = f", first partial {int((first_partial - started) * 1000)}ms in" if first_partial else ""
        print(f"  streaming {gap:5d}ms after end-of-speech  {' '.join(finals)!r}")
        print(f"            {partials} partial(s){lead}")
        if batch_ms > 0:
            print(f"            delta vs batch: {batch_ms - gap:+d}ms")
    else:
        print(f"  streaming no final transcript ({partials} partials)")


async def main() -> None:
    paths = sys.argv[1:]
    for p in paths:
        await probe(p)


if __name__ == "__main__":
    asyncio.run(main())
