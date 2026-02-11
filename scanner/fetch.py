from __future__ import annotations
import asyncio
import time
import os
from typing import Dict, Any, List, Tuple
from playwright.async_api import async_playwright
from .utils import ensure_dir, safe_filename, normalize_url, base_origin

def _run_dir(base_url: str) -> str:
    domain = base_origin(base_url).replace("https://", "").replace("http://", "")
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    return os.path.join("runs", domain, stamp)

def _is_skip_url(url: str, skip_contains: List[str]) -> bool:
    u = (url or "").lower()
    return any(s.lower() in u for s in skip_contains)

def _is_fast_url(url: str, fast_contains: List[str]) -> bool:
    u = (url or "").lower()
    return any(s.lower() in u for s in fast_contains)

async def _fetch_one(context, idx: int, total: int, url: str, out_dir: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    crawler = cfg.get("crawler", {})
    timeout_ms = int(crawler.get("timeout_ms", 25000))
    fast_contains = crawler.get("fast_url_contains", ["/checkout"])
    page = await context.new_page()

    t0 = time.monotonic()
    print(f"[fetch {idx}/{total}] {url}", flush=True)

    final_url = url
    status = None
    err = None
    html = ""
    text = ""
    screenshot_path = ""

    try:
        # If it's a "fast" URL (e.g. /checkout), do minimal waiting
        is_fast = _is_fast_url(url, fast_contains)

        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=min(timeout_ms, 12000) if is_fast else timeout_ms,
        )

        if resp is not None:
            status = resp.status
            final_url = resp.url or url
        else:
            final_url = page.url or url

        # minimal settle for JS
        await page.wait_for_timeout(600 if is_fast else 1200)

        # best-effort networkidle, but never block long
        if not is_fast:
            try:
                await page.wait_for_load_state("networkidle", timeout=2500)
            except Exception:
                pass

        shots_dir = os.path.join(out_dir, "screenshots")
        ensure_dir(shots_dir)
        fn = safe_filename(normalize_url(final_url))

        # Store screenshot paths *relative to run_dir* so report.html can render them reliably.
        screenshot_rel = f"screenshots/{fn}.png"
        screenshot_abs = os.path.join(out_dir, screenshot_rel)
        await page.screenshot(path=screenshot_abs, full_page=True)
        screenshot_path = screenshot_rel

        html = await page.content()
        try:
            text = await page.inner_text("body")
        except Exception:
            text = ""

        if len(text) > 30000:
            text = text[:30000]

    except Exception as e:
        err = str(e)
    finally:
        await page.close()

    dt = time.monotonic() - t0
    if err:
        print(f"[fetch {idx}/{total}] ❌ error in {dt:.1f}s :: {err[:160]}", flush=True)
    else:
        print(f"[fetch {idx}/{total}] ✅ status={status} in {dt:.1f}s -> {final_url}", flush=True)

    return {
        "url": url,
        "final_url": final_url,
        "status": status,
        "error": err,
        "html": html,
        "text": text,
        "screenshot_path": screenshot_path,
    }

async def _fetch_all_async(base_url: str, urls: List[str], cfg: Dict[str, Any], out_dir: str) -> List[Dict[str, Any]]:
    crawler = cfg.get("crawler", {})
    concurrency = int(crawler.get("concurrency", 6))
    ua = crawler.get("user_agent", "QC-FrenchFirst-Scanner/1.0")
    hard_timeout_s = int(crawler.get("hard_timeout_s", 35))
    skip_contains = crawler.get("skip_url_contains", ["/checkouts/"])  # deep Shopify checkout
    total = len(urls)

    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=ua,
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720},
        )

        async def bounded(i: int, u: str) -> Dict[str, Any]:
            if _is_skip_url(u, skip_contains):
                print(f"[fetch {i}/{total}] ⏭️  skipped (matches skip list): {u}", flush=True)
                return {
                    "url": u,
                    "final_url": u,
                    "status": None,
                    "error": "skipped",
                    "html": "",
                    "text": "",
                    "screenshot_path": "",
                }

            async with sem:
                try:
                    return await asyncio.wait_for(
                        _fetch_one(context, i, total, u, out_dir, cfg),
                        timeout=hard_timeout_s,
                    )
                except asyncio.TimeoutError:
                    print(f"[fetch {i}/{total}] ⏱️  HARD TIMEOUT after {hard_timeout_s}s: {u}", flush=True)
                    return {
                        "url": u,
                        "final_url": u,
                        "status": None,
                        "error": f"hard-timeout-{hard_timeout_s}s",
                        "html": "",
                        "text": "",
                        "screenshot_path": "",
                    }

        pages = await asyncio.gather(*[bounded(i+1, u) for i, u in enumerate(urls)])

        await context.close()
        await browser.close()
        return pages

def fetch_all(base_url: str, urls: List[str], cfg: Dict[str, Any], out_dir: str | None = None) -> Tuple[List[Dict[str, Any]], str]:
    """Fetch a list of URLs.

    If out_dir is provided, fetched artifacts (screenshots) are written into that same run folder.
    This enables a second "confirm" pass without creating a new run.
    """
    out_dir = out_dir or _run_dir(base_url)
    ensure_dir(out_dir)
    ensure_dir(os.path.join(out_dir, "screenshots"))
    print(f"[fetch] starting: {len(urls)} urls -> {out_dir}", flush=True)
    pages = asyncio.run(_fetch_all_async(base_url, urls, cfg, out_dir))
    ok = sum(1 for p in pages if p.get("status") == 200 and not p.get("error"))
    bad = sum(1 for p in pages if p.get("error") and p.get("error") != "skipped")
    skipped = sum(1 for p in pages if p.get("error") == "skipped")
    print(f"[fetch] done: ok={ok} errors={bad} skipped={skipped}", flush=True)
    return pages, out_dir
