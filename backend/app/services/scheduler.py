"""Cron engine — APScheduler tick that turns due ScheduledCalls into real calls.

Reliability rules (plan §17.7):
- idempotent slots via slot_key (enforced at materialization),
- never dial outside the patient's call window (defer to next open),
- no_answer/failed → retry with configured backoff up to max_retries → skipped,
- a skipped medicine slot emits a missed_dose CareEvent.
"""

import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import CallLog, CareEvent, Patient, ScheduledCall, utcnow
from . import careplus, telephony

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
_tick_lock = asyncio.Lock()


def start() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        tick, "interval",
        seconds=max(5, settings.sched_tick_seconds),
        id="care-tick", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info("scheduler started (tick every %ss)", settings.sched_tick_seconds)
    return _scheduler


def shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


async def tick() -> dict:
    """One pass over due slots. Serialized with a lock so ticks never overlap."""
    async with _tick_lock:
        return await _tick_inner()


async def _tick_inner() -> dict:
    now = utcnow().replace(tzinfo=None)
    placed, deferred, expired = 0, 0, 0
    db = SessionLocal()
    try:
        due = db.scalars(
            select(ScheduledCall).where(
                ScheduledCall.status.in_(["pending", "no_answer", "failed"]),
                ScheduledCall.due_at <= now,
                ScheduledCall.next_attempt_at <= now,
            ).order_by(ScheduledCall.due_at)
        ).all()

        for sc in due:
            patient = db.get(Patient, sc.patient_id)
            if patient is None:
                sc.status = "skipped"
                sc.last_error = "patient deleted"
                continue
            plan = sc.care_plan

            # medicine grace window: if we're far past the dose slot, mark missed
            if sc.kind == "medicine" and sc.status == "pending":
                grace = _max_grace_minutes(sc)
                if now > sc.due_at + timedelta(minutes=grace) and sc.attempts == 0:
                    _mark_skipped(db, sc, patient, "slot expired past grace window")
                    expired += 1
                    continue

            if not careplus.within_call_window(plan, patient, now):
                sc.next_attempt_at = careplus.next_window_open(plan, patient, now)
                deferred += 1
                continue

            max_retries = plan.max_retries if plan else 3
            if sc.attempts >= max_retries + 1:
                _mark_skipped(db, sc, patient, "max retries exhausted")
                expired += 1
                continue

            try:
                await _place_scheduled_call(db, sc, patient)
                placed += 1
            except Exception as e:  # noqa: BLE001 — one bad slot must not kill the tick
                logger.error("slot %s failed: %s", sc.slot_key, e)
                sc.attempts += 1
                sc.last_error = str(e)[:500]
                if sc.attempts >= (plan.max_retries if plan else 3) + 1:
                    _mark_skipped(db, sc, patient, f"failed: {e}")
                    expired += 1
                else:
                    sc.status = "failed"
                    sc.next_attempt_at = now + timedelta(minutes=careplus.backoff_minutes(plan, sc.attempts - 1))
            db.commit()
        db.commit()
    finally:
        db.close()
    result = {"placed": placed, "deferred": deferred, "expired": expired, "due": len(due)}
    if any(result.values()):
        logger.info("tick: %s", result)
    return result


async def _place_scheduled_call(db, sc: ScheduledCall, patient: Patient) -> CallLog:
    """Build script for exactly this slot's targets → translate → TTS → dial."""
    targets = list(sc.targets)
    script_en = await careplus.build_script(sc.kind, targets, patient)
    script_local = await careplus.localize_script(script_en, patient.preferred_language)
    audio_path = await careplus.synthesize(script_local, patient.preferred_language)

    call = CallLog(
        patient_id=patient.id, direction="outbound", kind=sc.kind,
        script_text=script_en, script_text_translated=script_local,
        tts_audio_path=audio_path, status="queued",
    )
    db.add(call)
    db.flush()

    sc.call_log_id = call.id
    sc.attempts += 1
    sc.status = "placed"
    # Commit before the carrier round-trip: it releases the SQLite write lock,
    # and the dialogue prep that runs while the phone rings reads this call in
    # its own session.
    db.commit()

    try:
        telephony.place_call(call, patient.phone)
    except telephony.TelephonyError as e:
        sc.status = "failed"
        sc.last_error = str(e)[:500]
        plan = sc.care_plan
        sc.next_attempt_at = utcnow().replace(tzinfo=None) + timedelta(
            minutes=careplus.backoff_minutes(plan, sc.attempts - 1)
        )
        return call

    db.add(CareEvent(
        patient_id=patient.id, type="call", severity="info",
        title=f"{sc.kind.capitalize()} call placed ({call.mode})",
        detail="; ".join(t.label for t in targets)[:400],
    ))
    return call


def _mark_skipped(db, sc: ScheduledCall, patient: Patient, reason: str) -> None:
    sc.status = "skipped"
    sc.last_error = reason[:500]
    if sc.kind == "medicine":
        db.add(CareEvent(
            patient_id=patient.id, type="missed_dose", severity="warn",
            title="Missed dose (call could not be completed)",
            detail=f"{'; '.join(t.label for t in sc.targets)[:300]} — {reason}",
        ))


def _max_grace_minutes(sc: ScheduledCall) -> int:
    """Largest grace window among the slot's medicines (default 30, min 30)."""
    from ..models import Medicine
    db = SessionLocal()
    try:
        windows = [30]
        for t in sc.targets:
            if t.ref_type == "medicine":
                med = db.get(Medicine, t.ref_id)
                if med:
                    windows.append(med.window_minutes)
        return max(windows)
    finally:
        db.close()
