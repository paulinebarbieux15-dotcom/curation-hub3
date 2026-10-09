from app import llm, referential
from app.sanitize import clean_html


def test_referential_loads_test_fixture():
    names = [f.name for f in referential.fields()]
    assert "Document type" in names and "Domain" in names and "Aircrafts" in names
    doc = referential.get_field("Document type")
    assert not doc.multi and doc.values


def test_validate_accepts_display_form_and_leaf_and_rejects_inventions():
    f = referential.get_field("Aircrafts")
    hier = next(v for v in f.values if "\\" in v.label)
    ok, ko = referential.validate("Aircrafts", [hier.display, "Invented aircraft"])
    assert ok == [hier.label] and ko == ["Invented aircraft"]


def test_high_certainty_filter_keeps_only_confident_evidenced_values():
    text = "Airbus unveiled a new hydrogen drone battery in Toulouse, written by Jane Doe on 3 March 2026."
    doc = referential.get_field("Document type").values[0].display
    raw = {
        "title": "T", "summary": "One.\n\nTwo.",
        "metadata": {"Document type": [
            {"value": doc, "confidence": 0.95, "evidence": "Airbus unveiled a new hydrogen drone battery"},
        ], "Domain": [
            {"value": referential.get_field("Domain").values[0].display, "confidence": 0.95, "evidence": "a sentence that is not in the article at all"},
            {"value": referential.get_field("Domain").values[1].display, "confidence": 0.5, "evidence": "Airbus unveiled"},
        ]},
        "info": {"author": {"value": "Jane Doe", "confidence": 0.9, "evidence": "written by Jane Doe"},
                 "country": {"value": "Germany", "confidence": 0.9, "evidence": "made in Germany"}},
        "themes": [{"value": "drones", "confidence": 0.9}, {"value": "Not a theme", "confidence": 0.99}],
    }
    out = llm.filter_answer(raw, text, ["Drones", "Space"])
    assert out["metadata"]["Document type"] == [referential.get_field("Document type").values[0].label]
    assert out["metadata"]["Domain"] == []                 # left empty: uncertain
    assert out["info"]["author"] == "Jane Doe"
    assert out["info"]["country"] is None                  # evidence not in article
    assert out["themes"] == ["Drones"]
    reasons = [d["reason"] for d in out["details"]["Domain"]]
    assert "evidence not found in article" in reasons and any("low confidence" in r for r in reasons)
    assert out["summary_html"] == "<p>One.</p><p>Two.</p>"


def test_sanitizer_strips_scripts_and_handlers():
    dirty = '<p onclick="x()">Hi <b>bold</b><script>alert(1)</script> <a href="javascript:x">l</a></p>'
    assert clean_html(dirty) == "<p>Hi <b>bold</b> <a>l</a></p>"
    assert llm.html_to_text("<p>A</p><ul><li>B</li></ul>") == "A\n• B"


def test_live_llm_call_is_parsed_and_filtered(monkeypatch):
    import json
    from app import config

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            doc = referential.get_field("Document type").values[0].display
            answer = {"title": "X", "summary": "S.", "metadata": {"Document type": [
                {"value": doc, "confidence": 0.9, "evidence": "drone battery for long missions"}]},
                "info": {}, "themes": [{"value": "Drones", "confidence": 0.95}]}
            return {"choices": [{"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}}]}
    sent = {}
    monkeypatch.setattr(config, "LLM_FAKE", False)
    monkeypatch.setattr(config, "LLM_URL", "https://llm.local/v1/chat/completions")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(llm.httpx, "post", lambda url, json, headers, timeout: sent.update(json=json, headers=headers) or R())
    out = llm.analyze("T", "A new drone battery for long missions was shown.", "https://x", ["Drones"])
    assert out["metadata"]["Document type"] and out["themes"] == ["Drones"]
    assert sent["headers"]["Authorization"] == "Bearer k" and sent["json"]["response_format"] == {"type": "json_object"}
    assert "Document type" in sent["json"]["messages"][0]["content"]
