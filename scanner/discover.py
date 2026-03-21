from __future__ import annotations

import re
import asyncio
import time
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Set, Tuple
from urllib.parse import urlparse
from playwright.async_api import async_playwright

from .utils import normalize_url, same_domain, base_origin, absolutize
from .platforms import detect_platform

SITEMAP_RE = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")

# "E-commerce" probe paths are useful for Shopify audits but create noise for non-Shopify sites.
_ECOM_KEY_PATH_HINTS = (
    "/cart",
    "/checkout",
    "/shop",
    "/product",
    "/products",
    "/collection",
    "/collections",
)

def _prefetch_platform(base_url: str, ua: str) -> Dict[str, Any]:
    """
    Best-effort platform detection before full crawl.
    Returns platform dict +:
      - _prefetch_html: homepage HTML (lower quality is ok, used only for probe filtering)
      - _prefetch_final_url: final URL after redirects
    """
    origin = base_origin(base_url).rstrip("/")
    try:
        r = requests.get(origin + "/", headers={"User-Agent": ua}, timeout=12, allow_redirects=True)
        html = r.text or ""
        final_url = r.url or (origin + "/")
        out = detect_platform(html, final_url) or {"name": "Unknown", "confidence": 0.0}
        out["_prefetch_html"] = html
        out["_prefetch_final_url"] = final_url
        return out
    except Exception:
        return {
            "name": "Unknown",
            "confidence": 0.0,
            "_prefetch_html": "",
            "_prefetch_final_url": origin + "/",
        }

def _is_probably_page(url: str) -> bool:
    u = (url or "").lower()
    bad_ext = (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
        ".pdf", ".zip", ".mp4", ".mp3", ".css", ".js", ".xml"
    )
    if any(u.endswith(x) for x in bad_ext):
        return False
    if "/sitemap" in u:
        return False
    return True

def _skip_contains(url: str, patterns: List[str]) -> bool:
    u = (url or "").lower()
    return any(p.lower() in u for p in patterns)

def _get(url: str, ua: str, timeout: int = 15) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text or ""
    except Exception:
        return 0, ""

def _discover_sitemaps(base_url: str, ua: str) -> List[str]:
    origin = base_origin(base_url)
    robots_url = origin.rstrip("/") + "/robots.txt"
    code, txt = _get(robots_url, ua)
    sitemaps: List[str] = []
    if code and txt:
        for m in SITEMAP_RE.finditer(txt):
            sitemaps.append(m.group(1).strip())
    if not sitemaps:
        sitemaps.append(origin.rstrip("/") + "/sitemap.xml")
    out, seen = [], set()
    for s in sitemaps:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out

def _parse_sitemap(xml_text: str) -> Tuple[List[str], bool]:
    try:
        soup = BeautifulSoup(xml_text, "xml")
        is_index = soup.find("sitemapindex") is not None
        locs = [loc.text.strip() for loc in soup.find_all("loc") if loc and loc.text]
        return locs, is_index
    except Exception:
        return [], False

def _collect_page_urls_from_sitemaps(base_url: str, ua: str, max_depth: int = 3) -> Set[str]:
    sitemaps = _discover_sitemaps(base_url, ua)
    seen_sm: Set[str] = set()
    out_pages: Set[str] = set()
    queue: List[Tuple[str, int]] = [(sm, 0) for sm in sitemaps]

    while queue:
        sm_url, d = queue.pop(0)
        if sm_url in seen_sm or d > max_depth:
            continue
        seen_sm.add(sm_url)

        code, txt = _get(sm_url, ua, timeout=20)
        if code != 200 or not txt:
            continue

        locs, is_index = _parse_sitemap(txt)

        if is_index:
            for child in locs:
                # keep only sitemap-ish children
                if "sitemap" in child.lower():
                    queue.append((child, d + 1))
            continue

        for u in locs:
            if not u.startswith("http"):
                continue
            nu = normalize_url(u)
            if same_domain(nu, base_url) and _is_probably_page(nu):
                out_pages.add(nu)

    return out_pages

async def _pw_extract_links(page) -> List[str]:
    return await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
          .map(a => a.href)
          .filter(Boolean)
    """)

async def _discover_with_playwright(base_url: str, cfg: Dict[str, Any], seed: Set[str]) -> Set[str]:
    crawler = cfg.get("crawler", {})
    disc = cfg.get("discover", {})
    ua = crawler.get("user_agent", "QC-FrenchFirst-Scanner/1.0")

    max_pages = int(disc.get("max_pages", 60))
    max_depth = int(disc.get("max_depth", 2))
    timeout_ms = int(disc.get("timeout_ms", 12000))
    # IMPORTANT:
    # We enforce a *soft* time budget inside the crawl loop (instead of asyncio.wait_for),
    # so Playwright can shut down cleanly without noisy "Future exception was never retrieved" warnings.
    hard_timeout_s = int(disc.get("hard_timeout_s", 45))
    max_queue = int(disc.get("max_queue", 250))
    exclude_contains = disc.get("exclude_contains", [])

    origin = base_origin(base_url).rstrip("/")
    start = normalize_url(origin + "/")

    visited: Set[str] = set()
    discovered: Set[str] = set()
    q: List[Tuple[str, int]] = [(start, 0)]

    print(f"[discover/pw] start={start} max_pages={max_pages} max_depth={max_depth} max_queue={max_queue}", flush=True)

    async def _runner() -> Set[str]:
        t_start = time.monotonic()
        async with async_playwright() as p:
            print("[discover/pw] launching chromium...", flush=True)
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=ua,
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 720},
            )

            step = 0
            while q and len(discovered) < max_pages:
                if (time.monotonic() - t_start) >= float(hard_timeout_s):
                    print(f"[discover/pw] HARD TIMEOUT after {hard_timeout_s}s -> returning what we have (+seed)", flush=True)
                    break

                if len(q) > max_queue:
                    del q[max_queue:]

                url, depth = q.pop(0)
                if url in visited or depth > max_depth:
                    continue
                visited.add(url)

                if (not same_domain(url, base_url)) or (not _is_probably_page(url)) or _skip_contains(url, exclude_contains):
                    continue

                step += 1
                print(f"[discover/pw] ({step}) depth={depth} visiting {url}", flush=True)

                page = await context.new_page()
                links: List[str] = []
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await page.wait_for_timeout(800)
                    links = await _pw_extract_links(page)
                except Exception as e:
                    print(f"[discover/pw]    ! error: {str(e)[:160]}", flush=True)
                finally:
                    await page.close()

                discovered.add(url)

                added = 0
                for href in links:
                    try:
                        n = normalize_url(href)
                    except Exception:
                        continue
                    if not same_domain(n, base_url):
                        continue
                    if not _is_probably_page(n):
                        continue
                    if _skip_contains(n, exclude_contains):
                        continue
                    if n not in visited:
                        q.append((n, depth + 1))
                        added += 1

                print(f"[discover/pw]    +{added} links (queue={len(q)}) discovered={len(discovered)}", flush=True)

            await context.close()
            await browser.close()

        discovered.update({normalize_url(u) for u in seed})
        return discovered

    try:
        return await _runner()
    except Exception as e:
        print(f"[discover/pw] FAILED: {e} -> seed only", flush=True)
        return {normalize_url(u) for u in seed}

def _sample_sitemap_urls(sitemap_pages: Set[str], cfg: Dict[str, Any]) -> Set[str]:
    disc = cfg.get("discover", {})
    product_n = int(disc.get("product_sample_n", 12))
    coll_n = int(disc.get("collection_sample_n", 6))

    def path(u: str) -> str:
        p = urlparse(u)
        return (p.path or "/").rstrip("/") or "/"

    products = sorted([u for u in sitemap_pages if ("/products/" in u) or path(u).endswith("/products")])
    colls = sorted([u for u in sitemap_pages if ("/collections/" in u) or path(u).endswith("/collections")])
    pages = sorted([u for u in sitemap_pages if ("/pages/" in u) or ("/policies/" in u) or ("/policy" in u) or ("/contact" in u)])

    out: Set[str] = set()
    out.update(pages[:120])
    out.update(products[:product_n])
    out.update(colls[:coll_n])

    # Never return 0: fallback sample
    if not out and sitemap_pages:
        out.update(sorted(sitemap_pages)[:30])

    return out

def _likely_key_urls(base_url: str, key_paths: List[str], fr_variants: List[str], en_variants: List[str]) -> Set[str]:
    origin = base_origin(base_url).rstrip("/")
    urls: Set[str] = set()
    urls.add(normalize_url(origin + "/"))
    urls.add(normalize_url(base_url))
    for kp in key_paths:
        urls.add(normalize_url(origin + kp))
    for v in fr_variants:
        urls.add(normalize_url(origin + v))
    for v in en_variants:
        urls.add(normalize_url(origin + v))

    # ✅ FORCE language landing pages for canada.ca (they may not appear in prefetch HTML)
    if "www.canada.ca" in origin:
        urls.add(normalize_url(origin + "/en.html"))
        urls.add(normalize_url(origin + "/fr.html"))

    return urls


def discover_urls(base_url: str, cfg: Dict[str, Any]) -> List[str]:
    crawler = cfg.get("crawler", {})
    heur = cfg.get("heuristics", {})
    disc = cfg.get("discover", {})
    ua = crawler.get("user_agent", "QC-FrenchFirst-Scanner/1.0")
    max_pages_final = int(crawler.get("max_pages", 120))

    # Preflight platform detection (best-effort). Used only to reduce noise from irrelevant probe paths.
    platform: Dict[str, Any] = {"name": "Unknown", "confidence": 0.0, "_prefetch_html": "", "_prefetch_final_url": base_origin(base_url).rstrip("/") + "/"}
    if bool(disc.get("prefetch_platform", True)):
        platform = _prefetch_platform(base_url, ua)

    # PATCH 2 — stop probing key paths unless the homepage actually references them
    home_html = (platform.get("_prefetch_html") or "")
    home_l = home_html.lower()

    def _mentioned(path: str) -> bool:
        p = (path or "").lower()
        return bool(p) and (p in home_l)

    # Filter language variants: only include if base_url already has it OR homepage links to it
    raw_fr_vars = list((cfg.get("heuristics", {}) or {}).get("french_home_variants", []) or [])
    raw_en_vars = list((cfg.get("heuristics", {}) or {}).get("english_home_variants", []) or [])

    fr_variants = [v for v in raw_fr_vars if (v in base_url) or _mentioned(v)]
    en_variants = [v for v in raw_en_vars if (v in base_url) or _mentioned(v)]

    # Filter key path probes: only include if homepage mentions it (or Shopify+cart)
    heur2 = cfg.get("heuristics", {}) or {}
    raw_key_paths = list(heur2.get("key_paths", []) or [])

    is_shopifyish = (platform.get("name") == "Shopify") and ((platform.get("confidence") or 0) >= 0.5)

    key_paths: List[str] = []
    for kp in raw_key_paths:
        if kp == "/":
            key_paths.append(kp)
            continue

        if kp in _ECOM_KEY_PATH_HINTS:
            if is_shopifyish or _mentioned(kp):
                key_paths.append(kp)
        else:
            if _mentioned(kp):
                key_paths.append(kp)

    seed = _likely_key_urls(base_url, key_paths, fr_variants, en_variants)

    sitemap_pages = _collect_page_urls_from_sitemaps(base_url, ua)
    sitemap_sample = _sample_sitemap_urls(sitemap_pages, cfg)

    pw_found: Set[str] = set()
    if bool(disc.get("use_playwright", True)):
        print("[discover] Playwright discovery enabled (JS-rendered links)", flush=True)
        pw_found = asyncio.run(_discover_with_playwright(base_url, cfg, seed))
    else:
        print("[discover] Playwright discovery disabled", flush=True)

    merged: List[str] = []
    seen: Set[str] = set()

    def add(u: str):
        u = normalize_url(u)
        if u not in seen and same_domain(u, base_url) and _is_probably_page(u):
            merged.append(u)
            seen.add(u)

    for u in sorted(seed):
        add(u)
    for u in sorted(pw_found):
        add(u)
    for u in sorted(sitemap_sample):
        add(u)

    print(
        f"[discover] seed={len(seed)} pw={len(pw_found)} sitemap_pages={len(sitemap_pages)} "
        f"sitemap_sample={len(sitemap_sample)} -> total={len(merged)} (cap {max_pages_final})",
        flush=True,
    )
    return merged[:max_pages_final]
