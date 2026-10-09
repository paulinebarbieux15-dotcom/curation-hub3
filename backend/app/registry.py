"""Immutable registry of approved links.

On approval:
- the PDF is copied and the CSV written into data/registry/ (permanent files,
  never overwritten: each approval gets its own entry id in the file name);
- an entry is appended to the `registry` table with SHA-256 of both files and a
  hash chain (entry_hash = sha256(prev_hash + entry content)).
SQLite triggers refuse any UPDATE/DELETE, and verify() recomputes the chain and
the file hashes, so any tampering is visible.
"""
import hashlib
import json
import shutil

from . import config, db, exports, llm

GENESIS = "0" * 64


def _sha256_file(path) -> str | None:
    if not path or not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry_hash(prev: str, e: dict) -> str:
    content = json.dumps({k: e[k] for k in ("link_id", "approved_at", "approved_by", "operator_name", "title",
                                            "url", "metadata", "summary_text", "pdf_sha256", "csv_sha256")},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev + content).encode()).hexdigest()


def append(con, link: dict, user: dict) -> dict:
    """Write the permanent files and append the registry entry (inside the caller's transaction)."""
    last = con.execute("SELECT id, entry_hash FROM registry ORDER BY id DESC LIMIT 1").fetchone()
    prev = last["entry_hash"] if last else GENESIS
    entry_no = (last["id"] if last else 0) + 1
    base = f"R{entry_no:05d}_{exports.base_name(link)}"

    pdf_name = csv_name = None
    src = exports.pdf_path(link)
    if src:
        pdf_name = f"{base}.pdf"
        shutil.copyfile(src, config.REGISTRY_DIR / pdf_name)
    csv_name = f"{base}.csv"
    (config.REGISTRY_DIR / csv_name).write_bytes(exports.csv_bytes([link]))

    e = {
        "link_id": link["id"],
        "approved_at": db.now(),
        "approved_by": user["id"],
        "operator_name": user["name"],
        "title": link.get("title") or "",
        "url": link["url"],
        "metadata": json.dumps({"antbox": link.get("metadata") or {}, "info": link.get("info") or {}},
                               ensure_ascii=False, sort_keys=True),
        "summary_text": llm.html_to_text(link.get("summary_html") or ""),
        "pdf_file": pdf_name,
        "csv_file": csv_name,
        "pdf_sha256": _sha256_file(config.REGISTRY_DIR / pdf_name) if pdf_name else None,
        "csv_sha256": _sha256_file(config.REGISTRY_DIR / csv_name),
        "prev_hash": prev,
    }
    e["entry_hash"] = _entry_hash(prev, e)
    cols = ",".join(e)
    cur = con.execute(f"INSERT INTO registry ({cols}) VALUES ({','.join('?' * len(e))})", tuple(e.values()))
    return {"id": cur.lastrowid, **e}


def verify() -> dict:
    """Recompute the chain and every file hash."""
    problems, prev, n = [], GENESIS, 0
    with db.tx() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM registry ORDER BY id")]
    for r in rows:
        n += 1
        if r["prev_hash"] != prev:
            problems.append({"id": r["id"], "problem": "chain broken (previous hash mismatch)"})
        if _entry_hash(r["prev_hash"], r) != r["entry_hash"]:
            problems.append({"id": r["id"], "problem": "entry content altered"})
        if r["pdf_file"] and _sha256_file(config.REGISTRY_DIR / r["pdf_file"]) != r["pdf_sha256"]:
            problems.append({"id": r["id"], "problem": "PDF file missing or altered"})
        if r["csv_file"] and _sha256_file(config.REGISTRY_DIR / r["csv_file"]) != r["csv_sha256"]:
            problems.append({"id": r["id"], "problem": "CSV file missing or altered"})
        prev = r["entry_hash"]
    return {"ok": not problems, "entries": n, "problems": problems, "last_hash": prev}
