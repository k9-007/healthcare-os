"""Latency benchmark for the real-time voice loop (STT → LLM → TTS).

Run: ./.venv/bin/python bench_voice.py
Numbers here drive the turn-latency budget in docs/realtime-voice.md.
"""

import base64
import time

import httpx

KEY = open(".env").readline().split("=", 1)[1].strip()
BASE = "https://api.sarvam.ai"
H = {"api-subscription-key": KEY}

TURN = "Patient said: I took my tablet this morning. Ask the next follow-up question about knee pain, one short sentence."


def timed(label, fn):
    t = time.perf_counter()
    try:
        info = fn()
        dt = time.perf_counter() - t
        print(f"{label:<44} {dt:6.2f}s   {info}")
        return dt
    except Exception as e:
        dt = time.perf_counter() - t
        print(f"{label:<44} {dt:6.2f}s   FAILED: {str(e)[:120]}")
        return None


def llm(model, max_tokens):
    def go():
        r = httpx.post(
            f"{BASE}/v1/chat/completions", headers=H, timeout=120,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a phone nurse. Reply with ONE short spoken sentence."},
                    {"role": "user", "content": TURN},
                ],
                "max_tokens": max_tokens, "temperature": 0.3,
            },
        )
        d = r.json()
        if "error" in d:
            raise RuntimeError(d["error"].get("message"))
        c = d["choices"][0]
        content = (c["message"].get("content") or "").strip()
        reasoning = c["message"].get("reasoning_content") or ""
        return (f"finish={c['finish_reason']} out_tok={d['usage']['completion_tokens']} "
                f"reason_chars={len(reasoning)} text={content[:52]!r}")
    return go


def tts(text, lang):
    def go():
        r = httpx.post(
            f"{BASE}/text-to-speech", headers=H, timeout=120,
            json={"text": text, "target_language_code": lang, "speaker": "priya", "model": "bulbul:v3"},
        )
        d = r.json()
        if "error" in d:
            raise RuntimeError(d["error"].get("message"))
        wav = base64.b64decode(d["audios"][0])
        secs = (len(wav) - 44) / (22050 * 2)
        return f"{len(wav)/1024:.0f} KB wav, {secs:.1f}s audio for {len(text)} chars"
    return go


def stt(wav_bytes):
    def go():
        r = httpx.post(
            f"{BASE}/speech-to-text", headers=H, timeout=120,
            files={"file": ("reply.wav", wav_bytes, "audio/wav")},
            data={"model": "saarika:v2.5"},
        )
        d = r.json()
        if "error" in d:
            raise RuntimeError(d["error"].get("message"))
        return f"lang={d.get('language_code')} text={d.get('transcript', '')[:52]!r}"
    return go


def make_clip(text, lang):
    r = httpx.post(
        f"{BASE}/text-to-speech", headers=H, timeout=120,
        json={"text": text, "target_language_code": lang, "speaker": "priya", "model": "bulbul:v3"},
    )
    return base64.b64decode(r.json()["audios"][0])


print("\n--- LLM: one conversational turn ---")
for model in ("sarvam-105b", "sarvam-105b-conversations"):
    for mt in (150, 400):
        timed(f"{model} max_tokens={mt}", llm(model, mt))

print("\n--- TTS: one spoken sentence ---")
timed("TTS hi-IN short (60 chars)", tts("क्या आपने आज अपनी दवा ली है?", "hi-IN"))
timed("TTS hi-IN medium (180 chars)", tts(
    "नमस्ते अनीता जी, यह अस्पताल से आपकी देखभाल कॉल है। कृपया बताइए कि क्या आपने भोजन के बाद अपनी मेटफॉर्मिन की गोली ली है और आज आपकी तबीयत कैसी है।",
    "hi-IN"))

print("\n--- STT: one patient utterance ---")
clip = make_clip("हाँ जी, मैंने खाने के बाद दवा ले ली है।", "hi-IN")
print(f"(test clip: {len(clip)/1024:.0f} KB, {(len(clip)-44)/(22050*2):.1f}s)")
timed("STT saarika:v2.5", stt(clip))
