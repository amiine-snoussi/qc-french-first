from __future__ import annotations
from typing import Dict, Any, List

def _issue(code: str, title: str, detail: str, priority: str) -> Dict[str, str]:
    return {"code": code, "title": title, "detail": detail, "priority": priority}

def score_site(findings: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    score = 100
    issues: List[Dict[str, str]] = []

    key_pages = findings.get("key_pages", {})
    signals = findings.get("signals", {})
    pages = findings.get("pages", [])

    leak_cfg = (cfg.get("heuristics", {}) or {}).get("leakage", {}) or {}
    en_ratio_warn = float(leak_cfg.get("en_ratio_warn", 0.12))
    sim_untranslated = float(leak_cfg.get("sim_untranslated", 0.85))

    # 1) No French at all
    if not signals.get("has_any_french", False):
        score -= 55
        issues.append(_issue(
            "NO_FRENCH_DETECTED",
            "No French content detected",
            "Crawler could not confirm French pages. This is typically the highest-risk signal.",
            "P0",
        ))

    # 2) Homepage French + switch
    home = key_pages.get("home")
    if home:
        fr = home.get("french", {})
        if fr.get("status") == "missing":
            score -= 40
            issues.append(_issue("FRENCH_HOME_MISSING", "French homepage not found",
                                 "No confirmed French version of the homepage was detected.", "P0"))
        elif fr.get("status") == "candidate":
            score -= 20
            issues.append(_issue("FRENCH_HOME_UNCONFIRMED", "French homepage not confirmed",
                                 f"A French homepage candidate was inferred ({fr.get('url')}), but not confirmed in the crawl set.", "P1"))
    else:
        score -= 15
        issues.append(_issue("HOME_NOT_CRAWLED", "Homepage not confidently analyzed",
                             "Homepage record wasn’t clearly identified; results may be incomplete.", "P1"))

    loc = signals.get("home_switch_location", "none")
    if loc == "none":
        score -= 20
        issues.append(_issue("NO_LANGUAGE_SWITCH", "No visible language switch detected on homepage",
                             "Visitors should be able to access French easily from the homepage (header/nav preferred).", "P0"))
    elif loc in ("footer", "other"):
        score -= 10
        issues.append(_issue("LANG_SWITCH_BURIED", "Language switch seems buried",
                             f"Language switch detected but appears in: {loc}. Header/nav is typically clearer.", "P1"))

    # 3) Key pages must have French
    key_order = ["contact", "services", "products", "faq", "returns", "cart", "checkout", "about"]
    key_penalty = 0
    for kt in key_order:
        kp = key_pages.get(kt)
        if not kp:
            continue
        fr = (kp.get("french") or {})
        if fr.get("status") == "missing":
            key_penalty += 10
            issues.append(_issue(
                f"KEY_{kt.upper()}_FR_MISSING",
                f"Key page missing in French: {kt}",
                f"Page: {kp.get('final_url')}",
                "P0" if kt in ("contact", "cart", "checkout") else "P1",
            ))
        elif fr.get("status") == "candidate":
            key_penalty += 5
            issues.append(_issue(
                f"KEY_{kt.upper()}_FR_UNCONFIRMED",
                f"French version not confirmed for: {kt}",
                f"Candidate inferred: {fr.get('url')}",
                "P1",
            ))

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
            issues.append(_issue(
                "FR_PAGE_LOOKS_UNTRANSLATED",
                "French page looks not translated (too similar to English)",
                f"{label}: similarity={sim} vs EN counterpart {leak.get('en_counterpart')}",
                "P0" if is_key else "P1",
            ))

        if en_ratio >= en_ratio_warn:
            score -= 8 if is_key else 4
            snippets = leak.get("snippets") or []
            sn = (" / ".join(snippets)) if snippets else "(no snippet)"
            issues.append(_issue(
                "ENGLISH_LEAK_IN_FRENCH",
                "English content detected inside French page",
                f"{label}: en_ratio={en_ratio} (en_chunks={leak.get('en_chunks')}/{leak.get('chunks_total')}) snippet: {sn}",
                "P1" if is_key else "P2",
            ))

    # check key pages
    for kt, kp in key_pages.items():
        check_leak(kp, f"key={kt} {kp.get('final_url')}", is_key=True)

    # check top few FR pages overall
    fr_pages = [p for p in pages if p.get("lang") == "fr" and p.get("leak")]
    fr_pages_sorted = sorted(fr_pages, key=lambda x: float((x.get("leak") or {}).get("en_ratio") or 0.0), reverse=True)[:8]
    for p in fr_pages_sorted:
        check_leak(p, f"page {p.get('final_url')}", is_key=False)

    score = max(0, min(100, score))

    if score >= 85:
        label = "Good"
    elif score >= 70:
        label = "Moderate risk"
    elif score >= 50:
        label = "High risk"
    else:
        label = "Critical"

    pr_rank = {"P0": 0, "P1": 1, "P2": 2}
    issues_sorted = sorted(issues, key=lambda x: pr_rank.get(x["priority"], 9))

    return {"score": score, "label": label, "issues": issues_sorted}
