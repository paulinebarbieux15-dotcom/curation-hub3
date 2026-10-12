"""Executive reports over a period.

period: '7d', '30d' (1 month) or 'custom' (start/end dates)
granularity: 'general' (one list), 'doctype' (grouped by document type), 'user' (grouped by analyst)

The LLM writes the synthesis (citing articles as [n]); the list of links with their
summaries is appended by code, so every link is always present and correct.
Output: Markdown (stored), HTML and PDF (data/reports/).
"""
import html
import json
import re
from datetime import date, datetime, time, timedelta, timezone

import markdown as md

from . import capture, config, db, llm

CSS = """
body{font-family:Inter,Segoe UI,Arial,sans-serif;color:#1d2433;max-width:820px;margin:32px auto;padding:0 24px;line-height:1.55}
h1{font-size:26px;margin:0 0 4px} .meta{color:#667085;font-size:13px;margin-bottom:24px}
h2{font-size:18px;border-bottom:2px solid #e4e7ec;padding-bottom:4px;margin-top:28px}
h3{font-size:15px;margin:20px 0 6px;color:#3538cd}
a{color:#3538cd;text-decoration:none} li{margin:4px 0}
.item{margin:10px 0 14px} .item p{margin:2px 0;color:#344054;font-size:14px}
"""


def period_bounds(period: str, start: str | None = None, end: str | None = None) -> tuple[datetime, datetime, str]:
    today = date.today()
    if period == "custom":
        if not start or not end:
            raise ValueError("Custom period needs start and end dates")
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        if e < s:
            raise ValueError("End date is before start date")
    else:
        days = {"7d": 7, "30d": 30, "1m": 30}.get(period)
        if not days:
            raise ValueError("period must be 7d, 30d or custom")
        s, e = today - timedelta(days=days - 1), today
    start_dt = datetime.combine(s, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(e, time.max, tzinfo=timezone.utc)
    return start_dt, end_dt, f"{s:%d %b %Y} – {e:%d %b %Y}"


def _items(con, start: datetime, end: datetime) -> list[dict]:
    rows = con.execute(
        "SELECT l.id, l.title, l.url, l.summary_html, l.metadata, l.approved_at, u.name AS analyst "
        "FROM links l LEFT JOIN users u ON u.id = l.approved_by "
        "WHERE l.status='approved' AND l.approved_at BETWEEN ? AND ? ORDER BY l.approved_at",
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))).fetchall()
    return [{"n": i + 1, "id": r["id"], "title": r["title"] or r["url"], "url": r["url"],
             "summary": llm.html_to_text(r["summary_html"] or ""), "doc_type": ((json.loads(r["metadata"] or "{}").get("Document type") or [""])[0]),
             "analyst": r["analyst"] or "Unknown", "approved_at": r["approved_at"]} for i, r in enumerate(rows)]


def _link_refs(text: str, items: list[dict]) -> str:
    """[3] -> [[3]](url) ; unknown numbers are removed (no invented citations)."""
    by_n = {it["n"]: it for it in items}

    def rep(m):
        it = by_n.get(int(m.group(1)))
        return f"[[{it['n']}]]({it['url']})" if it else ""
    return re.sub(r"\[(\d{1,4})\](?!\()", rep, text)


def _links_section(items: list[dict], granularity: str) -> str:
    def block(it):
        s = it["summary"].replace("\n", " ").strip()
        return f"- **[{it['n']}] [{_md_escape(it['title'])}]({it['url']})** — {s}"

    out = ["\n\n## Links and summaries"]
    if granularity == "doctype":
        groups: dict[str, list] = {}
        for it in items:
            groups.setdefault(it["doc_type"] or "Unclassified", []).append(it)
        for t in sorted(groups):
            out += ["", f"### {t} ({len(groups[t])})"] + [block(it) for it in groups[t]]
    elif granularity == "user":
        groups = {}
        for it in items:
            groups.setdefault(it["analyst"], []).append(it)
        for u in sorted(groups):
            out += ["", f"### {u} ({len(groups[u])})"] + [block(it) for it in groups[u]]
    else:
        out += [""] + [block(it) for it in items]
    return "\n".join(out)


def _md_escape(s: str) -> str:
    return re.sub(r"([\[\]*_`])", r"\\\1", s)


def _stats_section(items: list[dict], granularity: str) -> str:
    if granularity == "doctype":
        counts: dict[str, int] = {}
        for it in items:
            t = it["doc_type"] or "Unclassified"
            counts[t] = counts.get(t, 0) + 1
        head = "| Document type | Articles |\n|---|---|"
    elif granularity == "user":
        counts = {}
        for it in items:
            counts[it["analyst"]] = counts.get(it["analyst"], 0) + 1
        head = "| Analyst | Articles validated |\n|---|---|"
    else:
        return ""
    rows = "\n".join(f"| {k} | {v} |" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
    return f"\n\n## Breakdown\n\n{head}\n{rows}"


def generate(period: str, granularity: str, user_id: int | None, start: str | None = None,
             end: str | None = None, title: str | None = None) -> dict:
    if granularity not in ("general", "doctype", "user"):
        raise ValueError("granularity must be general, doctype or user")
    s, e, label = period_bounds(period, start, end)
    with db.tx() as con:
        items = _items(con, s, e)
    title = title or f"Technology watch report — {label}"
    if items:
        synthesis = _link_refs(llm.write_report(items, label, granularity), items)
    else:
        synthesis = "## Executive summary\nNo article was validated over this period."
    body = synthesis + _stats_section(items, granularity) + (_links_section(items, granularity) if items else "")
    markdown_text = f"# {title}\n\n*{len(items)} validated article(s) · {label} · { {"general": "general view", "doctype": "grouped by document type", "user": "grouped by analyst"}[granularity] }*\n\n{body}\n"

    html_body = md.markdown(markdown_text, extensions=["tables", "sane_lists"])
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{html_body}</body></html>")

    with db.tx() as con:
        cur = con.execute(
            "INSERT INTO reports (title, period_start, period_end, granularity, link_count, created_by, created_at, markdown) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, s.date().isoformat(), e.date().isoformat(), granularity, len(items), user_id, db.now(), markdown_text))
        rid = cur.lastrowid
    base = f"report_{rid:04d}_{capture.slug(label, 40)}"
    html_file = f"{base}.html"
    (config.REPORT_DIR / html_file).write_text(page, encoding="utf-8")
    pdf_file = f"{base}.pdf" if capture.html_to_pdf(page, config.REPORT_DIR / f"{base}.pdf") else None
    with db.tx() as con:
        con.execute("UPDATE reports SET html_file=?, pdf_file=? WHERE id=?", (html_file, pdf_file, rid))
        db.log(con, "report", None, user_id, title)
    return get(rid)


def get(report_id: int) -> dict | None:
    with db.tx() as con:
        r = con.execute("SELECT r.*, u.name AS created_by_name FROM reports r LEFT JOIN users u ON u.id=r.created_by "
                        "WHERE r.id=?", (report_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["html"] = md.markdown(d["markdown"], extensions=["tables", "sane_lists"])
    return d
