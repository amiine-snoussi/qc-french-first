import re, sys, json
from pathlib import Path

def load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

def find_score(run_dir: Path):
    summ = load_json(run_dir / "summary.json") or {}
    cov  = load_json(run_dir / "coverage.json") or {}
    diag = load_json(run_dir / "diagnostics.json") or {}

    # common places
    for obj in (summ, cov, diag):
        if isinstance(obj, dict):
            s = obj.get("score")
            if isinstance(s, (int, float)):
                return int(s), obj.get("score_label") or obj.get("label")

    # try report.html (very robust regex)
    rp = run_dir / "report.html"
    if rp.exists():
        html = rp.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"score"\s*:\s*(\d{1,3})', html)
        if m:
            return int(m.group(1)), None
        m = re.search(r'\bScore\b[^0-9]{0,50}(\d{1,3})', html, flags=re.IGNORECASE)
        if m:
            return int(m.group(1)), None

    return None, None

def parse_log(path: Path):
    run_dir = None
    statuses = {}  # url -> int status
    errors = set()
    pw_max_pages = None

    # suite logs can have slightly different shapes; be permissive
    txt = path.read_text(encoding="utf-8", errors="replace")
    for line in txt.splitlines():
        m = re.search(r"\[discover/pw\].*max_pages=(\d+)", line)
        if m:
            pw_max_pages = int(m.group(1))

        # run dir can appear in multiple formats; grab the first "runs/..." we see
        if run_dir is None:
            m = re.search(r"(runs/[A-Za-z0-9._\-\/]+)", line)
            if m:
                run_dir = m.group(1).rstrip(").,;]")

        # status line (allow status before/after arrow, and not necessarily end-of-line)
        m = re.search(r"status=(\d+)", line)
        if m:
            code = int(m.group(1))
            mu = re.search(r"(https?://\S+)", line)
            if mu:
                url = mu.group(1).rstrip(").,;]\"'")
                statuses[url] = code

        # error line (any line mentioning error and a URL)
        if "error" in line.lower():
            mu = re.search(r"(https?://\S+)", line)
            if mu:
                errors.add(mu.group(1).rstrip(").,;]\"'"))

    return run_dir, statuses, errors, pw_max_pages

def ok(code: int):
    return 200 <= code < 400

def get_key_pages(run_dir: Path):
    summ = load_json(run_dir / "summary.json") or {}
    cov  = load_json(run_dir / "coverage.json") or {}
    diag = load_json(run_dir / "diagnostics.json") or {}

    for obj in (diag, summ, cov):
        kp = obj.get("key_pages") if isinstance(obj, dict) else None
        if kp:
            return kp

    # sometimes nested under diagnostics["site"]["key_pages"] etc.
    for obj in (diag, summ, cov):
        if isinstance(obj, dict):
            for k in ("site", "analysis", "results"):
                sub = obj.get(k)
                if isinstance(sub, dict) and sub.get("key_pages"):
                    return sub["key_pages"]

    return []

def normalize_kp_list(key_pages):
    # allow dict form or list form
    if isinstance(key_pages, dict):
        out = []
        for k, v in key_pages.items():
            if isinstance(v, dict):
                v = dict(v)
                v.setdefault("key", k)
                out.append(v)
        return out
    if isinstance(key_pages, list):
        return [x for x in key_pages if isinstance(x, dict)]
    return []

def main():
    # PATCH: default to suite logs (WSL step you wrote)
    logs = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else sorted(Path("out").glob("suite_*.log"))
    if not logs:
        print("No logs found. Provide log paths or put logs in ./out/suite_*.log")
        sys.exit(2)

    any_fail = False

    for lp in logs:
        run_dir_rel, st, errs, pw_max = parse_log(lp)

        print("\n" + "="*90)
        print("LOG:", lp)

        if not run_dir_rel:
            print("❌ FAIL: Could not locate run dir in log.")
            any_fail = True
            continue

        run_dir = Path(run_dir_rel)
        print("RUN_DIR:", run_dir)

        score, label = find_score(run_dir)
        print("SCORE:", score, ("| " + str(label) if label else ""))

        # compute status counts from logs (truth source)
        codes = list(st.values())
        n2xx3xx = sum(1 for c in codes if ok(c))
        n4xx = sum(1 for c in codes if 400 <= c < 500)
        n5xx = sum(1 for c in codes if 500 <= c < 600)
        nerr = len(errs)
        total = len(codes) if codes else 0
        ratio_4xx = (n4xx / total) if total else 0.0

        print(f"FETCH_COUNTS: total={total} ok(2xx/3xx)={n2xx3xx} 4xx={n4xx} 5xx={n5xx} errors={nerr}")
        if pw_max is not None:
            print(f"DISCOVER_PW_MAX_PAGES(from log): {pw_max}")

        # strong sanity gate: high score with lots of 4xx is suspicious
        if isinstance(score, int) and score >= 90 and ratio_4xx >= 0.25:
            print("❌ FAIL: score>=90 but 4xx ratio>=25% (likely false confidence).")
            any_fail = True

        # key pages gate
        kps = normalize_kp_list(get_key_pages(run_dir))
        if not kps:
            print("⚠️ KEY_PAGES: not found in json -> cannot validate key pages yet (this should be fixed).")
            any_fail = True
            continue

        print("KEY_PAGES (validate EN/FR URLs actually return 2xx/3xx):")
        for kp in kps:
            key = kp.get("key")
            en = kp.get("en_url") or kp.get("url") or kp.get("en")
            fr = kp.get("fr_url") or kp.get("fr")

            def fmt(u):
                if not u:
                    return "—"
                code = st.get(u)
                if code is None:
                    return f"{u} => (no log match)"
                return f"{u} => {code}"

            print(f"- {key}:")
            print("   EN:", fmt(en))
            print("   FR:", fmt(fr))

            if en and (st.get(en) is not None) and (not ok(st[en])):
                print("   ❌ FAIL: EN key page is not 2xx/3xx.")
                any_fail = True
            if fr and (st.get(fr) is not None) and (not ok(st[fr])):
                print("   ❌ FAIL: FR key page is not 2xx/3xx.")
                any_fail = True

    print("\n" + "="*90)
    if any_fail:
        print("RESULT: FAIL (do not trust scores yet)")
        sys.exit(1)
    print("RESULT: PASS (scores + key pages consistent with fetch results)")
    sys.exit(0)

if __name__ == "__main__":
    main()
