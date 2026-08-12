"""The call script.

The care plan *is* the dialogue: the medicines due in this slot and the
doctor's follow-up questions become the turns, in order. Nothing here asks an
LLM what to say next — that would be slower, less predictable, and clinically
unaccountable. The LLM's job is understanding what the patient said, not
deciding what the nurse asks.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import CallLog, CallTarget, Patient, ScheduledCall
from ..sarvam import SarvamUnavailable, sarvam
from ..spoken import medicine_question, speakable
from . import prewarm

logger = logging.getLogger("voice.dialogue")

# Fixed lines, in English; localized per patient language and cached on disk.
# `{hospital}` is filled in at plan time — never with an invented name.
PHRASES = {
    "reprompt": "Sorry, I did not catch that. Could you say that once more?",
    "no_answer": "I could not hear you. I will call again later. Please take care.",
    "closing": "Thank you. Please take care, and call the hospital if anything feels wrong. Goodbye.",
    "emergency": (
        "This sounds serious. Please contact your doctor or go to the nearest hospital right away. "
        "I am alerting your care team now."
    ),
    "filler": "One moment please.",
    "ack": "Thank you.",

    # Replies to meta-intents — see `voice.meta`. Short on purpose: they are
    # spoken before the pending question is asked again, and the pair has to
    # stay inside one comfortable breath.
    "meta_identity": "I am a care assistant calling from {hospital}, from your doctor's care team.",
    "meta_purpose": (
        "This is just a routine follow-up call to check on your medicines and how you are feeling."
    ),
    "meta_identity_purpose": (
        "I am calling from {hospital}, from your care team. "
        "It is just a routine follow-up about your medicines and how you are feeling."
    ),
    "meta_repeat": "Of course, let me ask again.",
    "meta_confused": "No problem, let me put it simply.",
    "meta_busy": "That is alright. I will call again later. Please take care.",
    "meta_wrong_person": "Sorry about that, I think I have the wrong number. Apologies for the trouble.",
    "meta_stop": "Understood. I will pass that on to the care team. Thank you.",
    "meta_language": "Of course.",
    "meta_greeting": "Hello.",

    # Acknowledgements. The nurse reacts to what was just said before moving
    # on; without these the call is a questionnaire being read aloud.
    "ack_med_yes": "Good, that is great.",
    "ack_med_yes_alt": "Very good.",
    "ack_med_no": "Alright, no problem.",
    "ack_med_no_alt": "Okay, I have noted that.",
    "ack_symptom": "Alright, I understand.",
    "ack_neutral": "Okay.",
    "ack_wellbeing_good": "That is good to hear.",
}

# Lines written directly in the language instead of translated into it.
#
# `sarvam-translate` is faithful and stiff: "Sorry, I did not catch that"
# becomes "माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप कृपया इसे फिर से कह सकती हैं?" — correct,
# and nothing like what a nurse says on the phone. These are the lines the
# patient hears most often, so they are authored rather than translated.
# Anything missing here still falls back to translation.
NATIVE_PHRASES: dict[str, dict[str, str]] = {
    "hi": {
        "reprompt": "माफ़ कीजिए, मैं ठीक से सुन नहीं पाई। एक बार फिर बताइएगा?",
        "no_answer": "लगता है आवाज़ नहीं आ रही। मैं बाद में फिर कॉल करती हूँ। अपना ध्यान रखिएगा।",
        "closing": "जी शुक्रिया। अपना ध्यान रखिएगा, और कुछ भी परेशानी लगे तो अस्पताल ज़रूर फ़ोन कीजिए। नमस्ते।",
        "emergency": (
            "ये गंभीर लग रहा है। कृपया तुरंत अपने डॉक्टर से बात कीजिए या नज़दीकी अस्पताल जाइए। "
            "मैं आपकी केयर टीम को अभी बता रही हूँ।"
        ),
        "filler": "जी, एक मिनट।",
        "ack": "जी शुक्रिया।",

        "meta_identity": "जी, मैं {hospital} से आपकी केयर टीम की सहायिका बोल रही हूँ।",
        "meta_purpose": "ये बस एक रूटीन फ़ॉलो-अप कॉल है, आपकी दवा और तबीयत का हाल पूछने के लिए।",
        "meta_identity_purpose": (
            "जी, मैं {hospital} से आपकी केयर टीम से बोल रही हूँ। "
            "बस आपकी दवा और तबीयत का हाल पूछना था।"
        ),
        "meta_repeat": "जी ज़रूर, मैं फिर से पूछती हूँ।",
        "meta_confused": "कोई बात नहीं, मैं आसान शब्दों में पूछती हूँ।",
        "meta_busy": "जी, कोई बात नहीं। मैं बाद में फिर कॉल कर लूँगी। अपना ध्यान रखिएगा।",
        "meta_wrong_person": "ओह, माफ़ कीजिए। लगता है नंबर ग़लत लग गया। परेशानी के लिए खेद है।",
        "meta_stop": "जी, मैं समझ गई। मैं आपकी बात केयर टीम तक पहुँचा दूँगी। धन्यवाद।",
        "meta_language": "जी ज़रूर।",
        "meta_greeting": "जी नमस्ते।",

        "ack_med_yes": "बढ़िया।",
        "ack_med_yes_alt": "अच्छा, बहुत अच्छा।",
        "ack_med_no": "ठीक है, कोई बात नहीं।",
        "ack_med_no_alt": "अच्छा, मैंने नोट कर लिया।",
        "ack_symptom": "अच्छा, समझ गई।",
        "ack_neutral": "जी, ठीक है।",
        "ack_wellbeing_good": "सुनकर अच्छा लगा।",
    },
}

# Spoken greeting per language. Translated English greetings introduce the
# nurse as a "सहायिका" in a full formal clause; a real call opens shorter.
NATIVE_GREETINGS: dict[str, str] = {
    "hi": "नमस्ते {name} जी, मैं {hospital} से आपकी केयर टीम से बोल रही हूँ।",
}


@dataclass
class Step:
    """One scripted question in the call."""

    key: str  # greeting | med:<id> | q:<id> | closing
    text_en: str
    ref_type: str = ""  # medicine | followup
    ref_id: int | None = None
    kind: str = "question"  # greeting | question | closing
    text: str = ""  # localized; falls back to text_en

    @property
    def spoken(self) -> str:
        return self.text or self.text_en


@dataclass
class Plan:
    language: str
    steps: list[Step] = field(default_factory=list)
    phrases: dict[str, str] = field(default_factory=dict)

    def phrase(self, key: str) -> str:
        return self.phrases.get(key) or PHRASES[key]

    def questions(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "question"]


def hospital_name(language: str) -> str:
    """What the nurse calls the hospital she is ringing from.

    Only a configured name is ever spoken. With none set she says "the
    hospital", which is true; inventing an organization on a clinical call is
    not an option.
    """
    configured = get_settings().hospital_name.strip()
    if configured:
        return configured
    return "अस्पताल" if language.split("-")[0] == "hi" else "the hospital"


def build_steps(
    patient: Patient, targets: list[CallTarget], kind: str, language: str = "en-IN"
) -> list[Step]:
    meds = [t for t in targets if t.ref_type == "medicine"]
    questions = [t for t in targets if t.ref_type == "followup"]

    # One short sentence: TTS time scales with length, and every second here is
    # a second before the patient can say anything.
    name = speakable(patient.name)
    greeting = f"Hello {name}, this is your care assistant from the hospital."
    step = Step(key="greeting", text_en=greeting, kind="greeting")
    native = NATIVE_GREETINGS.get(language.split("-")[0])
    if native:
        step.text = native.format(name=name, hospital=hospital_name(language))
    steps = [step]

    # Labels are prescription shorthand ("Dolo 650mg — after food"); everything
    # spoken goes through `spoken` first so the voice says words, not fragments.
    for t in meds:
        steps.append(Step(
            key=f"med:{t.ref_id}",
            text_en=medicine_question(t.label),
            ref_type="medicine", ref_id=t.ref_id,
        ))
    for t in questions:
        steps.append(Step(
            key=f"q:{t.ref_id}", text_en=speakable(t.label),
            ref_type="followup", ref_id=t.ref_id,
        ))

    # Every call ends by asking about the patient's condition — this is where
    # red flags usually surface, and it is the only turn on a bare check-in.
    steps.append(Step(
        key="wellbeing",
        text_en="And how are you feeling right now? Any pain or new problems?",
        ref_type="wellbeing",
    ))
    if kind == "callback":
        steps = [steps[0], steps[-1]]
    return steps


async def localize(steps: list[Step], language: str) -> dict[str, str]:
    """Render the script and the fixed phrases in the patient's language.

    Authored lines win over translated ones; only what is left goes to the
    translator. Steps that already carry a native rendering (the greeting) are
    left alone.
    """
    hospital = hospital_name(language)
    native = NATIVE_PHRASES.get(language.split("-")[0], {})
    phrases: dict[str, str] = {
        key: (native.get(key) or english).format(hospital=hospital)
        if "{hospital}" in (native.get(key) or english) else (native.get(key) or english)
        for key, english in PHRASES.items()
    }
    if language.split("-")[0] == "en":
        for s in steps:
            if not s.text:
                s.text = s.text_en
        return phrases

    pending_steps = [s for s in steps if not s.text]
    pending_keys = [k for k in PHRASES if k not in native]
    texts = [s.text_en for s in pending_steps] + [phrases[k] for k in pending_keys]
    results = await asyncio.gather(
        *(sarvam.translate(t, language, "en-IN") for t in texts), return_exceptions=True
    )
    for step, res in zip(pending_steps, results[: len(pending_steps)]):
        step.text = res if isinstance(res, str) else step.text_en
    for key, res in zip(pending_keys, results[len(pending_steps) :]):
        if isinstance(res, str):
            phrases[key] = res
        else:
            logger.warning("translation failed for phrase %r: %s", key, res)
    return phrases


async def build_plan(
    db: Session, call: CallLog, *, do_prewarm: bool = True, language: str | None = None
) -> Plan:
    """Assemble and localize the dialogue for a call, pre-rendering its audio."""
    patient = call.patient
    sc = db.scalar(select(ScheduledCall).where(ScheduledCall.call_log_id == call.id))
    targets = list(sc.targets) if sc else []

    language = language or call.detected_language or patient.preferred_language or "en-IN"
    steps = build_steps(patient, targets, call.kind, language)
    try:
        phrases = await localize(steps, language)
    except SarvamUnavailable as e:
        logger.warning("localization unavailable (%s); speaking English", e)
        for s in steps:
            s.text = s.text_en
        phrases = dict(PHRASES)

    plan = Plan(language=language, steps=steps, phrases=phrases)
    if do_prewarm:
        await prewarm.prewarm([s.spoken for s in steps] + list(phrases.values()), language)
    return plan


# Plans are built (and their audio rendered) while Twilio is still dialling, so
# the greeting can start the instant the media stream connects.
_prepared: dict[int, Plan] = {}


def stash_plan(call_id: int, plan: Plan) -> None:
    _prepared[call_id] = plan


def take_plan(call_id: int) -> Plan | None:
    return _prepared.pop(call_id, None)
