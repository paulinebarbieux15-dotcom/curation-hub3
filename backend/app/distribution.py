"""Automatic distribution of new links: capped round-robin among active profiles.

- Only users that are not deleted and marked available take part.
- The pointer (last user served) is stored in settings, so turns survive restarts.
- A user whose personal queue already holds QUEUE_CAP_PER_USER to-do items is skipped.
- If nobody can take the link, it stays in the GLOBAL queue (assignee_id NULL),
  where anyone can claim it.
"""
import sqlite3

from . import config, db

TODO_SQL = "('queued','processing','to_review','error')"


def active_users(con: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in con.execute(
        "SELECT id FROM users WHERE deleted_at IS NULL AND available=1 ORDER BY id")]


def open_count(con: sqlite3.Connection, user_id: int) -> int:
    return con.execute(f"SELECT COUNT(*) FROM links WHERE assignee_id=? AND status IN {TODO_SQL}",
                       (user_id,)).fetchone()[0]


def next_assignee(con: sqlite3.Connection) -> int | None:
    users = active_users(con)
    if not users:
        return None
    last = db.get_setting(con, "rr_pointer", 0) or 0
    start = next((i for i, u in enumerate(users) if u > last), 0)
    order = users[start:] + users[:start]
    for uid in order:
        if config.QUEUE_CAP_PER_USER and open_count(con, uid) >= config.QUEUE_CAP_PER_USER:
            continue
        db.set_setting(con, "rr_pointer", uid)
        return uid
    return None  # everyone is full -> global queue


def redistribute(con: sqlite3.Connection, link_ids: list[int]) -> dict[int, int | None]:
    """Give each link a new owner by round-robin (None = global queue)."""
    result = {}
    for lid in link_ids:
        uid = next_assignee(con)
        con.execute("UPDATE links SET assignee_id=?, updated_at=? WHERE id=?", (uid, db.now(), lid))
        result[lid] = uid
    return result
