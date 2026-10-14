"""Test setup: temporary data folder, fake LLM, fake capture, worker driven manually."""
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="curation-tests-"))
os.environ.update({
    "DATA_DIR": str(_TMP),
    "REFERENTIAL_FILE": str(_TMP / "referential.txt"),
    "LLM_FAKE": "true",
    "CAPTURE_FAKE": "true",
    "WORKER_AUTOSTART": "false",
    "GOOGLE_CHAT_WEBHOOK": "",
    "QUEUE_CAP_PER_USER": "15",
})
shutil.copyfile(Path(__file__).parent / "referential_test.txt", _TMP / "referential.txt")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, worker  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    for suffix in ("", "-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    config.CONNECTOR_CSV.unlink(missing_ok=True)
    db.init()
    worker._next_slot.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def users(client):
    ids = [client.post("/api/users", json={"name": n}).json()["id"] for n in ("Alice", "Bob", "Chloe")]
    return ids


def as_user(uid):
    return {"X-User-Id": str(uid)}
