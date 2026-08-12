"""Smoke checks for Plivo streaming VAD wiring — run: python test_plivo_stream.py"""

import asyncio
import base64
import json

from app.services.telephony import plivo_response, stream_element
from app.services.voice.audio import PCM_BYTES_PER_FRAME, pcm16_to_ulaw, ulaw_to_pcm16
from app.services.voice.transport import PlivoTransport


class FakeWS:
    """Enough of a Starlette WebSocket for the transport read/write paths."""

    def __init__(self, incoming: list[dict] | None = None) -> None:
        self.incoming = [json.dumps(m) for m in (incoming or [])]
        self.sent: list[dict] = []
        self.client_state = None

    async def receive_text(self) -> str:
        if not self.incoming:
            raise AssertionError("no more messages queued")
        return self.incoming.pop(0)

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def media(payload_pcm: bytes, *, track: str = "inbound", seq: int = 1) -> dict:
    return {
        "event": "media",
        "sequenceNumber": str(seq),
        "media": {
            "track": track,
            "payload": base64.b64encode(pcm16_to_ulaw(payload_pcm)).decode(),
        },
    }


def drain(transport: PlivoTransport) -> list[bytes]:
    frames = []
    while not transport.inbound.empty():
        frames.append(transport.inbound.get_nowait())
    return frames


def test_stream_xml():
    xml = plivo_response(stream_element(
        "wss://example.com/ws/voice/plivo/42",
        status_callback_url="https://example.com/plivo/stream-status/42",
    ))
    assert "<Stream " in xml
    assert 'bidirectional="true"' in xml
    assert 'keepCallAlive="true"' in xml
    assert 'audioTrack="inbound"' in xml, "outbound track would feed our own TTS back"
    assert 'contentType="audio/x-mulaw;rate=8000"' in xml
    assert "wss://example.com/ws/voice/plivo/42" in xml
    assert "stream-status/42" in xml
    assert "<<<" not in xml
    print("ok  stream XML is inbound-only bidirectional μ-law")


def test_ulaw_roundtrip():
    # μ-law is lossy but monotonic: a mid-level tone must survive with the
    # right sample width and roughly the right amplitude.
    import math
    import struct

    pcm = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * n / 8000)))
        for n in range(160)
    )
    encoded = pcm16_to_ulaw(pcm)
    assert len(encoded) == 160, "μ-law is one byte per sample"
    decoded = ulaw_to_pcm16(encoded)
    assert len(decoded) == len(pcm) == PCM_BYTES_PER_FRAME
    originals = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    restored = struct.unpack(f"<{len(decoded) // 2}h", decoded)
    worst = max(abs(a - b) for a, b in zip(originals, restored))
    assert worst < 400, f"μ-law roundtrip error too large: {worst}"
    print("ok  μ-law roundtrip preserves 16-bit 8 kHz audio")


def test_inbound_only():
    async def go():
        ws = FakeWS([
            media(b"\x11\x11" * 160, track="outbound"),
            media(b"\x22\x22" * 160, track="inbound"),
        ])
        t = PlivoTransport(ws)  # type: ignore[arg-type]
        await t._read_one()
        await t._read_one()
        assert t.off_track_packets == 1, "outbound (our own TTS) must be dropped"
        assert t.frames_in == 1
        assert len(drain(t)) == 1
    asyncio.run(go())
    print("ok  outbound track dropped, inbound kept")


def test_reframes_arbitrary_chunks():
    """Plivo does not promise 20 ms packets; the endpointer requires them."""
    async def go():
        # 100 ms in one packet, then 30 ms, then 10 ms.
        ws = FakeWS([
            media(b"\x01\x01" * 800, seq=1),
            media(b"\x02\x02" * 240, seq=2),
            media(b"\x03\x03" * 80, seq=3),
        ])
        t = PlivoTransport(ws)  # type: ignore[arg-type]
        for _ in range(3):
            await t._read_one()
        frames = drain(t)
        assert all(len(f) == PCM_BYTES_PER_FRAME for f in frames), \
            [len(f) for f in frames]
        # 800 + 240 + 80 = 1120 samples = 7 whole frames, 0 left over.
        assert len(frames) == 7, len(frames)
        assert t._inbound_residual == b"", "no samples may be lost or padded"
        assert sorted(t.chunk_sizes) == [160, 480, 1600]
    asyncio.run(go())
    print("ok  arbitrary chunks re-framed into exact 20 ms frames")


def test_partial_frame_is_carried_not_padded():
    head, tail = b"\x04\x04" * 100, b"\x05\x05" * 60

    def through_ulaw(pcm: bytes) -> bytes:
        return ulaw_to_pcm16(pcm16_to_ulaw(pcm))

    async def go():
        ws = FakeWS([media(head, seq=1), media(tail, seq=2)])
        t = PlivoTransport(ws)  # type: ignore[arg-type]
        await t._read_one()
        assert t.frames_in == 0, "100 samples is less than one frame — hold it"
        assert len(t._inbound_residual) == 200
        await t._read_one()
        frames = drain(t)
        assert len(frames) == 1 and len(frames[0]) == PCM_BYTES_PER_FRAME
        # The carried tail must be the real audio, not zero padding.
        assert frames[0][:200] == through_ulaw(head)
        assert frames[0][200:] == through_ulaw(tail)
    asyncio.run(go())
    print("ok  partial frames carried across packets, never zero-padded")


def test_sequence_gaps_and_duplicates():
    async def go():
        ws = FakeWS([
            media(b"\x00\x00" * 160, seq=1),
            media(b"\x00\x00" * 160, seq=4),  # gap
            media(b"\x00\x00" * 160, seq=4),  # duplicate
            media(b"\x00\x00" * 160, seq=5),
        ])
        t = PlivoTransport(ws)  # type: ignore[arg-type]
        for _ in range(4):
            await t._read_one()
        assert t.sequence_gaps == 1, t.sequence_gaps
        assert t.duplicate_packets == 1, t.duplicate_packets
        assert "gaps=1" in t.quality_summary()
    asyncio.run(go())
    print("ok  sequence gaps and duplicates counted")


def test_start_event_marks_ready():
    async def go():
        ws = FakeWS([{
            "event": "start",
            "start": {
                "streamId": "s-1", "callId": "c-1", "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
            },
        }])
        t = PlivoTransport(ws)  # type: ignore[arg-type]
        await t._read_one()
        assert t.ready.is_set()
        assert t.stream_id == "s-1" and t.call_uuid == "c-1"
    asyncio.run(go())
    print("ok  start event captures streamId/callId")


def test_playaudio_and_checkpoint():
    async def go():
        t = PlivoTransport(FakeWS())  # type: ignore[arg-type]
        t.stream_id = "s-1"
        await t._send_frame(b"\x00\x00" * 160)
        sent = t.ws.sent[-1]  # type: ignore[attr-defined]
        assert sent["event"] == "playAudio"
        assert sent["media"]["contentType"] == PlivoTransport.CONTENT_TYPE
        assert sent["media"]["sampleRate"] == 8000
        assert len(base64.b64decode(sent["media"]["payload"])) == 160

        # Draining arms a checkpoint; playback stays pending until Plivo echoes it.
        t.playback_done.clear()
        await t._on_drained()
        checkpoint = t.ws.sent[-1]  # type: ignore[attr-defined]
        assert checkpoint["event"] == "checkpoint"
        assert checkpoint["streamId"] == "s-1"
        assert not t.playback_done.is_set(), "must wait for playedStream"

        t.ws.incoming.append(json.dumps({  # type: ignore[attr-defined]
            "event": "playedStream", "name": checkpoint["name"],
        }))
        await t._read_one()
        assert t.playback_done.is_set(), "playedStream should complete playback"
        if t._mark_guard:
            t._mark_guard.cancel()
    asyncio.run(go())
    print("ok  playAudio → checkpoint → playedStream completes playback")


def test_clear_audio():
    async def go():
        t = PlivoTransport(FakeWS())  # type: ignore[arg-type]
        t.stream_id = "s-1"
        t._pending_checkpoint = "m1"
        await t._send_clear()
        sent = t.ws.sent[-1]  # type: ignore[attr-defined]
        assert sent == {"event": "clearAudio", "streamId": "s-1"}
        assert t._pending_checkpoint == "", "a cleared line must not still be pending"
    asyncio.run(go())
    print("ok  clearAudio cancels far-end playback")


def test_stt_request_parameters():
    """Hinglish lock: saaras:v3 + codemix, never language auto-detect."""
    from app.services import sarvam as sarvam_mod

    client = sarvam_mod.SarvamClient()
    calls: list[dict] = []

    async def fake_request(method, path, *, data=None, files=None, **kw):
        calls.append(dict(data or {}))
        if data and data["model"] == sarvam_mod.STT_MODEL and len(calls) == 1:
            raise sarvam_mod.SarvamUnavailable("model not enabled for this account")
        return {"transcript": "ठीक है", "language_code": "hi-IN"}

    client._request = fake_request  # type: ignore[assignment]

    text, lang, _ = asyncio.run(client.stt(b"RIFF", "turn.wav", "hi-IN"))
    assert text == "ठीक है" and lang == "hi-IN"
    assert calls[0] == {"model": "saaras:v3", "language_code": "hi-IN", "mode": "codemix"}
    assert calls[1] == {"model": "saarika:v2.5", "language_code": "hi-IN"}, \
        "saarika has no codemix mode"

    # The rejection is remembered — no repeat round trip on the next turn.
    calls.clear()
    asyncio.run(client.stt(b"RIFF", "turn.wav", "hi-IN"))
    assert [c["model"] for c in calls] == ["saarika:v2.5"], calls
    print("ok  STT locks language and falls back saaras:v3 → saarika:v2.5")


def test_vad_warmup_builds_session():
    """A 20 ms frame is shorter than Silero's window — warmup must push enough."""
    from app.services.voice import vad

    assert vad.warmup() is True
    assert vad._session is not None, "model would otherwise load mid-call"
    print("ok  Silero VAD warms up at boot, not on the first live frame")


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — this is the test runner
            failures += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
