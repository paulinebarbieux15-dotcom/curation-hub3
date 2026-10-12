from app import llm, referential
from app.sanitize import clean_html

DOC_PRESS = "Press articles / Social Network"


def test_referential_uses_antbox_csv_column_names():
    names = [f.name for f in referential.fields()]
    # file names Components/Sub-System, Mission, Aircrafts are mapped to the CSV columns
    for col in ("Document type", "Domain", "Year", "Parts/Modules", "Optionnal", "OperationType", "Vehicles"):
        assert col in names
    assert referential.get_field("Vehicles").source_name == "Aircrafts"
    modes = {f.name: f.mode for f in referential.fields()}
    assert modes["Domain"] == "manual" and modes["Attachment redactor"] == "auto"
    assert modes["Meeting or Forum"] == "unused" and modes["Vehicles"] == "ai"


def test_document_type_is_restricted_to_the_two_connectors():
    doc = referential.get_field("Document type")
    assert [v.label for v in doc.values] == [DOC_PRESS, "Commercial Brochure / Manufacturer website"]
    assert not doc.multi


def test_validate_accepts_display_form_and_leaf_and_rejects_inventions():
    f = referential.get_field("Vehicles")
    hier = next(v for v in f.values if "\\" in v.label)
    ok, ko = referential.validate("Vehicles", [hier.display, "Invented aircraft"])
    assert ok == [hier.label] and ko == ["Invented aircraft"]


def test_high_certainty_filter_keeps_only_confident_evidenced_values():
    text = "Airbus unveiled a new hydrogen drone battery in Toulouse, written by Jane Doe on 3 March 2026."
    parts = referential.get_field("Parts/Modules").values
    raw = {
        "title": "T", "summary": "One.\n\nTwo.",
        "metadata": {
            "Document type": [{"value": DOC_PRESS, "confidence": 0.95, "evidence": "written by Jane Doe"}],
            "Parts/Modules": [
                {"value": parts[0].display, "confidence": 0.95, "evidence": "a sentence that is not in the article at all"},
                {"value": parts[1].display, "confidence": 0.5, "evidence": "Airbus unveiled"},
            ],
            "Domain": [{"value": referential.get_field("Domain").values[0].display, "confidence": 1, "evidence": "Airbus"}],
        },
        "info": {"author": {"value": "Jane Doe", "confidence": 0.9, "evidence": "written by Jane Doe"},
                 "country": {"value": "Germany", "confidence": 0.9, "evidence": "made in Germany"}},
    }
    out = llm.filter_answer(raw, text)
    assert out["metadata"]["Document type"] == [DOC_PRESS]
    assert out["metadata"]["Parts/Modules"] == []          # left empty: uncertain
    assert "Domain" not in out["metadata"]                 # not an AI column
    assert out["info"]["author"] == "Jane Doe"
    assert out["info"]["country"] is None
    reasons = [d["reason"] for d in out["details"]["Parts/Modules"]]
    assert "evidence not found in article" in reasons and any("low confidence" in r for r in reasons)
    assert out["summary_html"] == "<p>One.</p><p>Two.</p>"
    assert "themes" not in out


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
            answer = {"title": "X", "summary": "S.", "metadata": {"Document type": [
                {"value": "Commercial Brochure / Manufacturer website", "confidence": 0.9,
                 "evidence": "drone battery for long missions"}]}, "info": {}}
            return {"choices": [{"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}}]}
    sent = {}
    monkeypatch.setattr(config, "LLM_FAKE", False)
    monkeypatch.setattr(config, "LLM_URL", "https://llm.local/v1/chat/completions")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(llm.httpx, "post", lambda url, json, headers, timeout: sent.update(json=json, headers=headers) or R())
    out = llm.analyze("T", "A new drone battery for long missions was shown.", "https://x")
    assert out["metadata"]["Document type"] == ["Commercial Brochure / Manufacturer website"]
    assert sent["headers"]["Authorization"] == "Bearer k" and sent["json"]["response_format"] == {"type": "json_object"}
    prompt = sent["json"]["messages"][0]["content"]
    assert "### Vehicles" in prompt and "### Domain" not in prompt and "### Meeting or Forum" not in prompt
