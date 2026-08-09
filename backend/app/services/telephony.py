"""Telephony — Plivo outbound calls (turn-based IVR) with a zero-infra
simulation fallback. Simulation calls stay in status "ringing" until the
frontend submits a reply via /calls/{id}/simulate-reply.
"""

import logging

from ..config import get_settings
from ..models import CallLog

logger = logging.getLogger("telephony")


def effective_mode() -> str:
    s = get_settings()
    if s.telephony_mode == "plivo" and s.plivo_configured:
        if not _public_url_reachable():
            logger.warning(
                "TELEPHONY_MODE=plivo but PUBLIC_BASE_URL is local — "
                "Plivo needs a public answer/recording URL; using simulation"
            )
            return "simulation"
        return "plivo"
    if s.telephony_mode == "plivo":
        logger.warning("TELEPHONY_MODE=plivo but credentials missing — using simulation")
    # Legacy alias: treat "twilio" config as a request for real calls → plivo
    if s.telephony_mode == "twilio":
        logger.warning("TELEPHONY_MODE=twilio is deprecated; use TELEPHONY_MODE=plivo")
        if s.plivo_configured and _public_url_reachable():
            return "plivo"
    return "simulation"


def _public_url_reachable() -> bool:
    """Plivo can only fetch answer XML / audio from a public HTTPS URL (e.g. ngrok)."""
    base = get_settings().public_base_url
    return not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def normalize_e164(phone: str, default_country_code: str = "91") -> str:
    """Normalize Indian / E.164 phone numbers for Plivo (expects +XXXXXXXX)."""
    raw = (phone or "").strip().replace(" ", "").replace("-", "")
    if not raw:
        return raw
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if raw.startswith("+"):
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10 and default_country_code == "91":
        return f"+91{digits}"
    if digits.startswith(default_country_code) and len(digits) >= 11:
        return f"+{digits}"
    return f"+{digits}" if digits else raw


def place_call(call: CallLog, patient_phone: str) -> None:
    """Dial (plivo) or arm the simulated call. Mutates call.mode/status/twilio_sid
    (twilio_sid column stores the Plivo CallUUID for historical schema compat).
    """
    mode = effective_mode()
    call.mode = mode
    if mode == "simulation":
        call.status = "ringing"  # the browser "answers" it
        return

    s = get_settings()
    to_number = normalize_e164(patient_phone)
    try:
        import plivo

        client = plivo.RestClient(s.plivo_auth_id, s.plivo_auth_token)
        answer_url = f"{s.public_base_url}/plivo/voice/{call.id}"
        hangup_url = f"{s.public_base_url}/plivo/hangup/{call.id}"
        response = client.calls.create(
            from_=s.plivo_from_number,
            to_=to_number,
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST",
        )
        # SDK returns request_uuid / call_uuid depending on version
        call_uuid = (
            getattr(response, "call_uuid", None)
            or getattr(response, "request_uuid", None)
            or ""
        )
        if isinstance(response, dict):
            call_uuid = response.get("call_uuid") or response.get("request_uuid") or call_uuid
        call.twilio_sid = str(call_uuid or "")
        call.status = "ringing"
        logger.info("Plivo call placed id=%s uuid=%s to=%s", call.id, call.twilio_sid, to_number)
    except Exception as e:  # noqa: BLE001 — telephony failures must surface as failed calls, not 500s
        logger.error("Plivo call failed: %s", e)
        call.status = "failed"
        raise TelephonyError(str(e))


class TelephonyError(Exception):
    pass


def _speak_language(call: CallLog) -> str:
    """BCP-47-ish language for Plivo <Speak>; falls back to en-US (Plivo TTS langs are limited)."""
    lang = (call.patient.preferred_language if call.patient else "") or "en-IN"
    # Prefer playing Sarvam TTS audio; Speak is fallback only — map Indic → en-US.
    if lang.startswith("en"):
        return "en-US" if lang in ("en-IN", "en") else lang
    return "en-US"


def plivo_xml_for_call(call: CallLog) -> str:
    """Plivo XML: play the Sarvam TTS script, then record the patient's reply."""
    s = get_settings()
    audio_url = f"{s.public_base_url}/data/{call.tts_audio_path}" if call.tts_audio_path else ""
    say_fallback = _xml_escape(call.script_text_translated or call.script_text or "Hello from your care team.")
    # Plivo <Play> requires HTTPS for audio URLs.
    play_or_speak = (
        f"<Play>{_xml_escape(audio_url)}</Play>" if audio_url
        else f'<Speak language="{_speak_language(call)}">{say_fallback}</Speak>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  {play_or_speak}
  <Record action="{s.public_base_url}/plivo/recording/{call.id}"
          method="POST"
          maxLength="60"
          timeout="5"
          playBeep="true"
          fileFormat="wav"
          finishOnKey="#"/>
  <Speak>We did not receive a reply. Take care, goodbye.</Speak>
</Response>"""


# Back-compat alias used by older imports / docs
def twiml_for_call(call: CallLog) -> str:
    return plivo_xml_for_call(call)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
