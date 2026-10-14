"""Shared team social account (LinkedIn, X, Facebook, Instagram), always logged in.

Two mechanisms work together:

1. The SESSION (cookies) is stored in data/secrets/social_session.json and reused
   for every capture, so posts open as the logged-in team account.
2. The account CREDENTIALS (in .env only, never in code or in the database) let the
   app LOG BACK IN BY ITSELF when the session expires: it fills the network's login
   form, handles two-factor authentication when a TOTP secret is provided, then
   saves the fresh session.

`python -m app.social_login` remains available for a manual login when a network
asks for a check that cannot be automated (captcha, SMS code...).
"""
import os
import time
from datetime import datetime
from urllib.parse import urlsplit

from . import config, db

_LOGGED_OUT_SIGNS = ("/login", "/signin", "/authwall", "/checkpoint", "/uas/login", "/i/flow/login",
                     "accounts/login", "/session", "/challenge")

# How to log in to each network. CSS selectors target the login form fields:
# if a network changes its page, this is the place to update.
NETWORKS: dict[str, dict] = {
    # Each selector lists several variants (comma = "any of"): networks change their pages often.
    "linkedin": {
        "domains": ["linkedin.com", "lnkd.in"],
        "login_page": "https://www.linkedin.com/login",
        "steps": [
            ("fill", "#username, input[name=session_key], input[autocomplete=username]", "username"),
            ("fill", "#password, input[name=session_password], input[type=password]", "password"),
            ("click", "button[type=submit], button[data-litms-control-urn='login-submit']"),
        ],
        "otp_field": "input[name=pin], #input__phone_verification_pin, input[autocomplete=one-time-code]",
    },
    "x": {
        "domains": ["x.com", "twitter.com", "t.co"],
        "login_page": "https://x.com/i/flow/login",
        "steps": [
            ("fill", "input[autocomplete=username], input[name=text]", "username"),
            ("click", "button:has-text('Next'), button:has-text('Suivant'), div[role=button]:has-text('Next'), div[role=button]:has-text('Suivant')"),
            ("fill", "input[name=password], input[type=password]", "password"),
            ("click", "button[data-testid=LoginForm_Login_Button], div[data-testid=LoginForm_Login_Button]"),
        ],
        "otp_field": "input[data-testid=ocfEnterTextTextInput], input[autocomplete=one-time-code]",
    },
    "facebook": {
        "domains": ["facebook.com"],
        "login_page": "https://www.facebook.com/login",
        "steps": [
            ("fill", "#email, input[name=email]", "username"),
            ("fill", "#pass, input[name=pass], input[type=password]", "password"),
            ("click", "button[name=login], button[type=submit]"),
        ],
        "otp_field": "input[name=approvals_code], input[autocomplete=one-time-code]",
    },
    "instagram": {
        "domains": ["instagram.com"],
        "login_page": "https://www.instagram.com/accounts/login/",
        "steps": [
            ("fill", "input[name=username], input[name=email], input[autocomplete*=username], "
                     "form input[type=text]", "username"),
            ("fill", "input[name=password], input[name=pass], input[type=password]", "password"),
            ("click", "button[type=submit], div[role=button]:has-text('Log in'), "
                      "div[role=button]:has-text('Se connecter'), button:has-text('Log in'), "
                      "button:has-text('Se connecter')"),
        ],
        "otp_field": "input[name=verificationCode], input[name=approvals_code], input[autocomplete=one-time-code]",
    },
}

# Cookie / consent pop-ups shown before the login form (EU): accepted automatically.
_CONSENT_BUTTONS = [
    "Allow all cookies", "Autoriser tous les cookies", "Accept all", "Accept All", "Tout accepter",
    "Accepter tout", "Accept all cookies", "Accepter tous les cookies", "Allow essential and optional cookies",
    "Autoriser les cookies essentiels et optionnels", "Accept", "Accepter", "J'accepte", "I agree",
]


def dismiss_consent(page) -> None:
    for label in _CONSENT_BUTTONS:
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue


class SocialSessionError(Exception):
    pass


# ----------------------------------------------------------------- links
def _host(url: str) -> str:
    host = urlsplit(url if "://" in url else "https://" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_social(url: str) -> bool:
    return any(_matches(_host(url), d) for d in config.SOCIAL_DOMAINS)


def network_for(url: str) -> str | None:
    host = _host(url)
    return next((n for n, r in NETWORKS.items() if any(_matches(host, d) for d in r["domains"])), None)


def looks_logged_out(final_url: str) -> bool:
    return any(s in final_url.lower() for s in _LOGGED_OUT_SIGNS)


# ----------------------------------------------------------------- credentials (.env)
def credentials(name: str) -> dict:
    """SOCIAL_LINKEDIN_USERNAME, SOCIAL_LINKEDIN_PASSWORD, SOCIAL_LINKEDIN_TOTP."""
    prefix = f"SOCIAL_{name.upper()}_"
    return {
        "username": os.getenv(prefix + "USERNAME", ""),
        "password": os.getenv(prefix + "PASSWORD", ""),
        "totp": clean_secret(os.getenv(prefix + "TOTP", "")),
    }


def clean_secret(secret: str) -> str:
    """Authenticator setup keys are often shown in groups ("abcd efgh ...") or lower case."""
    return "".join(ch for ch in secret.upper() if ch.isalnum())


def has_credentials(name: str | None) -> bool:
    if not name:
        return False
    c = credentials(name)
    return bool(c["username"] and c["password"])


# ----------------------------------------------------------------- switch & session
def enabled() -> bool:
    with db.tx() as con:
        return bool(db.get_setting(con, "social_enabled", False))


def set_enabled(value: bool) -> None:
    with db.tx() as con:
        db.set_setting(con, "social_enabled", bool(value))


def record_login(name: str, result: str) -> None:
    with db.tx() as con:
        logins = db.get_setting(con, "social_logins", {})
        logins[name] = {"at": datetime.now().isoformat(timespec="minutes"), "result": result}
        db.set_setting(con, "social_logins", logins)


def session_present() -> bool:
    f = config.SOCIAL_SESSION_FILE
    return f.exists() and f.stat().st_size > 0


def protect_file() -> None:
    try:
        os.chmod(config.SOCIAL_SESSION_FILE, 0o600)
    except OSError:
        pass


def status() -> dict:
    """Never returns credentials, only whether they are configured."""
    updated = None
    if session_present():
        updated = datetime.fromtimestamp(config.SOCIAL_SESSION_FILE.stat().st_mtime).isoformat(timespec="minutes")
    with db.tx() as con:
        logins = db.get_setting(con, "social_logins", {})
        on = bool(db.get_setting(con, "social_enabled", False))
    return {
        "enabled": on,
        "session_present": session_present(),
        "session_updated_at": updated,
        "domains": config.SOCIAL_DOMAINS,
        "networks": [
            {
                "name": n,
                "credentials": has_credentials(n),
                "auto_2fa": bool(credentials(n)["totp"]),
                "last_login": logins.get(n),
            }
            for n in NETWORKS
        ],
    }


def check_before_capture(url: str) -> bool:
    """True when the URL must be captured with the shared account.
    Raises a clear error if collection is off or nothing allows logging in."""
    if not is_social(url):
        return False
    if not enabled():
        raise SocialSessionError("Social capture is turned off: enable it in Settings > Social account.")
    if not session_present() and not has_credentials(network_for(url)):
        raise SocialSessionError(
            "No saved session and no credentials for this network: add them to .env "
            "or run `python -m app.social_login`.")
    return True


# ----------------------------------------------------------------- automatic login
def auto_login(context, name: str) -> None:
    """Log in to `name` in the given Playwright context and save the session."""
    if name not in NETWORKS:
        raise SocialSessionError(f"Automatic login is not configured for '{name}'.")
    if not has_credentials(name):
        raise SocialSessionError(
            f"Missing {name} credentials: set SOCIAL_{name.upper()}_USERNAME and "
            f"SOCIAL_{name.upper()}_PASSWORD in .env.")

    r, cred = NETWORKS[name], credentials(name)
    page = context.new_page()
    try:
        page.goto(r["login_page"], wait_until="domcontentloaded", timeout=config.CAPTURE_TIMEOUT_MS)
        page.wait_for_timeout(2500)
        dismiss_consent(page)
        if not looks_logged_out(page.url):
            record_login(name, "already logged in")
            return
        for step in r["steps"]:
            dismiss_consent(page)
            el = page.locator(step[1]).first
            el.wait_for(state="visible", timeout=30000)
            if step[0] == "fill":
                el.fill(cred[step[2]])
            else:
                el.click()
            page.wait_for_timeout(800)
        _two_factor(page, r, cred, name)
        _wait_logged_in(page, name)
        context.storage_state(path=str(config.SOCIAL_SESSION_FILE))
        protect_file()
        record_login(name, "logged in automatically")
    except SocialSessionError as e:
        shot = _failure_screenshot(page, name)
        record_login(name, f"failed: {e}")
        raise SocialSessionError(f"{e}{shot}") from e
    except Exception as e:
        shot = _failure_screenshot(page, name)
        step = str(e).splitlines()[0][:200]
        record_login(name, f"failed: {type(e).__name__} at {page.url[:80]}")
        raise SocialSessionError(
            f"Automatic login to {name} failed ({type(e).__name__}: {step}). Page reached: {page.url[:120]}. "
            f"The login page may have changed or shows a security check: log in manually with "
            f"`python -m app.social_login`.{shot}") from e
    finally:
        try:
            page.close()
        except Exception:
            pass


def _two_factor(page, r: dict, cred: dict, name: str) -> None:
    try:
        page.locator(r["otp_field"]).first.wait_for(state="visible", timeout=6000)
    except Exception:
        return
    if not cred["totp"]:
        raise SocialSessionError(
            f"{name} asks for a verification code. Add SOCIAL_{name.upper()}_TOTP to .env "
            "(authenticator app secret) or log in manually with `python -m app.social_login`.")
    el = page.locator(r["otp_field"]).first
    el.fill(current_code(cred["totp"]))
    el.press("Enter")


def current_code(secret: str) -> str:
    """6-digit code; waits for a fresh one if the current code expires in less than 5 seconds."""
    import pyotp

    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 5:
        time.sleep(remaining + 0.5)
    return totp.now()


def check_codes() -> None:
    """python -m app.social : compare these codes with the ones on the authenticator app."""
    from datetime import datetime, timezone

    import httpx

    print(f"Computer clock (UTC): {datetime.now(timezone.utc):%H:%M:%S}")
    try:
        server = httpx.head("https://www.google.com", timeout=10).headers.get("date", "")
        print(f"Internet time (UTC):  {server[17:25]}   -> both must match within a few seconds")
    except Exception:
        print("Internet time: not reachable (check the clock against your phone)")
    found = False
    for name in NETWORKS:
        secret = credentials(name)["totp"]
        if not secret:
            continue
        found = True
        try:
            import pyotp
            totp = pyotp.TOTP(secret)
            left = int(totp.interval - (time.time() % totp.interval))
            print(f"{name:10s} code {totp.now()}  (valid {left}s more)  key ends with ...{secret[-4:]}")
        except Exception as e:
            print(f"{name:10s} INVALID KEY ({type(e).__name__}): copy the setup key again")
    if not found:
        print("No SOCIAL_..._TOTP key found in .env")


if __name__ == "__main__":
    config.use_system_certificates()
    check_codes()


def _failure_screenshot(page, name: str) -> str:
    """Save what the browser showed when the login failed (data/secrets/login_failed_<network>.png)."""
    try:
        path = config.SECRETS_DIR / f"login_failed_{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return f" Screenshot of the page: {path}"
    except Exception:
        return ""


def _wait_logged_in(page, name: str, timeout_s: float | None = None) -> None:
    # with a visible window, leave time to complete a security check by hand
    timeout_s = timeout_s or (180 if config.SOCIAL_SHOW_BROWSER else 25)
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if not looks_logged_out(page.url):
            return
        page.wait_for_timeout(1000)
    raise SocialSessionError(
        f"{name} did not accept the login (wrong credentials or security check). "
        "Check .env or log in manually with `python -m app.social_login`.")
