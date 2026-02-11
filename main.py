import argparse
import os
import subprocess

from scanner.config import load_config
from scanner.discover import discover_urls
from scanner.fetch import fetch_all
from scanner.analyze import analyze_site
from scanner.score import score_site
from scanner.report import render_report
from scanner.storage import save_run
from scanner.utils import normalize_url

def _open_report_windows(report_path: str) -> None:
    """
    In WSL2: convert to Windows path and open with explorer.exe
    Safe no-op if not available.
    """
    try:
        abs_path = os.path.abspath(report_path)
        win_path = subprocess.check_output(["wslpath", "-w", abs_path], text=True).strip()
        # explorer.exe will open the HTML in the default browser
        subprocess.Popen(["explorer.exe", win_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[open] Opened report in Windows: {win_path}")
    except Exception as e:
        print(f"[open] Could not auto-open report: {e}")

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Québec French-first website self-audit scanner (not legal advice)."
    )
    ap.add_argument("--url", required=True, help="Base URL, e.g. https://example.com")
    ap.add_argument("--max_pages", type=int, default=None, help="Override crawler.max_pages")
    args = ap.parse_args()

    cfg = load_config("config.yml")
    if args.max_pages is not None:
        cfg["crawler"]["max_pages"] = int(args.max_pages)

    base_url = args.url.strip()

    urls = discover_urls(base_url, cfg)

    # Phase 1: fetch discovered URLs
    pages, artifacts_dir = fetch_all(base_url, urls, cfg)

    findings = analyze_site(base_url, pages, cfg)

    # Phase 2 (bulletproofing): confirm inferred French candidates for key pages.
    # This eliminates the biggest source of "false risk" where a FR candidate exists but wasn't crawled.
    crawler_cfg = cfg.get("crawler", {}) or {}
    confirm = bool(crawler_cfg.get("confirm_candidates", True))
    confirm_only_key = bool(crawler_cfg.get("confirm_only_key", True))
    confirm_max = int(crawler_cfg.get("confirm_candidates_max", 20))

    if confirm and confirm_max > 0:
        already = set()
        for p in pages:
            try:
                already.add(normalize_url(p.get("final_url") or p.get("url") or ""))
            except Exception:
                pass

        cands = []

        # 1) Key pages first (highest impact on verdict/score)
        for _kt, kp in (findings.get("key_pages") or {}).items():
            fr = (kp.get("french") or {})
            if fr.get("status") == "candidate" and fr.get("url"):
                cands.append(fr["url"])

        # 2) Optionally expand beyond key pages (still capped)
        if not confirm_only_key:
            for pg in (findings.get("pages") or []):
                if pg.get("lang") != "en":
                    continue
                fr = (pg.get("french") or {})
                if fr.get("status") == "candidate" and fr.get("url"):
                    cands.append(fr["url"])

        # de-dupe + skip URLs we already fetched
        uniq = []
        seen = set()
        for u in cands:
            try:
                nu = normalize_url(u)
            except Exception:
                continue
            if nu in seen or nu in already:
                continue
            uniq.append(nu)
            seen.add(nu)
            if len(uniq) >= confirm_max:
                break

        if uniq:
            print(f"[confirm] fetching {len(uniq)} inferred French candidates (max={confirm_max})", flush=True)
            more_pages, _ = fetch_all(base_url, uniq, cfg, out_dir=artifacts_dir)
            pages.extend(more_pages)
            findings = analyze_site(base_url, pages, cfg)
    scored = score_site(findings, cfg)
    report_path = render_report(base_url, findings, scored, artifacts_dir, cfg)
    save_run(base_url, findings, scored, report_path, artifacts_dir)

    print(f"\n✅ Report generated:\n{report_path}\n")
    _open_report_windows(report_path)

if __name__ == "__main__":
    main()
