"""End-to-end test of the browser voice console — no microphone, no carrier.

Speaks exactly what `static/voice_console.html` speaks at the server: a `start`
event, raw PCM16 8 kHz binary frames from the mic, and a `mark` echo once the
nurse's audio has "finished playing". Exists because the console is the demo
path that needs no Twilio account, so a regression in it is a regression in the
only thing that can be shown live.

    python test_browser_console.py                  # patient 1, Hindi
    python test_browser_console.py --patient 3
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).parent))

from test_voice_stream import SCENARIOS, fixture_pcm  # noqa: E402

from app.services.voice.audio import (  # noqa: E402
    FRAME_MS, PCM_BYTES_PER_FRAME, SILENCE_FRAME, frame_pcm,
)

API = "http://localhost:8000"


class FakeBrowser:
    """The console's audio loop, minus the AudioContext."""

    def __init__(self, ws, replies: list[bytes]) -> None:
        self.ws = ws
        self.replies = list(replies)
        self.playing_until = 0.0
        self.frames_out = 0
        self.audio_in = 0
        self.done = asyncio.Event()

    async def run(self) -> None:
        await self.ws.send(json.dumps({"event": "start"}))
        await asyncio.gather(self._mic(), self._speaker())

    async def _speaker(self) -> None:
        """Receive nurse audio; echo `mark` when the buffer would have drained."""
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    self.audio_in += len(message)
                    # Mirror the browser's scheduler: audio queues up in real time.
                    play_ms = len(message) / PCM_BYTES_PER_FRAME * FRAME_MS
                    now = time.monotonic()
                    self.playing_until = max(now, self.playing_until) + play_ms / 1000
                    continue
                event = json.loads(message).get("event")
                if event == "expect_mark":
                    await self._mark_when_drained()
                elif event == "clear":
                    self.playing_until = 0.0
                    print("  <- clear (barge-in accepted)")
        except websockets.ConnectionClosed:
            pass
        finally:
            self.done.set()

    async def _mark_when_drained(self) -> None:
        wait = max(0.0, self.playing_until - time.monotonic())
        await asyncio.sleep(wait)
        secs = self.audio_in / PCM_BYTES_PER_FRAME * FRAME_MS / 1000
        print(f"  <- nurse finished speaking ({secs:.1f}s of audio so far)")
        self.audio_in = 0
        with_reply = self.replies.pop(0) if self.replies else None
        try:
            await self.ws.send(json.dumps({"event": "mark"}))
            if with_reply is not None:
                await self._say(with_reply)
            else:
                await self.ws.send(json.dumps({"event": "stop"}))
        except websockets.ConnectionClosed:
            pass

    async def _say(self, pcm: bytes) -> None:
        frames = list(frame_pcm(pcm))
        print(f"  -> patient replies ({len(frames) * FRAME_MS}ms of speech)")
        for frame in frames:
            await self.ws.send(frame)
            await asyncio.sleep(FRAME_MS / 1000)

    async def _mic(self) -> None:
        """Background silence, exactly as an open mic in a quiet room would send."""
        try:
            while not self.done.is_set():
                await self.ws.send(SILENCE_FRAME)
                self.frames_out += 1
                await asyncio.sleep(FRAME_MS / 1000)
        except (websockets.ConnectionClosed, RuntimeError):
            pass


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", type=int, default=1)
    ap.add_argument("--lang", default="hi-IN")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="adherent")
    ap.add_argument("--base", default=API)
    ap.add_argument(
        "--allow-dial", action="store_true",
        help="permit this run against a backend wired to a real carrier",
    )
    args = ap.parse_args()
    api = args.base.rstrip("/")

    async with httpx.AsyncClient(timeout=180) as http:
        # /patients/{id}/call is the production entrypoint: against a live
        # carrier it rings the patient's real phone, and the console then
        # fights that call's own media stream for the same agent.
        health = await http.get(f"{api}/health")
        mode = health.json().get("telephony_mode") if health.status_code < 400 else "?"
        if mode == "plivo" and not args.allow_dial:
            print(
                f"!! backend telephony_mode={mode} — this would place a real call to "
                f"patient {args.patient}. Re-run with --allow-dial if that is intended."
            )
            return 1

        res = await http.post(f"{api}/patients/{args.patient}/call")
        if res.status_code >= 400:
            print(f"!! could not place call: {res.status_code} {res.text[:300]}")
            return 1
        data = res.json()
        call_id = data["call"]["id"]
        if not data.get("stream_url"):
            print("!! backend is not in streaming voice mode")
            return 1
        print(f"call_id={call_id} patient={args.patient}")

        lines = SCENARIOS[args.scenario].get(args.lang) or SCENARIOS[args.scenario]["en-IN"]
        print(f"preparing patient replies ({args.scenario}, {args.lang})…")
        replies = [await fixture_pcm(t, args.lang) for t in lines]

        url = api.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{url}/ws/voice/browser/{call_id}"
        print(f"\nconnecting console stream → {url}\n")
        started = time.monotonic()
        async with websockets.connect(url, max_size=None) as ws:
            browser = FakeBrowser(ws, replies)
            await browser.run()
        print(f"\ncall finished in {time.monotonic() - started:.1f}s")

        call = (await http.get(f"{api}/calls/{call_id}")).json()
        turns = call.get("turns", [])
        print(f"\nstatus={call['status']}  turns={len(turns)}")
        print("-" * 72)
        for t in turns:
            meta = f"  [{t['latency_ms']}ms]" if t["latency_ms"] else ""
            print(f"{t['role'].upper():8}{t['step_key']:13}{t['text']}{meta}")
        print("-" * 72)
        heard = [t for t in turns if t["role"] == "patient"]
        ok = len(heard) >= 1 and call["status"] == "completed"
        print(f"\n{'PASS' if ok else 'FAIL'}: {len(heard)} patient turns transcribed")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
