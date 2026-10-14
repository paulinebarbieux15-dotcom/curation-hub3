import csv
import io
import zipfile

from app import db, worker
from conftest import as_user


def test_profiles_soft_delete_moves_pending_to_global_queue(client, users):
    alice, bob, _ = users
    r = client.post("/api/ingest", json={"urls": ["https://a.com/1", "https://a.com/2"], "mode": "me"}, headers=as_user(alice))
    assert len(r.json()["created"]) == 2
    r = client.delete(f"/api/users/{alice}?reassign=global")
    assert r.json()["to_global"] == 2
    names = [u["name"] for u in client.get("/api/users").json()]
    assert "Alice" not in names
    assert "Alice" in [u["name"] for u in client.get("/api/users?include_deleted=true").json()]
    g = client.get("/api/links?assignee=global").json()
    assert g["total"] == 2
    with db.tx() as con:  # history kept
        assert con.execute("SELECT COUNT(*) FROM events WHERE action='user_deleted'").fetchone()[0] == 1


def test_round_robin_distribution_and_redistribute_on_delete(client, users):
    r = client.post("/api/ingest", json={"urls": [f"https://b.com/{i}" for i in range(6)]})
    owners = [c["assignee_id"] for c in r.json()["created"]]
    assert owners == users + users
    alice = users[0]
    r = client.delete(f"/api/users/{alice}?reassign=round_robin").json()
    assert r["moved"] == 2 and r["to_global"] == 0


def test_away_user_is_skipped(client, users):
    client.patch(f"/api/users/{users[1]}", json={"available": False})
    owners = [c["assignee_id"] for c in client.post("/api/ingest", json={"urls": [f"https://c.com/{i}" for i in range(4)]}).json()["created"]]
    assert users[1] not in owners


def test_anti_duplicates_in_preview_and_ingest(client, users):
    client.post("/api/ingest", json={"urls": ["https://www.news.com/a?utm_source=x"]})
    p = client.post("/api/ingest/preview", json={"text": "see news.com/a and https://news.com/a/?fbclid=1 and https://new.com/b"}).json()
    states = [i["state"] for i in p["items"]]
    assert states.count("duplicate") == 1 and "repeat" in states and "new" in states
    r = client.post("/api/ingest", json={"text": "again https://news.com/a#x"}).json()
    assert r["created"] == [] and r["duplicates"][0]["id"] == 1


def test_full_flow_ai_fill_review_approve_registry_exports(client, users):
    alice = users[0]
    client.patch(f"/api/users/{alice}", json={"antbox_redactor": "Redacteur_002"})
    client.post("/api/ingest", json={"urls": ["https://press.com/drone-battery"], "mode": "me"}, headers=as_user(alice))
    assert worker.drain() == 1
    link = client.get("/api/links/1").json()
    assert link["status"] == "to_review"
    assert link["metadata"]["Document type"] == ["Press articles / Social Network"]
    assert link["metadata"]["Vehicles"]                    # confident, evidenced
    assert "InventedValue" not in str(link["metadata"])    # not in referential
    assert not link["metadata"].get("Domain")              # not an AI column
    assert link["info"]["author"] == "Jane Doe"
    assert link["info"]["published_date"] is None          # low confidence -> left empty
    assert "themes" not in link
    assert any(not d["kept"] for v in link["ai_details"].values() for d in v)

    # soft lock: Bob cannot edit while Alice has the panel open
    assert client.post("/api/links/1/lock", headers=as_user(alice)).json()["ok"]
    assert client.patch("/api/links/1", json={"title": "x"}, headers=as_user(users[1])).status_code == 409

    r = client.patch("/api/links/1", headers=as_user(alice), json={
        "title": "Drone battery", "summary_html": "<p>Edited <b>summary</b><script>x</script></p>",
        "metadata": {**link["metadata"], "Domain": ["Domaine_01"]},
    })
    assert r.status_code == 200 and "<script>" not in r.json()["summary_html"]

    r = client.post("/api/links/1/approve", headers=as_user(alice))
    assert r.status_code == 200, r.text
    reg = client.get("/api/registry").json()
    assert len(reg) == 1 and reg[0]["operator_name"] == "Alice"
    assert client.get(f"/api/registry/{reg[0]['id']}/pdf").status_code == 200
    csv_text = client.get(f"/api/registry/{reg[0]['id']}/csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(csv_text), delimiter=";"))
    assert rows[0] == ["File Name", "URL", "Check", "Attachment Clearance Level", "Attachment Co-redactor",
                       "Document type", "Domain", "Attachment redactor", "Meeting or Forum", "Year",
                       "Parts/Modules", "Optionnal", "OperationType", "Vehicles"]
    r = dict(zip(rows[0], rows[1]))
    assert r["File Name"].endswith(".pdf") and r["URL"] == "https://press.com/drone-battery"
    assert r["Check"] == "" and r["Meeting or Forum"] == ""
    assert r["Attachment Clearance Level"] == "Public" and r["Attachment Co-redactor"] == "Group: Co-Writer"
    assert r["Document type"] == "Press articles / Social Network" and r["Domain"] == "Domaine_01"
    assert r["Attachment redactor"] == "Redacteur_002"     # automatic: approving analyst's profile
    assert r["Year"] and r["Vehicles"]
    assert client.get("/api/registry/verify").json()["ok"]

    # immutability enforced by the database
    import sqlite3, pytest
    with pytest.raises(sqlite3.DatabaseError):
        with db.tx() as con:
            con.execute("UPDATE registry SET title='hacked'")

    pkg = zipfile.ZipFile(io.BytesIO(client.get("/api/export?approved=true").content)).namelist()
    assert "connector_press_article.csv" in pkg and any(n.endswith(".pdf") for n in pkg)

    both = client.get("/api/links/1/export?format=both")
    names = zipfile.ZipFile(io.BytesIO(both.content)).namelist()
    assert len(names) == 2 and names[0].rsplit(".", 1)[0] == names[1].rsplit(".", 1)[0]
    assert client.get("/api/links/1/export?format=pdf").headers["content-type"] == "application/pdf"
    assert client.get("/api/export?approved=true").status_code == 200

    s = client.get("/api/stats", headers=as_user(alice)).json()
    assert s["done_today_me"] == 1 and s["velocity"]["me"][-1] == 1 and s["ai_acceptance"] is not None


def test_required_fields_block_approval(client, users):
    client.post("/api/ingest", json={"urls": ["https://x.org/r"], "mode": "me"}, headers=as_user(users[0]))
    worker.drain()
    client.patch("/api/links/1", headers=as_user(users[0]), json={"metadata": {"Document type": []}})
    r = client.post("/api/links/1/approve", headers=as_user(users[0]))
    assert r.status_code == 422 and "Document type" in r.json()["detail"]


def test_report_includes_links_and_summaries(client, users):
    for i in range(3):
        client.post("/api/ingest", json={"urls": [f"https://r.com/{i}"], "mode": "me"}, headers=as_user(users[0]))
    worker.drain()
    for i in (1, 2, 3):
        assert client.post(f"/api/links/{i}/approve", headers=as_user(users[0])).status_code == 200
    for gran in ("general", "doctype", "user"):
        r = client.post("/api/reports", json={"period": "7d", "granularity": gran}, headers=as_user(users[0])).json()
        assert r["link_count"] == 3
        assert "https://r.com/0" in r["markdown"] and "## Executive summary" in r["markdown"]
    assert client.post("/api/reports", json={"period": "custom", "start": "2026-01-10", "end": "2026-01-01"}).status_code == 422


def test_social_links_need_switch_and_fail_cleanly(client, users):
    client.post("/api/ingest", json={"urls": ["https://www.linkedin.com/posts/abc"]})
    worker.drain()
    link = client.get("/api/links/1").json()
    assert link["status"] == "error" and "Social capture is turned off" in link["error"]
    assert link["source"] == "social"


def test_retry_with_backoff_then_dead_letter(client, users, monkeypatch):
    from app import capture
    calls = {"n": 0}

    def boom(url, lid):
        calls["n"] += 1
        raise RuntimeError("site down")
    monkeypatch.setattr(capture, "capture", boom)
    client.post("/api/ingest", json={"urls": ["https://down.com/a"]})
    worker.drain()
    assert client.get("/api/links/1").json()["status"] == "queued"   # retry scheduled later
    with db.tx() as con:
        con.execute("UPDATE jobs SET run_after='2000-01-01T00:00:00+00:00' WHERE status='pending'")
    worker.drain()
    with db.tx() as con:
        con.execute("UPDATE jobs SET run_after='2000-01-01T00:00:00+00:00' WHERE status='pending'")
    worker.drain()
    link = client.get("/api/links/1").json()
    assert link["status"] == "error" and calls["n"] == 3


def test_filters(client, users):
    a = users[0]
    client.post("/api/ingest", json={"urls": ["https://f.com/1", "https://f.com/2"], "mode": "me"}, headers=as_user(a))
    client.post("/api/ingest", json={"urls": ["https://f.com/3"], "mode": "global"})
    worker.drain()
    assert client.get("/api/links?assignee=me", headers=as_user(a)).json()["total"] == 2
    assert client.get("/api/links?assignee=global").json()["total"] == 1
    client.post("/api/links/1/approve", headers=as_user(a))
    assert client.get("/api/links?state=done").json()["total"] == 1
    assert client.get("/api/links?state=all&doc_type=Press articles / Social Network").json()["total"] == 3
    assert client.get("/api/links?state=all&doc_type=Commercial Brochure / Manufacturer website").json()["total"] == 0
    assert client.get("/api/links?state=all&date_from=2000-01-01&date_to=2000-01-02").json()["total"] == 0


def test_near_duplicate_and_redirect_duplicate(client, users, monkeypatch):
    from app import capture
    body = " ".join(f"word{i}" for i in range(300))

    def fake(url, lid):
        (capture.config.PDF_DIR / f"{lid}.pdf").write_bytes(capture._MINIMAL_PDF)
        final = "https://news.com/original" if "short" in url else url
        text = body if "copy" not in url else body.replace("word10 ", "changed ")
        return capture.CaptureResult(title="T", text=text, pdf_file=f"{lid}.pdf", final_url=final, canonical_url="")
    monkeypatch.setattr(capture, "capture", fake)
    client.post("/api/ingest", json={"urls": ["https://news.com/original", "https://other.com/copy", "https://sho.rt/short"]})
    worker.drain()
    assert client.get("/api/links/2").json()["near_duplicate_of"] == 1
    third = client.get("/api/links/3").json()
    assert third["status"] == "duplicate" and third["duplicate_of"] == 1


def test_tracking_params_removed_from_stored_url(client, users):
    r = client.post("/api/ingest", json={"text": "https://site.com/a?id=2&utm_source=x&fbclid=y#frag"}).json()
    assert r["created"][0]["url"] == "https://site.com/a?id=2"
    p = client.post("/api/ingest/preview", json={"text": "bad one http://bad and www."}).json()
    assert p["invalid"] == 1


def test_social_errors_are_requeued_after_login(client, users, monkeypatch):
    from app import social
    client.post("/api/ingest", json={"urls": ["https://www.instagram.com/p/abc/"]})
    worker.drain()
    assert client.get("/api/links/1").json()["status"] == "error"
    monkeypatch.setenv("SOCIAL_INSTAGRAM_USERNAME", "u")
    monkeypatch.setenv("SOCIAL_INSTAGRAM_PASSWORD", "p")
    client.put("/api/settings/social", json={"enabled": True})
    r = client.post("/api/settings/social/login/instagram").json()
    assert r["requeued"] == 1
    worker.drain()
    assert client.get("/api/links/1").json()["status"] == "to_review"
