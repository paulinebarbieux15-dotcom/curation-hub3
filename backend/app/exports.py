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

A package holds one CSV per document type (connector_press_article.csv,
connector_commercial_brochure.csv) plus every PDF.
"""
import csv
import io
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
            how = "LLM, from the referential (empty if not certain)"
        else:
            how = "Analyst (optional)"
        out.append({"column": col, "rule": how, "referential_field": (f.source_name or f.name) if f else None,
                    "values": len(f.values) if f else 0})
    return out


def connector_file(doc_type: str) -> str:
    k = referential.key(doc_type)
    if "press" in k or "social" in k:
        return "connector_press_article.csv"
    if "brochure" in k or "manufacturer" in k:
        return "connector_commercial_brochure.csv"
    return f"connector_{capture.slug(doc_type or 'other', 40)}.csv"


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
    """AntBox package: one import CSV per document type (connector) + every PDF."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    groups: dict[str, list[dict]] = {}
    for link in links:
        doc = ((link.get("metadata") or {}).get("Document type") or [""])[0]
        groups.setdefault(connector_file(doc), []).append(link)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if fmt in ("csv", "both"):
            for fname, group in groups.items():
                z.writestr(fname, csv_bytes(group))
        if fmt in ("pdf", "both"):
            for link in links:
                p = pdf_path(link)
                if p:
                    z.write(p, f"{base_name(link)}.pdf")
    return buf.getvalue(), f"antbox_import_{stamp}.zip"
