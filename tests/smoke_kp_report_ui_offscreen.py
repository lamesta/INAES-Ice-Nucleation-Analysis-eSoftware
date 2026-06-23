#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import zipfile

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
    str(Path(tempfile.gettempdir()) / f"inaes_desktop_session_smoke_kp_report_ui_{os.getpid()}.json"),
)

from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df, suggest_curves_column_mapping
from inaes_core.io_universal import read_table_from_path
from inaes_core.kneepoint import filter_kp_points_for_sample
from inaes_desktop.main_window import MainWindow
from inaes_desktop.state import LoadedTable


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: Kneepoint report UI batch flow (offscreen)")
    p.add_argument("--curves", type=Path, required=True, help="Curves/analyzed table path")
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


def _wait_thread_done(app: QApplication, thread_attr: str, owner: object, timeout_s: float = 180.0) -> None:
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


def _stub_message_boxes() -> None:
    def _noop(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    QMessageBox.information = staticmethod(_noop)  # type: ignore[method-assign]
    QMessageBox.warning = staticmethod(_noop)  # type: ignore[method-assign]
    QMessageBox.critical = staticmethod(_noop)  # type: ignore[method-assign]


def _extract_zip_path(artifact_text: str) -> Path | None:
    m = re.search(r"ZIP:\s*(.+)", str(artifact_text or ""))
    if not m:
        return None
    p = Path(m.group(1).strip())
    return p


def _pick_valid_sample_size(kp_tab, curves_df) -> tuple[str, str]:
    selected_dils = kp_tab.dil_box.selected_values() or kp_tab.dil_box.values()
    size_count = int(getattr(kp_tab.cb_size, "count", lambda: 0)())
    sample_count = int(getattr(kp_tab.cb_sample, "count", lambda: 0)())
    for i in range(size_count):
        kp_tab.cb_size.setCurrentIndex(i)
        size = kp_tab.cb_size.currentText().strip()
        if not size:
            continue
        for j in range(sample_count):
            kp_tab.cb_sample.setCurrentIndex(j)
            sample = kp_tab.cb_sample.currentText().strip()
            if not sample:
                continue
            try:
                pts = filter_kp_points_for_sample(
                    curves_df,
                    sample=sample,
                    size=size,
                    dilutions=selected_dils,
                    temp_min=None,
                    temp_max=None,
                )
            except Exception:
                continue
            if len(pts) > 0:
                return sample, size
    raise SystemExit("Kneepoint report UI: no valid Sample/Size pair found for report smoke.")


def main() -> int:
    args = _parser().parse_args()
    curves_df = read_table_from_path(args.curves)
    std_df, warnings, resolved = _prepare_standardized(curves_df)

    _stub_message_boxes()
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.state.set_curves_raw(LoadedTable(path=args.curves, df=curves_df))
    win.state.set_curves_standardized(LoadedTable(path=Path("standardized_from_smoke_kp_report.csv"), df=std_df))
    app.processEvents()

    tabs = win.centralWidget()
    if not isinstance(tabs, QTabWidget):
        raise SystemExit("Main window central widget is not QTabWidget.")
    kp_tab = tabs.widget(4)

    if int(getattr(kp_tab.kp_report_samples, "count", lambda: 0)()) <= 0:
        raise SystemExit("Kneepoint report sample list is empty.")
    if int(getattr(kp_tab.cb_size, "count", lambda: 0)()) <= 0:
        raise SystemExit("Kneepoint report UI: no size options available.")
    if hasattr(kp_tab, "dil_box") and int(getattr(kp_tab.dil_box.list, "count", lambda: 0)()) > 0:
        kp_tab.dil_box.select_all()
    sample_name, size_name = _pick_valid_sample_size(kp_tab, win.state.curves_standardized.df)

    # Select one sample to keep smoke fast.
    kp_tab._kp_report_clear_selection()
    for i in range(kp_tab.kp_report_samples.count()):
        item = kp_tab.kp_report_samples.item(i)
        if str(item.text()).strip() == sample_name:
            item.setSelected(True)
            break
    kp_tab.cb_size.setCurrentText(size_name)

    out_dir = Path(tempfile.gettempdir()) / "inaes_kp_report_ui_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    kp_tab.in_kp_report_dir.setText(str(out_dir))
    kp_tab.in_kp_report_prefix.setText("kp_smoke")
    kp_tab._create_kp_report()
    _wait_thread_done(app, "_kp_report_thread", kp_tab)
    app.processEvents()

    preview_status = str(kp_tab.lbl_kp_report_status.text() or "")
    if "ERROR" in preview_status.upper():
        raise SystemExit(f"Kneepoint report preview UI error: {preview_status}")
    if "CANCELLED" in preview_status.upper():
        raise SystemExit(f"Kneepoint report preview cancelled unexpectedly: {preview_status}")
    if not getattr(kp_tab, "_kp_report_preview", None):
        raise SystemExit("Kneepoint report preview was not generated.")
    if not bool(getattr(kp_tab.btn_kp_report_download, "isEnabled", lambda: False)()):
        raise SystemExit("Kneepoint report download button not enabled after preview.")

    kp_tab._download_kp_report()
    _wait_thread_done(app, "_kp_report_thread", kp_tab)
    app.processEvents()

    status_txt = str(kp_tab.lbl_kp_report_status.text() or "")
    if "ERROR" in status_txt.upper():
        raise SystemExit(f"Kneepoint report export UI error: {status_txt}")
    if "CANCELLED" in status_txt.upper():
        raise SystemExit(f"Kneepoint report export cancelled unexpectedly: {status_txt}")

    artifact_txt = str(kp_tab.kp_report_artifacts.toPlainText() or "")
    zip_path = _extract_zip_path(artifact_txt)
    if zip_path is None:
        raise SystemExit("Kneepoint report UI: ZIP path not found in artifacts panel text.")
    if not zip_path.exists():
        raise SystemExit(f"Kneepoint report UI: ZIP file not found at {zip_path}")

    expected_any = {"kneepoint_summary.csv", "kneepoint_parameters.csv", "all_samples_grid.pdf", "all_samples_grid.svg", "kneepoint_report.pdf", "kneepoint_report.svg"}
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    missing = [nm for nm in expected_any if nm not in names]
    if len(missing) == len(expected_any):
        raise SystemExit("Kneepoint report UI: expected core artifacts not found in ZIP.")

    print(
        "SMOKE_KP_REPORT_UI_OK",
        f"| sample={sample_name}",
        f"| zip={zip_path}",
        f"| curves_rows={len(curves_df)}",
        f"| standardized_rows={len(std_df)}",
        f"| resolved_map={resolved}",
        f"| warnings={len(warnings)}",
    )

    win.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
