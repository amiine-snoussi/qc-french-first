#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ✅ Make repo root importable so `import main` works even when running from ./tools/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# import your main entrypoint (main.py at repo root)
import main as app_main


def read_urls(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"[batch] urls file not found: {path}")
    urls: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)

    # de-dupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def run_one(url: str, max_pages: int) -> int:
    # run main.py as if invoked from CLI
    sys.argv = ["main.py", "--url", url, "--max_pages", str(max_pages)]
    try:
        app_main.main()
        return 0
    except SystemExit as e:
        # if your main uses sys.exit(...)
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"[batch] ❌ exception on {url}: {type(e).__name__}: {e}", flush=True)
        return 2


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch scan a list of URLs (one per line).")
    ap.add_argument("urls_file", help="Text file with one URL per line")
    ap.add_argument("--max_pages", type=int, default=60)
    ap.add_argument("--sleep_s", type=float, default=0.0, help="Optional sleep between scans")
    args = ap.parse_args()

    urls = read_urls(args.urls_file)
    if not urls:
        raise SystemExit("[batch] no URLs found in file")

    print(f"[batch] loaded {len(urls)} urls from {args.urls_file}", flush=True)

    failures = 0
    for i, u in enumerate(urls, 1):
        print("\n" + "=" * 90, flush=True)
        print(f"[batch] ({i}/{len(urls)}) SCAN {u}", flush=True)
        t0 = time.monotonic()
        code = run_one(u, args.max_pages)
        dt = time.monotonic() - t0
        print(f"[batch] ({i}/{len(urls)}) DONE exit={code} in {dt:.1f}s :: {u}", flush=True)

        if code != 0:
            failures += 1
        if args.sleep_s:
            time.sleep(args.sleep_s)

    print("\n" + "=" * 90, flush=True)
    if failures:
        print(f"[batch] FINISHED with {failures} failures", flush=True)
        raise SystemExit(1)
    print("[batch] FINISHED OK", flush=True)


if __name__ == "__main__":
    main()
