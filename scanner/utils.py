from __future__ import annotations
import re
import os
import unicodedata
from urllib.parse import urlparse, urlunparse, urljoin

def normalize_url(url: str) -> str:
    url = url.strip()
    p = urlparse(url)
    p = p._replace(fragment="")
    netloc = (p.netloc or "").lower()
    scheme = (p.scheme or "https").lower()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, p.params, p.query, ""))

def same_domain(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc or "").lower() == (pb.netloc or "").lower()

def base_origin(url: str) -> str:
    p = urlparse(url)
    scheme = p.scheme or "https"
    return f"{scheme}://{p.netloc}"

def safe_filename(s: str, max_len: int = 140) -> str:
    s = unicodedata.normalize("NFKD", s)
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
