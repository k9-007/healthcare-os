"""Meta-intents — the things people say on a phone call that are not answers.

A patient who picks up an unknown number does not begin by answering clinical
questions. She asks who is calling, why, and what was just said. Before this
layer existed the agent fed all of that to the yes/no classifier, recorded
"Took medicine: Unclear", advanced past the question the patient had not yet
heard, and eventually said goodbye to someone still asking who it was.

Detection is deliberately deterministic and local. These phrasings are short,
high-frequency and highly patterned, the reply set is curated, and a second
network round trip in the middle of a live call is the one cost the turn budget
cannot absorb. `understand()` also returns meta-intents from its existing LLM
call and the two are merged, so unusual phrasing is still caught without an
extra request.
"""

import re
from enum import Enum


class Intent(str, Enum):
    WHO_ARE_YOU = "who_are_you"
    WHY_CALLING = "why_calling"
    REPEAT = "repeat"
    CONFUSED = "confused"
    BUSY = "busy"
    WRONG_PERSON = "wrong_person"
    LANGUAGE = "language"
    STOP = "stop"
    GREETING = "greeting"


# Intents that end the call rather than returning to the script. Ordered: the
# first one present wins, so "don't ever call me again" is never downgraded to
# "call me later".
TERMINAL = (Intent.STOP, Intent.WRONG_PERSON, Intent.BUSY)

# Which intent's reply leads when several arrive together. Identity and purpose
# come before a repeat request: answer who you are, then re-ask.
PRIORITY = (
    Intent.STOP,
    Intent.WRONG_PERSON,
    Intent.BUSY,
    Intent.LANGUAGE,
    Intent.WHO_ARE_YOU,
    Intent.WHY_CALLING,
    Intent.REPEAT,
    Intent.CONFUSED,
    Intent.GREETING,
)

# Hindi speakers on a phone code-switch constantly and STT returns a mix of
# Devanagari and Latin for the same sentence, so every intent carries both.
_PATTERNS: dict[Intent, tuple[str, ...]] = {
    Intent.WHO_ARE_YOU: (
        r"\bwho\s+(is|are)\s+(this|you|calling)\b",
        r"\bwho'?s\s+(this|calling)\b",
        r"\bmay\s+i\s+know\s+who\b",
        r"\byour\s+good\s+name\b",
        r"आप\s*कौन",
        r"कौन\s*बोल\s*रह",
        r"कौन\s*बात\s*कर",
        r"तुम\s*कौन",
        r"\baap\s*kaun\b",
        r"\bkaun\s*bol\b",
        r"\btum\s*kaun\b",
    ),
    Intent.WHY_CALLING: (
        r"\bwhy\s+(are\s+you\s+)?calling\b",
        r"\bwhat\s+is\s+this\s+(call\s+)?(about|regarding)\b",
        r"\bwhat\s+do\s+you\s+want\b",
        r"\bwhat'?s\s+this\s+about\b",
        r"क्यों\s*(फ़ोन|फोन|कॉल)",
        r"(फ़ोन|फोन|कॉल)\s*क्यों",
        r"किस\s*(लिए|बारे)",
        r"कहाँ\s*से\s*बोल",
        r"कहां\s*से\s*बोल",
        r"\bkahan\s*se\b",
        r"\bkyun\s*(call|phone|fon)\b",
        r"\bkis\s*liye\b",
    ),
    Intent.REPEAT: (
        r"\b(can|could)\s+you\s+(please\s+)?(repeat|say\s+(that|it)\s+again)\b",
        r"\bsay\s+(that|it)\s+again\b",
        r"\bcome\s+again\b",
        r"\bi\s+(did\s*n[o']?t|didn'?t|could\s*n[o']?t)\s+(hear|catch|get)\b",
        r"\bpardon\b",
        r"फिर\s*से\s*(बता|बोल|कह|पूछ)",
        r"दुबारा\s*(बता|बोल|कह)",
        r"दोबारा\s*(बता|बोल|कह)",
        r"क्या\s*(बोला|कहा|बताया)",
        r"सुनाई\s*नहीं",
        r"सुना\s*नहीं",
        r"\bphir\s*se\b",
        r"\bdubara\b|\bdobara\b",
        r"\bkya\s*(bola|kaha|bataya)\b",
    ),
    Intent.CONFUSED: (
        r"\bi\s+(don'?t|do\s+not)\s+understand\b",
        r"\bwhat\s+do\s+you\s+mean\b",
        r"\bnot\s+clear\b",
        r"समझ\s*(में\s*)?नहीं\s*आ",
        r"क्या\s*मतलब",
        r"\bsamajh\s*(mein\s*)?nahi\b",
        r"\bkya\s*matlab\b",
    ),
    Intent.BUSY: (
        r"\b(i'?m|i\s+am)\s+busy\b",
        r"\b(call|ring)\s+(me\s+)?(back\s+)?later\b",
        r"\bnot\s+a\s+good\s+time\b",
        r"\bcan'?t\s+talk\b",
        r"\bin\s+a\s+meeting\b",
        r"\bdriving\b",
        r"अभी\s*(व्यस्त|busy|बिजी)",
        r"बाद\s*में\s*(बात|कॉल|फ़ोन|फोन)",
        r"अभी\s*बात\s*नहीं",
        r"\bbaad\s*mein\b",
        r"\babhi\s*busy\b",
    ),
    Intent.WRONG_PERSON: (
        r"\bwrong\s+(number|person)\b",
        r"\bno\s+one\s+by\s+that\s+name\b",
        r"\bthere'?s\s+no\s+\w+\s+here\b",
        r"\byou\s+have\s+the\s+wrong\b",
        r"(ग़लत|गलत)\s*(नंबर|नम्बर)",
        r"यहाँ\s*कोई\s*नहीं",
        r"ऐसा\s*कोई\s*नहीं",
        r"\bgalat\s*(number|nambar)\b",
    ),
    Intent.LANGUAGE: (
        r"\b(speak|talk)\s+in\s+(english|hindi|tamil|telugu|marathi|bengali|kannada)\b",
        r"\b(english|hindi|tamil|telugu|marathi|bengali|kannada)\s+(mein|me)\s+(bol|baat)\b",
        r"(अंग्रेज़ी|अंग्रेजी|इंग्लिश|हिंदी|हिन्दी)\s*में\s*(बोल|बात)",
    ),
    Intent.STOP: (
        r"\b(do\s*n[o']?t|don'?t|never)\s+call\s+(me\s+)?(again|back)\b",
        r"\bstop\s+calling\b",
        r"\bremove\s+my\s+(number|name)\b",
        r"\bunsubscribe\b",
        r"(दोबारा|दुबारा|फिर)\s*(कभी\s*)?(कॉल|फ़ोन|फोन)\s*(मत|नहीं)",
        r"(कॉल|फ़ोन|फोन)\s*(मत\s*कर|नहीं\s*कर)",
        r"परेशान\s*मत",
        # "…call mat karna/karo/kariye" — the verb ending varies, so no \b here.
        r"\b(call|phone|fon)\s*(mat|nahi|nahin)\s*kar",
        r"\b(mat|nahi|nahin)\s*(call|phone|fon)\s*kar",
    ),
    Intent.GREETING: (
        r"^\s*(hello|hallo|hi|hey|yes\s+hello)\b",
        r"^\s*(हैलो|हलो|नमस्ते|नमस्कार)\b",
        r"^\s*(halo|hallo)\b",
    ),
}

_COMPILED: dict[Intent, tuple[re.Pattern, ...]] = {
    intent: tuple(re.compile(p, re.IGNORECASE) for p in pats)
    for intent, pats in _PATTERNS.items()
}


def detect(text: str) -> list[Intent]:
    """Every meta-intent present in one utterance, in reply-priority order.

    Multiple intents are the norm, not the exception: "who is this, and can you
    say that again?" is one breath and has to be answered as one.
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    found = {
        intent
        for intent, patterns in _COMPILED.items()
        if any(p.search(stripped) for p in patterns)
    }
    # A bare "hello" is a greeting; "hello, who is this?" is not.
    if len(found) > 1:
        found.discard(Intent.GREETING)
    return [i for i in PRIORITY if i in found]


def terminal_intent(intents: list[Intent]) -> Intent | None:
    """The intent that should end the call, if any."""
    return next((i for i in TERMINAL if i in intents), None)


def coerce(values: object) -> list[Intent]:
    """Parse the `meta_intents` list the understanding model returns."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    known = {i.value: i for i in Intent}
    out: list[Intent] = []
    for v in values:
        intent = known.get(str(v).strip().lower())
        if intent is not None and intent not in out:
            out.append(intent)
    return [i for i in PRIORITY if i in out]
