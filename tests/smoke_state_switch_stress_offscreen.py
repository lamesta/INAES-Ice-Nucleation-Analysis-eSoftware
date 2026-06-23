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
    str(Path(tempfile.gettempdir()) / f"inaes_desktop_session_smoke_state_switch_{os.getpid()}.json"),
)

from PySide6.QtWidgets import QApplication, QTabWidget

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df, suggest_curves_column_mapping
from inaes_core.io_universal import read_table_from_path
from inaes_desktop.main_window import MainWindow
from inaes_desktop.state import LoadedTable


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stress smoke: repeated state-switch + all-panel run (offscreen)")
    p.add_argument("--curves", type=Path, required=True, help="Primary curves/analyzed table")
    p.add_argument("--metadata", type=Path, required=True, help="Primary metadata table")
    p.add_argument("--curves-alt", type=Path, default=None, help="Optional alternate curves table")
    p.add_argument("--metadata-alt", type=Path, default=None, help="Optional alternate metadata table")
    p.add_argument("--cycles", type=int, default=2, help="Number of switch/run cycles")
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


def _assert_ok_status(name: str, status_text: str) -> None:
    s = str(status_text or "").strip()
    if not s:
        raise SystemExit(f"{name}: empty status text")
    if "ERROR" in s.upper():
        raise SystemExit(f"{name}: {s}")


def _wait_thread_done(app: QApplication, thread_attr: str, owner: object, timeout_s: float = 120.0) -> None:
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


def _run_kneepoint_with_fallback(kp_tab, app: QApplication) -> None:
    if hasattr(kp_tab, "dil_box"):
        kp_tab.dil_box.select_all()
    size_count = int(getattr(kp_tab.cb_size, "count", lambda: 0)())
    sample_count = int(getattr(kp_tab.cb_sample, "count", lambda: 0)())
    if size_count <= 0 or sample_count <= 0:
        kp_tab._run_kp()
        _wait_thread_done(app, "_kp_thread", kp_tab)
        app.processEvents()
        _assert_ok_status("Kneepoint", kp_tab.lbl_status.text())
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


def _run_all_panels_once(win: MainWindow, app: QApplication) -> None:
    tabs = win.centralWidget()
    if not isinstance(tabs, QTabWidget):
        raise SystemExit("Main window central widget is not QTabWidget.")
    if tabs.count() < 7:
        raise SystemExit(f"Unexpected tab count: {tabs.count()}")

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
    _assert_ok_status("Freezing Curves", fc_tab.lbl_status.text())

    if hasattr(cmp_tab, "sample_box"):
        cmp_tab.sample_box.select_all()
    if hasattr(cmp_tab, "size_box"):
        cmp_tab.size_box.select_all()
    if hasattr(cmp_tab, "dil_box"):
        cmp_tab.dil_box.select_all()
    cmp_tab._run_compare()
    _wait_thread_done(app, "_cmp_thread", cmp_tab)
    app.processEvents()
    _assert_ok_status("Compare Samples FC", cmp_tab.lbl_status.text())

    if hasattr(ff_tab, "sample_box"):
        ff_tab.sample_box.select_all()
    if hasattr(ff_tab, "size_box"):
        ff_tab.size_box.select_all()
    if hasattr(ff_tab, "dil_box"):
        ff_tab.dil_box.select_all()
    ff_tab._run_ff()
    _wait_thread_done(app, "_ff_thread", ff_tab)
    app.processEvents()
    _assert_ok_status("Frozen Fraction", ff_tab.lbl_status.text())

    _run_kneepoint_with_fallback(kp_tab, app)

    _wait_thread_done(app, "_box_state_thread", box_tab)
    box_tab._run_boxplot()
    app.processEvents()
    _assert_ok_status("Boxplots", box_tab.lbl_status.text())

    _wait_thread_done(app, "_cor_state_thread", cor_tab)
    cor_tab._run_correlation()
    app.processEvents()
    _assert_ok_status("Correlations", cor_tab.lbl_status.text())


def main() -> int:
    args = _parser().parse_args()
    if args.cycles < 1:
        raise SystemExit("--cycles must be >= 1")

    curves_a = read_table_from_path(args.curves)
    meta_a = read_table_from_path(args.metadata)
    std_a, _, _ = _prepare_standardized(curves_a)

    curves_b = curves_a
    meta_b = meta_a
    std_b = std_a
    if args.curves_alt is not None:
        curves_b = read_table_from_path(args.curves_alt)
        if args.metadata_alt is None:
            raise SystemExit("When using --curves-alt you must also provide --metadata-alt.")
        meta_b = read_table_from_path(args.metadata_alt)
        std_b, _, _ = _prepare_standardized(curves_b)
    elif args.metadata_alt is not None:
        raise SystemExit("--metadata-alt requires --curves-alt.")

    app = QApplication.instance() or QApplication([])
    win = MainWindow()

    for i in range(args.cycles):
        use_alt = (i % 2 == 1)
        if use_alt:
            curves_df, std_df, meta_df = curves_b, std_b, meta_b
            c_path = args.curves_alt if args.curves_alt is not None else args.curves
            m_path = args.metadata_alt if args.metadata_alt is not None else args.metadata
        else:
            curves_df, std_df, meta_df = curves_a, std_a, meta_a
            c_path, m_path = args.curves, args.metadata

        win.state.set_curves_raw(LoadedTable(path=Path(c_path), df=curves_df))
        win.state.set_curves_standardized(LoadedTable(path=Path(f"standardized_cycle_{i+1}.csv"), df=std_df))
        win.state.set_metadata(LoadedTable(path=Path(m_path), df=meta_df))
        app.processEvents()

        _run_all_panels_once(win, app)

    print(
        "SMOKE_STATE_SWITCH_STRESS_OK",
        f"| cycles={args.cycles}",
        f"| rows_a={len(curves_a)}",
        f"| rows_b={len(curves_b)}",
        f"| metadata_a={len(meta_a)}",
        f"| metadata_b={len(meta_b)}",
    )

    win.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
