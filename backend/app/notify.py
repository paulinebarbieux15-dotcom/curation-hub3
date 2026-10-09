"""Google Chat notifications (incoming webhook): daily digest + test message."""
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db


def send_text(text: str) -> None:
    if not config.GOOGLE_CHAT_WEBHOOK:
        raise RuntimeError("GOOGLE_CHAT_WEBHOOK is not set in .env")
    r = httpx.post(config.GOOGLE_CHAT_WEBHOOK, json={"text": text}, timeout=20)
    r.raise_for_status()


def digest_text() -> str:
    warn = (datetime.now(timezone.utc) - timedelta(days=config.STALE_WARNING_DAYS)).isoformat(timespec="seconds")
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    with db.tx() as con:
        per_user = con.execute(
            "SELECT u.name, COUNT(l.id) n, SUM(CASE WHEN l.created_at < ? THEN 1 ELSE 0 END) stale "
            "FROM users u LEFT JOIN links l ON l.assignee_id=u.id AND l.status IN ('queued','processing','to_review','error') "
            "WHERE u.deleted_at IS NULL GROUP BY u.id ORDER BY u.name", (warn,)).fetchall()
        global_q = con.execute("SELECT COUNT(*) FROM links WHERE assignee_id IS NULL AND status IN "
                               "('queued','processing','to_review','error')").fetchone()[0]
        approved = con.execute("SELECT COUNT(*) FROM links WHERE status='approved' AND approved_at >= ?",
                               (since,)).fetchone()[0]
    lines = ["*Daily curation digest*", f"Validated in the last 24 h: *{approved}*",
             f"Global queue: *{global_q}* unassigned", ""]
    for r in per_user:
        stale = f" ({r['stale']} older than {config.STALE_WARNING_DAYS} days)" if r["stale"] else ""
        lines.append(f"• {r['name']}: {r['n']} to review{stale}")
    lines += ["", f"Open the dashboard: {config.PUBLIC_URL}"]
    return "\n".join(lines)


def send_digest() -> str:
    text = digest_text()
    send_text(text)
    return text
