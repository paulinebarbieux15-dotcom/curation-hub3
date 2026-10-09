"""Live updates (Server-Sent Events) and presence.

Any thread (worker, API) calls publish(); every open dashboard receives the event
through GET /api/events and refreshes only what changed.
"""
import asyncio
import json
import threading
import time

_loop: asyncio.AbstractEventLoop | None = None
_queues: set[asyncio.Queue] = set()
_lock = threading.Lock()
_presence: dict[int, float] = {}       # user_id -> last seen (monotonic)


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    with _lock:
        _queues.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _queues.discard(q)


def _put(q: asyncio.Queue, data: str) -> None:
    try:
        q.put_nowait(data)
    except asyncio.QueueFull:
        pass


def publish(kind: str, **payload) -> None:
    """Thread-safe: may be called from the worker thread."""
    if _loop is None:
        return
    data = json.dumps({"type": kind, **payload}, default=str)
    with _lock:
        queues = list(_queues)
    for q in queues:
        try:
            _loop.call_soon_threadsafe(_put, q, data)
        except RuntimeError:
            pass  # loop closed


def touch(user_id: int | None) -> None:
    if user_id:
        _presence[user_id] = time.monotonic()


def online(window_s: float = 60) -> list[int]:
    limit = time.monotonic() - window_s
    return sorted(u for u, t in _presence.items() if t >= limit)
