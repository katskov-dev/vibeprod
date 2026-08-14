"""Фоновые cron-расписания на APScheduler."""
import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from . import session_manager

log = logging.getLogger("harness.sched")

scheduler = BackgroundScheduler()
loop = None


def init_scheduler(main_loop):
    global loop
    loop = main_loop
    scheduler.start()
    for row in db.query("SELECT * FROM schedules"):
        if row["enabled"]:
            _add_job(row)
    log.info("scheduler started, %d jobs", len(scheduler.get_jobs()))


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _add_job(row):
    try:
        trigger = CronTrigger.from_crontab(row["cron"], timezone=row["timezone"] or "Europe/Moscow")
        scheduler.add_job(
            _fire,
            trigger,
            args=[row["id"]],
            id=f"sched-{row['id']}",
            replace_existing=True,
        )
        return trigger
    except ValueError:
        return None


def validate_cron(expr, tz):
    CronTrigger.from_crontab(expr, timezone=tz or "Europe/Moscow")
    return True


def job_next_run(schedule_id):
    job = scheduler.get_job(f"sched-{schedule_id}")
    return job.next_run_time.isoformat() if job and job.next_run_time else None


def apply_schedule(schedule_id):
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if not row:
        scheduler.remove_job(f"sched-{schedule_id}") if scheduler.get_job(f"sched-{schedule_id}") else None
        return None
    if row["enabled"]:
        _add_job(row)
        return job_next_run(schedule_id)
    if scheduler.get_job(f"sched-{schedule_id}"):
        scheduler.remove_job(f"sched-{schedule_id}")
    return None


def _fire(schedule_id):
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if not row:
        return
    try:
        run_id = db.execute(
            "INSERT INTO schedule_runs(schedule_id, status, started_at) VALUES(?, 'running', datetime('now'))",
            (schedule_id,),
        )
        sid = session_manager.create_session(
            row["agent_id"],
            row["title"] or f"Schedule #{schedule_id}",
            row["prompt"],
            source="schedule",
        )
        db.execute("UPDATE schedule_runs SET session_id=? WHERE id=?", (sid, run_id))
        db.execute("UPDATE schedules SET last_run=datetime('now') WHERE id=?", (schedule_id,))
        if loop:
            asyncio.run_coroutine_threadsafe(_run(sid, row["prompt"]), loop)
        else:
            db.execute(
                "UPDATE schedule_runs SET status='failed', error='no event loop', finished_at=datetime('now') WHERE id=?",
                (run_id,),
            )
    except Exception:
        log.exception("schedule fire %s", schedule_id)


async def _run(sid, prompt):
    await session_manager.start_session(sid, initial_prompt=prompt)
