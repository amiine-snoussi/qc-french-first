from __future__ import annotations
import yaml
from typing import Any, Dict

def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("crawler", {})
    cfg.setdefault("discover", {})
    cfg.setdefault("heuristics", {})
    cfg.setdefault("output", {})
    return cfg
