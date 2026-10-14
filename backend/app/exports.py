"""Exports for AntBox: the PDF + an import CSV following the AntBox connector template.

CSV columns (exact template order, ';' separated, one row per document):

  File Name                   PDF file name                      (use by IA)
  URL                         source URL                         (use by IA)
  Check                       empty, filled by the connector     (use by Connector)
  Attachment Clearance Level  constant ANTBOX_CLEARANCE_LEVEL    (Public)
  Attachment Co-redactor      constant ANTBOX_CO_REDACTOR        (Group: Co-Writer)
  Document type               press article / social network, or brochure / manufacturer website
  Domain                      set by the analyst if wanted       (too complex for the AI)
  Attachment redactor         from the analyst profile           (Automatic)
  Meeting or Forum            empty                              (Not used)
  Year, Parts/Modules, Optionnal, OperationType, Vehicles   filled by the LLM from the referential

All approved articles go into ONE connector file, data/connector/antbox_connector.csv:
approving an article adds its row (re-approving replaces it, reopening removes it).
After an import into AntBox, the file can be archived and a new one started.
"""
import csv
import io
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from . import capture, config, referential

COLUMNS = [
    "File Name", "URL", "Check", "Attachment Clearance Level", "Attachment Co-redactor", "Document type",
    "Domain", "Attachment redactor", "Meeting or Forum", "Year", "Parts/Modules", "Optionnal",
    "OperationType", "Vehicles",
]


def base_name(link: dict) -> str:
    """Shared name of the PDF and the CSV of one link: 00012_article-title."""
    if link.get("pdf_file"):
        return Path(link["pdf_file"]).stem
    return f"{link['id']:05d}_{capture.slug(link.get('title') or link['url'])}"


def field_values(link: dict, field_name: str) -> str:
    labels = (link.get("metadata") or {}).get(field_name) or []
    if config.CSV_VALUES == "id":
        labels = [referential.value_id(field_name, x) or x for x in labels]
    return config.CSV_MULTI_SEPARATOR.join(str(v) for v in labels if v)


def row(link: dict) -> list[str]:
    values = {
        "File Name": f"{base_name(link)}.pdf",
        "URL": link["url"],
        "Check": "",
        "Attachment Clearance Level": config.ANTBOX_CLEARANCE_LEVEL,
        "Attachment Co-redactor": config.ANTBOX_CO_REDACTOR,
    }
    out = []
    for col in COLUMNS:
        if col in values:
            out.append(values[col])
        elif col in config.UNUSED_FIELDS:
            out.append("")
        else:
            out.append(field_values(link, col))
    return out


def csv_bytes(links: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=config.CSV_SEPARATOR)
    w.writerow(COLUMNS)
    for link in links:
        w.writerow(row(link))
    return buf.getvalue().encode(config.CSV_ENCODING, errors="replace")


def column_rules() -> list[dict]:
    """How each column is filled (shown in Settings)."""
    rules = {
        "File Name": "PDF file name", "URL": "Source URL", "Check": "Empty (filled by the connector)",
        "Attachment Clearance Level": f"Constant: {config.ANTBOX_CLEARANCE_LEVEL}",
        "Attachment Co-redactor": f"Constant: {config.ANTBOX_CO_REDACTOR}",
    }
    out = []
    for col in COLUMNS:
        f = referential.get_field(col)
        if col in rules:
            how = rules[col]
        elif col in config.UNUSED_FIELDS:
            how = "Not used (empty)"
        elif col == config.REDACTOR_FIELD:
            how = "Automatic: AntBox redactor of the approving analyst"
        elif col in config.AI_FIELDS:
            how = "LLM, from the referential (analyst completes if the AI was not certain)"
        else:
            how = "Analyst (optional)"
        if col in config.REQUIRED_FIELDS:
            how += " — MANDATORY"
        out.append({"column": col, "rule": how, "required": col in config.REQUIRED_FIELDS, "referential_field": (f.source_name or f.name) if f else None,
                    "values": len(f.values) if f else 0})
    return out


# ----------------------------------------------------------------- the single connector file
_connector_lock = threading.Lock()


def _read_connector() -> list[list[str]]:
    f = config.CONNECTOR_CSV
    if not f.exists():
        return []
    with open(f, encoding=config.CSV_ENCODING, newline="") as fh:
        rows = list(csv.reader(fh, delimiter=config.CSV_SEPARATOR))
    return [r for r in rows[1:] if any(r)]       # without the header


def _write_connector(rows: list[list[str]]) -> None:
    tmp = config.CONNECTOR_CSV.with_suffix(".tmp")
    with open(tmp, "w", encoding=config.CSV_ENCODING, newline="") as fh:
        w = csv.writer(fh, delimiter=config.CSV_SEPARATOR)
        w.writerow(COLUMNS)
        w.writerows(rows)
    tmp.replace(config.CONNECTOR_CSV)


def connector_add(link: dict) -> int:
    """Add (or replace) the article's row in the connector file. Returns the number of rows."""
    new = row(link)
    with _connector_lock:
        rows = [r for r in _read_connector() if r[:1] != new[:1]]   # same File Name -> replaced
        rows.append(new)
        _write_connector(rows)
        return len(rows)


def connector_remove(link: dict) -> None:
    name = f"{base_name(link)}.pdf"
    with _connector_lock:
        rows = _read_connector()
        kept = [r for r in rows if r[:1] != [name]]
        if len(kept) != len(rows):
            _write_connector(kept)


def connector_info() -> dict:
    rows = _read_connector()
    archives = sorted((config.CONNECTOR_DIR / "archive").glob("*.csv"), reverse=True)
    return {"file": config.CONNECTOR_CSV.name, "rows": len(rows),
            "updated_at": datetime.fromtimestamp(config.CONNECTOR_CSV.stat().st_mtime).isoformat(timespec="minutes")
            if config.CONNECTOR_CSV.exists() else None,
            "archives": [a.name for a in archives[:20]]}


def connector_bytes() -> bytes:
    with _connector_lock:
        if not config.CONNECTOR_CSV.exists():
            _write_connector([])
        return config.CONNECTOR_CSV.read_bytes()


def connector_package() -> tuple[bytes, str]:
    """Zip: the connector CSV + the PDF of every row."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(config.CONNECTOR_CSV.name, connector_bytes())
        for r in _read_connector():
            pdf = config.PDF_DIR / r[0]
            if r and pdf.exists():
                z.write(pdf, r[0])
    return buf.getvalue(), f"antbox_connector_{stamp}.zip"


def connector_archive() -> str | None:
    """After an import into AntBox: keep a dated copy and start an empty file."""
    with _connector_lock:
        if not config.CONNECTOR_CSV.exists() or not _read_connector():
            return None
        name = f"antbox_connector_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
        shutil.copyfile(config.CONNECTOR_CSV, config.CONNECTOR_DIR / "archive" / name)
        _write_connector([])
        return name


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
    buf = io.BytesIO()   # both: same base name, one zip
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}.csv", csv_bytes([link]))
        p = pdf_path(link)
        if p:
            z.write(p, f"{name}.pdf")
    return buf.getvalue(), f"{name}.zip", "application/zip"


def bulk(links: list[dict], fmt: str = "both") -> tuple[bytes, str]:
    """Selection package: one connector CSV for the selected links + their PDFs."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if fmt in ("csv", "both"):
            z.writestr(config.CONNECTOR_CSV.name, csv_bytes(links))
        if fmt in ("pdf", "both"):
            for link in links:
                p = pdf_path(link)
                if p:
                    z.write(p, f"{base_name(link)}.pdf")
    return buf.getvalue(), f"antbox_export_{stamp}.zip"
