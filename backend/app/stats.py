"""Dashboard widgets: progress, velocity, personal vs global queue, aging, AI quality."""
import json
from datetime import date, datetime, timedelta, timezone

from . import config, db

TODO = "('queued','processing','to_review','error')"


def _day_start(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat(timespec="seconds")


def compute(user_id: int | None) -> dict:
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    warn = (datetime.now(timezone.utc) - timedelta(days=config.STALE_WARNING_DAYS)).isoformat(timespec="seconds")
    crit = (datetime.now(timezone.utc) - timedelta(days=config.STALE_CRITICAL_DAYS)).isoformat(timespec="seconds")
    with db.tx() as con:
        one = lambda sql, *p: con.execute(sql, p).fetchone()[0]  # noqa: E731
        my_todo = one(f"SELECT COUNT(*) FROM links WHERE assignee_id=? AND status IN {TODO}", user_id or -1)
        my_review = one("SELECT COUNT(*) FROM links WHERE assignee_id=? AND status='to_review'", user_id or -1)
        global_q = one(f"SELECT COUNT(*) FROM links WHERE assignee_id IS NULL AND status IN {TODO}")
        team_todo = one(f"SELECT COUNT(*) FROM links WHERE status IN {TODO}")
        processing = one("SELECT COUNT(*) FROM links WHERE status IN ('queued','processing')")
        errors = one("SELECT COUNT(*) FROM links WHERE status='error'")
        stale_warn = one(f"SELECT COUNT(*) FROM links WHERE status IN {TODO} AND created_at < ? AND created_at >= ?", warn, crit)
        stale_crit = one(f"SELECT COUNT(*) FROM links WHERE status IN {TODO} AND created_at < ?", crit)
        by_status = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM links GROUP BY status")}

        closed = con.execute(
            "SELECT user_id, substr(at,1,10) d, COUNT(*) n FROM events WHERE action IN ('approved','rejected') "
            "AND at >= ? GROUP BY user_id, d", (_day_start(days[0]),)).fetchall()
        vel_me = {d.isoformat(): 0 for d in days}
        vel_team = dict(vel_me)
        for r in closed:
            if r["d"] in vel_team:
                vel_team[r["d"]] += r["n"]
                if r["user_id"] == user_id:
                    vel_me[r["d"]] += r["n"]
        done_today_me = vel_me[today.isoformat()]
        done_today_team = vel_team[today.isoformat()]

        workload = [dict(r) for r in con.execute(
            f"SELECT u.id, u.name, u.available, "
            f"(SELECT COUNT(*) FROM links l WHERE l.assignee_id=u.id AND l.status IN {TODO}) todo, "
            f"(SELECT COUNT(*) FROM events e WHERE e.user_id=u.id AND e.action='approved' AND e.at >= ?) done_7d "
            f"FROM users u WHERE u.deleted_at IS NULL ORDER BY u.name", (_day_start(days[0]),))]

        proposed = kept = 0
        for r in con.execute("SELECT metadata, metadata_ai FROM links WHERE status='approved'"):
            final, ai = json.loads(r["metadata"] or "{}"), json.loads(r["metadata_ai"] or "{}")
            for name, values in ai.items():
                proposed += len(values)
                kept += len(set(values) & set(final.get(name) or []))
        total = one("SELECT COUNT(*) FROM links WHERE status<>'duplicate'")
        approved = by_status.get("approved", 0)
        rejected = by_status.get("rejected", 0)

    def pct(a, b):
        return round(100 * a / b) if b else 0

    return {
        "my_todo": my_todo,
        "my_to_review": my_review,
        "global_queue": global_q,
        "team_todo": team_todo,
        "processing": processing,
        "errors": errors,
        "done_today_me": done_today_me,
        "done_today_team": done_today_team,
        "progress_today_me": pct(done_today_me, done_today_me + my_todo),
        "progress_overall": pct(approved + rejected, total),
        "velocity": {"days": [d.isoformat() for d in days], "me": list(vel_me.values()), "team": list(vel_team.values()),
                     "avg_team_per_day": round(sum(vel_team.values()) / 7, 1)},
        "stale": {"warning": stale_warn, "critical": stale_crit,
                  "warning_days": config.STALE_WARNING_DAYS, "critical_days": config.STALE_CRITICAL_DAYS},
        "by_status": by_status,
        "workload": workload,
        "ai_acceptance": pct(kept, proposed) if proposed else None,
        "approved_total": approved,
    }
