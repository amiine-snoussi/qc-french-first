import argparse
import os
import subprocess
from urllib.parse import urlparse

from scanner.config import load_config
from scanner.discover import discover_urls
from scanner.fetch import fetch_all
from scanner.analyze import analyze_site
from scanner.score import score_site
from scanner.report import render_report
from scanner.storage import save_run
from scanner.utils import normalize_url
from scanner.pairs import build_pairs


def _open_report_windows(report_path: str) -> None:
    """
    In WSL2: convert to Windows path and open with explorer.exe
    Safe no-op if not available.
    """
    try:
        abs_path = os.path.abspath(report_path)
        win_path = subprocess.check_output(["wslpath", "-w", abs_path], text=True).strip()
        # explorer.exe will open the HTML in the default browser
        subprocess.Popen(
            ["explorer.exe", win_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[open] Opened report in Windows: {win_path}")
    except Exception as e:
        print(f"[open] Could not auto-open report: {e}")


def _attach_pairs_for_scoring(findings: dict, base_url: str) -> None:
    """
    Make scoring reliable by attaching:
      - findings["pairs"] built from ANALYZED pages (has key_type/lang)
      - findings["pairs_sample"] (EN pages + french status: ok/candidate/missing)
    """
    pages = findings.get("pages") or []

    # 1) Strict EN↔FR pairing table used in report summary (path-based)
    try:
        findings["pairs"] = build_pairs(pages, base_url)
    except Exception:
        findings["pairs"] = {"summary": {}, "rows": []}

    # 2) pairs_sample used by score_site() (candidate_fr counts as missing)
    def _priority_for_key_type(key_type: str | None) -> str:
        kt = (key_type or "").strip().lower()
        if kt in ("home", "checkout", "cart", "contact"):
            return "P0"
        if kt in ("products", "services", "returns", "faq", "about"):
            return "P1"
        return "P2"

    rows: list[dict] = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        lang = (p.get("lang") or "").strip().lower()
        if lang != "en":
            continue

        en_url = p.get("final_url") or p.get("url") or p.get("norm_url") or ""
        try:
            path = urlparse(str(p.get("norm_url") or en_url)).path or "/"
        except Exception:
            path = "/"

        kt = p.get("key_type") or "-"
        pr = _priority_for_key_type(kt)

        fr = p.get("french") or {}
        fr_status = fr.get("status") or "missing"
        if isinstance(fr_status, str):
            fr_status = fr_status.strip().lower()
        else:
            fr_status = "missing"

        if fr_status == "present":
            row_status = "ok"
            fr_url = fr.get("url")
        elif fr_status == "candidate":
            row_status = "candidate_fr"
            fr_url = fr.get("url")
        else:
            row_status = "missing_fr"
            fr_url = None

        rows.append(
            {
                "priority": pr,
                "key_type": kt,
                "path": path,
                "status": row_status,
                "en_url": en_url,
                "fr_url": fr_url,
            }
        )

    findings["pairs_sample"] = rows[:300]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Québec French-first website self-audit scanner (not legal advice)."
    )
    ap.add_argument("--url", required=True, help="Base URL, e.g. https://example.com")
    ap.add_argument(
        "--max_pages",
        type=int,
        default=None,
        help="Override max_pages caps (crawler + discover + playwright discovery).",
    )
    args = ap.parse_args()

    cfg = load_config("config.yml")

    # PATCH 1 — make --max_pages apply to Playwright discovery too
    if args.max_pages is not None:
        mp = int(args.max_pages)

        # Crawl cap
        cfg.setdefault("crawler", {})["max_pages"] = mp

        # Discovery caps (playwright uses its own max_pages)
        cfg.setdefault("discover", {})["max_pages"] = mp
        cfg.setdefault("discover", {}).setdefault("playwright", {})["max_pages"] = mp

    base_url = args.url.strip()

    urls = discover_urls(base_url, cfg)

    # Phase 1: fetch discovered URLs
    pages, artifacts_dir = fetch_all(base_url, urls, cfg)

    findings = analyze_site(base_url, pages, cfg)
    _attach_pairs_for_scoring(findings, base_url)

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
            print(
                f"[confirm] fetching {len(uniq)} inferred French candidates (max={confirm_max})",
                flush=True,
            )
            more_pages, _ = fetch_all(base_url, uniq, cfg, out_dir=artifacts_dir)
            pages.extend(more_pages)
            findings = analyze_site(base_url, pages, cfg)
            _attach_pairs_for_scoring(findings, base_url)

    scored = score_site(findings, cfg)
    report_path = render_report(base_url, findings, scored, artifacts_dir, cfg)
    save_run(base_url, findings, scored, report_path, artifacts_dir)

    print(f"\n✅ Report generated:\n{report_path}\n")
    _open_report_windows(report_path)


if __name__ == "__main__":
    main()
