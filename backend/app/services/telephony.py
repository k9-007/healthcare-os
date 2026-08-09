"""Telephony — Twilio outbound calls (turn-based IVR) with a zero-infra
simulation fallback. Simulation calls stay in status "ringing" until the
frontend submits a reply via /calls/{id}/simulate-reply.
"""

import logging

from ..config import get_settings
from ..models import CallLog

logger = logging.getLogger("telephony")


def effective_mode() -> str:
    s = get_settings()
    if s.telephony_mode == "twilio" and s.twilio_configured:
        return "twilio"
    if s.telephony_mode == "twilio":
        logger.warning("TELEPHONY_MODE=twilio but credentials missing — using simulation")
    return "simulation"


def _public_url_reachable() -> bool:
    """Twilio can only fetch webhooks/audio from a public URL (e.g. ngrok)."""
    base = get_settings().public_base_url
    return not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def place_call(call: CallLog, patient_phone: str) -> None:
    """Dial (twilio) or arm the simulated call. Mutates call.mode/status/twilio_sid.

    With a public PUBLIC_BASE_URL, the full turn-based IVR runs (webhook TwiML:
    play TTS + record reply → STT). Without one (no ngrok yet), the call is
    still placed for real using inline TwiML <Say>, so the patient hears the
    script — the spoken reply just can't be captured until a tunnel is set up.
    """
    mode = effective_mode()
    call.mode = mode
    if mode == "simulation":
        call.status = "ringing"  # the browser "answers" it
        return

    s = get_settings()
    try:
        from twilio.rest import Client

        # Prefer API key auth (SK... + secret) when provided, else classic auth token.
        if s.twilio_api_key_sid and s.twilio_api_key_secret:
            client = Client(s.twilio_api_key_sid, s.twilio_api_key_secret, s.twilio_account_sid)
        else:
            client = Client(s.twilio_account_sid, s.twilio_auth_token)
        kwargs: dict = {"to": patient_phone, "from_": s.twilio_from_number}
        if _public_url_reachable():
            kwargs["url"] = f"{s.public_base_url}/twilio/voice/{call.id}"
            kwargs["status_callback"] = f"{s.public_base_url}/twilio/status/{call.id}"
            kwargs["status_callback_event"] = ["initiated", "ringing", "answered", "completed"]
        else:
            logger.warning(
                "PUBLIC_BASE_URL is local — placing call %s with inline TwiML (no reply capture)", call.id
            )
            kwargs["twiml"] = inline_twiml_for_call(call)
        tw_call = client.calls.create(**kwargs)
        call.twilio_sid = tw_call.sid or ""
        call.status = "ringing"
    except Exception as e:  # noqa: BLE001 — twilio failures must surface as failed calls, not 500s
        logger.error("Twilio call failed: %s", e)
        call.status = "failed"
        raise TelephonyError(str(e))


class TelephonyError(Exception):
    pass


def _say_language(call: CallLog) -> str:
    """BCP-47 language for Twilio <Say>; falls back to en-IN."""
    lang = (call.patient.preferred_language if call.patient else "") or "en-IN"
    return lang


def twiml_for_call(call: CallLog) -> str:
    """TwiML: play the TTS script, then record the patient's reply."""
    s = get_settings()
    audio_url = f"{s.public_base_url}/data/{call.tts_audio_path}" if call.tts_audio_path else ""
    say_fallback = _xml_escape(call.script_text_translated or call.script_text or "Hello from your care team.")
    play_or_say = (
        f"<Play>{_xml_escape(audio_url)}</Play>" if audio_url
        else f'<Say language="{_say_language(call)}">{say_fallback}</Say>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  {play_or_say}
  <Record action="{s.public_base_url}/twilio/recording/{call.id}"
          method="POST" maxLength="60" timeout="5" playBeep="true"/>
  <Say>We did not receive a reply. Take care, goodbye.</Say>
</Response>"""


def inline_twiml_for_call(call: CallLog) -> str:
    """Self-contained TwiML for when no public webhook URL exists:
    speak the localized script twice with a pause — no recording possible."""
    text = _xml_escape(call.script_text_translated or call.script_text or "Hello from your care team.")
    lang = _say_language(call)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say language="{lang}">{text}</Say>'
        '<Pause length="1"/>'
        f'<Say language="{lang}">{text}</Say></Response>'
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
