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
        if s.twilio_account_sid.startswith("SK"):
            logger.warning(
                "TWILIO_ACCOUNT_SID holds an API key (SK…), not an Account SID (AC…) — "
                "set TWILIO_ACCOUNT_SID=AC… and put the SK… in TWILIO_API_KEY_SID; using simulation"
            )
        else:
            logger.warning("TELEPHONY_MODE=twilio but credentials missing — using simulation")
    return "simulation"


def to_e164(phone: str, default_country: str = "91") -> str:
    """Normalize a stored phone number to the E.164 form Twilio requires.

    Numbers are typed and seeded in local form ("6355351675", "063553 51675"),
    but Twilio rejects anything that is not E.164 with error 21211, and a trial
    account only connects to a verified number matched as an exact string.
    """
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return "+" + "".join(c for c in digits if c.isdigit())
    digits = digits.lstrip("0")
    if digits.startswith(default_country) and len(digits) > 10:
        return "+" + digits
    return f"+{default_country}{digits}"


def _public_url_reachable() -> bool:
    """Twilio can only fetch webhooks/audio from a public URL (e.g. ngrok)."""
    return not get_settings().public_base_url_is_local


def stream_ws_url(call_id: int) -> str:
    return f"{get_settings().public_ws_base_url}/ws/voice/twilio/{call_id}"


def streaming_twiml(call_id: int) -> str:
    """Hand the call's audio to our WebSocket agent.

    `<Connect><Stream>` is bidirectional and blocks until the socket closes,
    which is what makes a real conversation possible: unlike `<Play>`+`<Record>`,
    the server can hear the patient *while* it is speaking, so it can be
    interrupted.
    """
    settings = get_settings()
    if settings.public_base_url_is_local:
        logger.error(
            "PUBLIC_BASE_URL is %s — Twilio cannot reach a local address. "
            "Run `ngrok http 8000` and set PUBLIC_BASE_URL to the https URL it prints.",
            settings.public_base_url,
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{_xml_escape(stream_ws_url(call_id))}"/></Connect>'
        "</Response>"
    )


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

        # Prefer dedicated API key secret when set; else auth token doubles as secret.
        if s.twilio_api_key_sid:
            secret = s.twilio_api_key_secret or s.twilio_auth_token
            client = Client(s.twilio_api_key_sid, secret, s.twilio_account_sid)
        else:
            client = Client(s.twilio_account_sid, s.twilio_auth_token)
        to_number = to_e164(patient_phone)
        kwargs: dict = {"to": to_number, "from_": s.twilio_from_number}
        if _public_url_reachable():
            kwargs["url"] = f"{s.public_base_url}/twilio/voice/{call.id}"
            if not s.twilio_trial_account:
                # Trial accounts reject every Create-a-Call parameter beyond
                # to/from/url with a 400 ("limited parameter access"), status
                # callbacks included — so the call is placed without them.
                kwargs["method"] = "POST"
                kwargs["status_callback"] = f"{s.public_base_url}/twilio/status/{call.id}"
                kwargs["status_callback_event"] = ["initiated", "ringing", "answered", "completed"]
        else:
            logger.warning(
                "PUBLIC_BASE_URL is local — placing call %s with inline TwiML (no reply capture)", call.id
            )
            kwargs["twiml"] = inline_twiml_for_call(call)
        logger.info(
            "dialling call %s: to=%s (stored %r) from=%s via=%s",
            call.id, to_number, patient_phone, s.twilio_from_number,
            "webhook" if "url" in kwargs else "inline twiml",
        )
        tw_call = client.calls.create(**kwargs)
        call.twilio_sid = tw_call.sid or ""
        call.status = "ringing"
        logger.info("call %s accepted by Twilio: sid=%s", call.id, call.twilio_sid)
    except Exception as e:  # noqa: BLE001 — twilio failures must surface as failed calls, not 500s
        code = getattr(e, "code", None)
        logger.error(
            "Twilio dial failed for call %s to %s: code=%s %s",
            call.id, to_number, code, e, exc_info=True,
        )
        call.status = "failed"
        call.error_message = _dial_hint(code, str(e))
        raise TelephonyError(call.error_message)


def _dial_hint(code: int | None, message: str) -> str:
    """Turn a Twilio error into something an operator can act on."""
    hints = {
        70051: "Twilio rejected our credentials: the Restricted API key has no permissions. "
               "Create a Standard key (Console → Account → API keys & tokens).",
        21211: "Twilio rejected the destination number — it must be in E.164 form (+91…).",
        21219: "The destination is not a verified number. Trial accounts can only call "
               "numbers verified in Console → Phone Numbers → Verified Caller IDs.",
        21210: "The From number is not owned by this Twilio account.",
        21215: "This account is not enabled for calls to that country (Voice Geo Permissions).",
    }
    hint = hints.get(code or 0)
    return f"{hint} (Twilio {code})" if hint else f"Twilio error {code or '?'}: {message}"[:500]


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
