"""AntBox metadata referential (data/referentiel_antbox.txt or any antbox/referential .csv/.txt).

Each line of the AntBox export describes ONE allowed value:

    Metadata | Metadata id | Type | Cardinality | Value | Value id | Position
    Domain     5             List   Multi-value   Domain_02\\Sub_Domain_01   65   1

For every field (Document type, Domain, Aircrafts...) we derive:
- whether it accepts one value (Single-value) or several (Multi-value);
- its CLOSED list of values, with their AntBox id.

Hierarchical values use "\\" (Domain\\Sub-domain). For display and for the LLM
they are written with " > " (readable, no JSON escaping issue).
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from . import config

HIERARCHY_SEP = "\\"
DISPLAY_SEP = " > "

# line copied with spaces instead of tabs / semicolons
_SPACED_LINE = re.compile(
    r"^(?P<meta>.+?)\s+(?P<meta_id>\d+)\s+(?P<type>\S+)\s+(?P<card>Single-value|Multi-value)\s+"
    r"(?P<value>.+?)\s+(?P<val_id>\d+)\s+(?P<pos>\d+)\s*$"
)


@dataclass
class Value:
    label: str        # as AntBox writes it: "Domain_02\Sub_Domain_01"
    id: str
    position: int

    @property
    def display(self) -> str:
        return " ".join(self.label.split()).replace(HIERARCHY_SEP, DISPLAY_SEP)


@dataclass
class Field:
    name: str
    id: str
    multi: bool
    values: list[Value] = field(default_factory=list)

    source_name: str = ""     # name in the referential file, when it differs from the CSV column

    @property
    def ai(self) -> bool:
        return self.name in config.AI_FIELDS

    @property
    def mode(self) -> str:
        """ai (LLM), auto (from the analyst profile), unused, or manual."""
        if self.name in config.UNUSED_FIELDS:
            return "unused"
        if self.name == config.REDACTOR_FIELD:
            return "auto"
        return "ai" if self.ai else "manual"


def key(text: str) -> str:
    """Comparable form: no accents, lower case, unified separators."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    text = text.replace(HIERARCHY_SEP, "/").replace(">", "/")
    text = re.sub(r"[*\"']", " ", text)
    return "/".join(" ".join(p.split()) for p in text.split("/")).strip("/")


def decode(raw: bytes) -> str:
    """File text, whether it comes from AntBox (UTF-8) or Excel (Windows-1252)."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_rows(content: str) -> list[dict]:
    """Accepts ';', ',' or tab separated CSV, or space separated text."""
    first = content.splitlines()[0] if content.strip() else ""
    rows = []
    sep = next((s for s in ("\t", ";", ",") if first.count(s) >= 6), None)
    if sep:
        reader = csv.reader(io.StringIO(content), delimiter=sep)
        next(reader, None)  # headers
        for r in reader:
            if len(r) >= 7 and r[0].strip():
                rows.append({"meta": r[0], "meta_id": r[1], "card": r[3], "value": r[4], "val_id": r[5], "pos": r[6]})
    else:
        for line in content.splitlines()[1:]:
            m = _SPACED_LINE.match(line.strip())
            if m:
                rows.append(m.groupdict())
    return rows


def parse(content: str) -> list[Field]:
    fields: dict[str, Field] = {}
    for r in _read_rows(content):
        name = r["meta"].strip()
        if name not in fields:
            fields[name] = Field(name=name, id=r["meta_id"].strip(), multi="multi" in r["card"].lower())
        try:
            pos = int(r["pos"] or 0)
        except ValueError:
            pos = 0
        fields[name].values.append(Value(label=r["value"].strip(), id=r["val_id"].strip(), position=pos))
    return list(fields.values())


DOCUMENT_TYPE = "Document type"


def _adapt(fields: list[Field]) -> list[Field]:
    """Rename fields to the AntBox CSV column names (FIELD_MAP) and restrict
    Document type to the connector document types."""
    reverse = {src.lower(): col for col, src in config.FIELD_MAP.items()}
    for f in fields:
        col = reverse.get(f.name.lower())
        if col:
            f.source_name, f.name = f.name, col
    doc = next((f for f in fields if f.name == DOCUMENT_TYPE), None)
    if config.DOCUMENT_TYPES:
        if doc is None:
            doc = Field(name=DOCUMENT_TYPE, id="", multi=False)
            fields.insert(0, doc)
        by_key = {key(v.label): v for v in doc.values}
        doc.values = [by_key.get(key(t)) or Value(label=t, id="", position=i)
                      for i, t in enumerate(config.DOCUMENT_TYPES)]
        doc.multi = False
    return fields


@lru_cache(maxsize=1)
def _load(path: str, mtime: float) -> tuple[Field, ...]:
    from pathlib import Path
    return tuple(_adapt(parse(decode(Path(path).read_bytes()))))


def fields() -> list[Field]:
    """All metadata fields (re-read automatically when the file changes)."""
    path = config.find_referential()
    if not path.exists():
        return []
    return list(_load(str(path), path.stat().st_mtime))


def get_field(name: str) -> Field | None:
    return next((f for f in fields() if f.name == name), None)


def replace(raw: bytes) -> list[Field]:
    content = decode(raw)
    result = parse(content)
    if not result:
        raise ValueError("No line recognised in the referential file (expected 7 columns).")
    path = config.find_referential()
    path.write_text(content, encoding="utf-8")
    _load.cache_clear()
    return result


def validate(field_name: str, proposed: list[str]) -> tuple[list[str], list[str]]:
    """Keep only referential values, returned with their official label.

    Accepts "Domain_02 > Sub_01", "domain_02\\sub_01" or just the leaf ("Sub_01")
    when it is unambiguous. Returns (valid, rejected).
    """
    f = get_field(field_name)
    if f is None:
        return [], [str(p) for p in proposed]
    full = {key(v.label): v.label for v in f.values}
    leaves: dict[str, list[str]] = {}
    for v in f.values:
        leaves.setdefault(key(v.label).split("/")[-1], []).append(v.label)

    valid, rejected = [], []
    for p in proposed:
        k = key(p)
        official = full.get(k)
        if official is None and len(leaves.get(k.split("/")[-1], [])) == 1:
            official = leaves[k.split("/")[-1]][0]
        if official and official not in valid:
            valid.append(official)
        elif not official:
            rejected.append(str(p))
    if not f.multi:
        valid = valid[:1]
    return valid, rejected


def value_id(field_name: str, label: str) -> str:
    f = get_field(field_name)
    for v in (f.values if f else []):
        if v.label == label:
            return v.id
    return ""


def display(label: str) -> str:
    return " ".join(str(label).split()).replace(HIERARCHY_SEP, DISPLAY_SEP)


def as_json() -> list[dict]:
    """Shape sent to the dashboard."""
    return [
        {
            "name": f.name, "id": f.id, "multi": f.multi, "ai": f.ai, "mode": f.mode,
            "source_name": f.source_name,
            "required": f.name in config.REQUIRED_FIELDS,
            "values": [{"label": v.label, "display": v.display, "id": v.id}
                       for v in sorted(f.values, key=lambda v: v.display.lower())],
        }
        for f in fields()
    ]


if __name__ == "__main__":
    # Quick check:  python -m app.referential
    path = config.find_referential()
    print(f"File: {path}")
    result = fields()
    if not result:
        print("No metadata recognised. Check the file (columns, separator).")
    for f in result:
        sample = ", ".join(v.display for v in f.values[:3])
        src = f" (file: {f.source_name})" if f.source_name else ""
        print(f"- {f.name}{src} [{f.mode}]: {len(f.values)} values ({'multi' if f.multi else 'single'}) e.g. {sample}")
