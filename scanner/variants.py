from __future__ import annotations
from typing import List, Set
from urllib.parse import urlparse
from .utils import normalize_url, base_origin, same_domain

EN_PREFIXES = ["/en-ca", "/en-us", "/en"]
FR_PREFIXES = ["/fr-ca", "/fr"]

IMPORTANT_PREFIXES = (
    "/pages",
    "/policies",
    "/products",
    "/collections",
)

IMPORTANT_EXACT = (
    "/",
    "/cart",
    "/checkout",
    "/collections",
    "/products",
    "/pages",
    "/policies",
)

def _pick_prefix_from_urls(urls: List[str], candidates: List[str]) -> str:
    for c in candidates:
        for u in urls:
            try:
                p = urlparse(u).path or "/"
            except Exception:
                continue
            if p == c or p.startswith(c + "/"):
                return c
    return ""

def _strip_any_prefix(path: str, prefixes: List[str]) -> str:
    if path == "/":
        return "/"
    for pref in prefixes:
        if path == pref:
            return "/"
        if path.startswith(pref + "/"):
            rest = path[len(pref):]
            return rest if rest.startswith("/") else ("/" + rest)
    return path

def _is_important(path: str) -> bool:
    if path in IMPORTANT_EXACT:
        return True
    # treat /collections and /collections/... equally
    return any(path == p or path.startswith(p + "/") for p in IMPORTANT_PREFIXES)

def expand_language_variants(urls: List[str], base_url: str, cfg: dict) -> List[str]:
    origin = base_origin(base_url).rstrip("/")
    base_urls = [normalize_url(u) for u in urls if same_domain(u, base_url)]

    fr_pref = _pick_prefix_from_urls(base_urls, FR_PREFIXES) or "/fr"
    seen: Set[str] = set(base_urls)
    out: List[str] = list(base_urls)

    added_fr = 0
    added_en = 0
    MAX_ADD_FR = 80
    MAX_ADD_EN = 30

    for u in list(base_urls):
        try:
            p = urlparse(u)
            path = (p.path or "/").rstrip("/") or "/"
        except Exception:
            continue

        # Shopify checkout sessions are noisy + not pairable
        if path.startswith("/checkouts/"):
            continue

        if not _is_important(path):
            continue

        core = _strip_any_prefix(path, EN_PREFIXES + FR_PREFIXES)  # core path without lang prefix

        # FR variant
        if added_fr < MAX_ADD_FR:
            if core == "/":
                fr_path = fr_pref
            else:
                fr_path = fr_pref + core
            fr_url = normalize_url(origin + fr_path)
            if fr_url not in seen:
                out.append(fr_url); seen.add(fr_url); added_fr += 1

        # EN/root variant (from FR pages)
        if added_en < MAX_ADD_EN and (path == fr_pref or path.startswith(fr_pref + "/")):
            en_path = core
            en_url = normalize_url(origin + en_path)
            if en_url not in seen:
                out.append(en_url); seen.add(en_url); added_en += 1

    print(f"[variants] added_fr={added_fr} added_en={added_en} (fr_pref={fr_pref})", flush=True)
    return out
