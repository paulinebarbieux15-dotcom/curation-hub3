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
    method: str = "page"   # page, x-embed or x-data


def slug(text: str, length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:length].strip("-") or "article"


def _launch(p, headless: bool = True):
    # hide the "controlled by automation" flag that some networks (X, LinkedIn) refuse to serve
    opts = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled"]}
    if not headless:
        opts["args"].append("--start-maximized")
    if config.BROWSER_CHANNEL:
        opts["channel"] = config.BROWSER_CHANNEL
    return p.chromium.launch(**opts)


_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# Element that proves the post itself is displayed, per network
_READY_SELECTORS = {
    "x": "article[data-testid=tweet], [data-testid=tweetText]",
    "linkedin": "main, article, .feed-shared-update-v2",
    "instagram": "article, main",
    "facebook": "[role=article], [role=main]",
}
_ERROR_TEXTS = ("Something went wrong", "Un problème est survenu", "Une erreur s'est produite",
                "Try reloading", "Essayez de recharger", "This page isn’t available", "Hmm...this page doesn’t exist")


def _error_page(page) -> bool:
    try:
        body = page.inner_text("body", timeout=3000)[:3000]
    except Exception:
        return False
    return any(t in body for t in _ERROR_TEXTS) and len(body) < 8000


def _wait_for_post(page, url: str, network: str | None) -> None:
    """Wait until the post is displayed; reload when the network shows its error page (max 3 tries)."""
    selector = _READY_SELECTORS.get(network or "")
    if network == "x":
        # X: the post itself must be on the page, otherwise the fallback takes over
        try:
            page.wait_for_selector(selector, state="visible", timeout=25000)
            page.wait_for_timeout(1500)
            return
        except Exception:
            raise RuntimeError("X did not display the post (error page or blocked)") from None
    for attempt in range(3):
        if selector:
            try:
                page.wait_for_selector(selector, state="visible", timeout=20000)
            except Exception:
                pass
        page.wait_for_timeout(1500)
        if not _error_page(page):
            return
        page.wait_for_timeout(3000 * (attempt + 1))
        try:
            retry = page.get_by_role("button", name=re.compile("Retry|Réessayer|Try again", re.I))
            if retry.count():
                retry.first.click(timeout=3000)
            else:
                _open(page, url)
        except Exception:
            _open(page, url)
    raise RuntimeError(f"{network or 'The site'} answered with its error page (\"Something went wrong\") "
                       "after 3 attempts: it may be limiting automated access. The capture will be retried later.")


def _open(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=config.CAPTURE_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass  # some pages never go idle


# ----------------------------------------------------------------- X (Twitter) fallback
_X_STATUS = re.compile(r"(?:x|twitter)\.com/[^/?#]+/status(?:es)?/(\d+)", re.I)


def tweet_id(url: str) -> str | None:
    m = _X_STATUS.search(url or "")
    return m.group(1) if m else None


def _esc(v) -> str:
    import html
    return html.escape(str(v or ""))


def tweet_html(data: dict, url: str) -> str:
    """A clean, printable page built from X's public tweet data (syndication API)."""
    user = data.get("user") or {}
    photos = [m.get("media_url_https") for m in (data.get("mediaDetails") or []) if m.get("media_url_https")]
    quoted = data.get("quoted_tweet") or {}
    imgs = "".join(f'<img src="{_esc(p)}" alt="">' for p in photos)
    quote = ""
    if quoted:
        qu = quoted.get("user") or {}
        quote = (f'<div class="quote"><b>{_esc(qu.get("name"))}</b> @{_esc(qu.get("screen_name"))}'
                 f'<p>{_esc(quoted.get("text"))}</p></div>')
    stats = f'{data.get("favorite_count", 0)} likes · {data.get("conversation_count", 0)} replies'
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_esc(user.get("name"))} on X</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:30px auto;color:#0f1419}}
.card{{border:1px solid #cfd9de;border-radius:16px;padding:18px 20px}} .name{{font-weight:700;font-size:17px}}
.handle,.meta{{color:#536471;font-size:14px}} .text{{font-size:18px;line-height:1.45;white-space:pre-wrap;margin:14px 0}}
img{{max-width:100%;border-radius:12px;margin:6px 0;display:block}}
.quote{{border:1px solid #cfd9de;border-radius:12px;padding:10px 14px;margin:10px 0;font-size:15px}}
.src{{color:#536471;font-size:12px;margin-top:14px;word-break:break-all}}</style></head>
<body><article class="card"><div class="name">{_esc(user.get("name"))}</div>
<div class="handle">@{_esc(user.get("screen_name"))}</div>
<div class="text">{_esc(data.get("text"))}</div>{imgs}{quote}
<div class="meta">{_esc(data.get("created_at"))} · {_esc(stats)}</div>
<div class="src">Source: {_esc(url)}</div></article></body></html>"""


def _x_fallback(context, url: str):
    """X refuses to show the post to an automated browser: use the official embed, else the public data."""
    tid = tweet_id(url)
    if not tid:
        return None
    page = context.new_page()
    try:  # 1. official embedded tweet (what news sites display)
        page.goto(f"https://platform.twitter.com/embed/Tweet.html?id={tid}&theme=light&dnt=true&lang=en",
                  wait_until="domcontentloaded", timeout=config.CAPTURE_TIMEOUT_MS)
        page.wait_for_selector("article", state="visible", timeout=20000)
        page.wait_for_timeout(2000)
        if not _error_page(page) and len(page.inner_text("body").strip()) > 20:
            return page
    except Exception:
        pass
    try:  # 2. public tweet data -> our own printable page
        token = page.evaluate("id => ((Number(id) / 1e15) * Math.PI).toString(36).replace(/(0+|\\.)/g, '')", tid)
        resp = page.goto(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=en&token={token}",
                         wait_until="domcontentloaded", timeout=30000)
        if resp and resp.ok:
            import json as _json
            data = _json.loads(page.inner_text("body"))
            if data.get("text"):
                page.set_content(tweet_html(data, url), wait_until="load")
                return page
    except Exception:
        pass
    page.close()
    return None


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
            context.add_init_script(_STEALTH_JS)
            if with_session and not social.session_present():
                social.auto_login(context, network)

            page = context.new_page()
            _open(page, url)
            social.dismiss_consent(page)
            via_fallback = False
            method = "page"
            try:
                if with_session and social.looks_logged_out(page.url):
                    if social.has_credentials(network):
                        social.auto_login(context, network)
                        _open(page, url)
                    if social.looks_logged_out(page.url):
                        raise social.SocialSessionError(
                            "Social session expired and automatic login failed: check credentials in .env "
                            "or run `python -m app.social_login`.")
                if with_session:
                    _wait_for_post(page, url, network)
            except Exception:
                fallback = _x_fallback(context, url) if network == "x" else None
                if fallback is None:
                    raise
                page, via_fallback = fallback, True
                method = "x-data" if "syndication" in page.url or page.url == "about:blank" else "x-embed"

            title = (page.title() or "").strip()
            text = page.evaluate(_JS_TEXT) or ""
            final_url = url if via_fallback else page.url
            try:
                canonical = "" if via_fallback else (page.evaluate(_JS_CANONICAL) or "")
            except Exception:
                canonical = ""
            if via_fallback and (not title or title.lower() in ("x", "twitter")):
                title = "X post: " + " ".join(text.split())[:90]
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
    return CaptureResult(title=title, text=text, pdf_file=pdf_name, final_url=final_url, canonical_url=canonical,
                         method=method)


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
            context.add_init_script(_STEALTH_JS)
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
