#!/usr/bin/env python3
"""Batch scan a list of URLs and write a leads CSV.

Usage:
  python tools/batch_scan.py urls.txt --max_pages 60 --out leads.csv

Notes:
- Runs scans sequentially (safe/default). If you want speed, run multiple terminals.
- Output is designed for selling: you get PASS/FAIL + key missing count + report path.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


def _host_from_url(u: str) -> str:
    host = urlparse(u).netloc
    if host:
        return host
    # allow passing bare domains
    return u.replace("https://", "").replace("http://", "").split("/")[0]


def pick_latest_run_dir(host: str) -> str | None:
    dirs = sorted([p for p in glob.glob(f"runs/{host}/*") if os.path.isdir(p)])
    return dirs[-1] if dirs else None


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _verdict(diag: dict) -> tuple[str, int]:
    s = diag.get("stats", {}) or {}
    pairs = s.get("pairs", {}) or {}
    lang = s.get("lang", {}) or {}

    missing_key = int(pairs.get("missing_fr_key", 0) or 0)
    fr_pages = int(lang.get("fr", 0) or 0)

    if fr_pages == 0:
        return "FAIL (no FR)", 1
    if missing_key > 0:
        return f"FAIL ({missing_key} key missing FR)", 1
    return "PASS", 0


@dataclass
class Row:
    url: str
    host: str
    verdict: str
    pages_total: int
    fr_pages: int
    en_pages: int
    missing_fr_key: int
    missing_fr_total: int
    score: int
    report_path: str
    run_dir: str


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch scan URLs and export leads.csv")
    ap.add_argument("urls_file", help="Text file: one URL per line")
    ap.add_argument("--max_pages", type=int, default=80)
    ap.add_argument("--out", default="leads.csv")
    args = ap.parse_args()

    with open(args.urls_file, "r", encoding="utf-8") as f:
        urls = [ln.strip() for ln in f.readlines()]
    urls = [u for u in urls if u and not u.startswith("#")]

    rows: list[Row] = []

    for u in urls:
        host = _host_from_url(u)
        print(f"\n=== SCAN {host} ===")

        # Run the normal pipeline
        cmd = ["python", "main.py", "--url", u, "--max_pages", str(args.max_pages)]
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"[batch] scan failed (exit={proc.returncode}) for {u}")

        run_dir = pick_latest_run_dir(host) or ""
        report_path = os.path.join(run_dir, "report.html") if run_dir else ""
        diag_path = os.path.join(run_dir, "diagnostics.json") if run_dir else ""
        coverage_path = os.path.join(run_dir, "coverage.json") if run_dir else ""

        diag = _load_json(diag_path) if diag_path and os.path.exists(diag_path) else {}
        cov = _load_json(coverage_path) if coverage_path and os.path.exists(coverage_path) else {}

        s = diag.get("stats", {}) or {}
        pairs = s.get("pairs", {}) or {}
        lang = s.get("lang", {}) or {}

        verdict, _ = _verdict(diag)
        score = int(cov.get("score", 0) or 0) if isinstance(cov, dict) else 0

        row = Row(
            url=u,
            host=host,
            verdict=verdict,
            pages_total=int(s.get("pages_total", 0) or 0),
            fr_pages=int(lang.get("fr", 0) or 0),
            en_pages=int(lang.get("en", 0) or 0),
            missing_fr_key=int(pairs.get("missing_fr_key", 0) or 0),
            missing_fr_total=int(pairs.get("missing_fr_total", 0) or 0),
            score=score,
            report_path=report_path,
            run_dir=run_dir,
        )
        rows.append(row)

    # Write CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "url",
                "host",
                "verdict",
                "score",
                "pages_total",
                "fr_pages",
                "en_pages",
                "missing_fr_key",
                "missing_fr_total",
                "run_dir",
                "report_path",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.url,
                    r.host,
                    r.verdict,
                    r.score,
                    r.pages_total,
                    r.fr_pages,
                    r.en_pages,
                    r.missing_fr_key,
                    r.missing_fr_total,
                    r.run_dir,
                    r.report_path,
                ]
            )

    print(f"\n✅ Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
