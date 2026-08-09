"""Patient Care+ core: schedule materialization, per-slot call scripts, and
turning a patient's spoken reply into structured clinical data + events.
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    CallLog, CallTarget, CareEvent, CarePlan, Escalation, ExtractedResponse,
    FollowUpQuestion, Medicine, Patient, ScheduledCall, utcnow,
)
from .sarvam import SarvamUnavailable, sarvam
from .spoken import speakable

logger = logging.getLogger("careplus")

URGENT_KEYWORDS = (
    "chest pain", "छाती में दर्द", "seene mein dard", "breathless", "can't breathe", "cannot breathe",
    "सांस", "saans nahi", "unconscious", "बेहोश", "bleeding", "खून", "khoon", "severe", "बहुत तेज",
    "emergency", "vomiting blood", "मूर्छा", "fainted", "stroke", "heart attack",
)

SYMPTOM_KEYWORDS = {
    "fever": ("fever", "बुखार", "bukhar", "kaichal", "காய்ச்சல்"),
    "pain": ("pain", "दर्द", "dard", "வலி"),
    "nausea": ("nausea", "vomit", "उल्टी", "ulti", "மயக்கம்"),
    "dizziness": ("dizzy", "चक्कर", "chakkar", "தலைச்சுற்றல்"),
    "swelling": ("swelling", "सूजन", "soojan", "வீக்கம்"),
    "weakness": ("weak", "कमजोरी", "kamzori", "பலவீனம்"),
}

NEGATIVE_MED_PATTERNS = (
    "not taken", "didn't take", "did not take", "missed", "forgot", "नहीं ली", "नहीं लिया",
    "नहीं खाई", "bhool", "भूल", "miss ho", "எடுக்கவில்லை", "மறந்து",
)
POSITIVE_MED_PATTERNS = (
    "taken", "took", "yes", "ले ली", "ली है", "लिया", "खा ली", "kha li", "le li", "haan", "हां", "हाँ",
    "ஆம்", "எடுத்துவிட்டேன்", "sapten",
)


def _tzinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _to_utc(local_date: date, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime.combine(local_date, time(h, m), tzinfo=tz)
    return local.astimezone(timezone.utc).replace(tzinfo=None)  # stored naive-UTC


# ---------------- materialization ----------------

def materialize_schedule(db: Session, plan: CarePlan, horizon_hours: int | None = None) -> int:
    """(Re)build future pending ScheduledCall slots from the care-plan config.

    Idempotent via slot_key; already placed/completed history is never touched.
    Returns the number of pending slots after materialization.
    """
    settings = get_settings()
    horizon_hours = horizon_hours or settings.schedule_horizon_hours
    now = utcnow().replace(tzinfo=None)
    horizon_end = now + timedelta(hours=horizon_hours)
    patient = plan.patient
    tz = _tzinfo(patient.timezone)

    # Drop future pending slots — they'll be regenerated from current config.
    stale = db.scalars(
        select(ScheduledCall).where(
            ScheduledCall.care_plan_id == plan.id,
            ScheduledCall.status == "pending",
        )
    ).all()
    for sc in stale:
        db.delete(sc)
    db.flush()

    if plan.status != "active":
        return 0

    created = 0

    # --- medicine slots: group meds sharing the same due time into ONE call ---
    slot_meds: dict[datetime, list[Medicine]] = {}
    day = now.astimezone(timezone.utc).date() - timedelta(days=1)  # start yesterday to catch tz offsets
    days_span = int(horizon_hours / 24) + 3
    for d_off in range(days_span):
        the_date = day + timedelta(days=d_off)
        for med in plan.medicines:
            if med.start_date and the_date < med.start_date:
                continue
            if med.end_date and the_date > med.end_date:
                continue
            for hhmm in [t.strip() for t in med.schedule.split(",") if t.strip()]:
                try:
                    due = _to_utc(the_date, hhmm, tz)
                except (ValueError, IndexError):
                    logger.warning("bad schedule time '%s' on medicine %s", hhmm, med.id)
                    continue
                if now <= due <= horizon_end:
                    slot_meds.setdefault(due, []).append(med)

    for due, meds in sorted(slot_meds.items()):
        slot_key = f"p{patient.id}|{due.strftime('%Y-%m-%dT%H:%M')}|medicine"
        if _slot_exists(db, slot_key):
            continue
        sc = ScheduledCall(
            care_plan_id=plan.id, patient_id=patient.id, kind="medicine",
            due_at=due, slot_key=slot_key, status="pending", next_attempt_at=due,
        )
        db.add(sc)
        db.flush()
        for med in meds:
            label = f"{med.name} {med.dose}".strip() + (f" — {med.instructions}" if med.instructions else "")
            db.add(CallTarget(scheduled_call_id=sc.id, ref_type="medicine", ref_id=med.id, label=label))
        created += 1

    # --- follow-up question slots ---
    for q in plan.questions:
        if get_settings().time_scale_demo:
            # demo: interpret ask_after_days as minutes so judges see it fire fast
            due = now + timedelta(minutes=q.ask_after_days)
        else:
            the_date = plan.start_date + timedelta(days=q.ask_after_days)
            try:
                due = _to_utc(the_date, q.at_time, tz)
            except (ValueError, IndexError):
                logger.warning("bad at_time '%s' on question %s", q.at_time, q.id)
                continue
        if not (now <= due <= horizon_end):
            continue
        slot_key = f"p{patient.id}|q{q.id}|{due.strftime('%Y-%m-%dT%H:%M')}|followup"
        if _slot_exists(db, slot_key):
            continue
        sc = ScheduledCall(
            care_plan_id=plan.id, patient_id=patient.id, kind="followup",
            due_at=due, slot_key=slot_key, status="pending", next_attempt_at=due,
        )
        db.add(sc)
        db.flush()
        db.add(CallTarget(scheduled_call_id=sc.id, ref_type="followup", ref_id=q.id, label=q.text[:400]))
        created += 1

    db.flush()
    return created


def _slot_exists(db: Session, slot_key: str) -> bool:
    return db.scalar(select(ScheduledCall.id).where(ScheduledCall.slot_key == slot_key)) is not None


# ---------------- call window / retries ----------------

def within_call_window(plan: CarePlan | None, patient: Patient, now_utc: datetime) -> bool:
    window = plan.call_window if plan else "08:00-20:00"
    try:
        start_s, end_s = window.split("-")
        tz = _tzinfo(patient.timezone)
        local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz).time()
        start = time(*(int(x) for x in start_s.split(":")))
        end = time(*(int(x) for x in end_s.split(":")))
    except (ValueError, IndexError):
        return True
    if start <= end:
        return start <= local <= end
    return local >= start or local <= end  # overnight window e.g. 20:00-08:00


def next_window_open(plan: CarePlan | None, patient: Patient, now_utc: datetime) -> datetime:
    window = plan.call_window if plan else "08:00-20:00"
    try:
        start_s, _ = window.split("-")
        tz = _tzinfo(patient.timezone)
        local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
        h, m = (int(x) for x in start_s.split(":"))
        candidate = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, IndexError):
        return now_utc + timedelta(hours=1)


def backoff_minutes(plan: CarePlan | None, attempt: int) -> int:
    raw = plan.retry_backoff if plan else "15,60,240"
    try:
        steps = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        steps = [15, 60, 240]
    if not steps:
        steps = [15]
    return steps[min(attempt, len(steps) - 1)]


# ---------------- scripts ----------------

async def build_script(kind: str, targets: list[CallTarget], patient: Patient) -> str:
    """English call script covering exactly the targets of this slot.

    The script is spoken, never read, so the shorthand in the care plan is
    expanded before the model sees it — and the model is told to keep it that
    way, because "650mg" written back into the script is "650mg" mispronounced.
    """
    med_labels = [speakable(t.label) for t in targets if t.ref_type == "medicine"]
    q_labels = [speakable(t.label) for t in targets if t.ref_type == "followup"]

    try:
        prompt = (
            "Write a short, warm voice-call script (max 3 sentences, under 380 characters) an AI nurse "
            f"speaks to patient {speakable(patient.name)}. Plain spoken language, no markdown, no emojis, "
            "no stage directions. This text is read aloud by a text-to-speech voice over a phone line, so "
            "write every number, dose and unit as words exactly as they are given to you (say "
            '"six hundred fifty milligram", never "650mg"), keep medicine names spelled as given, and use '
            "no digits, abbreviations, symbols or parentheses anywhere. "
        )
        if kind == "medicine":
            prompt += (
                f"It is time for these medicines: {'; '.join(med_labels)}. Remind them to take exactly these now, "
                "mention any instructions given, then ask them to say whether they have taken them and how they are feeling."
            )
        elif kind == "followup":
            prompt += (
                f"This is a recovery follow-up. Ask these questions naturally: {'; '.join(q_labels)}. "
                "Ask them to answer by speaking after the tone."
            )
        else:
            prompt += "This is a courtesy check-in call. Ask how they are feeling and whether they took their medicines."
        script = await sarvam.chat(
            [{"role": "system", "content": "You write natural, clinically safe voice scripts for patients."},
             {"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=2048,
        )
        # The model still slips a "650mg" through now and then.
        return speakable(script)
    except SarvamUnavailable:
        return _template_script(kind, med_labels, q_labels, patient)


def _template_script(kind: str, meds: list[str], qs: list[str], patient: Patient) -> str:
    """Fallback script. `meds` and `qs` arrive already expanded for speech."""
    name = speakable(patient.name)
    if kind == "medicine" and meds:
        return (
            f"Hello {name}, this is your care assistant from the hospital. "
            f"It is time to take {', then '.join(meds)}. "
            "After the tone, please tell me if you have taken your medicine and how you are feeling."
        )
    if kind == "followup" and qs:
        return (
            f"Hello {name}, this is your recovery follow-up call. "
            f"Please answer after the tone: {' '.join(qs)}"
        )
    return (
        f"Hello {name}, this is your care assistant checking in. "
        "Please tell me how you are feeling and whether you have taken your medicines today."
    )


async def create_care_call(
    db: Session, patient: Patient, language: str | None = None, with_script: bool = True
) -> CallLog:
    """Build a call and everything the conversation needs to be about something.

    The ad-hoc ScheduledCall gives the call real CallTargets (the medicines due,
    the doctor's questions), which is what both the streaming agent and the
    classic recording flow use to know what to ask and what to extract.

    Script generation, translation and TTS run *before* anything is written.
    SQLite keeps a single writer, so flushing first and awaiting Sarvam
    afterwards held the write lock for the 10-40s those calls take, and every
    concurrent write in that window failed with "database is locked".
    """
    import time as _time
    import uuid as _uuid

    plan = patient.care_plan
    kind = "medicine" if plan and plan.medicines else "manual"
    sc_kind = "medicine" if kind == "manual" else kind
    lang = language or patient.preferred_language

    # Built in memory, attached to the session only after the slow work is done.
    targets = [
        CallTarget(
            ref_type="medicine", ref_id=med.id,
            label=f"{med.name} {med.dose}".strip()
                  + (f" — {med.instructions}" if med.instructions else ""),
        )
        for med in (plan.medicines if plan else [])
    ]

    # The streaming agent speaks from its own dialogue plan, so the one-shot
    # script and its audio are only worth the ~20s of Sarvam calls when the
    # classic play-then-record flow will actually play them.
    script_en = script_local = audio_path = ""
    if with_script:
        script_en = await build_script(sc_kind, targets, patient)
        script_local = await localize_script(script_en, lang)
        audio_path = await synthesize(script_local, lang)

    sc = ScheduledCall(
        care_plan_id=plan.id if plan else None,
        patient_id=patient.id, kind=sc_kind,
        due_at=utcnow().replace(tzinfo=None),
        slot_key=f"p{patient.id}|manual|{int(_time.time())}|{_uuid.uuid4().hex[:6]}",
        status="pending",
    )
    db.add(sc)
    db.flush()
    for target in targets:
        target.scheduled_call_id = sc.id
        db.add(target)

    call = CallLog(
        patient_id=patient.id, direction="outbound", kind=sc_kind,
        script_text=script_en, script_text_translated=script_local,
        tts_audio_path=audio_path, status="queued",
        # The language this call is conducted in — an override here must not
        # rewrite the patient's standing preference.
        detected_language=lang,
    )
    db.add(call)
    db.flush()
    sc.call_log_id = call.id
    sc.status = "placed"
    sc.attempts = 1
    db.flush()
    return call


async def localize_script(script_en: str, language: str) -> str:
    if language.split("-")[0] == "en":
        return script_en
    try:
        return await sarvam.translate(script_en, language, "en-IN")
    except SarvamUnavailable:
        return script_en  # fall back to English audio/text rather than failing the call


async def synthesize(script_localized: str, language: str) -> str:
    """Returns audio path relative to DATA_DIR, or '' if TTS unavailable."""
    try:
        return await sarvam.tts_to_file(script_localized, language)
    except (SarvamUnavailable, Exception) as e:  # noqa: BLE001 — audio must never break call creation
        logger.warning("TTS failed (%s); call proceeds without audio", e)
        return ""


# ---------------- reply understanding ----------------

async def process_reply(
    db: Session,
    call: CallLog,
    audio_bytes: bytes | None = None,
    filename: str = "reply.wav",
    transcript_text: str | None = None,
) -> tuple[Escalation | None, int]:
    """STT → text-analytics → ExtractedResponses + CareEvents (+ Escalation).

    Accepts either recorded audio or a typed transcript (text simulation).
    Returns (escalation_or_none, events_created).
    """
    patient = call.patient

    # 1. transcript
    if transcript_text:
        call.transcript = transcript_text.strip()
        call.detected_language = patient.preferred_language
        call.language_confidence = 1.0
    elif audio_bytes:
        try:
            transcript, lang, conf = await sarvam.stt(audio_bytes, filename, patient.preferred_language)
            call.transcript = transcript
            call.detected_language = lang or patient.preferred_language
            call.language_confidence = conf
        except SarvamUnavailable as e:
            call.status = "completed"
            call.transcript = ""
            db.add(CareEvent(
                patient_id=patient.id, type="call", severity="warn",
                title="Reply received but transcription failed",
                detail=f"STT unavailable: {e}",
            ))
            db.flush()
            return None, 1
    else:
        raise ValueError("either audio or transcript text is required")

    # english mirror for doctors / analytics
    if call.transcript and call.detected_language.split("-")[0] != "en":
        try:
            call.transcript_english = await sarvam.translate(
                call.transcript, "en-IN", call.detected_language or patient.preferred_language,
            )
        except SarvamUnavailable:
            call.transcript_english = ""
    else:
        call.transcript_english = call.transcript

    analysis_text = call.transcript_english or call.transcript

    # 2. resolve what this call was about
    sc = db.scalar(select(ScheduledCall).where(ScheduledCall.call_log_id == call.id))
    targets = list(sc.targets) if sc else []
    med_targets = [t for t in targets if t.ref_type == "medicine"]
    q_targets = [t for t in targets if t.ref_type == "followup"]

    # 3. structured extraction
    structured = await _extract_structured(analysis_text, med_targets, q_targets, db)

    events_created = 0
    escalation: Escalation | None = None

    # per-medicine adherence
    any_missed = False
    for t in med_targets:
        took = structured.get(f"took_medicine_{t.ref_id}")
        if took is None:
            took = structured.get("took_medicine")
        val = "unknown" if took is None else ("true" if took else "false")
        db.add(ExtractedResponse(
            call_log_id=call.id, medicine_id=t.ref_id,
            key="took_medicine", value=val, value_type="boolean",
        ))
        if val == "false":
            any_missed = True
            med = db.get(Medicine, t.ref_id)
            db.add(CareEvent(
                patient_id=patient.id, type="missed_dose", severity="warn",
                title=f"Missed dose: {med.name if med else t.label}",
                detail=f"Patient reported not taking {t.label}.",
            ))
            events_created += 1

    if not med_targets and "took_medicine" in structured:
        db.add(ExtractedResponse(
            call_log_id=call.id, key="took_medicine",
            value="true" if structured["took_medicine"] else "false", value_type="boolean",
        ))
        if structured["took_medicine"] is False:
            any_missed = True
            db.add(CareEvent(
                patient_id=patient.id, type="missed_dose", severity="warn",
                title="Missed dose reported", detail=analysis_text[:300],
            ))
            events_created += 1

    # follow-up question answers
    for t in q_targets:
        ans = structured.get(f"question_{t.ref_id}")
        if ans is not None:
            q = db.get(FollowUpQuestion, t.ref_id)
            db.add(ExtractedResponse(
                call_log_id=call.id, question_id=t.ref_id,
                key=(q.text[:120] if q else t.label[:120]),
                value=str(ans), value_type=(q.type if q else "text"),
            ))

    # symptoms
    symptoms = structured.get("symptoms") or []
    for s in symptoms:
        db.add(ExtractedResponse(call_log_id=call.id, key="symptom", value=s, value_type="text"))
        db.add(CareEvent(
            patient_id=patient.id, type="symptom", severity="info",
            title=f"Symptom reported: {s}", detail=analysis_text[:300],
        ))
        events_created += 1

    if structured.get("pain_score") is not None:
        db.add(ExtractedResponse(
            call_log_id=call.id, key="pain_score",
            value=str(structured["pain_score"]), value_type="number",
        ))

    # 4. urgency / escalation
    urgency = structured.get("urgency", "low")
    db.add(ExtractedResponse(call_log_id=call.id, key="urgency", value=urgency, value_type="enum"))
    if urgency == "high":
        escalation = Escalation(
            patient_id=patient.id, call_log_id=call.id,
            reason=structured.get("urgency_reason") or analysis_text[:300] or "Urgent symptoms detected",
            urgency="high", status="open",
        )
        db.add(escalation)
        db.add(CareEvent(
            patient_id=patient.id, type="alert", severity="critical",
            title="URGENT: escalation raised",
            detail=structured.get("urgency_reason") or analysis_text[:300],
        ))
        events_created += 1

    # 5. call completion event
    call.status = "completed"
    if sc:
        sc.status = "completed"
    db.add(CareEvent(
        patient_id=patient.id, type="call", severity="info",
        title=f"{call.kind.capitalize()} call completed",
        detail=(analysis_text[:280] + ("…" if len(analysis_text) > 280 else "")),
    ))
    events_created += 1

    db.flush()
    if escalation:
        db.flush()
    return escalation, events_created


async def _extract_structured(
    text: str, med_targets: list[CallTarget], q_targets: list[CallTarget], db: Session
) -> dict:
    """Sarvam text-analytics first; deterministic keyword fallback second."""
    if not text.strip():
        return {"urgency": "low"}

    questions = [
        {"id": "urgency", "text": "Does the patient report urgent or dangerous symptoms (severe pain, chest pain, breathlessness, bleeding, fainting)? Answer exactly one of: low, medium, high.", "type": "enum", "properties": {"options": ["low", "medium", "high"]}},
        {"id": "symptoms", "text": "List any symptoms the patient mentions, comma separated. If none, answer 'none'.", "type": "short answer"},
        {"id": "pain_score", "text": "If the patient mentions a pain level from 0 to 10, what is it? If not mentioned answer 'none'.", "type": "short answer"},
        {"id": "took_medicine", "text": "Did the patient say they took their medicine? Answer exactly one of: yes, no, unclear.", "type": "enum", "properties": {"options": ["yes", "no", "unclear"]}},
    ]
    for t in med_targets:
        questions.append({
            "id": f"took_medicine_{t.ref_id}",
            "text": f"Did the patient take this specific medicine: {t.label}? Answer exactly one of: yes, no, unclear.",
            "type": "enum", "properties": {"options": ["yes", "no", "unclear"]},
        })
    for t in q_targets:
        q = db.get(FollowUpQuestion, t.ref_id)
        questions.append({
            "id": f"question_{t.ref_id}",
            "text": f"Answer this question from the patient's words: {q.text if q else t.label}. If not addressed answer 'none'.",
            "type": "short answer",
        })

    try:
        answers = await sarvam.text_analytics(text, questions)
        return _shape_analytics(answers, text)
    except SarvamUnavailable as e:
        logger.warning("text-analytics unavailable (%s); trying LLM extraction", e)

    try:
        answers = await sarvam.text_analytics_llm(text, questions)
        return _shape_analytics(answers, text)
    except SarvamUnavailable as e:
        logger.warning("LLM extraction unavailable (%s); keyword fallback", e)
        return _keyword_extract(text, med_targets)


def _shape_analytics(answers: list[dict], text: str) -> dict:
    out: dict = {}
    for a in answers:
        qid = a.get("id") or ""
        resp = str(a.get("response") or "").strip()
        low = resp.lower()
        if qid == "urgency":
            out["urgency"] = low if low in {"low", "medium", "high"} else "low"
            if out["urgency"] == "high":
                out["urgency_reason"] = text[:300]
        elif qid == "symptoms":
            if low and low not in {"none", "n/a", "no"}:
                out["symptoms"] = [s.strip() for s in resp.split(",") if s.strip()][:8]
        elif qid == "pain_score":
            m = re.search(r"\d+", resp)
            if m:
                out["pain_score"] = max(0, min(10, int(m.group())))
        elif qid.startswith("took_medicine"):
            val = True if low == "yes" else False if low == "no" else None
            out[qid if qid != "took_medicine" else "took_medicine"] = val
        elif qid.startswith("question_"):
            if low and low != "none":
                out[qid] = resp
    # keyword safety net: never let the analytics miss an obvious red flag
    if out.get("urgency") != "high" and _is_urgent(text):
        out["urgency"] = "high"
        out["urgency_reason"] = text[:300]
    out.setdefault("urgency", "low")
    return out


def _keyword_extract(text: str, med_targets: list[CallTarget]) -> dict:
    low = text.lower()
    out: dict = {"urgency": "high" if _is_urgent(text) else "low"}
    if out["urgency"] == "high":
        out["urgency_reason"] = text[:300]

    took: bool | None = None
    if any(p in low for p in NEGATIVE_MED_PATTERNS):
        took = False
    elif any(p in low for p in POSITIVE_MED_PATTERNS):
        took = True
    if took is not None:
        out["took_medicine"] = took
        for t in med_targets:
            out[f"took_medicine_{t.ref_id}"] = took

    symptoms = [name for name, kws in SYMPTOM_KEYWORDS.items() if any(k in low for k in kws)]
    if symptoms:
        out["symptoms"] = symptoms

    m = re.search(r"pain[^0-9]{0,20}(\d{1,2})|(\d{1,2})\s*(?:/|out of)\s*10", low)
    if m:
        score = int(next(g for g in m.groups() if g))
        out["pain_score"] = max(0, min(10, score))
    return out


def _is_urgent(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in URGENT_KEYWORDS)


# ---------------- adherence ----------------

def adherence_for_patient(db: Session, patient_id: int) -> float | None:
    """% of medicine responses reporting the dose was taken. None = no data."""
    rows = db.execute(
        select(ExtractedResponse.value)
        .join(CallLog, ExtractedResponse.call_log_id == CallLog.id)
        .where(CallLog.patient_id == patient_id, ExtractedResponse.key == "took_medicine")
    ).scalars().all()
    known = [v for v in rows if v in {"true", "false"}]
    # skipped slots count as misses
    skipped = db.scalar(
        select(ScheduledCall.id).where(
            ScheduledCall.patient_id == patient_id,
            ScheduledCall.kind == "medicine",
            ScheduledCall.status == "skipped",
        ).limit(1)
    )
    skipped_count = 0
    if skipped is not None:
        skipped_count = len(db.scalars(
            select(ScheduledCall.id).where(
                ScheduledCall.patient_id == patient_id,
                ScheduledCall.kind == "medicine",
                ScheduledCall.status == "skipped",
            )
        ).all())
    total = len(known) + skipped_count
    if total == 0:
        return None
    taken = sum(1 for v in known if v == "true")
    return round(100.0 * taken / total, 1)
