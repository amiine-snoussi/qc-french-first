from __future__ import annotations
import re
from typing import Dict, Any, List, Optional, Tuple
from bs4 import BeautifulSoup
import langid
from urllib.parse import urlparse, parse_qs

from .utils import normalize_url, base_origin, same_domain, absolutize
from .platforms import detect_platform

FR_TOKENS = ["français", "francais", "fr", "fr-ca", "fr_ca", "french"]
EN_TOKENS = ["english", "en", "en-ca", "en_ca", "anglais"]

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def _ok_fetch(page: dict) -> bool:
    """True only for 2xx/3xx, or when status is missing but HTML exists.
    IMPORTANT: status might be int or str (e.g. "404")."""
    st = page.get("status")
    st_i = None
    try:
        st_i = int(st)
    except Exception:
        st_i = None

    if st_i is not None:
        return 200 <= st_i < 400
    return bool(page.get("html"))

def _get_html_lang(soup: BeautifulSoup) -> Optional[str]:
    html = soup.find("html")
    if html and html.get("lang"):
        return _clean(html.get("lang"))
    return None

def _get_hreflang_alternates(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for link in soup.find_all("link", attrs={"rel": True, "hreflang": True, "href": True}):
        rel = link.get("rel")
        if isinstance(rel, list):
            rel = " ".join(rel)
        if rel and "alternate" in str(rel).lower():
            hl = _clean(link.get("hreflang"))
            href = link.get("href")
            if hl and href:
                out[hl] = href
    return out

def _lang_guess(text: str, min_conf: float) -> Tuple[str, float]:
    if not text or len(text) < 80:
        return ("unknown", 0.0)
    lang, _score = langid.classify(text)
    if lang not in ("fr", "en"):
        return ("unknown", 0.0)
    conf = min(0.99, max(0.5, len(text) / 4000.0))
    if conf < min_conf:
        return ("unknown", conf)
    return (lang, conf)

def _find_switch_links(soup: BeautifulSoup, page_url: str) -> Dict[str, List[Dict[str, str]]]:
    areas = []
    for tag in ["header", "nav", "footer"]:
        for node in soup.find_all(tag):
            areas.append((tag, node))

    found = {"header": [], "nav": [], "footer": [], "other": []}

    common = soup.find_all(attrs={"class": re.compile(r"(lang|language|locale|switch)", re.I)})
    for node in common:
        areas.append(("other", node))

    seen = set()
    for area_tag, node in areas:
        for a in node.find_all("a", href=True):
            href = a.get("href", "").strip()
            txt = _clean(a.get_text(" ", strip=True))
            if not href:
                continue
            absu = absolutize(page_url, href)
            key = (area_tag, absu, txt)
            if key in seen:
                continue
            seen.add(key)

            blob = f"{txt} {absu.lower()}"
            looks_lang = any(t in blob for t in (FR_TOKENS + EN_TOKENS))
            q = parse_qs(urlparse(absu).query)
            if any(k.lower() in ("lang", "locale") for k in q.keys()):
                looks_lang = True

            if looks_lang:
                bucket = area_tag if area_tag in found else "other"
                found[bucket].append({"text": txt, "href": absu})
    return found

def _pick_french_link(switch_links: Dict[str, List[Dict[str, str]]]) -> Optional[str]:
    order = ["header", "nav", "footer", "other"]
    for area in order:
        for item in switch_links.get(area, []):
            blob = f"{item.get('text','')} {item.get('href','')}".lower()
            if "lang=fr" in blob or "locale=fr" in blob or "/fr" in blob or "fr-ca" in blob or "fr_" in blob:
                return item.get("href")
            if any(tok in blob for tok in ["français", "francais", "french"]):
                return item.get("href")
    return None

def _derive_french_candidate(en_url: str) -> List[str]:
    p = urlparse(en_url)
    origin = f"{p.scheme}://{p.netloc}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    candidates = []
    if path.startswith("/en/"):
        candidates.append(origin + "/fr/" + path[len("/en/"):])
    if path == "/en":
        candidates.append(origin + "/fr")

    if not path.startswith("/fr"):
        if path == "/":
            candidates.extend([origin + "/fr", origin + "/fr-ca"])
        else:
            candidates.append(origin + "/fr" + path)
            candidates.append(origin + "/fr-ca" + path)

    candidates.append(origin + (path if path else "/") + ("?lang=fr" if not p.query else "&lang=fr"))
    candidates.append(origin + (path if path else "/") + ("?locale=fr_CA" if not p.query else "&locale=fr_CA"))

    host = p.netloc
    if not host.startswith("fr."):
        candidates.append(f"{p.scheme}://fr.{host}{path}")

    out = []
    seen = set()
    for c in candidates:
        try:
            u = normalize_url(c)
            if u not in seen:
                out.append(u)
                seen.add(u)
        except Exception:
            pass
    return out

# PATCHED: home label matches /, /en, /fr, /en-ca, /fr-ca, etc.
# (_key_type strips trailing slash already, so /en/ becomes /en)
KEY_LABELS = [
    ("home", re.compile(r"^/(?:$|en(?:-[a-z]{2})?|fr(?:-[a-z]{2})?)$")),
    ("contact", re.compile(r"/contact(-us)?$")),
    ("about", re.compile(r"/about(-us)?$")),
    ("services", re.compile(r"/services?$|/service$")),
    ("products", re.compile(r"/products?$|/product$|/collections?$|/shop$")),
    ("cart", re.compile(r"/cart$")),
    ("checkout", re.compile(r"/checkout$")),
    ("faq", re.compile(r"/faq$|/help$")),
    ("returns", re.compile(r"/returns$|/shipping$|/policies$")),
]

def _key_type(url: str) -> Optional[str]:
    path = urlparse(url).path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    for name, rx in KEY_LABELS:
        if rx.search(path):
            return name
    return None

def analyze_site(base_url: str, pages: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    heur = cfg.get("heuristics", {})
    min_conf = float(heur.get("langid_min_conf", 0.82))

    by_url: Dict[str, Dict[str, Any]] = {}
    for p in pages:
        nu = normalize_url(p.get("final_url") or p.get("url") or "")
        p["norm_url"] = nu
        by_url[nu] = p

    # PATCH 2 — choose "home" based on what the user actually started scanning
    start_url_n = normalize_url(base_url)
    start_path = urlparse(start_url_n).path or "/"

    origin = base_origin(base_url).rstrip("/")
    origin_home_n = normalize_url(origin + "/")

    # If user started at a non-root path (e.g. /en), treat that as "home"
    home_target_n = start_url_n if start_path != "/" else origin_home_n

    # pick a representative home page for platform + signals
    home_candidates = []
    for u in (home_target_n, start_url_n, origin_home_n):
        if u and u not in home_candidates:
            home_candidates.append(u)

    home_page = None
    for c in home_candidates:
        if c in by_url:
            home_page = by_url[c]
            break
    if home_page is None and pages:
        home_page = pages[0]

    platform = detect_platform(home_page.get("html", ""), home_page.get("final_url", ""))

    analyzed_pages: List[Dict[str, Any]] = []
    for p in pages:
        html = p.get("html", "") or ""
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

        html_lang = _get_html_lang(soup)
        alt = _get_hreflang_alternates(soup)
        sw = _find_switch_links(soup, p.get("final_url") or p.get("url") or base_url)

        guess, conf = _lang_guess(p.get("text", "") or "", min_conf=min_conf)
        if html_lang:
            if html_lang.startswith("fr"):
                guess, conf = "fr", 0.99
            elif html_lang.startswith("en"):
                guess, conf = "en", 0.99

        fr_from_hreflang = None
        for k, v in alt.items():
            if k.startswith("fr"):
                fr_from_hreflang = v
                break
        fr_from_switch = _pick_french_link(sw)

        fr_candidates = []
        if fr_from_hreflang:
            fr_candidates.append(normalize_url(absolutize(p.get("final_url") or base_url, fr_from_hreflang)))
        if fr_from_switch:
            fr_candidates.append(normalize_url(fr_from_switch))
        fr_candidates.extend(_derive_french_candidate(p["norm_url"]))

        uniq = []
        seen = set()
        for u in fr_candidates:
            if u and same_domain(u, base_url) and u not in seen:
                uniq.append(u)
                seen.add(u)

        fr_present_url = None
        for cand in uniq:
            if cand in by_url and _ok_fetch(by_url.get(cand) or {}):
                fr_present_url = cand
                break

        fr_status = "missing"
        fr_url = None
        if fr_present_url:
            fr_status = "present"
            fr_url = fr_present_url
        elif uniq:
            fr_status = "candidate"
            fr_url = uniq[0]

        has_switch = any(sw.get(k) for k in sw.keys())
        switch_location = "none"
        if sw.get("header"):
            switch_location = "header"
        elif sw.get("nav"):
            switch_location = "nav"
        elif sw.get("footer"):
            switch_location = "footer"
        elif sw.get("other"):
            switch_location = "other"

        analyzed_pages.append({
            "norm_url": p["norm_url"],
            "url": p.get("url"),
            "final_url": p.get("final_url"),
            "status": p.get("status"),
            "error": p.get("error"),
            "screenshot_path": p.get("screenshot_path"),
            "html_lang": html_lang,
            "lang": guess,
            "lang_conf": conf,
            "key_type": _key_type(p["norm_url"]),
            "switch": {
                "has_switch": bool(has_switch),
                "location": switch_location,
                "links": sw,
            },
            "french": {
                "status": fr_status,
                "url": fr_url,
                "all_candidates": uniq[:8],
            },
            "hreflang": alt,
        })

    def _status_ok_for_keypage(ap: dict) -> bool:
        st = ap.get("status")
        try:
            st_i = int(st)
        except Exception:
            st_i = None
        return (st_i is not None) and (st_i < 400)

    # PATCH 3 — never treat a 4xx/5xx page as a key page
    # Key pages (best available instance for each type)
    key_pages: Dict[str, Dict[str, Any]] = {}
    for ap in analyzed_pages:
        # Only consider real pages (2xx/3xx)
        if not _status_ok_for_keypage(ap):
            continue

        kt = ap.get("key_type")
        if not kt:
            continue

        cur = key_pages.get(kt)
        if cur is None:
            key_pages[kt] = ap
        else:
            s1 = cur.get("status") or 0
            s2 = ap.get("status") or 0
            if (s2 == 200 and s1 != 200):
                key_pages[kt] = ap

    # PATCH 2 (continued) — force key_pages["home"] to match start URL when start URL is not "/"
    # Only use origin "/" as home when user started at "/".
    forced_home_src = None
    for ap in analyzed_pages:
        if ap.get("norm_url") == home_target_n and _status_ok_for_keypage(ap):
            forced_home_src = ap
            break

    if forced_home_src is None:
        cur_home = key_pages.get("home")
        if isinstance(cur_home, dict) and _status_ok_for_keypage(cur_home):
            forced_home_src = cur_home

    if forced_home_src is not None:
        forced_home = dict(forced_home_src)  # do not mutate analyzed_pages
        forced_home["key_type"] = "home"
        forced_home["url"] = home_target_n
        forced_home["final_url"] = home_target_n
        forced_home["norm_url"] = home_target_n
        key_pages["home"] = forced_home

    # home switch location
    home_switch_loc = "none"
    home_n = home_target_n
    if home_page:
        try:
            home_n = normalize_url(home_page.get("final_url") or home_page.get("url") or home_n)
        except Exception:
            pass

    for ap in analyzed_pages:
        if ap["norm_url"] == home_n:
            home_switch_loc = (ap.get("switch") or {}).get("location", "none")
            break

    return {
        "base_url": base_url,
        "origin": origin,
        "platform": platform,
        "pages": analyzed_pages,
        "key_pages": key_pages,
        "signals": {
            "has_any_french": any(ap.get("lang") == "fr" for ap in analyzed_pages),
            "home_switch_location": home_switch_loc,
        },
    }
