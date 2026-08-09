"""Checks that prescription shorthand comes out speakable — run: python test_spoken.py

The dose cases matter clinically: "Vitamin D3 60000 IU" left unseparated was
translated into three lakh sixty thousand units, a dose error spoken aloud.
"""

from app.services.spoken import medicine_question, number_words, speakable

NUMBERS = [
    ("5", "five"),
    ("12", "twelve"),
    ("650", "six hundred fifty"),
    ("60000", "sixty thousand"),
    ("60,000", "sixty thousand"),
    ("2.5", "two point five"),
]

PHRASES = [
    ("Dolo 650mg — after food", "Dolo six hundred fifty milligram, after food"),
    ("B12 500mcg", "B twelve, five hundred microgram"),
    ("Vitamin D3 60000 IU", "Vitamin D three, sixty thousand units"),
    ("Tab. Pan-D 40mg 1-0-1", "tablet Pan-D forty milligram one in the morning and one at night"),
    ("Shelcal 500 1/2 tab OD", "Shelcal five hundred half tablet once a day"),
    ("Augmentin 625 BD x 5 days", "Augmentin six hundred twenty five twice a day for five days"),
    ("ORS sachet in 1 L water", "O R S sachet in one litre water"),
    ("Insulin 10 U SOS", "Insulin ten units if needed"),
    # Plain prose must survive untouched.
    ("Are you able to walk without help?", "Are you able to walk without help?"),
]

QUESTIONS = [
    ("DOLO 650mg — if fever", "Have you taken your Dolo six hundred fifty milligram?"),
    ("B12 500mcg — once a week", "Have you taken your B twelve, five hundred microgram?"),
]

failures = 0
for raw, want in NUMBERS:
    got = number_words(raw)
    failures += got != want
    print(f"{'ok  ' if got == want else 'FAIL'} {raw!r} -> {got!r}" + ("" if got == want else f" (want {want!r})"))

for raw, want in PHRASES + QUESTIONS:
    got = speakable(raw) if (raw, want) in PHRASES else medicine_question(raw)
    failures += got != want
    print(f"{'ok  ' if got == want else 'FAIL'} {raw!r} -> {got!r}" + ("" if got == want else f" (want {want!r})"))

# No digit may reach the voice: bulbul:v3 reads them literally and merges
# neighbours ("B12 500mcg" became "B12500 MCG").
for raw, _ in PHRASES + QUESTIONS:
    out = speakable(raw)
    if any(c.isdigit() for c in out):
        failures += 1
        print(f"FAIL digits left in {out!r}")

print(f"\n{failures} failure(s)")
raise SystemExit(1 if failures else 0)
