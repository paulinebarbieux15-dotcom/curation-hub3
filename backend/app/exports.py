"""Exports: PDF, CSV (AntBox metadata) or both, synchronized under the same name.

CSV columns:
- fixed columns (FIXED_COLUMNS below);
- then ONE column per AntBox referential field, in referential order.
Theme tags are dashboard-only and are NEVER exported. The summary is exported as
plain text (the WYSIWYG formatting is for the dashboard).

>>> ADAPT: column names/order must match a real AntBox import file.
>>> .env settings: CSV_SEPARATOR, CSV_MULTI_SEPARATOR, CSV_VALUES (label or id).
"""
import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

from . import capture, config, llm, referential


def _cell(value) -> str:
    if isinstance(value, list):
        return config.CSV_MULTI_SEPARATOR.join(str(v) for v in value if v)
    return "" if value is None else str(value)


def base_name(link: dict) -> str:
    """Shared file name of the PDF and the CSV of one link: 00012_article-title."""
    if link.get("pdf_file"):
        return Path(link["pdf_file"]).stem
    return f"{link['id']:05d}_{capture.slug(link.get('title') or link['url'])}"


FIXED_COLUMNS = [
    ("file", lambda l: f"{base_name(l)}.pdf"),
    ("title", lambda l: l.get("title") or ""),
    ("source_url", lambda l: l["url"]),
    ("published_date", lambda l: (l.get("info") or {}).get("published_date") or ""),
    ("author", lambda l: (l.get("info") or {}).get("author") or ""),
    ("source", lambda l: (l.get("info") or {}).get("source") or ""),
    ("summary", lambda l: llm.html_to_text(l.get("summary_html") or "")),
]


def field_values(link: dict, field_name: str) -> str:
    labels = (link.get("metadata") or {}).get(field_name) or []
    if config.CSV_VALUES == "id":
        labels = [referential.value_id(field_name, x) or x for x in labels]
    return _cell(labels)


def columns() -> list[tuple]:
    dynamic = [(f.name, (lambda n: lambda l: field_values(l, n))(f.name)) for f in referential.fields()]
    return FIXED_COLUMNS + dynamic


def csv_bytes(links: list[dict]) -> bytes:
    cols = columns()
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=config.CSV_SEPARATOR)
    w.writerow([name for name, _ in cols])
    for link in links:
        w.writerow([fn(link) for _, fn in cols])
    return buf.getvalue().encode(config.CSV_ENCODING, errors="replace")


def pdf_path(link: dict) -> Path | None:
    if link.get("pdf_file"):
        p = config.PDF_DIR / link["pdf_file"]
        if p.exists():
            return p
    return None


def single(link: dict, fmt: str) -> tuple[bytes | Path, str, str]:
    """(content or path, download name, media type) for one link."""
    name = base_name(link)
    if fmt == "pdf":
        p = pdf_path(link)
        if not p:
            raise FileNotFoundError("No PDF captured for this link")
        return p, f"{name}.pdf", "application/pdf"
    if fmt == "csv":
        return csv_bytes([link]), f"{name}.csv", "text/csv"
    # both, synchronized: same base name, one zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}.csv", csv_bytes([link]))
        p = pdf_path(link)
        if p:
            z.write(p, f"{name}.pdf")
    return buf.getvalue(), f"{name}.zip", "application/zip"


def bulk(links: list[dict], fmt: str = "both") -> tuple[bytes, str]:
    """AntBox package: one metadata.csv for all links + every PDF."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if fmt in ("csv", "both"):
            z.writestr("metadata.csv", csv_bytes(links))
        if fmt in ("pdf", "both"):
            for link in links:
                p = pdf_path(link)
                if p:
                    z.write(p, f"{base_name(link)}.pdf")
    return buf.getvalue(), f"antbox_export_{stamp}.zip"
