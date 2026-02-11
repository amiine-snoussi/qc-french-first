from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _atomic_json_dump(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_full_diagnostics(payload: dict) -> bool:
    # minimal schema check, not “truthiness”
    if not isinstance(payload, dict):
        return False
    return any(k in payload for k in ("stats", "fix_first", "key_pages", "pairs_sample", "base_url", "origin"))


def _extract_payload(scored) -> tuple[dict, dict]:
    """
    Patch A intent:
      - diagnostics.json must be scan diagnostics (stats/lang/pairs/key pages…)
      - coverage-like score dict must go to coverage.json

    We keep compatibility with callers passing:
      - a plain coverage dict {score,label,issues}
      - a wrapper dict {"diagnostics": {...}, "coverage": {...}} (future)
    """
    if scored is None:
        return {}, {}

    if isinstance(scored, dict):
        # future-proof wrapper support
        d = scored.get("diagnostics")
        c = scored.get("coverage")
        if isinstance(d, dict) and _is_full_diagnostics(d):
            return d, c if isinstance(c, dict) else {}
        # current: score_site returns coverage dict
        if "issues" in scored and "score" in scored and "label" in scored:
            return {}, scored

    return {}, {}


def _infer_lang_from_url(url: str) -> str:
    try:
        u = urlparse(url)
        path = (u.path or "").strip("/")
        first = path.split("/", 1)[0].lower() if path else ""
        q = (u.query or "").lower()

        fr_prefixes = {"fr", "fr-ca", "fr-fr", "fr-be", "fr-ch", "fr-us"}
        en_prefixes = {"en", "en-ca", "en-us", "en-gb"}

        if first in fr_prefixes or "locale=fr" in q:
            return "fr"
        if first in en_prefixes or "locale=en" in q:
            return "en"
        return "unknown"
    except Exception:
        return "unknown"


def _coerce_page_items(findings):
    """
    analyze_site() returns a dict:
      { "pages": [...], "key_pages": {...}, "signals": {...}, ... }

    Older code assumed a list[dict] directly.
    """
    if isinstance(findings, dict) and isinstance(findings.get("pages"), list):
        return findings["pages"]
    if isinstance(findings, list):
        return findings
    return []


def _compute_basic_stats(findings) -> dict:
    """
    Best-effort stats if upstream diagnostics are missing/incomplete.
    Works with findings being:
      - dict from analyze_site() (preferred): {"pages":[...], ...}
      - list[dict] legacy
    """
    items = _coerce_page_items(findings)
    pages_total = len(items)

    status_counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0, "none": 0}
    lang_counts = {"en": 0, "fr": 0, "unknown": 0}

    for it in items:
        if not isinstance(it, dict):
            continue

        url = str(it.get("norm_url") or it.get("url") or it.get("final_url") or it.get("page_url") or "")
        lang = it.get("lang") or _infer_lang_from_url(url)
        lang = (lang or "unknown").strip().lower()
        if lang not in ("en", "fr"):
            lang = "unknown"
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

        st = it.get("status") if it.get("status") is not None else it.get("status_code")
        if st is None:
            status_counts["none"] += 1
        else:
            try:
                st_int = int(st)
                if 200 <= st_int < 300:
                    status_counts["2xx"] += 1
                elif 300 <= st_int < 400:
                    status_counts["3xx"] += 1
                elif 400 <= st_int < 500:
                    status_counts["4xx"] += 1
                elif 500 <= st_int < 600:
                    status_counts["5xx"] += 1
                else:
                    status_counts["other"] += 1
            except Exception:
                status_counts["other"] += 1

    return {
        "pages_total": pages_total,
        "status": status_counts,
        "lang": lang_counts,
        "pairs": {
            "total_pairs": 0,
            "missing_fr_total": 0,
            "missing_fr_key": 0,
            "missing_en_total": 0,
        },
    }


def _priority_for_key_type(key_type: str | None) -> str:
    kt = (key_type or "").strip().lower()
    if kt in ("home", "checkout", "cart", "contact"):
        return "P0"
    if kt in ("products", "services", "returns", "faq", "about"):
        return "P1"
    return "P2"


def _build_pairs_from_findings(findings: dict) -> tuple[list[dict], dict]:
    """Build minimal pairing diagnostics from analyze_site() output."""
    pages = _coerce_page_items(findings)
    pairs_rows: list[dict] = []

    key_types = {"home", "contact", "services", "products", "cart", "checkout", "faq", "returns", "about"}
    missing_fr_total = 0
    missing_fr_key = 0

    for p in pages:
        if not isinstance(p, dict):
            continue

        lang = p.get("lang") or "unknown"
        if isinstance(lang, str):
            lang = lang.strip().lower()
        if lang != "en":
            continue

        en_url = p.get("final_url") or p.get("url") or p.get("norm_url") or ""
        try:
            path = urlparse(str(p.get("norm_url") or en_url)).path or "/"
        except Exception:
            path = "/"

        kt = p.get("key_type")
        pr = _priority_for_key_type(kt)

        fr = p.get("french") or {}
        fr_status = fr.get("status") or "missing"
        if isinstance(fr_status, str):
            fr_status = fr_status.strip().lower()
        else:
            fr_status = "missing"

        fr_url = fr.get("url")

        if fr_status == "present":
            row_status = "ok"
        elif fr_status == "candidate":
            row_status = "candidate_fr"
            missing_fr_total += 1
            if (kt or "") in key_types:
                missing_fr_key += 1
        else:
            row_status = "missing_fr"
            missing_fr_total += 1
            if (kt or "") in key_types:
                missing_fr_key += 1

        pairs_rows.append(
            {
                "priority": pr,
                "key_type": kt or "-",
                "path": path,
                "status": row_status,
                "en_url": en_url,
                "fr_url": fr_url if fr_status in ("present", "candidate") else None,
            }
        )

    pairs_stats = {
        "total_pairs": len(pairs_rows),
        "missing_fr_total": missing_fr_total,
        "missing_fr_key": missing_fr_key,
        "missing_en_total": 0,
    }

    # prioritize fix-first rows: P0 then P1, missing/candidate only
    pr_rank = {"P0": 0, "P1": 1, "P2": 2}
    fix_first = [r for r in pairs_rows if r.get("status") in ("missing_fr", "candidate_fr")]
    fix_first = sorted(fix_first, key=lambda r: (pr_rank.get(r.get("priority", "P9"), 9), str(r.get("path", ""))))
    fix_first = fix_first[:40]

    return pairs_rows, {"pairs": pairs_stats, "fix_first": fix_first}


def _build_key_pages_from_findings(findings: dict) -> list[dict]:
    key_pages = findings.get("key_pages") if isinstance(findings, dict) else None
    if not isinstance(key_pages, dict):
        return []

    out: list[dict] = []
    for key, kp in key_pages.items():
        if not isinstance(kp, dict):
            continue
        fr = kp.get("french") or {}
        fr_status = fr.get("status") or "missing"
        if isinstance(fr_status, str):
            fr_status = fr_status.strip().lower()
        else:
            fr_status = "missing"

        if fr_status == "present":
            st = "ok"
        elif fr_status == "candidate":
            st = "candidate_fr"
        else:
            st = "missing_fr"

        out.append(
            {
                "key": key,
                "priority": _priority_for_key_type(key),
                "status": st,
                "en_url": kp.get("final_url") or kp.get("url") or kp.get("norm_url"),
                "fr_url": fr.get("url"),
                "screenshot": kp.get("screenshot_path"),
            }
        )

    pr_rank = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(out, key=lambda r: (pr_rank.get(r.get("priority", "P9"), 9), str(r.get("key", ""))))


def _build_diagnostics_from_findings(base_url: str, findings: dict) -> dict:
    """Construct scan diagnostics from analyze_site() output (findings)."""
    diag: dict = {
        "base_url": base_url,
        "origin": findings.get("origin"),
        "platform": findings.get("platform"),
        "homepage_switch": (findings.get("signals") or {}).get("home_switch_location", "-")
        if isinstance(findings.get("signals"), dict)
        else "-",
    }

    diag["stats"] = _compute_basic_stats(findings)

    pairs_rows, extras = _build_pairs_from_findings(findings)
    diag["stats"]["pairs"] = extras["pairs"]
    diag["fix_first"] = extras["fix_first"]
    diag["pairs_sample"] = pairs_rows[:300]

    diag["key_pages"] = _build_key_pages_from_findings(findings)
    return diag


def _ensure_stats(diag: dict, findings) -> dict:
    s = diag.get("stats")
    if not isinstance(s, dict):
        s = {}
    basic = _compute_basic_stats(findings)

    def _all_zeros(d: object) -> bool:
        if not isinstance(d, dict) or not d:
            return True
        try:
            return sum(int(v or 0) for v in d.values()) == 0
        except Exception:
            return False

    pages_total = int(s.get("pages_total") or 0) if isinstance(s.get("pages_total"), (int, float, str)) else 0
    status_stub = _all_zeros(s.get("status"))
    lang_stub = _all_zeros(s.get("lang"))
    stats_stub = (pages_total == 0) and status_stub and lang_stub

    # If stats look like the old stub, prefer computed basics.
    if stats_stub:
        merged = dict(basic)
    else:
        merged = dict(basic)
        for k, v in s.items():
            if k == "pages_total" and int(v or 0) == 0 and int(basic.get("pages_total") or 0) > 0:
                continue
            merged[k] = v

    if not isinstance(merged.get("status"), dict):
        merged["status"] = basic["status"]
    if not isinstance(merged.get("lang"), dict):
        merged["lang"] = basic["lang"]
    if not isinstance(merged.get("pairs"), dict):
        merged["pairs"] = basic["pairs"]

    diag["stats"] = merged
    return diag


def _coverage_score(diag: dict) -> tuple[int, str]:
    # Heuristic display score (not legal advice)
    s = (diag.get("stats", {}) or {})
    pairs = (s.get("pairs", {}) or {})
    lang = (s.get("lang", {}) or {})

    fr_pages = int(lang.get("fr", 0) or 0)
    missing_key = int(pairs.get("missing_fr_key", 0) or 0)
    missing_total = int(pairs.get("missing_fr_total", 0) or 0)

    if fr_pages <= 0:
        return (0, "Fail: no French detected")

    non_key_missing = max(0, missing_total - missing_key)
    score = 100 - (missing_key * 5) - (non_key_missing * 1)
    score = max(0, min(100, score))

    if missing_key > 0:
        return (score, f"Fail: {missing_key} key pages missing French")
    if missing_total > 0:
        return (score, f"Warn: {missing_total} pages missing French")
    return (score, "Pass")


def _make_signals(diag: dict) -> dict:
    s = (diag.get("stats", {}) or {})
    lang = (s.get("lang", {}) or {})
    fr_pages = int(lang.get("fr", 0) or 0)

    return {
        "has_any_french": fr_pages > 0,
        "homepage_switch": diag.get("homepage_switch", "-") or "-",
    }


def render_report(base_url: str, findings, scored, run_dir: str, cfg: dict) -> str:
    """
    Called by main.py:
      report_path = render_report(base_url, findings, scored, artifacts_dir, cfg)

    Goal:
    - report.html always renders (no undefined vars)
    - diagnostics.json remains the *real scan* diagnostics schema
    - any small/other diag (score/label/issues) goes to coverage.json
    """
    diag_from_scored, coverage_diag = _extract_payload(scored)

    diag_path = os.path.join(run_dir, "diagnostics.json")
    existing_diag = _load_json(diag_path) if os.path.exists(diag_path) else {}

    def _has_signal(d: dict) -> bool:
        if not _is_full_diagnostics(d):
            return False
        s = d.get("stats") or {}
        pages_total = int(s.get("pages_total") or 0)
        status = s.get("status") or {}
        lang = s.get("lang") or {}
        if pages_total > 0:
            return True
        if isinstance(status, dict) and sum(int(v or 0) for v in status.values()) > 0:
            return True
        if isinstance(lang, dict) and sum(int(v or 0) for v in lang.values()) > 0:
            return True
        return bool(d.get("pairs_sample") or d.get("fix_first") or d.get("key_pages"))

    if _is_full_diagnostics(diag_from_scored):
        diag = diag_from_scored
    elif _has_signal(existing_diag):
        diag = existing_diag
    elif isinstance(findings, dict) and isinstance(findings.get("pages"), list):
        diag = _build_diagnostics_from_findings(base_url, findings)
    else:
        diag = {}

    diag = _ensure_stats(diag, findings)

    score, score_label = _coverage_score(diag)
    signals = _make_signals(diag)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    platform = diag.get("platform", {"name": "unknown", "confidence": 0.0})

    _atomic_json_dump(diag_path, diag)

    if isinstance(coverage_diag, dict) and coverage_diag:
        _atomic_json_dump(os.path.join(run_dir, "coverage.json"), coverage_diag)

    summary = {
        "site": base_url,
        "generated": generated,
        "platform": platform,
        "score": score,
        "score_label": score_label,
        "stats": diag.get("stats", {}) or {},
        "signals": signals,
        "fix_first": diag.get("fix_first", []) or [],
        "key_pages": diag.get("key_pages", []) or [],
        "pairs_sample": diag.get("pairs_sample", []) or [],
    }
    _atomic_json_dump(os.path.join(run_dir, "summary.json"), summary)

    with open(os.path.join(run_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"Site: {base_url}\n")
        f.write(f"Generated: {generated}\n")
        f.write(f"Platform: {platform}\n")
        f.write(f"Score: {score} ({score_label})\n")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    templates_dir = os.path.join(root, "templates")

    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("report.html")

    pairs_rows = summary["pairs_sample"]

    html = tpl.render(
        site=base_url,
        generated=generated,
        platform=platform,
        score=score,
        score_label=score_label,
        stats=summary["stats"],
        signals=signals,
        homepage_switch=signals.get("homepage_switch", "-"),
        fix_first=summary["fix_first"],
        key_pages=summary["key_pages"],
        key_rows=summary["key_pages"],     # alias for your template
        pairs_sample=summary["pairs_sample"],
        pairs_rows=pairs_rows,             # alias for your template
    )

    out_path = os.path.join(run_dir, "report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
