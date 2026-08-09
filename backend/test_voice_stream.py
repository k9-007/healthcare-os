"""End-to-end test of the conversational voice agent — no phone, no Twilio.

Speaks Twilio's Media Streams protocol at the server exactly as Twilio would:
sends a `start` event, streams μ-law frames in real time, echoes `mark` events
when playback finishes, and plays scripted patient replies (synthesized with
Sarvam so the STT path is genuinely exercised) after each nurse question.

    python test_voice_stream.py                 # default: adherent patient, Hindi
    python test_voice_stream.py --scenario emergency
    python test_voice_stream.py --lang en-IN --scenario missed

Requires the backend running on http://localhost:8000.
"""

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).parent))

from app.services.sarvam import sarvam  # noqa: E402
from app.services.voice.audio import (  # noqa: E402
    PCM_BYTES_PER_FRAME, SILENCE_FRAME, frame_pcm, pcm16_to_ulaw, wav_to_pcm8k,
)

API = "http://localhost:8000"
FIXTURES = Path(__file__).parent / "data" / "test_audio"

SCENARIOS = {
    "adherent": {
        "hi-IN": [
            "हाँ, मैंने दवा ले ली है",
            "हाँ, वो भी ले ली है",
            "मैं ठीक हूँ, कोई तकलीफ नहीं है",
        ],
        "en-IN": [
            "Yes, I have taken my medicine",
            "Yes, I took that one as well",
            "I am feeling fine, no problems at all",
        ],
    },
    "missed": {
        "hi-IN": [
            "नहीं, मैं भूल गया था",
            "नहीं, वो भी नहीं ली",
            "थोड़ी कमजोरी लग रही है और बुखार भी है",
        ],
        "en-IN": [
            "No, I forgot to take it",
            "No, I missed that one too",
            "I feel weak and I have a fever",
        ],
    },
    "emergency": {
        "hi-IN": [
            "हाँ ले ली है",
            "हाँ वो भी ली है",
            "मुझे छाती में बहुत तेज दर्द हो रहा है और सांस नहीं आ रही",
        ],
        "en-IN": [
            "Yes I took it",
            "Yes I took that one too",
            "I have severe chest pain and I cannot breathe properly",
        ],
    },
}


async def fixture_pcm(text: str, language: str) -> bytes:
    """Patient audio, synthesized once and cached on disk between runs."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{language}_{abs(hash(text)) % (10**10)}.wav"
    if not path.exists():
        print(f"  synthesizing fixture: {text!r}")
        # A different voice from the nurse's, so the fixture is not just our own
        # TTS being fed straight back into our own STT.
        path.write_bytes(await sarvam.tts_telephony(text, language, speaker="rahul"))
    return wav_to_pcm8k(path.read_bytes())


class FakeTwilio:
    """The Twilio side of a Media Stream, driven by a script of replies."""

    def __init__(self, ws, replies: list[bytes], barge_in_after_ms: int = 0) -> None:
        self.ws = ws
        self.replies = replies
        self.barge_in_after_ms = barge_in_after_ms
        self.stream_sid = "MZtest0000000000000000000000000000"
        self.nurse_audio_ms = 0
        self.marks = 0
        self.barge_ins = 0
        self.speaking = False
        self.finished = asyncio.Event()

    async def run(self) -> None:
        await self.ws.send(json.dumps({
            "event": "start",
            "streamSid": self.stream_sid,
            "start": {"streamSid": self.stream_sid, "callSid": "CAtest000000000000000000000000000"},
        }))
        sender = asyncio.create_task(self._send_patient_audio())
        try:
            await asyncio.wait_for(self._receive(), timeout=180)
        except asyncio.TimeoutError:
            print("!! timed out waiting for the agent")
        finally:
            sender.cancel()

    async def _receive(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            event = msg.get("event")
            if event == "media":
                self.nurse_audio_ms += 20
                self.speaking = True
            elif event == "mark":
                self.marks += 1
                secs = self.nurse_audio_ms / 1000
                print(f"  <- nurse finished line {self.marks} ({secs:.1f}s of audio)")
                self.nurse_audio_ms = 0
                self.speaking = False
                # Twilio echoes the mark once the caller has actually heard it.
                await self.ws.send(json.dumps({"event": "mark", "mark": msg["mark"]}))
            elif event == "clear":
                print("  <- agent cleared its buffer (barge-in accepted)")
                self.nurse_audio_ms = 0
                self.speaking = False
                self.barge_ins += 1
        self.finished.set()

    async def _send_patient_audio(self) -> None:
        """Stream silence continuously; inject a reply after each nurse line."""
        reply_index = 0
        pending: bytes = b""
        next_frame = time.monotonic()
        spoken_marks = 0
        while True:
            next_frame += 0.02
            await asyncio.sleep(max(0, next_frame - time.monotonic()))

            interrupting = (
                self.barge_in_after_ms
                and self.speaking
                and self.nurse_audio_ms >= self.barge_in_after_ms
            )
            answering = not self.speaking and self.marks > spoken_marks

            if not pending and reply_index < len(self.replies) and (interrupting or answering):
                if answering:
                    # Let the line settle before answering, like a real person.
                    await asyncio.sleep(0.4)
                    spoken_marks = self.marks
                pending = self.replies[reply_index]
                how = "interrupts" if interrupting else "replies"
                print(f"  -> patient {how} ({len(pending) // 320 * 20}ms of speech)")
                reply_index += 1
            elif answering:
                spoken_marks = self.marks

            if pending:
                frame, pending = pending[:PCM_BYTES_PER_FRAME], pending[PCM_BYTES_PER_FRAME:]
                if len(frame) < PCM_BYTES_PER_FRAME:
                    frame += b"\x00" * (PCM_BYTES_PER_FRAME - len(frame))
            else:
                frame = SILENCE_FRAME
            await self._send_frame(frame)

    async def _send_frame(self, pcm_frame: bytes) -> None:
        await self.ws.send(json.dumps({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": base64.b64encode(pcm16_to_ulaw(pcm_frame)).decode()},
        }))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="adherent")
    ap.add_argument("--lang", default="hi-IN")
    ap.add_argument("--patient", type=int, default=None)
    ap.add_argument(
        "--barge-in", type=int, default=0, metavar="MS",
        help="interrupt the nurse after MS of her speech instead of waiting politely",
    )
    ap.add_argument(
        "--base", default=API, metavar="URL",
        help="backend base URL; point at the public tunnel to test the path Twilio uses",
    )
    args = ap.parse_args()
    api = args.base.rstrip("/")

    async with httpx.AsyncClient(timeout=120) as http:
        health = (await http.get(f"{api}/health")).json()
        print(f"backend: voice_mode={health['voice_mode']} sarvam={health['sarvam_configured']}")

        params = {"lang": args.lang}
        if args.patient:
            params["patient_id"] = args.patient
        print("\nfetching TwiML from the console demo entrypoint…")
        twiml = (await http.post(f"{api}/twilio/voice/demo", params=params)).text
        print(f"  {twiml}")
        if "Stream" not in twiml:
            print("!! not a streaming TwiML response")
            return 1
        call_id = int(twiml.split("/ws/voice/twilio/")[1].split('"')[0])
        print(f"  call_id={call_id}")

        lines = SCENARIOS[args.scenario].get(args.lang) or SCENARIOS[args.scenario]["en-IN"]
        print(f"\npreparing patient replies ({args.scenario}, {args.lang})…")
        replies = [await fixture_pcm(t, args.lang) for t in lines]

        url = api.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{url}/ws/voice/twilio/{call_id}"
        print(f"\nconnecting media stream → {url}\n")
        started = time.monotonic()
        async with websockets.connect(url, max_size=None) as ws:
            twilio = FakeTwilio(ws, replies, barge_in_after_ms=args.barge_in)
            await twilio.run()
        print(f"\ncall finished in {time.monotonic() - started:.1f}s")

        call = (await http.get(f"{api}/calls/{call_id}")).json()
        print(f"\nstatus={call['status']}  turns={len(call['turns'])}")
        print("-" * 72)
        for t in call["turns"]:
            tag = "NURSE  " if t["role"] == "nurse" else "PATIENT"
            extra = f"  [{t['latency_ms']}ms]" if t["latency_ms"] else ""
            barge = "  (barge-in)" if t["barge_in"] else ""
            print(f"{tag} {t['step_key'] or '-':<12} {t['text']}{extra}{barge}")
        print("-" * 72)
        print("extracted:")
        for r in call["responses"]:
            print(f"  {r['key']} = {r['value']}")

        escalations = (await http.get(f"{api}/escalations")).json()
        open_for_call = [e for e in escalations if e.get("call_log_id") == call_id]
        if open_for_call:
            print(f"\nESCALATION RAISED: {open_for_call[0]['reason'][:100]}")

        patient_turns = [t for t in call["turns"] if t["role"] == "patient" and t["text"].strip()]
        ok = len(patient_turns) >= 1 and call["status"] == "completed"
        print(f"\n{'PASS' if ok else 'FAIL'}: {len(patient_turns)} patient turns transcribed")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
