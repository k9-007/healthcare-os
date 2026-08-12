"""Meta-intent handling — run: python test_meta_intents.py

Covers the defect from the real call: the patient asked who was calling and to
have the question repeated, and the agent recorded "Took medicine: Unclear",
skipped the question she had not heard, and eventually said goodbye.

Nothing here touches the database or the network: persistence and speech are
stubbed, so a failing assertion is about routing, not about Sarvam being up.
"""

import asyncio

from app.services.voice import agent as agent_mod
from app.services.voice import dialogue
from app.services.voice.agent import VoiceAgent
from app.services.voice.dialogue import Plan, Step
from app.services.voice.meta import Intent, detect, terminal_intent
from app.services.voice.understand import Understanding
from app.services.voice.vad import Stats

# The transcript Sarvam returned on the real call, verbatim.
SCREENSHOT = (
    "क्या आप फिर से मेरे को बता सकते हैं कि आपने क्या बोला है "
    "हाँ आप कौन बोल रहे हो कहाँ से बोल रही हो"
)

HI_PHRASES = asyncio.run(dialogue.localize([], "hi-IN"))


def make_plan() -> Plan:
    return Plan(
        language="hi-IN",
        steps=[
            Step(
                key="med:5", text_en="Have you taken your B twelve, one?",
                ref_type="medicine", ref_id=5,
                text="क्या आपने अपना बी बारह, एक ले लिया है?",
            ),
            Step(
                key="wellbeing", text_en="And how are you feeling right now?",
                ref_type="wellbeing", text="और अभी आप कैसा महसूस कर रही हैं?",
            ),
        ],
        phrases=dict(HI_PHRASES),
    )


class Recorder:
    """Stands in for `voice.persist`; remembers what would have been written."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, str]] = []
        self.interruptions: list[str] = []

    def add_turn(self, db, call, **kw):
        return None

    def record_answer(self, db, call, step, u):
        self.answers.append((step.key, u.yes_no))
        return 0, None

    def note_interruption(self, db, call, intent, transcript):
        self.interruptions.append(intent)

    def save_audio(self, *a, **kw):
        return ""


class FakeDB:
    def commit(self):
        return None

    def rollback(self):
        return None


class FakeTransport:
    """Playback is instantaneous here — the paths that end a call wait on it."""

    def __init__(self) -> None:
        self.playback_done = asyncio.Event()
        self.playback_done.set()


class Agent(VoiceAgent):
    """A VoiceAgent with speech, STT, DB and understanding replaced."""

    def __init__(self, understanding: Understanding, transcript: str) -> None:
        self.call_id = 1
        self.transport = FakeTransport()
        self.state = agent_mod.State.THINKING
        self.settings = agent_mod.get_settings()
        self.db = FakeDB()
        self.call = object()
        self.plan = make_plan()
        self.step_index = 0
        self.turn_index = 0
        self.reprompts = 0
        self.no_answer_streak = 0
        self.escalated = False
        self.meta_replies = 0
        self._ack_turn = 0
        self._turn_task = None
        self._barge_in_pending = False
        self._playing = False
        self.spoken: list[tuple[str, str, str]] = []
        self._understanding = understanding
        self._transcript = transcript
        self.closed = False

    async def _transcribe(self, pcm):
        return self._transcript, "hi-IN", 0.9

    async def _speak(self, text, *, step_key="", record=True, lead=""):
        self.spoken.append((step_key, lead, text))

    async def _close(self, *, lead=""):
        self.closed = True
        self.spoken.append(("closing", lead, self.plan.phrase("closing")))


def run_turn(understanding: Understanding, transcript: str) -> tuple[Agent, Recorder]:
    recorder = Recorder()
    original_persist, original_understand = agent_mod.persist, agent_mod.understand

    async def fake_understand(question, text, *, expects_yes_no):
        u = understanding
        merged = set(u.meta_intents) | set(detect(text))
        u.meta_intents = [i for i in agent_mod.meta.PRIORITY if i in merged]
        return u

    agent_mod.persist = recorder
    agent_mod.understand = fake_understand
    try:
        a = Agent(understanding, transcript)
        asyncio.run(a._handle_utterance(b"\x00" * 8000, False, Stats()))
        return a, recorder
    finally:
        agent_mod.persist = original_persist
        agent_mod.understand = original_understand


# ---------- detection ----------

def test_detects_the_screenshot_transcript():
    intents = detect(SCREENSHOT)
    assert Intent.REPEAT in intents, intents
    assert Intent.WHO_ARE_YOU in intents, intents
    assert Intent.WHY_CALLING in intents, intents
    assert terminal_intent(intents) is None
    print("ok  screenshot transcript detected as repeat + identity + purpose")


def test_detects_each_intent():
    cases = {
        "आप कौन बोल रहे हो": Intent.WHO_ARE_YOU,
        "who is this?": Intent.WHO_ARE_YOU,
        "किस लिए फ़ोन किया": Intent.WHY_CALLING,
        "फिर से बताइए": Intent.REPEAT,
        "समझ में नहीं आया": Intent.CONFUSED,
        "abhi busy hoon baad mein call karna": Intent.BUSY,
        "wrong number": Intent.WRONG_PERSON,
        "ग़लत नंबर लग गया": Intent.WRONG_PERSON,
        "मुझे दोबारा कॉल मत करना": Intent.STOP,
        "stop calling me": Intent.STOP,
        "hello": Intent.GREETING,
    }
    for text, expected in cases.items():
        assert expected in detect(text), f"{text!r} -> {detect(text)}"
    print("ok  every meta-intent detected in Hindi, Hinglish and English")


def test_real_answers_are_not_meta():
    for text in (
        "हाँ मैंने दवा ले ली है",
        "नहीं, भूल गई",
        "मैं ठीक हूँ कोई तकलीफ़ नहीं है।",
        "मुझे बुखार है",
    ):
        assert detect(text) == [], f"{text!r} -> {detect(text)}"
    print("ok  genuine clinical answers are not mistaken for meta-intents")


# ---------- routing ----------

def test_screenshot_turn_holds_the_step_and_records_nothing():
    """The exact defect: no bogus result, no skipped question, a real answer."""
    a, rec = run_turn(
        Understanding(answered=True, yes_no="unclear", answer="asked who is calling"),
        SCREENSHOT,
    )
    assert rec.answers == [], f"a question was recorded as answered: {rec.answers}"
    assert a.step_index == 0, "the pending medicine question must not be skipped"
    assert len(a.spoken) == 1
    step_key, lead, text = a.spoken[0]
    assert step_key == "clarify", step_key
    assert lead == HI_PHRASES["meta_identity_purpose"], lead
    assert text == a.plan.steps[0].spoken, "the pending question is asked again"
    assert not a.closed
    print("ok  screenshot turn: identity answered, question re-asked, nothing recorded")


def test_repeat_only_re_asks():
    a, rec = run_turn(
        Understanding(answered=False, yes_no="unclear"), "क्या बोला आपने, फिर से बताइए"
    )
    assert rec.answers == []
    assert a.step_index == 0
    assert a.spoken[0][1] == HI_PHRASES["meta_repeat"]
    assert a.spoken[0][2] == a.plan.steps[0].spoken
    print("ok  repeat request re-asks the same question, verbatim")


def test_answer_plus_question_records_and_moves_on():
    """Case 4: capture the answer, reply to the question, do not re-ask."""
    a, rec = run_turn(
        Understanding(answered=True, yes_no="yes", answer="took it"),
        "हाँ ले ली है, वैसे आप कौन बोल रहे हो",
    )
    assert rec.answers == [("med:5", "yes")], rec.answers
    assert a.step_index == 1, "a real answer must advance the script"
    step_key, lead, text = a.spoken[0]
    assert step_key == "wellbeing", step_key
    assert lead == HI_PHRASES["meta_identity"], lead
    print("ok  answer + question: answer recorded, question answered, no repeat")


def test_plain_answer_gets_an_acknowledgement():
    a, rec = run_turn(Understanding(answered=True, yes_no="yes"), "हाँ मैंने ले ली है")
    assert rec.answers == [("med:5", "yes")]
    assert a.step_index == 1
    step_key, lead, _ = a.spoken[0]
    assert step_key == "wellbeing"
    assert lead == HI_PHRASES["ack_med_yes"], lead
    print("ok  a plain answer is acknowledged before the next question")


def test_missed_dose_acknowledgement_is_not_congratulatory():
    a, _ = run_turn(Understanding(answered=True, yes_no="no"), "नहीं, भूल गई")
    assert a.spoken[0][1] == HI_PHRASES["ack_med_no"]
    print("ok  a missed dose is acknowledged without judgement")


def test_acknowledgements_alternate():
    leads = []
    for _ in range(2):
        a, _ = run_turn(Understanding(answered=True, yes_no="yes"), "हाँ ले ली")
        a._ack_turn = len(leads)  # continue the rotation across turns
        leads.append(a._acknowledgement(a.plan.steps[0], Understanding(yes_no="yes")))
    assert leads[0] != leads[1], leads
    print("ok  repeated medicine questions do not get an identical reaction")


def test_wrong_person_discloses_nothing_and_ends():
    a, rec = run_turn(Understanding(answered=False), "ग़लत नंबर है, यहाँ कोई नहीं")
    assert rec.answers == []
    assert rec.interruptions == ["wrong_person"]
    assert a.state is agent_mod.State.DONE
    spoken = " ".join(t for _, _, t in a.spoken)
    assert HI_PHRASES["meta_wrong_person"] in spoken
    for leak in ("बी बारह", "दवा", "तबीयत"):
        assert leak not in spoken, f"leaked {leak!r} to a stranger: {spoken}"
    print("ok  wrong number: apologises, records nothing clinical, discloses nothing")


def test_stop_is_honoured_and_flagged():
    a, rec = run_turn(Understanding(answered=False), "मुझे दोबारा कॉल मत करना")
    assert rec.interruptions == ["stop"]
    assert rec.answers == []
    assert a.state is agent_mod.State.DONE
    print("ok  do-not-call honoured immediately and raised to the care team")


def test_busy_ends_politely():
    a, rec = run_turn(Understanding(answered=False), "abhi busy hoon, baad mein call karna")
    assert rec.interruptions == ["busy"]
    assert a.state is agent_mod.State.DONE
    print("ok  busy patient: call ends politely and is flagged for a retry")


def test_clarification_loop_is_bounded():
    a, rec = run_turn(Understanding(answered=False), "आप कौन बोल रहे हो")
    for _ in range(agent_mod.MAX_META_REPLIES + 2):
        asyncio.run(a._on_meta([Intent.WHO_ARE_YOU], a.plan.steps[0]))
    assert a.step_index == 0
    assert any(key == "reprompt" for key, _, _ in a.spoken), a.spoken
    print("ok  endless clarification falls back to the reprompt ladder")


def test_emergency_path_still_wins():
    a, rec = run_turn(
        Understanding(answered=True, urgency="high", answer="chest pain", symptoms=["pain"]),
        "बहुत तेज़ दर्द हो रहा है सीने में, आप कौन हो",
    )
    assert a.state is agent_mod.State.DONE
    assert any(key == "emergency" for key, _, _ in a.spoken), a.spoken
    print("ok  a red flag still beats every meta-intent")


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
