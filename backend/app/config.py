"""Application settings, read from backend/.env (or environment variables).

Secrets (LLM key, social account credentials) live in .env only, never in code.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


def _load_env() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# --- Storage ----------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "curation.db"))
PDF_DIR = DATA_DIR / "pdfs"
REGISTRY_DIR = DATA_DIR / "registry"        # permanent, immutable files
EXPORT_DIR = DATA_DIR / "exports"           # on-demand exports and AntBox packages
REPORT_DIR = DATA_DIR / "reports"
SECRETS_DIR = DATA_DIR / "secrets"
DASHBOARD_DIR = Path(os.getenv("DASHBOARD_DIR", BASE_DIR / "dashboard"))

# --- Internal LLM (OpenAI-compatible: POST /chat/completions) ---------------
LLM_URL = os.getenv("LLM_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_JSON_MODE = _bool("LLM_JSON_MODE", True)
LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "15000"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))
LLM_FAKE = _bool("LLM_FAKE", False)            # simulated answers (no LLM access yet)
# Anti-hallucination: a value is kept only if confidence >= threshold AND its
# evidence quote is found in the article text.
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))
AI_REQUIRE_EVIDENCE = _bool("AI_REQUIRE_EVIDENCE", True)
AI_FEW_SHOT_EXAMPLES = int(os.getenv("AI_FEW_SHOT_EXAMPLES", "3"))

# --- Capture ------------------------------------------------------------------
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "chrome")  # "chrome", "msedge" or "" (bundled Chromium)
CAPTURE_FAKE = _bool("CAPTURE_FAKE", False)               # no browser (tests)
CAPTURE_TIMEOUT_MS = int(os.getenv("CAPTURE_TIMEOUT_MS", "45000"))

# --- Job queue ------------------------------------------------------------------
WORKER_AUTOSTART = _bool("WORKER_AUTOSTART", True)
JOB_MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "3"))
DOMAIN_MIN_INTERVAL_S = float(os.getenv("DOMAIN_MIN_INTERVAL_S", "2"))
SOCIAL_MIN_INTERVAL_S = float(os.getenv("SOCIAL_MIN_INTERVAL_S", "20"))
SOCIAL_MAX_INTERVAL_S = float(os.getenv("SOCIAL_MAX_INTERVAL_S", "40"))
SOCIAL_DAILY_CAP = int(os.getenv("SOCIAL_DAILY_CAP", "150"))

# --- Distribution -------------------------------------------------------------
QUEUE_CAP_PER_USER = int(os.getenv("QUEUE_CAP_PER_USER", "15"))  # 0 = no cap
NEAR_DUPLICATE_MAX_DISTANCE = int(os.getenv("NEAR_DUPLICATE_MAX_DISTANCE", "3"))
STALE_WARNING_DAYS = int(os.getenv("STALE_WARNING_DAYS", "3"))
STALE_CRITICAL_DAYS = int(os.getenv("STALE_CRITICAL_DAYS", "7"))

# --- AntBox -------------------------------------------------------------------
REFERENTIAL_FILE = Path(os.getenv("REFERENTIAL_FILE", DATA_DIR / "referentiel_antbox.txt"))
AI_SKIPPED_FIELDS = _list("AI_SKIPPED_FIELDS", "Attachment redactor")   # never filled by the LLM
REQUIRED_FIELDS = _list("REQUIRED_FIELDS", "Document type,Domain")     # needed before approval
CSV_SEPARATOR = os.getenv("CSV_SEPARATOR", ";")
CSV_ENCODING = os.getenv("CSV_ENCODING", "utf-8-sig")
CSV_MULTI_SEPARATOR = os.getenv("CSV_MULTI_SEPARATOR", "|")
CSV_VALUES = os.getenv("CSV_VALUES", "label")     # "label" or "id"

# --- Themes (dashboard-only tags, never exported) -----------------------------
DEFAULT_THEMES = _list(
    "DEFAULT_THEMES",
    "Artificial Intelligence,Autonomy,Batteries & Energy,Cybersecurity,Defense,Drones,"
    "Hydrogen,Manufacturing,Materials,Space,Sustainability,Maintenance",
)

# --- Shared social account ----------------------------------------------------
SOCIAL_SESSION_FILE = Path(os.getenv("SOCIAL_SESSION_FILE", SECRETS_DIR / "social_session.json"))
SOCIAL_DOMAINS = _list(
    "SOCIAL_DOMAINS",
    "linkedin.com,lnkd.in,x.com,twitter.com,t.co,facebook.com,instagram.com,threads.net,reddit.com,youtube.com",
)
SOCIAL_LOGIN_PAGES = _list("SOCIAL_LOGIN_PAGES", "https://www.linkedin.com/login")

# --- Notifications ------------------------------------------------------------
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK", "")
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "9"))   # daily digest time (local), -1 = off
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")

CORS_ORIGINS = _list("CORS_ORIGINS", "http://localhost:4200")


def find_referential() -> Path:
    """Use REFERENTIAL_FILE if present, else the first .csv/.txt in data/ whose
    name contains 'referentiel', 'referential' or 'antbox'."""
    global REFERENTIAL_FILE
    if REFERENTIAL_FILE.exists() or os.getenv("REFERENTIAL_FILE"):
        return REFERENTIAL_FILE
    for f in sorted(DATA_DIR.glob("*")):
        name = f.name.lower()
        if f.suffix.lower() in (".csv", ".txt", ".tsv") and any(k in name for k in ("referentiel", "referential", "antbox")):
            REFERENTIAL_FILE = f
            break
    return REFERENTIAL_FILE


def use_system_certificates() -> None:
    """Trust the OS certificate store (corporate proxy)."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


for _d in (DATA_DIR, PDF_DIR, REGISTRY_DIR, EXPORT_DIR, REPORT_DIR, SECRETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
