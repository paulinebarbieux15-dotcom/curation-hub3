"""ETCA technology watch — REST API.

Run (from backend/):   uvicorn app.main:app --reload
Dashboard:             http://localhost:8000
API docs:              http://localhost:8000/docs

The current user is sent by the dashboard in the X-User-Id header (profile switcher).
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import config

config.use_system_certificates()

from . import capture, db, distribution, exports, live, llm, notify, pipeline, referential, registry, reports, social, stats, worker  # noqa: E402
from .sanitize import clean_html  # noqa: E402
from .urls import extract_urls  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

LOCK_TTL_S = 60
TODO = db.TODO_STATUSES


@asynccontextmanager
async def lifespan(_app):
    db.init()
    live.bind_loop(asyncio.get_running_loop())
    if config.WORKER_AUTOSTART:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="ETCA", version="2.0.0", lifespan=lifespan,
              description="Link ingestion, capture, AI auto-fill, validation, immutable registry and reporting.")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


# ================================================================= identity
def current_user(x_user_id: str | None = Header(default=None)) -> dict | None:
    if not x_user_id or not x_user_id.isdigit():
        return None
    with db.tx() as con:
        u = con.execute("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (int(x_user_id),)).fetchone()
    if u:
        live.touch(u["id"])
    return dict(u) if u else None


def require_user(user: dict | None = Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(401, "Select your profile first (top-right profile switcher).")
    return user


# ================================================================= schemas
class UserIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    antbox_redactor: str | None = None


class UserPatch(BaseModel):
    name: str | None = None
    antbox_redactor: str | None = None
    available: bool | None = None


class IngestIn(BaseModel):
    urls: list[str] = []
    text: str | None = None          # free text: URLs are extracted
    mode: str = "auto"               # auto | me | global | <user id>
    source: str = "paste"


class PreviewIn(BaseModel):
    text: str


class LinkPatch(BaseModel):
    title: str | None = None
    summary_html: str | None = None
    metadata: dict[str, list[str]] | None = None
    info: dict | None = None


class AssignIn(BaseModel):
    assignee_id: int | None = None   # None = global queue


class RejectIn(BaseModel):
    reason: str = ""


class BulkIn(BaseModel):
    ids: list[int]
    action: str                       # claim | assign | unassign | reject | retry | distribute
    assignee_id: int | None = None
    reason: str = ""


class ReportIn(BaseModel):
    period: str = "7d"                # 7d | 30d | custom
    start: str | None = None
    end: str | None = None
    granularity: str = "general"      # general | doctype | user
    title: str | None = None


class ToggleIn(BaseModel):
    enabled: bool


# ================================================================= helpers
LINK_SELECT = (
    "SELECT l.*, u.name AS assignee_name, a.name AS approved_by_name, k.name AS locked_by_name "
    "FROM links l LEFT JOIN users u ON u.id=l.assignee_id LEFT JOIN users a ON a.id=l.approved_by "
    "LEFT JOIN users k ON k.id=l.locked_by "
)


def _stale(created_at: str, status: str) -> str | None:
    if status not in TODO:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(created_at)
    if age >= timedelta(days=config.STALE_CRITICAL_DAYS):
        return "critical"
    if age >= timedelta(days=config.STALE_WARNING_DAYS):
        return "warning"
    return None


def _decorate(d: dict) -> dict:
    d["stale"] = _stale(d["created_at"], d["status"])
    d["locked"] = bool(d.get("locked_until") and d["locked_until"] > db.now())
    if not d["locked"]:
        d["locked_by"] = d["locked_by_name"] = None
    return d


def _link(con, link_id: int, with_text: bool = False) -> dict:
    row = con.execute(LINK_SELECT + "WHERE l.id=?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Link not found")
    d = _decorate(db.row_to_dict(row, with_text=with_text))
    return d


def _check_lock(link: dict, user: dict) -> None:
    if link["locked"] and link["locked_by"] != user["id"]:
        raise HTTPException(409, f"{link['locked_by_name']} is editing this link right now.")


def _editable(link: dict) -> None:
    if link["status"] in ("approved", "rejected", "duplicate"):
        raise HTTPException(409, f"Link is {link['status']}: reopen it first.")


# ================================================================= health / identity
@app.get("/api/health", tags=["system"])
def health():
    return {"ok": True, "llm_fake": config.LLM_FAKE, "capture_fake": config.CAPTURE_FAKE,
            "referential_fields": len(referential.fields())}


# ================================================================= users (header profiles)
@app.get("/api/users", tags=["users"])
def list_users(include_deleted: bool = False):
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM links l WHERE l.assignee_id=u.id AND l.status IN "
           "('queued','processing','to_review','error')) AS todo FROM users u")
    if not include_deleted:
        sql += " WHERE u.deleted_at IS NULL"
    with db.tx() as con:
        users = [dict(r) for r in con.execute(sql + " ORDER BY u.deleted_at IS NOT NULL, u.name")]
    online = set(live.online())
    for u in users:
        u["online"] = u["id"] in online
        u["available"] = bool(u["available"])
    return users


@app.post("/api/users", tags=["users"])
def create_user(data: UserIn):
    with db.tx() as con:
        if con.execute("SELECT 1 FROM users WHERE lower(name)=lower(?) AND deleted_at IS NULL", (data.name.strip(),)).fetchone():
            raise HTTPException(409, "A profile with this name already exists")
        cur = con.execute("INSERT INTO users (name, antbox_redactor, created_at) VALUES (?,?,?)",
                          (data.name.strip(), (data.antbox_redactor or "").strip() or None, db.now()))
        db.log(con, "user_created", None, cur.lastrowid, data.name)
        uid = cur.lastrowid
    live.publish("users")
    return {"id": uid, "name": data.name.strip()}


@app.patch("/api/users/{user_id}", tags=["users"])
def update_user(user_id: int, data: UserPatch):
    with db.tx() as con:
        if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "User not found")
        if data.name is not None:
            con.execute("UPDATE users SET name=? WHERE id=?", (data.name.strip(), user_id))
        if data.antbox_redactor is not None:
            con.execute("UPDATE users SET antbox_redactor=? WHERE id=?", (data.antbox_redactor.strip() or None, user_id))
        if data.available is not None:
            con.execute("UPDATE users SET available=? WHERE id=?", (int(data.available), user_id))
    live.publish("users")
    return {"ok": True}


@app.delete("/api/users/{user_id}", tags=["users"])
def delete_user(user_id: int, reassign: str = Query("global", pattern="^(global|round_robin)$"),
                actor: dict | None = Depends(current_user)):
    """Soft delete: history kept; pending items go to the global queue or are redistributed."""
    with db.tx() as con:
        u = con.execute("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not u:
            raise HTTPException(404, "User not found")
        con.execute("UPDATE users SET deleted_at=?, available=0 WHERE id=?", (db.now(), user_id))
        q = ",".join("?" * len(TODO))
        pending = [r["id"] for r in con.execute(f"SELECT id FROM links WHERE assignee_id=? AND status IN ({q})", (user_id, *TODO))]
        con.execute("UPDATE links SET locked_by=NULL, locked_until=NULL WHERE locked_by=?", (user_id,))
        if reassign == "round_robin":
            moved = distribution.redistribute(con, pending)
        else:
            con.execute(f"UPDATE links SET assignee_id=NULL, updated_at=? WHERE assignee_id=? AND status IN ({q})",
                        (db.now(), user_id, *TODO))
            moved = {i: None for i in pending}
        db.log(con, "user_deleted", None, actor["id"] if actor else None,
               f"{u['name']}: {len(pending)} pending item(s) -> {reassign}")
    live.publish("users")
    live.publish("links", ids=pending)
    return {"ok": True, "moved": len(moved), "to_global": sum(1 for v in moved.values() if v is None)}


@app.post("/api/users/{user_id}/restore", tags=["users"])
def restore_user(user_id: int):
    with db.tx() as con:
        con.execute("UPDATE users SET deleted_at=NULL, available=1 WHERE id=?", (user_id,))
    live.publish("users")
    return {"ok": True}


# ================================================================= ingestion
@app.post("/api/ingest/preview", tags=["ingestion"])
def ingest_preview(data: PreviewIn):
    """Smart paste: extract URLs from free text and flag new / duplicate / invalid in real time."""
    items = pipeline.preview(data.text)
    return {"items": items, "new": sum(1 for i in items if i["state"] == "new"),
            "duplicates": sum(1 for i in items if i["state"] == "duplicate"),
            "invalid": sum(1 for i in items if i["state"] == "invalid")}


@app.post("/api/ingest", tags=["ingestion"])
def ingest(data: IngestIn, user: dict | None = Depends(current_user)):
    urls = list(data.urls)
    if data.text:
        urls += extract_urls(data.text)
    if data.mode == "me" and not user:
        raise HTTPException(401, "Select your profile to assign links to yourself")
    result = pipeline.ingest(urls, data.source, data.mode, user["id"] if user else None)
    worker.wake()
    return result


@app.post("/api/ingest/file", tags=["ingestion"])
async def ingest_file(file: UploadFile = File(...), mode: str = Form("auto"), user: dict | None = Depends(current_user)):
    """Any CSV / TXT file: every URL inside is extracted (backlog import)."""
    text = referential.decode(await file.read())
    result = pipeline.ingest(extract_urls(text), "file", mode, user["id"] if user else None)
    worker.wake()
    return result


# ================================================================= links
@app.get("/api/links", tags=["links"])
def list_links(
    assignee: str = "all", state: str = "todo", status: str | None = None, doc_type: str | None = None,
    date_from: str | None = None, date_to: str | None = None, q: str | None = None,
    sort: str = "created_at", order: str = "desc", limit: int = Query(200, le=1000), offset: int = 0,
    user: dict | None = Depends(current_user),
):
    where, params = ["1=1"], []
    if assignee == "me":
        where.append("l.assignee_id=?")
        params.append(user["id"] if user else -1)
    elif assignee == "global":
        where.append("l.assignee_id IS NULL")
    elif assignee.isdigit():
        where.append("l.assignee_id=?")
        params.append(int(assignee))
    if status:
        where.append("l.status=?")
        params.append(status)
    elif state == "todo":
        where.append(f"l.status IN ({','.join('?' * len(TODO))})")
        params += TODO
    elif state == "done":
        where.append(f"l.status IN ({','.join('?' * len(db.DONE_STATUSES))})")
        params += db.DONE_STATUSES
    if doc_type:
        where.append("EXISTS (SELECT 1 FROM json_each(l.metadata, '$.\"Document type\"') WHERE value = ?)")
        params.append(doc_type)
    if date_from:
        where.append("substr(l.created_at,1,10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(l.created_at,1,10) <= ?")
        params.append(date_to)
    if q:
        where.append("(l.title LIKE ? OR l.url LIKE ? OR l.summary_html LIKE ? OR l.metadata LIKE ?)")
        params += [f"%{q}%"] * 4
    col = {"created_at": "l.created_at", "title": "l.title", "status": "l.status", "updated_at": "l.updated_at",
           "assignee": "assignee_name", "approved_at": "l.approved_at"}.get(sort, "l.created_at")
    direction = "ASC" if order.lower() == "asc" else "DESC"
    sql_where = " WHERE " + " AND ".join(where)
    with db.tx() as con:
        total = con.execute("SELECT COUNT(*) FROM links l" + sql_where, params).fetchone()[0]
        rows = con.execute(LINK_SELECT + sql_where + f" ORDER BY {col} {direction}, l.id DESC LIMIT ? OFFSET ?",
                           (*params, limit, offset)).fetchall()
    return {"total": total, "items": [_decorate(db.row_to_dict(r)) for r in rows]}


@app.get("/api/links/{link_id}", tags=["links"])
def get_link(link_id: int):
    with db.tx() as con:
        d = _link(con, link_id, with_text=True)
        d["text_length"] = len(d.pop("text") or "")
        d["history"] = [dict(r) for r in con.execute(
            "SELECT e.action, e.detail, e.at, u.name AS user FROM events e LEFT JOIN users u ON u.id=e.user_id "
            "WHERE e.link_id=? ORDER BY e.id DESC LIMIT 50", (link_id,))]
        for key in ("near_duplicate_of", "duplicate_of"):
            if d.get(key):
                r = con.execute("SELECT id, title, url, status FROM links WHERE id=?", (d[key],)).fetchone()
                d[key + "_link"] = dict(r) if r else None
        d["registry"] = [dict(r) for r in con.execute(
            "SELECT id, approved_at, operator_name FROM registry WHERE link_id=? ORDER BY id DESC", (link_id,))]
    return d


@app.get("/api/links/{link_id}/text", tags=["links"])
def get_link_text(link_id: int):
    with db.tx() as con:
        r = con.execute("SELECT text FROM links WHERE id=?", (link_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Link not found")
    return {"text": r["text"] or ""}


@app.patch("/api/links/{link_id}", tags=["links"])
def update_link(link_id: int, data: LinkPatch, user: dict = Depends(require_user)):
    rejected = {}
    with db.tx() as con:
        link = _link(con, link_id)
        _editable(link)
        _check_lock(link, user)
        sets, params = [], []
        if data.title is not None:
            sets.append("title=?")
            params.append(data.title.strip())
        if data.summary_html is not None:
            sets.append("summary_html=?")
            params.append(clean_html(data.summary_html))
        if data.metadata is not None:
            clean = {}
            for name, values in data.metadata.items():
                ok, ko = referential.validate(name, values)
                clean[name] = ok
                if ko:
                    rejected[name] = ko
            sets.append("metadata=?")
            params.append(json.dumps(clean, ensure_ascii=False))
        if data.info is not None:
            sets.append("info=?")
            params.append(json.dumps({**link["info"], **data.info}, ensure_ascii=False))
        if sets:
            con.execute(f"UPDATE links SET {', '.join(sets)}, updated_at=? WHERE id=?", (*params, db.now(), link_id))
            db.log(con, "edited", link_id, user["id"], ", ".join(s.split("=")[0] for s in sets))
        out = _link(con, link_id)
    live.publish("link", id=link_id)
    out["rejected_values"] = rejected
    return out


@app.post("/api/links/{link_id}/approve", tags=["validation"])
def approve(link_id: int, user: dict = Depends(require_user)):
    with db.tx() as con:
        link = _link(con, link_id)
        if link["status"] != "to_review":
            raise HTTPException(409, f"Only links 'to review' can be approved (this one is {link['status']}).")
        _check_lock(link, user)
        missing = [f for f in config.REQUIRED_FIELDS
                   if referential.get_field(f) and not (link["metadata"].get(f) or [])]
        if missing:
            raise HTTPException(422, f"Required field(s) empty: {', '.join(missing)}")
        now = db.now()
        auto = pipeline.redactor_values(user.get("antbox_redactor"))
        if auto:   # 'Attachment redactor' = the analyst who validates
            meta = {**link["metadata"], config.REDACTOR_FIELD: auto}
            con.execute("UPDATE links SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), link_id))
        con.execute("UPDATE links SET status='approved', approved_at=?, approved_by=?, locked_by=NULL, "
                    "locked_until=NULL, updated_at=? WHERE id=?", (now, user["id"], now, link_id))
        link = _link(con, link_id)
        entry = registry.append(con, link, user)
        db.log(con, "approved", link_id, user["id"], f"registry entry #{entry['id']}")
    live.publish("link", id=link_id, status="approved")
    live.publish("registry")
    return {"link": link, "registry_id": entry["id"]}


@app.post("/api/links/{link_id}/reject", tags=["validation"])
def reject(link_id: int, data: RejectIn, user: dict = Depends(require_user)):
    with db.tx() as con:
        link = _link(con, link_id)
        _editable(link)
        _check_lock(link, user)
        con.execute("UPDATE links SET status='rejected', locked_by=NULL, locked_until=NULL, updated_at=? WHERE id=?",
                    (db.now(), link_id))
        db.log(con, "rejected", link_id, user["id"], data.reason[:500])
    live.publish("link", id=link_id, status="rejected")
    return {"ok": True}


@app.post("/api/links/{link_id}/reopen", tags=["validation"])
def reopen(link_id: int, user: dict = Depends(require_user)):
    """Back to review. The registry keeps the previous approval (immutable); a new approval adds an entry."""
    with db.tx() as con:
        link = _link(con, link_id)
        if link["status"] not in ("approved", "rejected", "duplicate"):
            raise HTTPException(409, "Link is not closed")
        if not link.get("pdf_file"):
            raise HTTPException(409, "This link was never captured: use Retry instead")
        con.execute("UPDATE links SET status='to_review', updated_at=? WHERE id=?", (db.now(), link_id))
        db.log(con, "reopened", link_id, user["id"], f"was {link['status']}")
    live.publish("link", id=link_id)
    return {"ok": True}


@app.post("/api/links/{link_id}/claim", tags=["links"])
def claim(link_id: int, user: dict = Depends(require_user)):
    return assign(link_id, AssignIn(assignee_id=user["id"]), user)


@app.post("/api/links/{link_id}/assign", tags=["links"])
def assign(link_id: int, data: AssignIn, user: dict = Depends(require_user)):
    with db.tx() as con:
        link = _link(con, link_id)
        if data.assignee_id and not con.execute("SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL",
                                                (data.assignee_id,)).fetchone():
            raise HTTPException(404, "User not found")
        con.execute("UPDATE links SET assignee_id=?, updated_at=? WHERE id=?", (data.assignee_id, db.now(), link_id))
        db.log(con, "assigned", link_id, user["id"], f"{link['assignee_name'] or 'global'} -> {data.assignee_id or 'global'}")
    live.publish("link", id=link_id)
    return {"ok": True}


@app.post("/api/links/{link_id}/retry", tags=["links"])
def retry(link_id: int, user: dict | None = Depends(current_user)):
    with db.tx() as con:
        link = _link(con, link_id)
        if link["status"] not in ("error", "queued", "to_review", "duplicate"):
            raise HTTPException(409, f"Cannot re-run a {link['status']} link")
        con.execute("UPDATE links SET status='queued', error=NULL, duplicate_of=NULL, updated_at=? WHERE id=?",
                    (db.now(), link_id))
        pipeline.enqueue(con, link_id, link["url"])
        db.log(con, "retry", link_id, user["id"] if user else None, "")
    worker.wake()
    live.publish("link", id=link_id, status="queued")
    return {"ok": True}


@app.post("/api/links/{link_id}/lock", tags=["links"])
def lock(link_id: int, user: dict = Depends(require_user)):
    """Soft lock while a user has the side panel open (renewed every 30 s by the dashboard)."""
    with db.tx() as con:
        link = _link(con, link_id)
        if link["locked"] and link["locked_by"] != user["id"]:
            return {"ok": False, "locked_by": link["locked_by_name"]}
        until = (datetime.now(timezone.utc) + timedelta(seconds=LOCK_TTL_S)).isoformat(timespec="seconds")
        con.execute("UPDATE links SET locked_by=?, locked_until=? WHERE id=?", (user["id"], until, link_id))
    if not link["locked"]:
        live.publish("lock", id=link_id, user=user["name"])
    return {"ok": True, "until": until}


@app.delete("/api/links/{link_id}/lock", tags=["links"])
def unlock(link_id: int, user: dict = Depends(require_user)):
    with db.tx() as con:
        con.execute("UPDATE links SET locked_by=NULL, locked_until=NULL WHERE id=? AND locked_by=?", (link_id, user["id"]))
    live.publish("lock", id=link_id, user=None)
    return {"ok": True}


@app.post("/api/links/bulk", tags=["links"])
def bulk(data: BulkIn, user: dict = Depends(require_user)):
    done, skipped = [], []
    for lid in data.ids:
        try:
            if data.action == "claim":
                claim(lid, user)
            elif data.action == "assign":
                assign(lid, AssignIn(assignee_id=data.assignee_id), user)
            elif data.action == "unassign":
                assign(lid, AssignIn(assignee_id=None), user)
            elif data.action == "reject":
                reject(lid, RejectIn(reason=data.reason or "bulk reject"), user)
            elif data.action == "retry":
                retry(lid, user)
            elif data.action == "distribute":
                with db.tx() as con:
                    distribution.redistribute(con, [lid])
                live.publish("link", id=lid)
            else:
                raise HTTPException(400, f"Unknown action {data.action}")
            done.append(lid)
        except HTTPException as e:
            if e.status_code == 400:
                raise
            skipped.append({"id": lid, "reason": e.detail})
    return {"done": done, "skipped": skipped}


# ================================================================= files & exports
@app.get("/api/links/{link_id}/pdf", tags=["exports"])
def link_pdf(link_id: int):
    with db.tx() as con:
        link = _link(con, link_id)
    p = exports.pdf_path(link)
    if not p:
        raise HTTPException(404, "No PDF captured yet")
    return FileResponse(p, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{p.name}"'})


@app.get("/api/links/{link_id}/export", tags=["exports"])
def export_link(link_id: int, format: str = Query("both", pattern="^(pdf|csv|both)$")):
    """PDF, AntBox import CSV, or both (zip, same base name)."""
    with db.tx() as con:
        link = _link(con, link_id)
    try:
        content, name, media = exports.single(link, format)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    if hasattr(content, "is_file"):
        return FileResponse(content, media_type=media, filename=name)
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/export", tags=["exports"])
def export_many(ids: str = "", format: str = Query("both", pattern="^(pdf|csv|both)$"), approved: bool = False):
    """AntBox package for several links (ids=1,2,3) or every approved link (approved=true)."""
    with db.tx() as con:
        if approved:
            rows = con.execute(LINK_SELECT + "WHERE l.status='approved' ORDER BY l.id").fetchall()
        else:
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
            if not id_list:
                raise HTTPException(400, "No ids given")
            rows = con.execute(LINK_SELECT + f"WHERE l.id IN ({','.join('?' * len(id_list))}) ORDER BY l.id", id_list).fetchall()
    links = [db.row_to_dict(r) for r in rows]
    if not links:
        raise HTTPException(404, "Nothing to export")
    content, name = exports.bulk(links, format)
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ================================================================= registry
@app.get("/api/registry", tags=["registry"])
def list_registry(q: str | None = None, date_from: str | None = None, date_to: str | None = None,
                  operator: str | None = None, limit: int = Query(500, le=5000)):
    where, params = ["1=1"], []
    if q:
        where.append("(title LIKE ? OR url LIKE ? OR metadata LIKE ?)")
        params += [f"%{q}%"] * 3
    if date_from:
        where.append("substr(approved_at,1,10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(approved_at,1,10) <= ?")
        params.append(date_to)
    if operator:
        where.append("operator_name = ?")
        params.append(operator)
    with db.tx() as con:
        rows = con.execute(f"SELECT * FROM registry WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                           (*params, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = json.loads(d["metadata"] or "{}")
        out.append(d)
    return out


@app.get("/api/registry/verify", tags=["registry"])
def verify_registry():
    return registry.verify()


@app.get("/api/registry/{entry_id}/{kind}", tags=["registry"])
def registry_file(entry_id: int, kind: str):
    """Permanent download links: /api/registry/12/pdf and /api/registry/12/csv"""
    if kind not in ("pdf", "csv"):
        raise HTTPException(404, "Unknown file type")
    with db.tx() as con:
        r = con.execute("SELECT pdf_file, csv_file FROM registry WHERE id=?", (entry_id,)).fetchone()
    name = r and r[f"{kind}_file"]
    if not name or not (config.REGISTRY_DIR / name).exists():
        raise HTTPException(404, "File not found")
    return FileResponse(config.REGISTRY_DIR / name, filename=name,
                        media_type="application/pdf" if kind == "pdf" else "text/csv")


# ================================================================= reports
@app.post("/api/reports", tags=["reports"])
def create_report(data: ReportIn, user: dict | None = Depends(current_user)):
    try:
        return reports.generate(data.period, data.granularity, user["id"] if user else None,
                                data.start, data.end, data.title)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/reports", tags=["reports"])
def list_reports():
    with db.tx() as con:
        return [dict(r) for r in con.execute(
            "SELECT r.id, r.title, r.period_start, r.period_end, r.granularity, r.link_count, r.created_at, "
            "r.pdf_file, r.html_file, u.name AS created_by_name FROM reports r LEFT JOIN users u ON u.id=r.created_by "
            "ORDER BY r.id DESC LIMIT 200")]


@app.get("/api/reports/{report_id}", tags=["reports"])
def get_report(report_id: int):
    r = reports.get(report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    return r


@app.get("/api/reports/{report_id}/{kind}", tags=["reports"])
def report_file(report_id: int, kind: str):
    r = reports.get(report_id)
    if not r or kind not in ("pdf", "html", "md"):
        raise HTTPException(404, "Report not found")
    if kind == "md":
        return Response(r["markdown"], media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.md"'})
    name = r[f"{kind}_file"]
    if not name or not (config.REPORT_DIR / name).exists():
        raise HTTPException(404, "File not available (PDF needs a browser: check BROWSER_CHANNEL)")
    return FileResponse(config.REPORT_DIR / name, filename=name,
                        media_type="application/pdf" if kind == "pdf" else "text/html")


# ================================================================= stats & live
@app.get("/api/stats", tags=["dashboard"])
def get_stats(user: dict | None = Depends(current_user)):
    s = stats.compute(user["id"] if user else None)
    s["worker"] = worker.queue_status()
    return s


@app.get("/api/events", tags=["dashboard"])
async def events(request: Request, user_id: int | None = None):
    """Server-Sent Events stream: the dashboard refreshes live."""
    q = live.subscribe()
    live.touch(user_id)
    live.publish("presence")

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    live.touch(user_id)
                    yield ": ping\n\n"
        finally:
            live.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ================================================================= referential & settings
@app.get("/api/referential", tags=["settings"])
def get_referential():
    return {"file": config.find_referential().name, "fields": referential.as_json()}


@app.post("/api/referential", tags=["settings"])
async def upload_referential(file: UploadFile = File(...)):
    try:
        fields = referential.replace(await file.read())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"fields": len(fields), "values": sum(len(f.values) for f in fields)}


@app.get("/api/settings", tags=["settings"])
def get_settings():
    from urllib.parse import urlsplit
    return {
        "social": social.status(),
        "llm": {"fake": config.LLM_FAKE, "configured": bool(config.LLM_URL), "model": config.LLM_MODEL,
                "host": urlsplit(config.LLM_URL).netloc if config.LLM_URL else "",
                "confidence_threshold": config.AI_CONFIDENCE_THRESHOLD, "require_evidence": config.AI_REQUIRE_EVIDENCE},
        "referential": {"file": config.find_referential().name, "fields": len(referential.fields()),
                        "required": config.REQUIRED_FIELDS},
        "antbox_csv": exports.column_rules(),
        "distribution": {"queue_cap_per_user": config.QUEUE_CAP_PER_USER},
        "notifications": {"google_chat": bool(config.GOOGLE_CHAT_WEBHOOK), "digest_hour": config.DIGEST_HOUR},
        "csv": {"separator": config.CSV_SEPARATOR, "multi_separator": config.CSV_MULTI_SEPARATOR, "values": config.CSV_VALUES},
        "worker": worker.queue_status(),
    }


@app.put("/api/settings/social", tags=["settings"])
def set_social(data: ToggleIn):
    social.set_enabled(data.enabled)
    return social.status()


@app.post("/api/settings/social/login/{network}", tags=["settings"])
def social_login(network: str):
    try:
        status = capture.login_network(network)
    except social.SocialSessionError as e:
        raise HTTPException(422, str(e)) from e
    status["requeued"] = pipeline.requeue_social_errors()   # failed posts are captured again
    worker.wake()
    return status


@app.post("/api/settings/llm/test", tags=["settings"])
def test_llm():
    try:
        return llm.test_connection()
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/settings/notify/test", tags=["settings"])
def test_notify():
    try:
        return {"sent": notify.send_digest()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


# ================================================================= dashboard (built Angular app)
@app.get("/{path:path}", include_in_schema=False)
def dashboard(path: str):
    if path.startswith("api/") or path == "api":
        raise HTTPException(404, "Unknown API route")
    root = config.DASHBOARD_DIR.resolve()
    index = root / "index.html"
    if not index.exists():
        raise HTTPException(404, "Dashboard not built: run `npx ng serve` in frontend/ and open http://localhost:4200")
    f = (root / path).resolve()
    if path and f.is_file() and root in f.parents and f.name != "index.html":
        # hashed bundles (main-XXXX.js) never change: cache them; a new version has a new name
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})
    if path.endswith((".js", ".css", ".map", ".ico", ".png", ".svg", ".woff2")):
        raise HTTPException(404, "File not found")   # an old bundle: never answer with index.html
    # index.html is never cached, so the browser always loads the current version after an update
    return FileResponse(index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
