"""Internal LLM (OpenAI-compatible): auto-fill + executive report synthesis.

Anti-hallucination policy ("high certainty"):
- for every AntBox field the LLM only sees the CLOSED list of allowed values;
- every proposed value comes with a confidence (0-1) and an evidence quote copied
  from the article;
- a value is KEPT only if confidence >= AI_CONFIDENCE_THRESHOLD, the evidence quote
  is actually found in the article text, and the value exists in the referential.
  Otherwise the field stays EMPTY for the analyst to fill.
Every decision (kept / dropped + reason) is stored in links.ai_details so the
dashboard can show confidence badges and evidence tooltips.
"""
import html
import json
import re
import unicodedata

import httpx

from . import config, referential

INFO_FIELDS = ["author", "published_date", "source", "country", "companies"]

SYSTEM_PROMPT = """You are a technology-watch analyst. You read one article and prepare its record for the AntBox document database.

STRICT RULES — accuracy matters more than completeness:
1. "summary": 3 to 5 factual sentences in English, no opinion.
2. For each AntBox field below, choose ONLY among the listed values, copying their text exactly.
   - "single" fields: at most 1 value. "multi" fields: only the clearly relevant values (usually 1-3).
   - Hierarchical values are written "Parent > Child": pick the most precise level that applies.
3. Every value MUST include:
   - "confidence": a number between 0 and 1 (1 = explicitly stated in the article);
   - "evidence": a SHORT quote (5-25 words) copied VERBATIM from the article that justifies it.
   If you are not sure, or cannot quote the article, return an EMPTY list. Never guess, never invent.
4. "info" fields: null when the article does not state them explicitly. Same confidence/evidence rule.
   published_date format: YYYY-MM-DD.
5. "Document type": "Press articles / Social Network" for news articles, press releases, blogs and social
   network posts; "Commercial Brochure / Manufacturer website" for a manufacturer's own product pages,
   datasheets and brochures.
6. Reply with ONE valid JSON object and nothing else.

AntBox fields:
{fields}
{examples}
Expected JSON:
{{
  "title": "article title",
  "summary": "3-5 sentences",
  "metadata": {{
{metadata_example}
  }},
  "info": {{
    "author": {{"value": "name", "confidence": 0.9, "evidence": "quote"}} or null,
    "published_date": {{"value": "YYYY-MM-DD", "confidence": 0.9, "evidence": "quote"}} or null,
    "source": {{"value": "publication name", "confidence": 0.9, "evidence": "quote"}} or null,
    "country": {{"value": "country", "confidence": 0.9, "evidence": "quote"}} or null,
    "companies": [{{"value": "company", "confidence": 0.9, "evidence": "quote"}}]
  }}
}}"""

REPORT_PROMPT = """You are a senior technology-watch analyst writing an executive briefing for management.
You receive the articles validated by the team over a period, numbered [1], [2]...
Write in English, in Markdown:
## Executive summary
3 to 6 sentences on what matters most.
## Key developments
Bullet points; every bullet cites its sources with their numbers, e.g. "... [2][5]".
## Signals to watch
2 to 4 bullets.
Rules: use ONLY facts present in the summaries provided; cite numbers that exist; no invented facts,
no figures that are not in the summaries. Do not list the articles themselves (they are appended automatically)."""


class LLMError(Exception):
    pass


# ----------------------------------------------------------------- text helpers
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def evidence_found(evidence: str, text_norm: str) -> bool:
    """Is the quote in the article? Exact (normalised) match, or 85% of its 3-word shingles."""
    ev = _norm(evidence)
    if len(ev) < 4:
        return False
    if ev in text_norm:
        return True
    words = ev.split()
    if len(words) < 6:
        return False
    shingles = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    hits = sum(1 for s in shingles if s in text_norm)
    return hits / len(shingles) >= 0.85


def summary_to_html(summary: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", summary or "") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def html_to_text(value: str) -> str:
    """Plain text of the WYSIWYG summary (for CSV, registry, reports)."""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    value = re.sub(r"(?i)</(p|div|li|h\d)>", "\n", value)
    value = re.sub(r"(?i)<li[^>]*>", "• ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in value.splitlines())).strip()


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("LLM answer contains no JSON")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON returned by the LLM: {e}") from e


# ----------------------------------------------------------------- prompt
def build_prompt(examples: list[dict] | None = None) -> str:
    blocks, example = [], []
    for f in referential.fields():
        if not f.ai:
            continue
        values = "\n".join(f"  - {v.display}" for v in sorted(f.values, key=lambda v: v.display.lower()))
        blocks.append(f"### {f.name} ({'multi' if f.multi else 'single'})\n{values}")
        example.append(f'    "{f.name}": [{{"value": "...", "confidence": 0.9, "evidence": "quote"}}]')
    ex = ""
    if examples:
        lines = ["\nExamples of records validated by analysts (follow the same tagging habits):"]
        for e in examples:
            meta = {k: [referential.display(x) for x in v] for k, v in (e.get("metadata") or {}).items() if v}
            lines.append(f"- \"{e.get('title') or ''}\" -> {json.dumps(meta, ensure_ascii=False)}")
        ex = "\n".join(lines) + "\n"
    return SYSTEM_PROMPT.format(
        fields="\n\n".join(blocks) or "(none)",
        examples=ex,
        metadata_example=",\n".join(example),
    )


# ----------------------------------------------------------------- filtering
def _as_items(raw) -> list[dict]:
    """Accept [{"value","confidence","evidence"}], ["value"], "value" or {"value":...}."""
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out = []
    for r in raw:
        if isinstance(r, dict):
            if r.get("value") in (None, "", []):
                continue
            out.append({"value": str(r["value"]).strip(), "confidence": _num(r.get("confidence")),
                        "evidence": str(r.get("evidence") or "").strip()})
        elif r not in (None, ""):
            out.append({"value": str(r).strip(), "confidence": 0.0, "evidence": ""})
    return out


def _num(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _judge(item: dict, text_norm: str, need_evidence: bool = True) -> str | None:
    """None = keep, otherwise the reason it was dropped."""
    if item["confidence"] < config.AI_CONFIDENCE_THRESHOLD:
        return f"low confidence ({item['confidence']:.2f})"
    if need_evidence and config.AI_REQUIRE_EVIDENCE and not evidence_found(item["evidence"], text_norm):
        return "evidence not found in article"
    return None


def filter_answer(raw: dict, text: str) -> dict:
    """Apply the high-certainty policy to a raw LLM answer."""
    text_norm = _norm(text)
    details: dict[str, list[dict]] = {}
    metadata: dict[str, list[str]] = {}

    raw_meta = raw.get("metadata") or {}
    for f in referential.fields():
        if not f.ai:
            continue
        kept, decisions = [], []
        for item in _as_items(raw_meta.get(f.name)):
            reason = _judge(item, text_norm)
            official = None
            if reason is None:
                ok, _ = referential.validate(f.name, [item["value"]])
                official = ok[0] if ok else None
                if official is None:
                    reason = "not in referential"
                elif official in kept:
                    continue
                elif not f.multi and kept:
                    reason = "single-value field already filled"
            decisions.append({**item, "value": official or item["value"], "kept": reason is None,
                              "reason": reason or ""})
            if reason is None:
                kept.append(official)
        metadata[f.name] = kept
        if decisions:
            details[f.name] = decisions

    info: dict = {}
    raw_info = raw.get("info") or {}
    for name in INFO_FIELDS:
        items = _as_items(raw_info.get(name))
        decisions, kept = [], []
        for item in items:
            reason = _judge(item, text_norm)
            if name == "published_date" and reason is None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["value"]):
                reason = "invalid date format"
            decisions.append({**item, "kept": reason is None, "reason": reason or ""})
            if reason is None:
                kept.append(item["value"])
        info[name] = kept if name == "companies" else (kept[0] if kept else None)
        if decisions:
            details[f"info.{name}"] = decisions

    return {
        "title": str(raw.get("title") or "").strip(),
        "summary_html": summary_to_html(str(raw.get("summary") or "")),
        "metadata": metadata,
        "info": info,
        "details": details,
    }


# ----------------------------------------------------------------- calls
def analyze(title: str, text: str, url: str, examples: list[dict] | None = None) -> dict:
    """Returns {title, summary_html, metadata, info, details}."""
    if config.LLM_FAKE:
        raw = _fake_answer(title, text)
    else:
        user = f"URL: {url}\nTitle: {title}\n\nArticle text:\n{text[:config.LLM_MAX_CHARS]}"
        raw = extract_json(_chat(build_prompt(examples), user, json_mode=True))
    result = filter_answer(raw, f"{title}\n{text}")
    result["title"] = result["title"] or title
    return result


def write_report(items: list[dict], period_label: str, granularity: str) -> str:
    """Executive synthesis in Markdown. `items`: [{n, title, summary, doc_type, analyst}]."""
    if config.LLM_FAKE or not items:
        return _fake_report(items, period_label)
    lines = [f"Period: {period_label}. Grouping requested: {granularity}.", ""]
    for it in items:
        lines.append(f"[{it['n']}] {it['title']} (type: {it['doc_type'] or '-'}; "
                     f"analyst: {it['analyst']})\n{it['summary'][:1200]}\n")
    return _chat(REPORT_PROMPT, "\n".join(lines)[: config.LLM_MAX_CHARS * 2], json_mode=False).strip()


def _chat(system: str, user: str, json_mode: bool) -> str:
    if not config.LLM_URL:
        raise LLMError("LLM_URL is not set in .env (or set LLM_FAKE=true)")
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0}
    if config.LLM_MODEL:
        body["model"] = config.LLM_MODEL
    if json_mode and config.LLM_JSON_MODE:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    try:
        r = httpx.post(config.LLM_URL, json=body, headers=headers, timeout=config.LLM_TIMEOUT)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        raise LLMError(f"LLM answered {e.response.status_code}: {e.response.text[:300]}") from e
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMError(f"LLM call failed: {e}") from e


def test_connection() -> dict:
    if config.LLM_FAKE:
        return {"ok": True, "mode": "fake", "answer": "LLM_FAKE=true (simulated answers)"}
    answer = _chat("Reply with the single word: pong", "ping", json_mode=False)
    return {"ok": True, "mode": "live", "model": config.LLM_MODEL, "answer": answer[:100]}


# ----------------------------------------------------------------- fake mode
def _fake_answer(title: str, text: str) -> dict:
    """LLM_FAKE: plausible answer exercising every filter path."""
    words = text.split()
    quote = " ".join(words[:8]) if words else ""
    metadata = {}
    for f in referential.fields():
        if f.ai and f.values:
            first = sorted(f.values, key=lambda v: v.position)[0]
            metadata[f.name] = [{"value": first.display, "confidence": 0.93, "evidence": quote}]
    if "Vehicles" in metadata:
        metadata["Vehicles"].append({"value": "InventedValue", "confidence": 0.99, "evidence": quote})  # not in referential
    if metadata:
        k = next(iter(metadata))
        metadata[k].append({"value": "Uncertain", "confidence": 0.4, "evidence": quote})          # low confidence
    author = re.search(r"by ([A-Z][a-z]+ [A-Z][a-z]+)", text)
    return {
        "title": title,
        "summary": "Simulated summary (LLM_FAKE=true).\n\nThe real summary will appear once the internal LLM is connected.",
        "metadata": metadata,
        "info": {
            "author": {"value": author.group(1), "confidence": 0.95, "evidence": author.group(0)} if author else None,
            "published_date": {"value": "2026-03-03", "confidence": 0.6, "evidence": "3 March 2026"},
            "source": None, "country": None, "companies": [],
        },
    }


def _fake_report(items: list[dict], period_label: str) -> str:
    types: dict[str, int] = {}
    for it in items:
        t = it["doc_type"] or "Unclassified"
        types[t] = types.get(t, 0) + 1
    top = sorted(types.items(), key=lambda x: -x[1])[:5]
    lines = ["## Executive summary",
             f"{len(items)} article(s) were validated over {period_label}. "
             + (f"Breakdown by document type: {', '.join(f'{t} ({n})' for t, n in top)}." if top else ""),
             "", "## Key developments"]
    for it in items[:8]:
        first = (it["summary"].split(". ")[0] or it["title"]).strip().rstrip(".")
        lines.append(f"- {first}. [{it['n']}]")
    lines += ["", "## Signals to watch", "- Simulated synthesis (LLM_FAKE=true): connect the internal LLM for a real analysis."]
    return "\n".join(lines)
