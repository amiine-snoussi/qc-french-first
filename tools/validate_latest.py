#!/usr/bin/env python3
import glob
import json
import os
import sys
from urllib.parse import urlparse


def pick_latest_run_dir(host: str) -> str | None:
    dirs = sorted([p for p in glob.glob(f"runs/{host}/*") if os.path.isdir(p)])
    return dirs[-1] if dirs else None


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_valid_diag(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    s = d.get("stats")
    return isinstance(s, dict) and any(k in s for k in ("pages_total", "lang", "pairs", "status"))


def main():
    if len(sys.argv) < 2:
        print("usage: python tools/validate_latest.py https://example.com/")
        sys.exit(2)

    base = sys.argv[1].strip()
    host = urlparse(base).netloc or base.replace("https://", "").replace("http://", "").split("/")[0]

    run_dir = pick_latest_run_dir(host)
    if not run_dir:
        print(f"[validate] No runs found under runs/{host}/")
        sys.exit(1)

    diag_path = os.path.join(run_dir, "diagnostics.json")
    summary_path = os.path.join(run_dir, "summary.json")

    d = _load_json(diag_path) if os.path.exists(diag_path) else {}
    source = diag_path if d else None

    # Fallback if diagnostics.json is missing or not the real schema
    if not _is_valid_diag(d):
        s = _load_json(summary_path) if os.path.exists(summary_path) else {}
        if _is_valid_diag(s):
            d = s
            source = summary_path

    if not _is_valid_diag(d):
        print(f"[validate] No usable diagnostics found in latest run: {run_dir}")
        print(f"[validate] Tried: {diag_path} and {summary_path}")
        sys.exit(1)

    s = d.get("stats", {}) or {}
    pairs = s.get("pairs", {}) or {}
    lang = s.get("lang", {}) or {}
    status = s.get("status", {}) or {}

    print(f"[validate] run_dir: {run_dir}")
    print(f"[validate] source: {source}")
    print(f"[validate] pages_total={s.get('pages_total')}")
    print(
        "[validate] status: "
        f"2xx={status.get('2xx',0)} 3xx={status.get('3xx',0)} 4xx={status.get('4xx',0)} "
        f"5xx={status.get('5xx',0)} other={status.get('other',0)} none={status.get('none',0)}"
    )
    print(f"[validate] lang: en={lang.get('en',0)} fr={lang.get('fr',0)} unknown={lang.get('unknown',0)}")
    print(
        "[validate] pairs: "
        f"total_pairs={pairs.get('total_pairs',0)} "
        f"missing_fr_total={pairs.get('missing_fr_total',0)} "
        f"missing_fr_key={pairs.get('missing_fr_key',0)} "
        f"missing_en_total={pairs.get('missing_en_total',0)}"
    )

    rows = d.get("pairs_sample") or d.get("pairs_rows") or []
    p01 = [
    r for r in rows
    if isinstance(r, dict)
    and r.get("status") in ("missing_fr", "candidate_fr")
    and r.get("priority") in ("P0", "P1")
    ]
    p01 = p01[:15]

    if p01:
        print("\n[validate] TOP missing FR (P0/P1):")
        for r in p01:
            print(f"  {r.get('priority')} {r.get('key_type','-'):>8} {r.get('path')} -> {r.get('en_url')}")
    else:
        print("\n[validate] No P0/P1 missing FR detected.")

    missing_key = int(pairs.get("missing_fr_key", 0) or 0)
    fr_pages = int(lang.get("fr", 0) or 0)

    if fr_pages == 0:
        verdict = "FAIL (no French pages found)"
        code = 1
    elif missing_key > 0:
        verdict = f"FAIL ({missing_key} key pages missing French)"
        code = 1
    else:
        verdict = "PASS (key pages covered)"
        code = 0

    print(f"\n[validate] VERDICT: {verdict}")
    sys.exit(code)


if __name__ == "__main__":
    main()
