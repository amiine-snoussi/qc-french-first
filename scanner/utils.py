from __future__ import annotations

import re
import os
import unicodedata
from urllib.parse import urlparse, urlunparse, urljoin, parse_qsl, urlencode


# Drop common tracking params to avoid duplicate URLs
_TRACKING_KEYS = {"gclid", "fbclid", "msclkid", "wbraid", "gbraid"}


def _is_tracking_key(k: str) -> bool:
    k = (k or "").lower()
    return k.startswith("utm_") or (k in _TRACKING_KEYS)


def normalize_url(url: str) -> str:
    """
    Canonical URL used everywhere:
    - lowercases scheme + host
    - strips fragment
    - strips trailing slash (except '/')
    - keeps query params (EXCEPT common trackers like utm_*, gclid, fbclid...)
      IMPORTANT: preserves query-based language switches like ?uselang=fr, ?lang=fr, ?locale=fr_CA
    """
    url = (url or "").strip()
    p = urlparse(url)
    p = p._replace(fragment="")

    netloc = (p.netloc or "").lower()
    scheme = (p.scheme or "https").lower()

    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query = p.query or ""
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        pairs = [(k, v) for (k, v) in pairs if not _is_tracking_key(k)]
        query = urlencode(pairs, doseq=True)

    return urlunparse((scheme, netloc, path, p.params, query, ""))


def _apex_domain(host: str) -> str:
    # Normalize host (strip port, lowercase, strip trailing dot)
    host = (host or "").split(":")[0].lower().strip(".")
    if not host:
        return ""

    # Very small IP heuristic (IPv4) — keep as-is
    if all(c.isdigit() or c == "." for c in host):
        return host

    parts = [x for x in host.split(".") if x]
    if len(parts) <= 2:
        return host

    # Common “2-level” public suffixes (good enough for most cases)
    two_level_suffixes = {
        "co.uk", "org.uk", "ac.uk", "gov.uk",
        "com.au", "net.au", "org.au",
        "co.nz",
        "co.jp",
        "com.br",
        "com.mx",
    }

    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:])

    # ex: foo.bar.co.uk -> bar.co.uk
    if last2 in two_level_suffixes and len(parts) >= 3:
        return last3

    # default: example.com
    return last2


def same_domain(a: str, b: str) -> bool:
    """
    Treat subdomains as the same “site” (apex domain match).
    Examples:
      - www.example.com == m.example.com  -> True
      - fr.example.com  == example.com    -> True
      - example.com     == example.org    -> False
    """
    try:
        pa = urlparse(a)
        pb = urlparse(b)
    except Exception:
        return False

    ha = (pa.hostname or "").lower()
    hb = (pb.hostname or "").lower()
    if not ha or not hb:
        return False

    if ha == hb:
        return True

    return _apex_domain(ha) == _apex_domain(hb)


def base_origin(url: str) -> str:
    p = urlparse(url)
    scheme = p.scheme or "https"
    return f"{scheme}://{p.netloc}"


def safe_filename(s: str, max_len: int = 140) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii", "ignore").decode("ascii", errors="ignore")
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    s = s.strip("_")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "page"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def absolutize(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def norm_url(u: str) -> str:
    # Backwards-compatible alias (DO NOT drop query, language switches depend on it)
    return normalize_url(u)
