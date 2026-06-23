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
    str(Path(tempfile.gettempdir()) / f"inaes_desktop_session_smoke_plot_structure_{os.getpid()}.json"),
)

from PySide6.QtWidgets import QApplication, QTabWidget

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df, suggest_curves_column_mapping
from inaes_core.io_universal import read_table_from_path
from inaes_desktop.main_window import MainWindow
from inaes_desktop.state import LoadedTable


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: plot structure regression across desktop tabs")
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


def _assert_fig_basic(name: str, fig, *, expect_log_y: bool | None = None, expect_title_contains: str | None = None) -> None:
    if fig is None:
        raise SystemExit(f"{name}: figure is None")
    traces = list(getattr(fig, "data", []) or [])
    if len(traces) == 0:
        raise SystemExit(f"{name}: no traces in figure")
    layout = getattr(fig, "layout", None)
    if layout is None:
        raise SystemExit(f"{name}: missing layout")
    title_txt = str(getattr(getattr(layout, "title", None), "text", "") or "")
    if expect_title_contains and expect_title_contains.lower() not in title_txt.lower():
        raise SystemExit(f"{name}: unexpected title '{title_txt}'")

    x_title = str(getattr(getattr(layout, "xaxis", None), "title", None).text or "")
    y_title = str(getattr(getattr(layout, "yaxis", None), "title", None).text or "")
    if not x_title.strip():
        raise SystemExit(f"{name}: x-axis title empty")
    if not y_title.strip():
        raise SystemExit(f"{name}: y-axis title empty")

    if expect_log_y is not None:
        y_type = str(getattr(getattr(layout, "yaxis", None), "type", "") or "")
        is_log = y_type.lower() == "log"
        if bool(is_log) != bool(expect_log_y):
            raise SystemExit(f"{name}: y-axis type mismatch. expected_log={expect_log_y} got='{y_type}'")


def _run_kneepoint_with_fallback(kp_tab, app: QApplication) -> None:
    if hasattr(kp_tab, "dil_box"):
        kp_tab.dil_box.select_all()
    size_count = int(getattr(kp_tab.cb_size, "count", lambda: 0)())
    sample_count = int(getattr(kp_tab.cb_sample, "count", lambda: 0)())
    if size_count <= 0 or sample_count <= 0:
        kp_tab._run_kp()
        _wait_thread_done(app, "_kp_thread", kp_tab)
        app.processEvents()
        if "ERROR" in str(kp_tab.lbl_status.text() or "").upper():
            raise SystemExit(f"Kneepoint: {kp_tab.lbl_status.text()}")
        return

    last_err = ""
    for i in range(size_count):
        kp_tab.cb_size.setCurrentIndex(i)
        app.processEvents()
        if hasattr(kp_tab, "dil_box"):
            kp_tab.dil_box.select_all()
        sample_count = int(getattr(kp_tab.cb_sample, "count", lambda: 0)())
        for j in range(sample_count):
            kp_tab.cb_sample.setCurrentIndex(j)
            app.processEvents()
            kp_tab._run_kp()
            _wait_thread_done(app, "_kp_thread", kp_tab)
            app.processEvents()
            status = str(kp_tab.lbl_status.text() or "")
            if "ERROR" not in status.upper():
                return
            last_err = status
    raise SystemExit(f"Kneepoint: {last_err or 'No valid Sample/Size combination found.'}")


def main() -> int:
    args = _parser().parse_args()
    curves_df = read_table_from_path(args.curves)
    meta_df = read_table_from_path(args.metadata)
    std_df, warnings, resolved = _prepare_standardized(curves_df)

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.state.set_curves_raw(LoadedTable(path=args.curves, df=curves_df))
    win.state.set_curves_standardized(LoadedTable(path=Path("standardized_from_smoke_plot_structure.csv"), df=std_df))
    win.state.set_metadata(LoadedTable(path=args.metadata, df=meta_df))
    app.processEvents()

    tabs = win.centralWidget()
    if not isinstance(tabs, QTabWidget):
        raise SystemExit("Main window central widget is not QTabWidget.")

    fc_tab = tabs.widget(1)
    cmp_tab = tabs.widget(2)
    ff_tab = tabs.widget(3)
    kp_tab = tabs.widget(4)
    box_tab = tabs.widget(5)
    cor_tab = tabs.widget(6)

    if hasattr(fc_tab, "size_box"):
        fc_tab.size_box.select_all()
    if hasattr(fc_tab, "dil_box"):
        fc_tab.dil_box.select_all()
    if hasattr(fc_tab, "loc_box"):
        fc_tab.loc_box.select_all()
    fc_tab._run_curves()
    _wait_thread_done(app, "_fc_thread", fc_tab)
    app.processEvents()
    _assert_fig_basic("FreezingCurves(main)", fc_tab._last_main_fig, expect_log_y=True, expect_title_contains="Freezing")
    _assert_fig_basic("FreezingCurves(mean)", fc_tab._last_mean_fig, expect_log_y=True, expect_title_contains="Mean")

    if hasattr(cmp_tab, "sample_box"):
        cmp_tab.sample_box.select_all()
    if hasattr(cmp_tab, "size_box"):
        cmp_tab.size_box.select_all()
    if hasattr(cmp_tab, "dil_box"):
        cmp_tab.dil_box.select_all()
    cmp_tab._run_compare()
    _wait_thread_done(app, "_cmp_thread", cmp_tab)
    app.processEvents()
    _assert_fig_basic("CompareSamplesFC", cmp_tab._last_plot_fig, expect_log_y=True, expect_title_contains="Compare")

    if hasattr(ff_tab, "sample_box"):
        ff_tab.sample_box.select_all()
    if hasattr(ff_tab, "size_box"):
        ff_tab.size_box.select_all()
    if hasattr(ff_tab, "dil_box"):
        ff_tab.dil_box.select_all()
    ff_tab._run_ff()
    _wait_thread_done(app, "_ff_thread", ff_tab)
    app.processEvents()
    _assert_fig_basic("FrozenFraction", ff_tab._last_plot_fig, expect_log_y=False, expect_title_contains="Frozen")

    _run_kneepoint_with_fallback(kp_tab, app)
    if int(kp_tab.table_bp.rowCount()) <= 0:
        raise SystemExit("Kneepoint: results table is empty after successful run.")

    _wait_thread_done(app, "_box_state_thread", box_tab)
    box_tab._run_boxplot_async()
    _wait_thread_done(app, "_box_thread", box_tab)
    app.processEvents()
    _assert_fig_basic("Boxplots", box_tab._last_plot_fig, expect_title_contains="Box")

    _wait_thread_done(app, "_cor_state_thread", cor_tab)
    cor_tab._run_correlation_async()
    _wait_thread_done(app, "_cor_thread", cor_tab)
    app.processEvents()
    _assert_fig_basic("Correlations", cor_tab._last_plot_fig, expect_title_contains="Correlation")

    print(
        "SMOKE_PLOT_STRUCTURE_OK",
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
