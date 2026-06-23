#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
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
os.environ.setdefault(
    "INAES_DESKTOP_SESSION_FILE",
    str(Path(tempfile.gettempdir()) / f"inaes_desktop_session_smoke_async_box_cor_{os.getpid()}.json"),
)

from PySide6.QtWidgets import QApplication, QTabWidget

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df, suggest_curves_column_mapping
from inaes_core.io_universal import read_table_from_path
from inaes_desktop.main_window import MainWindow
from inaes_desktop.state import LoadedTable


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: async Boxplots/Correlations worker paths")
    p.add_argument("--curves", type=Path, required=True, help="Curves/analyzed table path")
    p.add_argument("--metadata", type=Path, required=True, help="Metadata table path")
    return p


def _prepare_standardized(curves_df):
    sugg = suggest_curves_column_mapping(curves_df)
    cfg = CurvesMappingConfig(
        map_sample=sugg.get("Sample"),
        map_size=sugg.get("Size"),
        map_location=sugg.get("Location"),
        map_temp=sugg.get("Freezing.temperature"),
        map_nm=sugg.get("nm"),
        map_control=sugg.get("Control"),
        map_dilution=sugg.get("Dilution.factor"),
        map_ff=sugg.get("FF"),
        use_size_grouping=True,
        use_location_grouping=True,
        auto_dilution_from_sample=True,
        auto_control_from_sample=True,
    )
    std_df, warnings, resolved = standardize_curves_df(curves_df, cfg)
    return std_df, warnings, resolved


def _wait_thread_done(app: QApplication, thread_attr: str, owner: object, timeout_s: float = 90.0) -> None:
    t0 = time.time()
    while True:
        app.processEvents()
        thread = getattr(owner, thread_attr, None)
        if thread is None:
            return
        if not thread.isRunning():
            return
        if (time.time() - t0) > timeout_s:
            raise SystemExit(f"Timeout waiting for {thread_attr} to finish.")
        time.sleep(0.02)


def _assert_ok_status(name: str, status_text: str) -> None:
    s = str(status_text or "").strip()
    if not s:
        raise SystemExit(f"{name}: empty status text")
    if "ERROR" in s.upper():
        raise SystemExit(f"{name}: {s}")


def main() -> int:
    args = _parser().parse_args()
    curves_df = read_table_from_path(args.curves)
    meta_df = read_table_from_path(args.metadata)
    std_df, warnings, resolved = _prepare_standardized(curves_df)

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.state.set_curves_raw(LoadedTable(path=args.curves, df=curves_df))
    win.state.set_curves_standardized(LoadedTable(path=Path("standardized_from_smoke_async.csv"), df=std_df))
    win.state.set_metadata(LoadedTable(path=args.metadata, df=meta_df))
    app.processEvents()

    tabs = win.centralWidget()
    if not isinstance(tabs, QTabWidget):
        raise SystemExit("Main window central widget is not QTabWidget.")

    box_tab = tabs.widget(5)
    cor_tab = tabs.widget(6)

    _wait_thread_done(app, "_box_state_thread", box_tab)
    box_tab._run_boxplot_async()
    _wait_thread_done(app, "_box_thread", box_tab)
    app.processEvents()
    _assert_ok_status("Boxplots async", box_tab.lbl_status.text())

    _wait_thread_done(app, "_cor_state_thread", cor_tab)
    cor_tab._run_correlation_async()
    _wait_thread_done(app, "_cor_thread", cor_tab)
    app.processEvents()
    _assert_ok_status("Correlations async", cor_tab.lbl_status.text())

    print(
        "SMOKE_ASYNC_BOX_COR_OK",
        f"| curves_rows={len(curves_df)}",
        f"| standardized_rows={len(std_df)}",
        f"| metadata_rows={len(meta_df)}",
        f"| resolved_map={resolved}",
        f"| warnings={len(warnings)}",
    )

    win.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
