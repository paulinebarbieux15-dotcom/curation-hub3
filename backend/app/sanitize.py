"""Whitelist HTML sanitizer for the WYSIWYG summary (no scripts, no event handlers)."""
import html
from html.parser import HTMLParser

ALLOWED = {"p", "br", "b", "strong", "i", "em", "u", "s", "ul", "ol", "li", "h3", "h4", "blockquote", "a", "div", "span"}
VOID = {"br"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.skip += 1
            return
        if tag not in ALLOWED or self.skip:
            return
        if tag == "div":
            tag = "p"
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith(("http://", "https://", "mailto:")):
                self.out.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">')
            else:
                self.out.append("<a>")
            return
        if tag == "span":
            return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.skip = max(0, self.skip - 1)
            return
        if tag not in ALLOWED or tag in VOID or tag == "span" or self.skip:
            return
        self.out.append(f"</{'p' if tag == 'div' else tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean_html(value: str) -> str:
    c = _Cleaner()
    c.feed(value or "")
    c.close()
    return "".join(c.out).strip()
