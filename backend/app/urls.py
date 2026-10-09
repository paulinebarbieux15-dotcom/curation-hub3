"""URL extraction (smart paste), normalization (duplicate detection) and text fingerprints."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "igsh", "ref", "ref_src",
    "ref_url", "si", "s", "spm", "trk", "trkInfo", "_hsenc", "_hsmi", "mkt_tok", "yclid", "vero_id",
    "rcm", "lipi", "originalSubdomain", "share", "feature", "cmpid", "ocid", "sr_share",
}

# http(s) links, www. links and bare domains followed by a path ("lnkd.in/abc")
_URL_RE = re.compile(
    r"""(?xi)
    \b(
      (?:https?://|www\.)[^\s<>"'`]+
      |
      (?:[a-z0-9-]+\.)+[a-z]{2,24}/[^\s<>"'`]+
    )""")
_TRAILING = ".,;:!?)]}>'\"»”’"


def extract_urls(text: str) -> list[str]:
    """Every URL found in free text, in order, without exact repeats."""
    found, seen = [], set()
    for match in _URL_RE.finditer(text or ""):
        url = match.group(1)
        # strip trailing punctuation, keeping balanced parentheses (Wikipedia-style URLs)
        while url and url[-1] in _TRAILING:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if url.lower().startswith("www."):
            url = "https://" + url
        elif "://" not in url:
            url = "https://" + url
        if is_valid_url(url) and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def is_valid_url(url: str) -> bool:
    try:
        p = urlsplit(url.strip() if "://" in url else "https://" + url.strip())
    except ValueError:
        return False
    host = p.hostname or ""
    return p.scheme in ("http", "https") and "." in host and " " not in url.strip() \
        and re.fullmatch(r"[a-z0-9.-]+", host.lower()) is not None


def normalize_url(url: str) -> str:
    """https://www.Site.com/Article/?utm_source=x&id=3#top  ->  https://site.com/Article?id=3"""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:   # mobile sites
        host = host[2:]
    path = re.sub(r"/{2,}", "/", p.path).rstrip("/")
    params = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k not in TRACKING_PARAMS
    )
    return urlunsplit(("https", host, path, urlencode(params), ""))


def clean_url(url: str) -> str:
    """The URL as pasted, minus tracking parameters (utm_*, fbclid...) and the #fragment."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith("utm_") and k not in TRACKING_PARAMS]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(params), ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------ SimHash
_WORD = re.compile(r"[\w'-]{3,}", re.UNICODE)


def simhash(text: str, bits: int = 64) -> str:
    """64-bit SimHash of the text (as hex). Close texts give close hashes."""
    words = _WORD.findall((text or "").lower())
    if len(words) < 20:
        return ""
    shingles = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    weights = [0] * bits
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            weights[i] += 1 if (h >> i) & 1 else -1
    value = sum(1 << i for i in range(bits) if weights[i] > 0)
    return f"{value:016x}"


def hamming(a: str, b: str) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")
