#!/usr/bin/env python3
import sys, re, json
from pathlib import Path
from glob import glob

RUN_RE = re.compile(r"->\s*(runs/[^\s]+)")

def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as e:
        return None, str(e)

def find_run_dir(log_text: str):
    m = RUN_RE.search(log_text)
    return m.group(1) if m else None

def main(argv):
    if len(argv) < 2:
        print("Usage: python3 tools/qa_check_from_logs.py out/scan_*.log")
        return 2

    patterns = argv[1:]
    log_files = []
    for pat in patterns:
        log_files.extend(sorted(glob(pat)))
    if not log_files:
        print("No logs matched:", patterns)
        return 2

    any_fail = False

    for lf in log_files:
        print("\n" + "="*90)
        print("LOG:", lf)
        txt = Path(lf).read_text(encoding="utf-8", errors="ignore")
        run_dir = find_run_dir(txt)
        print("RUN_DIR:", run_dir or "NOT FOUND")
        if not run_dir:
            print("❌ FAIL: could not find run dir in log")
            any_fail = True
            continue

        rd = Path(run_dir)
        if not rd.exists():
            print("❌ FAIL: run dir does not exist on disk")
            any_fail = True
            continue

        files = sorted([p.name for p in rd.glob("*")])
        print("RUN_FILES:", files)

        summary_p = rd / "summary.json"
        diag_p = rd / "diagnostics.json"

        summary, se = load_json(summary_p) if summary_p.exists() else (None, "missing")
        diag, de = load_json(diag_p) if diag_p.exists() else (None, "missing")

        if summary is None:
            print(f"❌ FAIL: summary.json not usable ({se})")
            any_fail = True
        if diag is None:
            print(f"⚠️ diagnostics.json not usable ({de})")

        score = None
        if summary:
            score = summary.get("score")
        if score is None and diag:
            score = diag.get("score")

        print("SCORE:", score)

        # Validate score
        if score is None or not isinstance(score, int) or not (0 <= score <= 100):
            print("❌ FAIL: score missing/invalid (expect int 0..100)")
            any_fail = True

        # Validate key_pages consistency (only if present)
        key_pages = []
        if summary and isinstance(summary.get("key_pages"), list):
            key_pages = summary["key_pages"]

        if not key_pages:
            print("⚠️ KEY_PAGES: none (ok for now, but you probably want them)")
        else:
            bad = 0
            for kp in key_pages:
                st = kp.get("status")
                en_status = kp.get("en_status")
                fr_status_code = kp.get("fr_status_code")
                key = kp.get("key")

                if st == "ok":
                    if en_status is None or en_status >= 400 or fr_status_code is None or fr_status_code >= 400:
                        print(f"❌ FAIL: key_page '{key}' marked ok but statuses are en={en_status} fr={fr_status_code}")
                        bad += 1

            if bad:
                any_fail = True

    print("\n" + "="*90)
    if any_fail:
        print("RESULT: FAIL (do not trust scores yet)")
        return 1
    print("RESULT: PASS (scores consistent with artifacts)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

