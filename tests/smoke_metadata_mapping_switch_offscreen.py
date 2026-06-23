#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-software-rasterizer",
)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from inaes_desktop.main_window import MainWindow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: metadata mapping switch/reload stability")
    p.add_argument("--curves", type=Path, required=True, help="Curves/analyzed file path")
    p.add_argument("--metadata", type=Path, required=True, help="Metadata file path")
    p.add_argument("--metadata-alt", type=Path, default=None, help="Optional second metadata file")
    p.add_argument("--cycles", type=int, default=80, help="Mapping churn cycles")
    return p


def _pump(app: QApplication, ms: int = 80) -> None:
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(app.quit)
    timer.start(max(10, int(ms)))
    app.exec()


def main() -> int:
    args = _parser().parse_args()
    curves = args.curves.expanduser().resolve()
    meta_a = args.metadata.expanduser().resolve()
    meta_b = args.metadata_alt.expanduser().resolve() if args.metadata_alt else meta_a

    if not curves.exists():
        raise SystemExit(f"Curves file not found: {curves}")
    if not meta_a.exists():
        raise SystemExit(f"Metadata file not found: {meta_a}")
    if not meta_b.exists():
        raise SystemExit(f"Metadata alt file not found: {meta_b}")

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    tab = win.tab_data_upload
    if tab is None:
        raise SystemExit("Data Upload tab not initialized.")

    if not tab._load_curves_from_path(curves, show_errors=False):
        raise SystemExit("Failed to load curves.")
    if not tab._load_metadata_from_path(meta_a, show_errors=False):
        raise SystemExit("Failed to load metadata.")

    _pump(app, 180)

    for i in range(max(1, int(args.cycles))):
        if tab.cb_meta_sample.count() > 0:
            tab.cb_meta_sample.setCurrentIndex(i % tab.cb_meta_sample.count())
        _pump(app, 20)

    # Reload metadata while app is open, then churn again.
    if not tab._load_metadata_from_path(meta_b, show_errors=False):
        raise SystemExit("Failed to reload metadata.")
    _pump(app, 180)
    for i in range(max(1, int(args.cycles // 2))):
        if tab.cb_meta_sample.count() > 0:
            tab.cb_meta_sample.setCurrentIndex((i * 3) % tab.cb_meta_sample.count())
        _pump(app, 20)

    out = tab.state.metadata
    if out is None:
        raise SystemExit("Mapped metadata missing after mapping churn.")
    if "Sample" not in [str(c) for c in out.df.columns]:
        raise SystemExit("Mapped metadata missing required 'Sample' column.")
    if len(out.df) == 0:
        raise SystemExit("Mapped metadata unexpectedly empty.")

    print(
        "SMOKE_METADATA_MAPPING_SWITCH_OK",
        f"| rows={len(out.df)}",
        f"| cols={len(out.df.columns)}",
        f"| status={tab.lbl_meta_map_status.text()}",
    )

    win.close()
    win.deleteLater()
    app.processEvents()
    time.sleep(0.03)
    app.processEvents()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

