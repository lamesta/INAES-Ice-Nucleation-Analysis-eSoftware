from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from inaes_desktop.resources import resource_path
from inaes_desktop.theme import apply_builtin_theme


def _configure_qtwebengine_process() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_path = Path(sys.executable).resolve()
    roots = [exe_path.parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(str(meipass)).resolve()
        if meipass_path not in roots:
            roots.append(meipass_path)

    process_candidates: list[Path] = []
    resources_candidates: list[Path] = []
    locales_candidates: list[Path] = []

    if sys.platform == "darwin":
        contents = exe_path.parents[1]
        qtwebengine_bundle_resources = (
            contents
            / "Frameworks"
            / "PySide6"
            / "Qt"
            / "lib"
            / "QtWebEngineCore.framework"
            / "Versions"
            / "Resources"
        )
        process_candidates.append(
            qtwebengine_bundle_resources
            / "Helpers"
            / "QtWebEngineProcess.app"
            / "Contents"
            / "MacOS"
            / "QtWebEngineProcess"
        )
        resources_candidates.append(qtwebengine_bundle_resources / "Resources")
        locales_candidates.append(qtwebengine_bundle_resources / "Resources" / "qtwebengine_locales")
    elif sys.platform.startswith("win"):
        for root in roots:
            process_candidates.extend(
                [
                    root / "PySide6" / "Qt6" / "bin" / "QtWebEngineProcess.exe",
                    root / "PySide6" / "Qt" / "bin" / "QtWebEngineProcess.exe",
                    root / "QtWebEngineProcess.exe",
                ]
            )
            resources_candidates.extend(
                [
                    root / "PySide6" / "Qt6" / "resources",
                    root / "PySide6" / "Qt" / "resources",
                    root / "resources",
                ]
            )
            locales_candidates.extend(
                [
                    root / "PySide6" / "Qt6" / "translations" / "qtwebengine_locales",
                    root / "PySide6" / "Qt" / "translations" / "qtwebengine_locales",
                    root / "translations" / "qtwebengine_locales",
                    root / "qtwebengine_locales",
                ]
            )

    for key, candidates in (
        ("QTWEBENGINEPROCESS_PATH", process_candidates),
        ("QTWEBENGINE_RESOURCES_PATH", resources_candidates),
        ("QTWEBENGINE_LOCALES_PATH", locales_candidates),
    ):
        if os.environ.get(key):
            continue
        for path in candidates:
            if path.exists():
                os.environ[key] = str(path)
                break
        else:
            if sys.platform.startswith("win"):
                for root in roots:
                    if key == "QTWEBENGINEPROCESS_PATH":
                        matches = list(root.rglob("QtWebEngineProcess.exe"))
                        value = matches[0] if matches else None
                    elif key == "QTWEBENGINE_RESOURCES_PATH":
                        matches = list(root.rglob("qtwebengine_resources.pak"))
                        value = matches[0].parent if matches else None
                    else:
                        matches = [p for p in root.rglob("qtwebengine_locales") if p.is_dir()]
                        value = matches[0] if matches else None
                    if value is not None and value.exists():
                        os.environ[key] = str(value)
                        break


def _apply_app_icon(app: QApplication) -> None:
    for name in ("app_icon.png", "app_icon.icns"):
        path = resource_path("assets", name)
        if path.exists():
            app.setWindowIcon(QIcon(str(path)))
            return


def run() -> None:
    _configure_qtwebengine_process()
    app = QApplication(sys.argv)
    # Force the Fusion style so the custom QSS theme (tab bar, radii, sliders)
    # actually renders instead of being overridden by the native macOS/Windows
    # widget chrome, which largely ignores stylesheet rules for QTabBar/QPushButton.
    app.setStyle("Fusion")
    app.setApplicationName("INAES")
    app.setApplicationDisplayName("INAES")
    app.setOrganizationName("INAES")
    _apply_app_icon(app)
    apply_builtin_theme(app, "inaes_dark", font_size=13, compact=False)

    finish_splash = None
    startup_warnings: list[str] = []
    splash_enabled = os.environ.get("INAES_DISABLE_SPLASH", "").strip().lower() not in {"1", "true", "yes"}
    if splash_enabled:
        try:
            from inaes_desktop.splash import finish_startup_splash, run_startup_splash

            finish_splash = finish_startup_splash
            startup_warnings = run_startup_splash(app)
        except Exception as exc:
            startup_warnings.append(f"Startup preflight warning: {exc}")
            print(f"INAES startup preflight warning: {exc}", file=sys.stderr)

    from inaes_desktop.main_window import MainWindow

    win = MainWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    if finish_splash is not None:
        try:
            finish_splash(app)
        except Exception as exc:
            startup_warnings.append(f"Splash close warning: {exc}")
            print(f"INAES splash close warning: {exc}", file=sys.stderr)
    if startup_warnings:
        try:
            win.statusBar().showMessage("Startup warnings: " + " | ".join(startup_warnings), 9000)
        except Exception:
            pass
    sys.exit(app.exec())
