"""
api.py — REST wrapper around the qc-french-first scanner.

Endpoints:
  POST /scan          { "url": "https://example.com", "max_pages": 50 }
                      → { "job_id": "...", "status": "queued" }

  GET  /scan/{job_id} → { "job_id": "...", "status": "running|done|error",
                           "score": 82, "label": "Good", "issues": [...],
                           "report_url": "/reports/<job_id>.html" }

  GET  /reports/pdf/{job_id} → PDF version of the HTML report (weasyprint)
  GET  /reports/{filename}   → serves the HTML report file

Jobs run in a background thread. State is persisted in jobs.sqlite so
restarts don't lose in-flight results.
"""

from __future__ import annotations
from contextlib import asynccontextmanager

import os
import sqlite3
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from scanner.config import load_config
from scanner.discover import discover_urls
from scanner.fetch import fetch_all
from scanner.analyze import analyze_site
from scanner.score import score_site
from scanner.report import render_report
from scanner.storage import save_run
from scanner.utils import normalize_url
from scanner.pairs import build_pairs

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
JOBS_DB    = BASE_DIR / "runs" / "jobs.sqlite"
REPORTS_DIR = BASE_DIR / "runs" / "reports"
CONFIG_PATH = BASE_DIR / "config.yml"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── job store (SQLite) ─────────────────────────────────────────────────────────
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(JOBS_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_jobs_db() -> None:
    JOBS_DB.parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'queued',
                score       INTEGER,
                label       TEXT,
                issues_json TEXT,
                report_path TEXT,
                error       TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()


def _upsert_job(job_id: str, **fields) -> None:
    set_clauses = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with _db_lock:
        conn = _get_conn()
        conn.execute(
            f"UPDATE jobs SET {set_clauses}, updated_at = datetime('now') WHERE job_id = ?",
            values,
        )
        conn.commit()
        conn.close()


def _get_job(job_id: str) -> Optional[sqlite3.Row]:
    with _db_lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
    return row


def _create_job(job_id: str, url: str) -> None:
    with _db_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO jobs (job_id, url, status) VALUES (?, ?, 'queued')",
            (job_id, url),
        )
        conn.commit()
        conn.close()


# ── scanner logic (copied from main.py, returns scored dict) ───────────────────
def _attach_pairs(findings: dict, base_url: str) -> None:
    pages = findings.get("pages") or []
    try:
        findings["pairs"] = build_pairs(pages, base_url)
    except Exception:
        findings["pairs"] = {"summary": {}, "rows": []}

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
        pr = (
            "P0" if kt in ("home", "checkout", "cart", "contact")
            else "P1" if kt in ("products", "services", "returns", "faq", "about")
            else "P2"
        )
        fr = p.get("french") or {}
        fr_status = (fr.get("status") or "missing").strip().lower()
        if fr_status == "present":
            row_status, fr_url = "ok", fr.get("url")
        elif fr_status == "candidate":
            row_status, fr_url = "candidate_fr", fr.get("url")
        else:
            row_status, fr_url = "missing_fr", None
        rows.append({"priority": pr, "key_type": kt, "path": path,
                     "status": row_status, "en_url": en_url, "fr_url": fr_url})
    findings["pairs_sample"] = rows[:300]


def _run_scan(job_id: str, url: str, max_pages: Optional[int]) -> None:
    """Runs in a background thread. Updates job status in DB throughout."""
    try:
        _upsert_job(job_id, status="running")

        cfg = load_config(str(CONFIG_PATH))
        if max_pages is not None:
            cfg.setdefault("crawler", {})["max_pages"] = max_pages
            cfg.setdefault("discover", {})["max_pages"] = max_pages

        urls = discover_urls(url, cfg)
        pages, artifacts_dir = fetch_all(url, urls, cfg)
        findings = analyze_site(url, pages, cfg)
        _attach_pairs(findings, url)

        # Phase-2 candidate confirmation
        crawler_cfg = cfg.get("crawler", {}) or {}
        if crawler_cfg.get("confirm_candidates", True):
            confirm_max = int(crawler_cfg.get("confirm_candidates_max", 20))
            already = set()
            for p in pages:
                try:
                    already.add(normalize_url(p.get("final_url") or p.get("url") or ""))
                except Exception:
                    pass
            cands = []
            for _kt, kp in (findings.get("key_pages") or {}).items():
                fr = kp.get("french") or {}
                if fr.get("status") == "candidate" and fr.get("url"):
                    cands.append(fr["url"])
            uniq, seen = [], set()
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
                more_pages, _ = fetch_all(url, uniq, cfg, out_dir=artifacts_dir)
                pages.extend(more_pages)
                findings = analyze_site(url, pages, cfg)
                _attach_pairs(findings, url)

        scored = score_site(findings, cfg)

        # Save HTML report
        report_path = render_report(url, findings, scored, artifacts_dir, cfg)
        save_run(url, findings, scored, report_path, artifacts_dir)

        # Copy report to a stable, publicly servable path
        import shutil, json as _json
        report_filename = f"{job_id}.html"
        dest = REPORTS_DIR / report_filename
        shutil.copy2(report_path, dest)

        _upsert_job(
            job_id,
            status="done",
            score=int(scored.get("score", 0)),
            label=str(scored.get("label", "")),
            issues_json=_json.dumps(scored.get("issues", []), ensure_ascii=False),
            report_path=str(dest),
        )

    except Exception:
        _upsert_job(job_id, status="error", error=traceback.format_exc())


# ── FastAPI app ────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    url: HttpUrl
    max_pages: Optional[int] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize jobs database
    _init_jobs_db()
    yield
app = FastAPI(
    title="QC French-First Scanner API",
    description="Scan any Quebec website for Bill 96 compliance.",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/scan", status_code=202)
def start_scan(req: ScanRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Enqueue a scan. Returns immediately with a job_id."""
    job_id = str(uuid.uuid4())
    url = str(req.url)
    _create_job(job_id, url)
    background_tasks.add_task(_run_scan, job_id, url, req.max_pages)
    return {"job_id": job_id, "status": "queued", "url": url}


@app.get("/scan/{job_id}")
def get_scan(job_id: str) -> Dict[str, Any]:
    """Poll for scan results."""
    row = _get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result: Dict[str, Any] = {
        "job_id":     row["job_id"],
        "url":        row["url"],
        "status":     row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

    if row["status"] == "done":
        import json as _json
        result["score"]      = row["score"]
        result["label"]      = row["label"]
        result["issues"]     = _json.loads(row["issues_json"] or "[]")
        result["report_url"] = f"/reports/{job_id}.html"
        result["pdf_url"]    = f"/reports/pdf/{job_id}"

    if row["status"] == "error":
        result["error"] = row["error"]

    return result


@app.get("/reports/pdf/{job_id}")
def serve_report_pdf(job_id: str) -> FileResponse:
    """Convert HTML report to PDF via weasyprint and serve it.

    The PDF is cached on first generation so subsequent requests are instant.
    """
    # Sanitize job_id (must look like a UUID, no path traversal)
    import re as _re
    if not _re.match(r"^[a-f0-9\-]{36}$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    pdf_path = REPORTS_DIR / f"{job_id}.pdf"
    html_path = REPORTS_DIR / f"{job_id}.html"

    # Serve cached PDF if it exists
    if pdf_path.exists() and pdf_path.is_file():
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"qc-french-first-{job_id[:8]}.pdf",
        )

    # HTML source must exist
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML report not found — run a scan first")

    # Generate PDF
    try:
        from weasyprint import HTML as WeasyHTML
        WeasyHTML(filename=str(html_path)).write_pdf(str(pdf_path))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {str(exc)[:200]}",
        )

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"qc-french-first-{job_id[:8]}.pdf",
    )


@app.get("/reports/{filename}")
def serve_report(filename: str) -> FileResponse:
    """Serve a generated HTML report."""
    path = REPORTS_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    # Basic path traversal guard
    if not str(path.resolve()).startswith(str(REPORTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return FileResponse(path, media_type="text/html")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


# ── entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)