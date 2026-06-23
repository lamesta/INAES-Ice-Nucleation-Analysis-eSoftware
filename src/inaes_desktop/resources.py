from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(str(base)).joinpath(*parts)
    return project_root().joinpath(*parts)


def manual_pdf_path() -> Path:
    candidates = [
        resource_path("docs", "INAES_Software_Manual.pdf"),
        resource_path("assets", "manual", "INAES_Software_Manual.pdf"),
        project_root().parent / "docs" / "INAES_Software_Manual.pdf",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]
