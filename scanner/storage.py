from __future__ import annotations
import os
import json
import sqlite3
from typing import Dict, Any
from urllib.parse import urlparse
from .utils import ensure_dir

DB_PATH = os.path.join("runs", "history.sqlite")

def _init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL,
        base_url TEXT NOT NULL,
        created_at TEXT NOT NULL,
        score INTEGER NOT NULL,
        label TEXT NOT NULL,
        report_path TEXT NOT NULL,
        artifacts_dir TEXT NOT NULL,
        platform TEXT,
        issues_json TEXT
    );
    """)
    conn.commit()

def save_run(base_url: str, findings: Dict[str, Any], scored: Dict[str, Any], report_path: str, artifacts_dir: str) -> None:
    ensure_dir("runs")
    conn = sqlite3.connect(DB_PATH)
    _init_db(conn)

    domain = urlparse(base_url).netloc.lower()
    platform = (findings.get("platform") or {}).get("name", "Unknown")
    issues_json = json.dumps(scored.get("issues", []), ensure_ascii=False)

    cur = conn.cursor()
    cur.execute("""
    INSERT INTO runs (domain, base_url, created_at, score, label, report_path, artifacts_dir, platform, issues_json)
    VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)
    """, (domain, base_url, int(scored.get("score", 0)), str(scored.get("label","")), report_path, artifacts_dir, platform, issues_json))
    conn.commit()
    conn.close()
