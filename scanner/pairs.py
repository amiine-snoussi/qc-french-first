from __future__ import annotations
from typing import Dict, Any, List
from urllib.parse import urlparse, urlunparse

EN_PREFIXES = ["/en-ca", "/en-us", "/en", "/en.html"]
FR_PREFIXES = ["/fr-ca", "/fr", "/fr.html"]

KEY_TYPES_P0 = {"home", "cart", "checkout", "contact"}
KEY_TYPES_P1 = {"products", "services", "faq", "returns", "about"}

def _norm(url: str) -> str:
    try:
        u = urlparse(url)
        # drop query/fragment for pairing stability
        u = u._replace(query="", fragment="")
        return urlunparse(u)
    except Exception:
        return url

def _path(url: str) -> str:
    try:
        p = urlparse(url).path or "/"
        p = p.rstrip("/") or "/"
        return p
    except Exception:
        return "/"

def _strip_prefix(path: str, pref: str) -> str:
    if path == pref:
        return "/"
    if path.startswith(pref + "/"):
        rest = path[len(pref):]
        return rest if rest.startswith("/") else "/" + rest
    return path

def _strip_lang(path: str) -> str:
    for pref in EN_PREFIXES + FR_PREFIXES:
        path2 = _strip_prefix(path, pref)
        if path2 != path:
            return path2
    return path

# Patch B (recommended): set lang based on path including .html
# ✅ Replace your current _lang_from_path with THIS (in pairs.py)

def _lang_from_path(path: str) -> str | None:
    # Canada-style language roots
    if path == "/fr.html":
        return "fr"
    if path == "/en.html":
        return "en"

    for pref in FR_PREFIXES:
        if path == pref or path.startswith(pref + "/"):
            return "fr"
    for pref in EN_PREFIXES:
        if path == pref or path.startswith(pref + "/"):
            return "en"
    return None


def _pick_best(urls: List[str], prefer_prefixes: List[str]) -> str | None:
    if not urls:
        return None
    # scoring: prefer shorter + preferred prefixes
    best = None
    best_score = -10**9
    for u in urls:
        p = _path(u)
        score = -len(p)
        for i, pref in enumerate(prefer_prefixes):
            if p == pref or p.startswith(pref + "/"):
                score += (100 - i)
        if score > best_score:
            best_score = score
            best = u
    return best

def build_pairs(pages: List[Dict[str, Any]], base_url: str) -> Dict[str, Any]:
    """
    Build EN↔FR pairing by canonical core path (path stripped of /fr, /en-us, etc).
    """
    groups: Dict[str, Dict[str, Any]] = {}

    for pg in pages or []:
        url = pg.get("final_url") or pg.get("url")
        url = _norm(str(url or ""))
        if not url:
            continue

        path = _path(url)

        # Only pair pages that are actually reachable. If EN page is 404, it should NOT trigger "missing FR".
        if pg.get("error"):
            continue
        st = pg.get("status")
        try:
            st_i = int(st) if st is not None else None
        except Exception:
            st_i = None
        if st_i is None or st_i >= 400:
            continue

        # skip noisy checkout sessions
        if path.startswith("/checkouts/"):
            continue

        core = _strip_lang(path)
        core = core.rstrip("/") or "/"

        lang = _lang_from_path(path) or pg.get("lang") or "unknown"
        if lang not in ("en", "fr"):
            # treat unknown as en (common when html lang missing but URL is root)
            lang = "en" if _lang_from_path(path) is None else lang

        g = groups.setdefault(core, {"core": core, "en": [], "fr": [], "key_type": None})
        g[lang].append(url)

        kt = pg.get("key_type")
        if kt and not g["key_type"]:
            g["key_type"] = kt

    rows: List[Dict[str, Any]] = []
    missing_fr_total = 0
    missing_en_total = 0
    missing_fr_key = 0
    total_pairs = 0

    for core, g in groups.items():
        # Patch A (must): prefer /en.html over /
        en_best = _pick_best(g["en"], prefer_prefixes=EN_PREFIXES + ["/"])
        fr_best = _pick_best(g["fr"], prefer_prefixes=FR_PREFIXES)

        if not en_best and not fr_best:
            continue

        total_pairs += 1
        if en_best and not fr_best:
            status = "missing_fr"
            missing_fr_total += 1
            if g.get("key_type") in KEY_TYPES_P0 or g.get("key_type") in KEY_TYPES_P1:
                missing_fr_key += 1
        elif fr_best and not en_best:
            status = "missing_en"
            missing_en_total += 1
        else:
            status = "ok"

        kt = g.get("key_type") or ""
        if kt in KEY_TYPES_P0:
            pr = "P0"
        elif kt in KEY_TYPES_P1:
            pr = "P1"
        else:
            pr = "P2" if status != "ok" else "P3"

        rows.append({
            "path": core,
            "key_type": kt or None,
            "priority": pr,
            "status": status,
            "en_url": en_best,
            "fr_url": fr_best,
        })

    # sort: missing_fr first, then by priority
    pr_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    st_rank = {"missing_fr": 0, "missing_en": 1, "ok": 2}
    rows.sort(key=lambda r: (st_rank.get(r["status"], 9), pr_rank.get(r["priority"], 9), r["path"]))

    return {
        "summary": {
            "total_pairs": total_pairs,
            "missing_fr_total": missing_fr_total,
            "missing_fr_key": missing_fr_key,
            "missing_en_total": missing_en_total,
        },
        "rows": rows,
    }
