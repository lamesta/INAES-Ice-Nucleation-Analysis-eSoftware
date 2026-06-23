from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import plotly.io as pio
from PySide6.QtWidgets import QFileDialog, QWidget


@dataclass
class PlotExportConfig:
    fmt: str
    width: int
    height: int
    scale: float
    stem: str


class PlotExportManager:
    """Centralized plot export manager for all desktop tabs."""

    SUPPORTED_FORMATS = {"svg", "pdf", "png"}
    _default_export_dir: Path | None = None

    def set_default_export_dir(self, directory: str | Path | None) -> None:
        if directory is None:
            self._default_export_dir = None
            return
        txt = str(directory).strip()
        if not txt:
            self._default_export_dir = None
            return
        p = Path(txt).expanduser()
        self._default_export_dir = p

    def save_plotly_figure(self, parent: QWidget, fig: Any, cfg: PlotExportConfig) -> Path | None:
        fmt = str(cfg.fmt or "svg").strip().lower()
        if fmt not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported export format: {fmt}")

        ext_filter = {
            "svg": "SVG files (*.svg)",
            "pdf": "PDF files (*.pdf)",
            "png": "PNG files (*.png)",
        }[fmt]
        default_name = f"{cfg.stem}.{fmt}"
        start_path = default_name
        if self._default_export_dir is not None:
            try:
                self._default_export_dir.mkdir(parents=True, exist_ok=True)
                start_path = str(self._default_export_dir / default_name)
            except Exception:
                start_path = default_name
        out_path_txt, _ = QFileDialog.getSaveFileName(parent, "Save plot", start_path, ext_filter)
        if not out_path_txt:
            return None

        out_path = Path(out_path_txt)
        if out_path.suffix.lower() != f".{fmt}":
            out_path = out_path.with_suffix(f".{fmt}")

        try:
            payload = pio.to_image(
                fig,
                format=fmt,
                width=int(cfg.width),
                height=int(cfg.height),
                scale=(1.0 if fmt == "pdf" else float(cfg.scale)),
            )
        except Exception as exc:
            msg = str(exc)
            if "kaleido" in msg.lower():
                raise RuntimeError(
                    "Export requires Kaleido. Install with: python -m pip install --upgrade kaleido"
                ) from exc
            raise RuntimeError(msg) from exc

        out_path.write_bytes(payload)
        return out_path


default_export_manager = PlotExportManager()
