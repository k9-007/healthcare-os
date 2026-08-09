"""Turning prescription shorthand into words a voice can actually say.

Care plans are written the way prescriptions are written — "B12 500mcg",
"Vitamin D3 60000 IU", "Dolo 650mg — after food". Bulbul reads that literally
and gets it wrong: synthesizing that line and transcribing the audio back gives
"B12500 MCG" and "Vitamin D 360000 IU", because a letter glued to a digit, and
two numbers in a row, are ambiguous in any language. In Hindi it is worse —
"बी12" is read out as "बी एक दो", B-one-two.

So the shorthand is expanded here, in English, *before* translation: the
translator then renders the patient's language in words too, instead of leaving
Latin fragments for the voice to guess at. bulbul:v3 dropped the
`enable_preprocessing` normalization that used to hide some of this, which is
why it has to be explicit.
"""

import re

ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = ((10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand"), (100, "hundred"))

# Units as a nurse says them, not as the prescription writes them.
UNITS = {
    "mg": "milligram", "mgs": "milligram", "mcg": "microgram", "ug": "microgram",
    "µg": "microgram", "g": "gram", "gm": "gram", "gms": "gram", "kg": "kilogram",
    "ml": "millilitre", "mls": "millilitre", "l": "litre", "ltr": "litre",
    "iu": "units", "u": "units",
    "unit": "unit", "units": "units", "tsp": "teaspoon", "tbsp": "tablespoon",
    "drop": "drop", "drops": "drops", "puff": "puff", "puffs": "puffs",
}

# Dosing shorthand a patient would never recognize spoken as letters.
ABBREVIATIONS = {
    "od": "once a day", "bd": "twice a day", "bid": "twice a day",
    "tds": "three times a day", "tid": "three times a day", "qid": "four times a day",
    "hs": "at night", "sos": "if needed", "prn": "if needed", "stat": "right now",
    "ac": "before food", "pc": "after food", "po": "by mouth",
    "tab": "tablet", "tabs": "tablets", "cap": "capsule", "caps": "capsule",
    "syp": "syrup", "syr": "syrup", "inj": "injection", "supp": "suppository",
    "sr": "slow release", "xr": "extended release", "er": "extended release",
}

_DASH = re.compile(r"\s*[—–]+\s*")
# "Tab." must not expand into a sentence-ending "tablet."
_ABBREV_DOT = re.compile(r"\b(tabs?|caps?|syp|syr|inj|supp|sr|xr|er)\.", re.I)
# Prescriptions write a course as "x 5 days".
_COURSE = re.compile(r"\bx\s*(?=\d)", re.I)
_NUM_UNIT = re.compile(r"(\d+(?:[.,]\d+)*)\s*(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")\b", re.I)
_LETTER_CODE = re.compile(r"\b([A-Za-z])-?(\d{1,2})\b(?P<strength>\s*\d)?")
_FREQUENCY = re.compile(r"\b([0-2])\s*-\s*([0-2])\s*-\s*([0-2])\b")
_FRACTION = re.compile(r"\b1\s*/\s*([24])\b")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)*\b")
_WORD = re.compile(r"[A-Za-zµ]+")
_TIMES_OF_DAY = ("in the morning", "in the afternoon", "at night")


def number_words(raw: str) -> str:
    """"650" -> "six hundred fifty"; "60,000" -> "sixty thousand"; "2.5" -> "two point five"."""
    raw = raw.replace(",", "")
    if "." in raw:
        whole, _, frac = raw.partition(".")
        digits = " ".join(ONES[int(d)] for d in frac)
        return f"{number_words(whole)} point {digits}"
    if not raw.isdigit():
        return raw
    # Long digit strings are identifiers, not quantities — read them out.
    if len(raw) > 9:
        return " ".join(ONES[int(d)] for d in raw)
    return _int_words(int(raw))


def _int_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return TENS[tens] + (f" {ONES[ones]}" if ones else "")
    for size, name in SCALES:
        if n >= size:
            head, rest = divmod(n, size)
            return f"{_int_words(head)} {name}" + (f" {_int_words(rest)}" if rest else "")
    return ONES[n]


def _frequency(m: re.Match) -> str:
    """"1-0-1" -> "one in the morning and one at night"."""
    doses = [
        f"{ONES[int(count)]} {when}"
        for count, when in zip(m.groups(), _TIMES_OF_DAY)
        if count != "0"
    ]
    if not doses:
        return "as needed"
    return " and ".join([", ".join(doses[:-1]), doses[-1]] if len(doses) > 2 else doses)


def _letter_code(m: re.Match) -> str:
    """"B12" -> "B twelve", "D3 60000" -> "D three, 60000".

    The comma is load-bearing: a vitamin code sitting directly against its
    strength gets read as one quantity. "Vitamin D three sixty thousand units"
    came back from the translator as three lakh sixty thousand — a dose error
    spoken to a patient. A pause between them keeps the two numbers separate.
    """
    code = f"{m.group(1).upper()} {number_words(m.group(2))}"
    return f"{code}, {m.group('strength').strip()}" if m.group("strength") else code


def _shout(m: re.Match) -> str:
    """Brand names arrive shouting: "DOLO" is a word, "ORS" is three letters."""
    word = m.group(0)
    if not word.isupper() or len(word) < 2:
        return word
    if len(word) <= 3 and not any(c in "AEIOU" for c in word[1:]):
        return " ".join(word)
    return word.capitalize()


def speakable(text: str) -> str:
    """Expand clinical shorthand so TTS reads it the way a nurse would say it."""
    if not text:
        return ""
    out = _DASH.sub(", ", " ".join(text.split()))
    out = _ABBREV_DOT.sub(r"\1", out)
    out = _COURSE.sub("for ", out)
    out = _FRACTION.sub(lambda m: "half" if m.group(1) == "2" else "quarter", out)
    out = _FREQUENCY.sub(_frequency, out)
    out = _LETTER_CODE.sub(_letter_code, out)
    # Units before bare numbers, so "500mcg" stays one quantity.
    out = _NUM_UNIT.sub(lambda m: f"{number_words(m.group(1))} {UNITS[m.group(2).lower()]}", out)
    out = _WORD.sub(lambda m: ABBREVIATIONS.get(m.group(0).lower(), m.group(0)), out)
    out = _NUMBER.sub(lambda m: number_words(m.group(0)), out)
    out = _WORD.sub(_shout, out)
    out = re.sub(r"\s*([,.?!])", r"\1", out)
    out = re.sub(r"(,\s*)+,", ",", out)
    return " ".join(out.split())


def medicine_question(label: str) -> str:
    """The question asked about one medicine.

    A CallTarget label carries everything the console needs to show —
    "Metformin 500mg — after food". Spoken, the instructions belong to the
    answer, not the question: a patient asked "have you taken your Metformin
    five hundred milligram, after food?" hears two questions. So the label is
    cut at its instruction separator and only the drug and its strength are
    read out.
    """
    drug = speakable(label.split("—")[0])
    return f"Have you taken your {drug}?" if drug else "Have you taken your medicine?"
