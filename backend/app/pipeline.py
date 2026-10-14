"""Ingestion and processing of a link.

ingest()   paste / manual / CSV  -> validation, anti-duplicates, distribution, job queued
process()  run by the worker     -> capture -> duplicate checks -> AI auto-fill -> 'to_review'
"""
import json
import logging
import re

from . import capture, config, db, distribution, live, llm, social
from .urls import clean_url, domain_of, extract_urls, hamming, is_valid_url, normalize_url, simhash

log = logging.getLogger("curation")


# ----------------------------------------------------------------- automatic fields
def redactor_values(redactor: str | None) -> list[str]:
    """'Attachment redactor' column: the analyst's AntBox redactor, if it is in the referential."""
    if not redactor:
        return []
    from . import referential
    ok, _ = referential.validate(config.REDACTOR_FIELD, [redactor])
    return ok


def default_document_type(url: str) -> list[str]:
    """Social network posts are always 'Press articles / Social Network'."""
    from . import referential
    if social.is_social(url) and config.DOCUMENT_TYPES:
        ok, _ = referential.validate("Document type", [config.DOCUMENT_TYPES[0]])
        return ok or [config.DOCUMENT_TYPES[0]]
    return []


# ----------------------------------------------------------------- preview / ingest
def _existing(con, norm: str):
    return con.execute(
        "SELECT l.id, l.title, l.status, l.assignee_id, u.name AS assignee FROM links l "
        "LEFT JOIN users u ON u.id = l.assignee_id "
        "WHERE l.normalized_url=? OR l.final_url=? OR l.canonical_url=? ORDER BY l.id LIMIT 1",
        (norm, norm, norm)).fetchone()


def preview(text: str) -> list[dict]:
    """Live check of pasted text: every URL found, flagged new / duplicate / invalid."""
    candidates = extract_urls(text)
    # words that look like an attempt at a URL but are not valid ("http://bad", "www.")
    for word in (text or "").split():
        w = word.strip(".,;:!?()[]<>\"'")
        if re.match(r"^(https?:|www\.)", w, re.I) and not extract_urls(w):
            candidates.append(w)
    out, seen = [], set()
    with db.tx() as con:
        for url in candidates:
            if not is_valid_url(url):
                out.append({"url": url, "state": "invalid", "message": "Not a valid URL"})
                continue
            norm = normalize_url(url)
            if norm in seen:
                out.append({"url": url, "normalized": norm, "state": "repeat", "message": "Repeated in this paste"})
                continue
            seen.add(norm)
            ex = _existing(con, norm)
            if ex:
                out.append({"url": url, "normalized": norm, "state": "duplicate", "existing": dict(ex),
                            "message": f"Already in the database (#{ex['id']}, {ex['status']})"})
            else:
                out.append({"url": url, "normalized": norm, "state": "new",
                            "social": social.is_social(url), "domain": domain_of(url)})
    return out


def enqueue(con, link_id: int, url: str) -> None:
    con.execute("UPDATE jobs SET status='done', finished_at=? WHERE link_id=? AND status IN ('pending','failed')",
                (db.now(), link_id))
    con.execute(
        "INSERT INTO jobs (link_id, domain, is_social, status, attempts, run_after, created_at) "
        "VALUES (?,?,?,?,0,?,?)",
        (link_id, domain_of(url), int(social.is_social(url)), "pending", db.now(), db.now()))


def ingest(urls: list[str], source: str = "manual", mode: str = "auto", user_id: int | None = None) -> dict:
    """mode: 'auto' (round-robin), 'me' (current user), 'global' (shared queue), or a user id."""
    created, duplicates, invalid = [], [], []
    with db.tx() as con:
        for raw in urls:
            url = (raw or "").strip()
            if not url:
                continue
            if "://" not in url and is_valid_url(url):
                url = "https://" + url
            if not is_valid_url(url):
                invalid.append(url)
                continue
            url = clean_url(url)
            norm = normalize_url(url)
            ex = _existing(con, norm)
            if ex:
                duplicates.append({"url": url, **dict(ex)})
                continue
            if mode == "me" and user_id:
                assignee = user_id
            elif mode == "global":
                assignee = None
            elif str(mode).isdigit():
                assignee = int(mode)
            else:
                assignee = distribution.next_assignee(con)
            src = "social" if social.is_social(url) else source
            cur = con.execute(
                "INSERT INTO links (url, normalized_url, source, status, assignee_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (url, norm, src, "queued", assignee, db.now(), db.now()))
            lid = cur.lastrowid
            db.log(con, "added", lid, user_id, f"{src}; assigned to {assignee or 'global queue'}")
            enqueue(con, lid, url)
            created.append({"id": lid, "url": url, "assignee_id": assignee})
    if created:
        live.publish("links", ids=[c["id"] for c in created])
    return {"created": created, "duplicates": duplicates, "invalid": invalid}


def requeue_social_errors() -> int:
    """After a successful social login: re-run every social link that failed (login problems)."""
    with db.tx() as con:
        rows = con.execute("SELECT id, url FROM links WHERE status='error' AND source='social'").fetchall()
        for r in rows:
            con.execute("UPDATE links SET status='queued', error=NULL, updated_at=? WHERE id=?", (db.now(), r["id"]))
            enqueue(con, r["id"], r["url"])
            db.log(con, "retry", r["id"], None, "social session restored")
    if rows:
        live.publish("links", ids=[r["id"] for r in rows])
    return len(rows)


# ----------------------------------------------------------------- few-shot
_WORDS = re.compile(r"[a-z]{4,}")


def few_shot(con, title: str, text: str) -> list[dict]:
    """The most similar validated records (word overlap), used as tagging examples."""
    n = config.AI_FEW_SHOT_EXAMPLES
    if n <= 0:
        return []
    target = set(_WORDS.findall(f"{title} {text[:1500]}".lower()))
    if not target:
        return []
    scored = []
    for r in con.execute("SELECT title, metadata, substr(text,1,1500) AS t FROM links "
                         "WHERE status='approved' ORDER BY approved_at DESC LIMIT 300"):
        words = set(_WORDS.findall(f"{r['title'] or ''} {r['t'] or ''}".lower()))
        if words:
            score = len(target & words) / len(target | words)
            scored.append((score, {"title": r["title"], "metadata": json.loads(r["metadata"] or "{}")}))
    scored.sort(key=lambda x: -x[0])
    return [e for s, e in scored[:n] if s > 0.05]


# ----------------------------------------------------------------- processing
class PermanentError(Exception):
    """Errors that retrying will not fix (configuration, social login)."""


def process(link_id: int) -> str:
    """Returns the resulting status. Raises on failure (the worker decides about retries)."""
    with db.tx() as con:
        row = con.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
        if not row or row["status"] not in ("queued", "error", "processing"):
            return row["status"] if row else "missing"
        url = row["url"]
        con.execute("UPDATE links SET status='processing', error=NULL, updated_at=? WHERE id=?", (db.now(), link_id))
    live.publish("link", id=link_id, status="processing")

    try:
        cap = capture.capture(url, link_id)
    except social.SocialSessionError as e:
        raise PermanentError(str(e)) from e

    final_norm = normalize_url(cap.final_url) if cap.final_url else None
    canon_norm = normalize_url(cap.canonical_url) if cap.canonical_url and is_valid_url(cap.canonical_url) else None
    fingerprint = simhash(cap.text)

    with db.tx() as con:
        # exact duplicate revealed by redirects / canonical URL
        keys = [k for k in (final_norm, canon_norm) if k]
        dup = None
        if keys:
            q = ",".join("?" * len(keys))
            dup = con.execute(
                f"SELECT id FROM links WHERE id<>? AND status<>'duplicate' AND "
                f"(normalized_url IN ({q}) OR final_url IN ({q}) OR canonical_url IN ({q})) ORDER BY id LIMIT 1",
                (link_id, *keys, *keys, *keys)).fetchone()
        if dup:
            con.execute("UPDATE links SET status='duplicate', duplicate_of=?, final_url=?, canonical_url=?, "
                        "title=?, pdf_file=?, updated_at=? WHERE id=?",
                        (dup["id"], final_norm, canon_norm, cap.title, cap.pdf_file, db.now(), link_id))
            db.log(con, "duplicate", link_id, None, f"same page as #{dup['id']} (redirect/canonical)")
            con.execute("UPDATE jobs SET status='done', finished_at=? WHERE link_id=? AND status='running'",
                        (db.now(), link_id))
            live.publish("link", id=link_id, status="duplicate")
            return "duplicate"

        near = None
        if fingerprint:
            for r in con.execute("SELECT id, simhash FROM links WHERE id<>? AND simhash IS NOT NULL AND simhash<>'' "
                                 "AND status<>'duplicate' ORDER BY id DESC LIMIT 3000", (link_id,)):
                if hamming(fingerprint, r["simhash"]) <= config.NEAR_DUPLICATE_MAX_DISTANCE:
                    near = r["id"]
                    break
        examples = few_shot(con, cap.title, cap.text)
        assignee = row["assignee_id"]
        redactor = None
        if assignee:
            u = con.execute("SELECT antbox_redactor, name FROM users WHERE id=?", (assignee,)).fetchone()
            redactor = u and (u["antbox_redactor"] or None)

    ai = llm.analyze(cap.title, cap.text, url, examples)

    metadata = dict(ai["metadata"])
    if not metadata.get("Document type"):
        metadata["Document type"] = default_document_type(url)
    if redactor_values(redactor):
        metadata[config.REDACTOR_FIELD] = redactor_values(redactor)
    with db.tx() as con:
        con.execute(
            """UPDATE links SET status='to_review', title=?, text=?, pdf_file=?, final_url=?, canonical_url=?,
               simhash=?, near_duplicate_of=?, summary_html=?, metadata=?, metadata_ai=?, info=?,
               ai_details=?, error=NULL, updated_at=? WHERE id=?""",
            (ai["title"], cap.text, cap.pdf_file, final_norm, canon_norm, fingerprint, near, ai["summary_html"],
             json.dumps(metadata, ensure_ascii=False), json.dumps(ai["metadata"], ensure_ascii=False),
             json.dumps(ai["info"], ensure_ascii=False), json.dumps(ai["details"], ensure_ascii=False),
             db.now(), link_id))
        dropped = sum(1 for v in ai["details"].values() for d in v if not d["kept"])
        db.log(con, "ai_filled", link_id, None,
               f"AI auto-fill; {dropped} uncertain value(s) left empty" + (f"; near-duplicate of #{near}" if near else ""))
    live.publish("link", id=link_id, status="to_review")
    return "to_review"
