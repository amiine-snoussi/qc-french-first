from __future__ import annotations
from typing import Dict, Any, List


def _issue(code: str, title: str, detail: str, priority: str) -> Dict[str, str]:
    return {"code": code, "title": title, "detail": detail, "priority": priority}


def score_site(findings: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    score = 100
    issues: List[Dict[str, str]] = []
    label_override: str | None = None  # for "insufficient_evidence"

    key_pages = findings.get("key_pages", {}) or {}
    signals = findings.get("signals", {}) or {}
    pages = findings.get("pages", []) or []

    # --- Patch B: Pairing summary ingestion ---
    pairs_summary = (findings.get("pairs") or {}).get("summary") or {}
    try:
        missing_fr_total = int(pairs_summary.get("missing_fr_total") or 0)
        missing_fr_key = int(pairs_summary.get("missing_fr_key") or 0)
    except Exception:
        missing_fr_total = 0
        missing_fr_key = 0
    # --- end Patch B ---

    # --- Patch: always compute from pairs_sample (validator truth) and take max signal ---
    pairs_sample = findings.get("pairs_sample") or []

    def _is_key_row(r: dict) -> bool:
        return r.get("priority") in ("P0", "P1")

    sample_missing_total = 0
    sample_missing_key = 0
    for r in pairs_sample:
        if not isinstance(r, dict):
            continue
        if r.get("status") != "ok":
            sample_missing_total += 1
            if _is_key_row(r):
                sample_missing_key += 1

    # take the strongest signal (aligns score with validate_latest)
    missing_fr_total = max(missing_fr_total, sample_missing_total)
    missing_fr_key = max(missing_fr_key, sample_missing_key)
    # --- end Patch ---

    leak_cfg = (cfg.get("heuristics", {}) or {}).get("leakage", {}) or {}
    en_ratio_warn = float(leak_cfg.get("en_ratio_warn", 0.12))
    sim_untranslated = float(leak_cfg.get("sim_untranslated", 0.85))

    # --- Coverage gate: prevent "perfect score" from shallow crawl ---
    scoring_cfg = (cfg.get("scoring") or {})
    min_total = int(scoring_cfg.get("min_pages_total", 15))
    min_ok = int(scoring_cfg.get("min_pages_ok", 10))
    cap = int(scoring_cfg.get("cap_score_if_insufficient", 70))

    stats = findings.get("stats") or {}
    try:
        pages_total = int(stats.get("pages_total") or 0)
    except Exception:
        pages_total = 0

    status = stats.get("status") or {}
    try:
        ok_pages = int(status.get("2xx") or 0) + int(status.get("3xx") or 0)
    except Exception:
        ok_pages = 0

    # PATCH: Fallback when analyze_site() doesn't populate stats (derive from pages)
    if pages:

        def _is_ok(p: Dict[str, Any]) -> bool:
            st = p.get("status") if p.get("status") is not None else p.get("status_code")
            try:
                st_i = int(st)
            except Exception:
                return False
            return 200 <= st_i < 400

        computed_total = len(pages)
        computed_ok = sum(1 for p in pages if isinstance(p, dict) and _is_ok(p))

        if pages_total <= 0:
            pages_total = computed_total
        # Only override if we can prove ok pages exist
        if ok_pages <= 0 and computed_ok > 0:
            ok_pages = computed_ok
    # --- end PATCH ---

    # --- Patch B: 4xx ratio penalty ---
    try:
        n4xx = int(status.get("4xx") or 0)
    except Exception:
        n4xx = 0

    ratio_4xx = (n4xx / pages_total) if pages_total else 0.0
    if ratio_4xx >= 0.25:
        issues.append(
            _issue(
                "HIGH_4XX_RATE",
                "High 4xx rate during crawl",
                f"4xx={n4xx} of pages_total={pages_total} (ratio={ratio_4xx:.2f})",
                "P1",
            )
        )
        score -= int(100 * min(0.4, ratio_4xx))  # up to -40
    # --- end Patch B ---

    if pages_total < min_total or ok_pages < min_ok:
        issues.append(
            {
                "priority": "P1",
                "code": "INSUFFICIENT_COVERAGE",
                "title": "Insufficient crawl coverage to trust compliance score",
                "detail": f"pages_total={pages_total} (min {min_total}), ok_pages={ok_pages} (min {min_ok})",
            }
        )
        score = min(score, cap)
        label_override = "insufficient_evidence"
    # --- end coverage gate ---

    # --- Patch B: Pairing gate (apply after coverage gate) ---
    # If EN↔FR pairs show missing French key pages, score must drop
    if missing_fr_key > 0:
        issues.append(
            _issue(
                "PAIRS_KEY_FR_MISSING",
                "Key pages missing French counterparts (from EN↔FR pairing)",
                f"missing_fr_key={missing_fr_key}, missing_fr_total={missing_fr_total}",
                "P0",
            )
        )
        score -= min(60, 12 * missing_fr_key)  # hard penalty

    elif missing_fr_total > 0:
        issues.append(
            _issue(
                "PAIRS_FR_MISSING",
                "Some pages missing French counterparts (from EN↔FR pairing)",
                f"missing_fr_total={missing_fr_total}",
                "P1",
            )
        )
        score -= min(30, 2 * missing_fr_total)
    # --- end pairing gate ---

    # 1) No French at all
    if not signals.get("has_any_french", False):
        score -= 55
        issues.append(
            _issue(
                "NO_FRENCH_DETECTED",
                "No French content detected",
                "Crawler could not confirm French pages. This is typically the highest-risk signal.",
                "P0",
            )
        )

    # 2) Homepage French + switch
    home = key_pages.get("home")
    if home:
        fr = home.get("french", {})
        if fr.get("status") == "missing":
            score -= 40
            issues.append(
                _issue(
                    "FRENCH_HOME_MISSING",
                    "French homepage not found",
                    "No confirmed French version of the homepage was detected.",
                    "P0",
                )
            )
        elif fr.get("status") == "candidate":
            score -= 20
            issues.append(
                _issue(
                    "FRENCH_HOME_UNCONFIRMED",
                    "French homepage not confirmed",
                    f"A French homepage candidate was inferred ({fr.get('url')}), but not confirmed in the crawl set.",
                    "P1",
                )
            )
    else:
        score -= 15
        issues.append(
            _issue(
                "HOME_NOT_CRAWLED",
                "Homepage not confidently analyzed",
                "Homepage record wasn’t clearly identified; results may be incomplete.",
                "P1",
            )
        )

    loc = signals.get("home_switch_location", "none")
    if loc == "none":
        score -= 20
        issues.append(
            _issue(
                "NO_LANGUAGE_SWITCH",
                "No visible language switch detected on homepage",
                "Visitors should be able to access French easily from the homepage (header/nav preferred).",
                "P0",
            )
        )
    elif loc in ("footer", "other"):
        score -= 10
        issues.append(
            _issue(
                "LANG_SWITCH_BURIED",
                "Language switch seems buried",
                f"Language switch detected but appears in: {loc}. Header/nav is typically clearer.",
                "P1",
            )
        )

    # 3) Key pages must have French (skip EN key pages that are not reachable)
    key_order = ["contact", "services", "products", "faq", "returns", "cart", "checkout", "about"]
    key_penalty = 0
    for kt in key_order:
        kp = key_pages.get(kt)
        if not kp:
            continue

        # EN status gate: if EN key page doesn't exist/reachable, not applicable
        st = kp.get("status")
        try:
            st_i = int(st)
        except Exception:
            st_i = None
        if st_i is None or st_i >= 400:
            continue

        fr = (kp.get("french") or {})
        if fr.get("status") == "missing":
            key_penalty += 10
            issues.append(
                _issue(
                    f"KEY_{kt.upper()}_FR_MISSING",
                    f"Key page missing in French: {kt}",
                    f"Page: {kp.get('final_url')}",
                    "P0" if kt in ("contact", "cart", "checkout") else "P1",
                )
            )
        elif fr.get("status") == "candidate":
            key_penalty += 5
            issues.append(
                _issue(
                    f"KEY_{kt.upper()}_FR_UNCONFIRMED",
                    f"French version not confirmed for: {kt}",
                    f"Candidate inferred: {fr.get('url')}",
                    "P1",
                )
            )

    score -= min(key_penalty, 30)

    # 4) English leakage inside FR pages (especially key pages)
    def check_leak(page: Dict[str, Any], label: str, is_key: bool):
        nonlocal score, issues
        leak = (page.get("leak") or {})
        if (page.get("lang") != "fr") or not leak:
            return

        en_ratio = float(leak.get("en_ratio") or 0.0)
        sim = leak.get("sim_to_en", None)

        if sim is not None and float(sim) >= sim_untranslated:
            score -= 12 if is_key else 6
            issues.append(
                _issue(
                    "FR_PAGE_LOOKS_UNTRANSLATED",
                    "French page looks not translated (too similar to English)",
                    f"{label}: similarity={sim} vs EN counterpart {leak.get('en_counterpart')}",
                    "P0" if is_key else "P1",
                )
            )

        if en_ratio >= en_ratio_warn:
            score -= 8 if is_key else 4
            snippets = leak.get("snippets") or []
            sn = (" / ".join(snippets)) if snippets else "(no snippet)"
            issues.append(
                _issue(
                    "ENGLISH_LEAK_IN_FRENCH",
                    "English content detected inside French page",
                    f"{label}: en_ratio={en_ratio} (en_chunks={leak.get('en_chunks')}/{leak.get('chunks_total')}) snippet: {sn}",
                    "P1" if is_key else "P2",
                )
            )

    # check key pages (skip EN-unreachable)
    for kt, kp in key_pages.items():
        st = kp.get("status")
        try:
            st_i = int(st)
        except Exception:
            st_i = None
        if st_i is None or st_i >= 400:
            continue
        check_leak(kp, f"key={kt} {kp.get('final_url')}", is_key=True)

    # check top few FR pages overall
    fr_pages = [p for p in pages if isinstance(p, dict) and p.get("lang") == "fr" and p.get("leak")]
    fr_pages_sorted = sorted(
        fr_pages,
        key=lambda x: float((x.get("leak") or {}).get("en_ratio") or 0.0),
        reverse=True,
    )[:8]
    for p in fr_pages_sorted:
        check_leak(p, f"page {p.get('final_url')}", is_key=False)

    # Patch 4 (kept for debug/reporting): count missing FR on applicable key pages only
    missing_key = 0
    for kp in key_pages.values():
        st = kp.get("status")
        try:
            st_i = int(st)
        except Exception:
            st_i = None
        if st_i is None or st_i >= 400:
            continue  # EN key page doesn't exist -> not applicable

        fr = kp.get("french") or {}
        if fr.get("status") != "present":
            missing_key += 1

    score = max(0, min(100, score))

    # Label
    if score >= 85:
        label = "Good"
    elif score >= 70:
        label = "Moderate risk"
    elif score >= 50:
        label = "High risk"
    else:
        label = "Critical"

    if label_override:
        label = label_override

    pr_rank = {"P0": 0, "P1": 1, "P2": 2}
    issues_sorted = sorted(issues, key=lambda x: pr_rank.get(x.get("priority", "P9"), 9))

    return {"score": score, "label": label, "issues": issues_sorted}
