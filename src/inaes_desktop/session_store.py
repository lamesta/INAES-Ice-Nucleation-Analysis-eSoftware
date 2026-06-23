from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SESSION_SCHEMA_VERSION = 1


def _session_path() -> Path:
    override = os.environ.get("INAES_DESKTOP_SESSION_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".inaes_desktop" / "session_state_v1.json"


def load_session_state() -> dict[str, Any] | None:
    path = _session_path()
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    return data


def save_session_state(payload: dict[str, Any]) -> Path:
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    txt = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    tmp.write_text(txt, encoding="utf-8")
    tmp.replace(path)
    return path

