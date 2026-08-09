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

from ...models import CallLog, CallTarget, Patient, ScheduledCall
from ..sarvam import SarvamUnavailable, sarvam
from . import prewarm

logger = logging.getLogger("voice.dialogue")

# Fixed lines, in English; localized per patient language and cached on disk.
PHRASES = {
    "reprompt": "Sorry, I did not catch that. Could you please say it again?",
    "no_answer": "I could not hear you. I will call again later. Please take care.",
    "closing": "Thank you. Please take care, and call the hospital if anything feels wrong. Goodbye.",
    "emergency": (
        "This sounds serious. Please contact your doctor or go to the nearest hospital right away. "
        "I am alerting your care team now."
    ),
    "filler": "One moment please.",
    "ack": "Thank you.",
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


def build_steps(patient: Patient, targets: list[CallTarget], kind: str) -> list[Step]:
    meds = [t for t in targets if t.ref_type == "medicine"]
    questions = [t for t in targets if t.ref_type == "followup"]

    # One short sentence: TTS time scales with length, and every second here is
    # a second before the patient can say anything.
    greeting = f"Hello {patient.name}, this is your care assistant from the hospital."
    steps = [Step(key="greeting", text_en=greeting, kind="greeting")]

    for t in meds:
        steps.append(Step(
            key=f"med:{t.ref_id}",
            text_en=f"Have you taken your {t.label}?",
            ref_type="medicine", ref_id=t.ref_id,
        ))
    for t in questions:
        steps.append(Step(
            key=f"q:{t.ref_id}", text_en=t.label,
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
    """Translate the script and the fixed phrases into the patient's language."""
    phrases: dict[str, str] = dict(PHRASES)
    if language.split("-")[0] == "en":
        for s in steps:
            s.text = s.text_en
        return phrases

    texts = [s.text_en for s in steps] + list(PHRASES.values())
    results = await asyncio.gather(
        *(sarvam.translate(t, language, "en-IN") for t in texts), return_exceptions=True
    )
    for step, res in zip(steps, results[: len(steps)]):
        step.text = res if isinstance(res, str) else step.text_en
    for key, res in zip(PHRASES, results[len(steps) :]):
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
    steps = build_steps(patient, targets, call.kind)

    language = language or call.detected_language or patient.preferred_language or "en-IN"
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
