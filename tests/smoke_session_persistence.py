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

from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtCore import QThread

from inaes_desktop.main_window import MainWindow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: session persistence save/restore")
    p.add_argument("--curves", type=Path, required=True, help="Curves/analyzed table path")
    p.add_argument("--metadata", type=Path, required=True, help="Metadata table path")
    return p


def _wait_thread_done(app: QApplication, thread_attr: str, owner: object, timeout_s: float = 60.0) -> None:
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


def _iter_owner_threads(owner: object) -> list[str]:
    out: list[str] = []
    for name in dir(owner):
        if not name.endswith("_thread"):
            continue
        try:
            obj = getattr(owner, name)
        except Exception:
            continue
        if isinstance(obj, QThread):
            out.append(name)
    return sorted(set(out))


def _wait_background_tasks(app: QApplication, win: MainWindow) -> None:
    owners: list[object] = [win]
    tabs = win.centralWidget()
    if isinstance(tabs, QTabWidget):
        for i in range(tabs.count()):
            owners.append(tabs.widget(i))

    for owner in owners:
        for attr in _iter_owner_threads(owner):
            _wait_thread_done(app, attr, owner)


def main() -> int:
    args = _parser().parse_args()
    curves = args.curves.expanduser().resolve()
    metadata = args.metadata.expanduser().resolve()
    if not curves.exists():
        raise SystemExit(f"Curves file not found: {curves}")
    if not metadata.exists():
        raise SystemExit(f"Metadata file not found: {metadata}")

    session_file = Path(tempfile.gettempdir()) / "inaes_desktop_session_smoke.json"
    if session_file.exists():
        session_file.unlink()
    os.environ["INAES_DESKTOP_SESSION_FILE"] = str(session_file)

    app = QApplication.instance() or QApplication([])

    win = MainWindow()
    tab = win.tab_data_upload
    if tab is None:
        raise SystemExit("Data Upload tab not initialized.")

    ok_curves = tab._load_curves_from_path(curves, show_errors=False)
    ok_meta = tab._load_metadata_from_path(metadata, show_errors=False)
    if not ok_curves or not ok_meta:
        raise SystemExit("Failed to load curves/metadata in initial window.")

    tab._set_combo_by_data(tab.cb_workflow_mode, "analyzed_upload")
    tab._apply_workflow_mode_ui()
    tab._auto_fill_mapping()
    tab._standardize_curves()
    _wait_thread_done(app, "_std_thread", tab)
    tab._set_combo_by_data(tab.cb_nm_method, "liquid_volume_K")
    tab._apply_nm_method_selection()
    tab.chk_auto_ctrl.setChecked(True)
    tab.in_control_keywords.setText("MilliQ,mq,blank")
    app.processEvents()

    saved = win.save_session_state_now()
    if saved is None or not saved.exists():
        raise SystemExit("Session state was not saved.")

    _wait_background_tasks(app, win)
    win.close()
    win.deleteLater()
    app.processEvents()
    time.sleep(0.05)
    app.processEvents()

    win2 = MainWindow()
    app.processEvents()
    tab2 = win2.tab_data_upload
    if tab2 is None:
        raise SystemExit("Restored Data Upload tab not initialized.")
    _wait_background_tasks(app, win2)

    # Current product policy: startup restore keeps UI/settings, but does not auto-load files.
    if tab2.state.curves_raw is not None:
        raise SystemExit("Session restore policy violated: curves should not auto-load.")
    if tab2.state.metadata is not None:
        raise SystemExit("Session restore policy violated: metadata should not auto-load.")
    if tab2.state.curves_standardized is not None:
        raise SystemExit("Session restore policy violated: standardized curves should not auto-load.")
    if str(tab2.cb_nm_method.currentData() or "") != "liquid_volume_K":
        raise SystemExit("Session restore failed: nM method not restored.")
    if str(tab2._workflow_mode()) != "analyzed_upload":
        raise SystemExit("Session restore failed: workflow mode not restored.")
    if tab2.in_control_keywords.text().strip() != "MilliQ,mq,blank":
        raise SystemExit("Session restore failed: control keywords not restored.")

    print(
        "SMOKE_SESSION_PERSISTENCE_OK",
        "| curves_rows=0",
        "| metadata_rows=0",
        "| standardized_rows=0",
        f"| nm_method={tab2.cb_nm_method.currentData()}",
        f"| session_file={session_file}",
    )

    _wait_background_tasks(app, win2)
    win2.close()
    win2.deleteLater()
    app.processEvents()
    time.sleep(0.05)
    app.processEvents()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
