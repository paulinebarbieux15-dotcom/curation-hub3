"""Background job queue (one thread).

- Jobs live in the `jobs` table, so nothing is lost on restart.
- Politeness: at least DOMAIN_MIN_INTERVAL_S between two captures of the same site;
  social networks share ONE slot with a random 20-40 s gap and a daily cap,
  so the shared account behaves like a human and is not flagged.
- Failures are retried with backoff (30 s, 2 min, 10 min); after JOB_MAX_ATTEMPTS
  the link goes to 'error' (dead letter) and can be retried from the dashboard.
- Also sends the daily Google Chat digest.
"""
import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db, live, pipeline

log = logging.getLogger("curation.worker")

BACKOFF_S = [30, 120, 600]

_next_slot: dict[str, float] = {}       # domain or "social" -> monotonic time when allowed
_stop = threading.Event()
_thread: threading.Thread | None = None
_wake = threading.Event()


def _key(job) -> str:
    return "social" if job["is_social"] else job["domain"]


def _social_done_today(con) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return con.execute("SELECT COUNT(*) FROM jobs WHERE is_social=1 AND status IN ('done','failed') "
                       "AND finished_at >= ?", (today,)).fetchone()[0]


def _pick(ignore_spacing: bool = False):
    with db.tx() as con:
        rows = con.execute("SELECT * FROM jobs WHERE status='pending' AND run_after <= ? "
                           "ORDER BY run_after, id LIMIT 100", (db.now(),)).fetchall()
        social_full = config.SOCIAL_DAILY_CAP and _social_done_today(con) >= config.SOCIAL_DAILY_CAP
        now = time.monotonic()
        for job in rows:
            if job["is_social"] and social_full and not ignore_spacing:
                continue
            if not ignore_spacing and _next_slot.get(_key(job), 0) > now:
                continue
            con.execute("UPDATE jobs SET status='running', attempts=attempts+1 WHERE id=?", (job["id"],))
            return dict(job, attempts=job["attempts"] + 1)
    return None


def _run(job: dict) -> None:
    gap = random.uniform(config.SOCIAL_MIN_INTERVAL_S, config.SOCIAL_MAX_INTERVAL_S) if job["is_social"] \
        else config.DOMAIN_MIN_INTERVAL_S
    _next_slot[_key(job)] = time.monotonic() + gap
    try:
        pipeline.process(job["link_id"])
        with db.tx() as con:
            con.execute("UPDATE jobs SET status='done', finished_at=? WHERE id=?", (db.now(), job["id"]))
    except Exception as e:  # noqa: BLE001
        permanent = isinstance(e, pipeline.PermanentError)
        msg = str(e)[:1000] or type(e).__name__
        log.warning("Job %s (link %s) failed: %s", job["id"], job["link_id"], msg)
        with db.tx() as con:
            if not permanent and job["attempts"] < config.JOB_MAX_ATTEMPTS:
                delay = BACKOFF_S[min(job["attempts"] - 1, len(BACKOFF_S) - 1)]
                run_after = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
                con.execute("UPDATE jobs SET status='pending', run_after=?, last_error=? WHERE id=?",
                            (run_after, msg, job["id"]))
                con.execute("UPDATE links SET status='queued', error=?, updated_at=? WHERE id=?",
                            (f"Attempt {job['attempts']} failed, retrying in {delay}s: {msg}", db.now(), job["link_id"]))
            else:
                con.execute("UPDATE jobs SET status='failed', last_error=?, finished_at=? WHERE id=?",
                            (msg, db.now(), job["id"]))
                con.execute("UPDATE links SET status='error', error=?, updated_at=? WHERE id=?",
                            (msg, db.now(), job["link_id"]))
                db.log(con, "error", job["link_id"], None, msg[:500])
        live.publish("link", id=job["link_id"])


def drain(max_jobs: int = 1000) -> int:
    """Process every runnable job now, ignoring spacing (tests, CLI)."""
    n = 0
    while n < max_jobs:
        job = _pick(ignore_spacing=True)
        if not job:
            break
        _run(job)
        n += 1
    return n


def wake() -> None:
    _wake.set()


def _maybe_digest() -> None:
    if config.DIGEST_HOUR < 0 or not config.GOOGLE_CHAT_WEBHOOK:
        return
    local = datetime.now()
    if local.hour != config.DIGEST_HOUR:
        return
    with db.tx() as con:
        if db.get_setting(con, "last_digest") == local.date().isoformat():
            return
        db.set_setting(con, "last_digest", local.date().isoformat())
    from . import notify
    try:
        notify.send_digest()
    except Exception as e:  # noqa: BLE001
        log.warning("Digest failed: %s", e)


def _loop() -> None:
    log.info("Worker started")
    last_digest_check = 0.0
    while not _stop.is_set():
        try:
            job = _pick()
            if job:
                _run(job)
                continue
            if time.monotonic() - last_digest_check > 60:
                last_digest_check = time.monotonic()
                _maybe_digest()
        except Exception:  # noqa: BLE001
            log.exception("Worker loop error")
        _wake.wait(1.0)
        _wake.clear()


def start() -> None:
    global _thread
    with db.tx() as con:  # jobs interrupted by a restart go back to the queue
        con.execute("UPDATE jobs SET status='pending' WHERE status='running'")
        con.execute("UPDATE links SET status='queued' WHERE status='processing'")
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="curation-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def queue_status() -> dict:
    with db.tx() as con:
        rows = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        social_today = _social_done_today(con)
    return {"pending": rows.get("pending", 0), "running": rows.get("running", 0), "failed": rows.get("failed", 0),
            "social_today": social_today, "social_daily_cap": config.SOCIAL_DAILY_CAP}
