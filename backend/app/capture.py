"""Web page capture: clean PDF + plain text (for the LLM) + final/canonical URL.

The text is read from the live page (more reliable than re-reading the PDF).
"""
import re
import threading
import unicodedata
from dataclasses import dataclass

from . import config, social

# One browser at a time: keeps the machine responsive.
_browser_lock = threading.Lock()

_JS_TEXT = """
() => {
  const zone = document.querySelector('article') || document.querySelector('main') || document.body;
  const meta = document.querySelector('meta[property="og:description"]');
  const lead = meta ? meta.content + '\\n\\n' : '';
  return lead + (zone ? zone.innerText : '');
}
"""

_JS_CANONICAL = """
() => {
  const l = document.querySelector('link[rel="canonical"]');
  const og = document.querySelector('meta[property="og:url"]');
  return (l && l.href) || (og && og.content) || '';
}
"""

# Removes cookie banners / pop-ups BEFORE printing. Only floating elements
# (fixed/sticky) or modal dialogs are touched — never <html>, <body> or the main
# content (hiding anything with a "cookie" class blanked pages whose <body> had it).
_JS_CLEANUP = """
() => {
  const pattern = /cookie|consent|gdpr|rgpd|didomi|onetrust|newsletter|popup|modal|paywall/i;
  const main = document.querySelector('article, main');
  for (const el of document.querySelectorAll('body *')) {
    if (main && (el === main || el.contains(main))) continue;
    const style = getComputedStyle(el);
    const floating = style.position === 'fixed' || style.position === 'sticky';
    const name = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '');
    if ((floating && pattern.test(name)) || el.getAttribute('aria-modal') === 'true') el.remove();
  }
  for (const el of [document.documentElement, document.body]) {
    el.style.setProperty('overflow', 'visible', 'important');
    el.style.setProperty('position', 'static', 'important');
  }
}
"""

# Scroll to load lazy images, then back to top
_JS_SCROLL = """
async () => {
  for (let y = 0; y < document.body.scrollHeight && y < 30000; y += 800) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 120));
  }
  window.scrollTo(0, 0);
}
"""

_DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
_PDF_MARGINS = {"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"}


@dataclass
class CaptureResult:
    title: str
    text: str
    pdf_file: str          # file name in data/pdfs/
    final_url: str
    canonical_url: str


def slug(text: str, length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:length].strip("-") or "article"


def _launch(p, headless: bool = True):
    opts = {"headless": headless}
    if not headless:
        opts["args"] = ["--start-maximized"]
    if config.BROWSER_CHANNEL:
        opts["channel"] = config.BROWSER_CHANNEL
    return p.chromium.launch(**opts)


def _open(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=config.CAPTURE_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass  # some pages never go idle


def capture(url: str, link_id: int) -> CaptureResult:
    with_session = social.check_before_capture(url)
    if config.CAPTURE_FAKE:
        return _fake_capture(url, link_id)

    from playwright.sync_api import sync_playwright

    network = social.network_for(url) if with_session else None
    with _browser_lock, sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(
                locale="en-US",
                user_agent=_DESKTOP_UA if with_session else None,
                storage_state=str(config.SOCIAL_SESSION_FILE) if with_session and social.session_present() else None,
            )
            if with_session and not social.session_present():
                social.auto_login(context, network)

            page = context.new_page()
            _open(page, url)
            if with_session and social.looks_logged_out(page.url):
                if social.has_credentials(network):
                    social.auto_login(context, network)
                    _open(page, url)
                if social.looks_logged_out(page.url):
                    raise social.SocialSessionError(
                        "Social session expired and automatic login failed: check credentials in .env "
                        "or run `python -m app.social_login`.")
            if with_session:
                page.wait_for_timeout(2000)

            title = (page.title() or "").strip()
            text = page.evaluate(_JS_TEXT) or ""
            final_url = page.url
            try:
                canonical = page.evaluate(_JS_CANONICAL) or ""
            except Exception:
                canonical = ""
            try:
                page.evaluate(_JS_SCROLL)
                page.evaluate(_JS_CLEANUP)
            except Exception:
                pass
            page.emulate_media(media="screen")
            pdf_name = f"{link_id:05d}_{slug(title or url)}.pdf"
            page.pdf(path=str(config.PDF_DIR / pdf_name), format="A4", print_background=True, margin=_PDF_MARGINS)
            if with_session:
                context.storage_state(path=str(config.SOCIAL_SESSION_FILE))  # networks rotate cookies
                social.protect_file()
        finally:
            browser.close()

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return CaptureResult(title=title, text=text, pdf_file=pdf_name, final_url=final_url, canonical_url=canonical)


def html_to_pdf(html: str, path) -> bool:
    """Render an HTML document to PDF (used for reports). Returns False if no browser."""
    if config.CAPTURE_FAKE:
        path.write_bytes(_MINIMAL_PDF)
        return True
    try:
        from playwright.sync_api import sync_playwright

        with _browser_lock, sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(path=str(path), format="A4", print_background=True, margin=_PDF_MARGINS)
            finally:
                browser.close()
        return True
    except Exception:
        return False


def login_network(name: str) -> dict:
    """Automatic login to one network, triggered from Settings."""
    if config.CAPTURE_FAKE:
        if not social.has_credentials(name):
            raise social.SocialSessionError(f"Missing {name} credentials.")
        config.SOCIAL_SESSION_FILE.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        social.record_login(name, "logged in automatically")
        return social.status()

    from playwright.sync_api import sync_playwright

    with _browser_lock, sync_playwright() as p:
        browser = _launch(p, headless=not config.SOCIAL_SHOW_BROWSER)
        try:
            context = browser.new_context(
                locale="en-US",
                # a regular desktop Chrome identity: some networks block "HeadlessChrome"
                user_agent=_DESKTOP_UA,
                storage_state=str(config.SOCIAL_SESSION_FILE) if social.session_present() else None)
            social.auto_login(context, name)
        finally:
            browser.close()
    return social.status()


_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _fake_capture(url: str, link_id: int) -> CaptureResult:
    """Test mode: minimal PDF, no browser."""
    pdf_name = f"{link_id:05d}_{slug(url)}.pdf"
    (config.PDF_DIR / pdf_name).write_bytes(_MINIMAL_PDF)
    return CaptureResult(
        title=f"Test article ({url})",
        text="Simulated text: a company presents a new battery for drones. Published on 3 March 2026 by Jane Doe.",
        pdf_file=pdf_name, final_url=url, canonical_url="",
    )
