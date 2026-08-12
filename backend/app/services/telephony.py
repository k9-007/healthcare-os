"""Telephony — Plivo outbound calls (turn-based IVR) with a zero-infra
simulation fallback. Simulation calls stay in status "ringing" until the
frontend submits a reply via /calls/{id}/simulate-reply.
"""

import asyncio
import logging

from ..config import get_settings
from ..models import CallLog

logger = logging.getLogger("telephony")


def effective_mode() -> str:
    s = get_settings()
    if s.telephony_mode == "plivo" and s.plivo_configured:
        return "plivo"
    if s.telephony_mode == "plivo":
        logger.warning(
            "TELEPHONY_MODE=plivo but PLIVO_AUTH_ID/PLIVO_AUTH_TOKEN/PLIVO_FROM_NUMBER "
            "are incomplete — using simulation"
        )
    return "simulation"


def to_e164(phone: str, default_country: str = "91") -> str:
    """Normalize a stored phone number to the E.164 form Plivo requires.

    Numbers are typed and seeded in local form ("6355351675", "063553 51675"),
    but Plivo needs the country code to route the call at all.
    """
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return "+" + "".join(c for c in digits if c.isdigit())
    digits = digits.lstrip("0")
    if digits.startswith(default_country) and len(digits) > 10:
        return "+" + digits
    return f"+{default_country}{digits}"


def place_call(call: CallLog, patient_phone: str) -> None:
    """Dial (plivo) or arm the simulated call. Mutates call.mode/status/twilio_sid.

    Plivo has no inline-XML option: it always fetches answer_url when the
    patient picks up, so a publicly reachable PUBLIC_BASE_URL is mandatory for
    a real call. The answer XML plays the TTS script and records the reply.
    """
    mode = effective_mode()
    call.mode = mode
    if mode == "simulation":
        call.status = "ringing"  # the browser "answers" it
        return

    s = get_settings()
    if s.public_base_url_is_local:
        call.status = "failed"
        call.error_message = (
            f"PUBLIC_BASE_URL is {s.public_base_url} — Plivo must fetch the answer XML and the "
            "TTS audio over the internet. Start a tunnel (e.g. `cloudflared tunnel --url "
            "http://localhost:8000`) and set PUBLIC_BASE_URL to the https URL it prints."
        )
        logger.error("call %s not placed: %s", call.id, call.error_message)
        raise TelephonyError(call.error_message)

    to_number = to_e164(patient_phone)
    try:
        import plivo

        client = plivo.RestClient(s.plivo_auth_id, s.plivo_auth_token)
        logger.info(
            "dialling call %s: to=%s (stored %r) from=%s",
            call.id, to_number, patient_phone, s.plivo_from_number,
        )
        resp = client.calls.create(
            from_=to_e164(s.plivo_from_number),
            to_=to_number,
            answer_url=f"{s.public_base_url}/plivo/voice/{call.id}",
            answer_method="POST",
            hangup_url=f"{s.public_base_url}/plivo/hangup/{call.id}",
            hangup_method="POST",
        )
        # Plivo answers with a RequestUUID; the CallUUID only exists once the
        # call is set up, so the answer webhook overwrites this with it.
        call.twilio_sid = str(getattr(resp, "request_uuid", "") or "")
        call.status = "ringing"
        logger.info("call %s accepted by Plivo: request_uuid=%s", call.id, call.twilio_sid)
        prepare_dialogue(call.id)
    except Exception as e:  # noqa: BLE001 — carrier failures must surface as failed calls, not 500s
        logger.error("Plivo dial failed for call %s to %s: %s", call.id, to_number, e, exc_info=True)
        call.status = "failed"
        call.error_message = _dial_hint(e)
        raise TelephonyError(call.error_message)


_prep_tasks: dict[int, asyncio.Task] = {}


def prepare_dialogue(call_id: int) -> None:
    """Render the call's dialogue while the phone is still ringing.

    Plivo fetches the answer XML the instant the patient picks up, and it waits
    for our reply — so without this head start the patient's first seconds are
    silence while a translate and a TTS render happen.
    """
    from .voice import agent

    try:
        task = asyncio.get_running_loop().create_task(agent.prepare_call(call_id))
    except RuntimeError:  # no event loop — the answer webhook builds it instead
        return
    _prep_tasks[call_id] = task
    task.add_done_callback(lambda _t, cid=call_id: _prep_tasks.pop(cid, None))


def preparation_task(call_id: int) -> asyncio.Task | None:
    """The still-running dialogue preparation for a call, if any.

    A patient who answers before the prewarm finishes should wait for the
    render already in flight, not pay for a second one from scratch.
    """
    return _prep_tasks.get(call_id)


def _dial_hint(error: Exception) -> str:
    """Turn a Plivo error into something an operator can act on."""
    message = str(error)
    name = type(error).__name__
    hints = {
        "AuthenticationError": "Plivo rejected our credentials — check PLIVO_AUTH_ID and "
                               "PLIVO_AUTH_TOKEN (Console → Account → Keys & Credentials).",
        "ValidationError": "Plivo rejected the request parameters — the from number must be a "
                           "voice-enabled number on this account and both numbers E.164 (+91…).",
        "InvalidRequestError": "Plivo rejected the request — check the destination number and "
                               "that the account has credit and voice permissions for that country.",
    }
    hint = hints.get(name)
    return (f"{hint} ({message})" if hint else f"Plivo error ({name}): {message}")[:500]


class TelephonyError(Exception):
    pass


def data_url(relative_path: str) -> str:
    """Public URL of a file under DATA_DIR, for Plivo to fetch and play."""
    return f"{get_settings().public_base_url}/data/{relative_path}"


def plivo_response(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response>{''.join(elements)}</Response>"
    )


def play_element(audio_url: str) -> str:
    return f"<Play>{_xml_escape(audio_url)}</Play>"


def speak_element(text: str) -> str:
    """Plivo's own voices cover no Indian language, so this is a last resort for
    when Sarvam TTS produced nothing — not a second rendering path."""
    return f"<Speak>{_xml_escape(text)}</Speak>"


def record_element(action_url: str, *, max_length: int = 25, silence_timeout: int = 2) -> str:
    """Record one patient turn, ending it on `silence_timeout` seconds of silence.

    Used only when VOICE_MODE=classic. The streaming path (VOICE_MODE=stream)
    uses Silero VAD over a bidirectional `<Stream>` instead — see `stream_element`.

    `max_length` must stay under 30 s: Sarvam's synchronous STT rejects longer
    audio outright, which turns the whole reply into a lost turn. It is also the
    ceiling on dead air when background noise keeps the silence window from
    firing.

    `silence_timeout` is most of the perceived wait after the patient stops
    talking. 2 s is the floor: Hindi replies pause between phrases, and a
    shorter window cuts them off mid-sentence. `#` still ends a turn instantly.
    """
    return (
        f'<Record action="{_xml_escape(action_url)}" method="POST" fileFormat="wav" '
        f'maxLength="{max_length}" timeout="{silence_timeout}" finishOnKey="#" playBeep="true"/>'
    )


def stream_element(ws_url: str, *, status_callback_url: str = "") -> str:
    """Bidirectional Plivo Audio Stream — Silero VAD lives on our side of the socket.

    `keepCallAlive=true` holds the call open until the WebSocket closes (agent
    hangup). μ-law @ 8 kHz matches telephony and our existing PCM↔ulaw helpers.

    `audioTrack=inbound` is what keeps the agent from hearing itself: the
    outbound track is the TTS we just sent, and mixing it into the endpointer
    makes every line trigger its own barge-in.
    """
    attrs = (
        'bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
        'contentType="audio/x-mulaw;rate=8000"'
    )
    if status_callback_url:
        attrs += (
            f' statusCallbackUrl="{_xml_escape(status_callback_url)}" '
            'statusCallbackMethod="POST"'
        )
    return f"<Stream {attrs}>{_xml_escape(ws_url)}</Stream>"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
