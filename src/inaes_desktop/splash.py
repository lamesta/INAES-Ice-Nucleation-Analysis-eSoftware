from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from inaes_desktop.resources import project_root, resource_path


_ACTIVE_SPLASH: QWidget | None = None


@dataclass(frozen=True)
class StartupCheck:
    label: str
    fn: Callable[[], tuple[bool, str]]
    required: bool = True


def _project_root() -> Path:
    return project_root()


def _asset_path(name: str) -> Path:
    return resource_path("assets", name)


class StartupSplash(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(860, 430)
        self._pixmap = QPixmap(str(_asset_path("splash_loading.png")))

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 30, 34, 28)
        root.setSpacing(10)
        root.addStretch(1)

        self.title = QLabel("INAES")
        self.title.setStyleSheet("color: #f8fafc; font-size: 30px; font-weight: 800;")
        self.title.setAlignment(Qt.AlignLeft)
        root.addWidget(self.title)

        self.subtitle = QLabel("Scientific ice nucleation analysis")
        self.subtitle.setStyleSheet("color: rgba(226, 232, 240, 0.92); font-size: 14px;")
        root.addWidget(self.subtitle)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        self.progress.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid rgba(147, 197, 253, 0.55);
                border-radius: 6px;
                background: rgba(15, 23, 42, 0.70);
            }
            QProgressBar::chunk {
                border-radius: 5px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #38bdf8, stop:0.45 #22c55e, stop:0.72 #facc15, stop:1 #f43f5e);
            }
            """
        )
        root.addWidget(self.progress)

        self.status = QLabel("Starting...")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: rgba(248, 250, 252, 0.96); font-size: 13px;")
        root.addWidget(self.status)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: rgba(203, 213, 225, 0.82); font-size: 11px;")
        root.addWidget(self.detail)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._pixmap.isNull():
            painter.drawPixmap(self.rect(), self._pixmap)
            painter.fillRect(self.rect(), QColor(2, 6, 23, 115))
        else:
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0.0, QColor("#020617"))
            grad.setColorAt(0.55, QColor("#082f49"))
            grad.setColorAt(1.0, QColor("#111827"))
            painter.fillRect(self.rect(), grad)
            painter.setPen(QColor(56, 189, 248, 95))
            font = QFont("Monaco", 92, QFont.Bold)
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(26, 14, -26, -110), Qt.AlignCenter, "*")
        super().paintEvent(event)

    def set_step(self, pct: int, status: str, detail: str = "") -> None:
        self.progress.setValue(int(max(0, min(100, pct))))
        self.status.setText(str(status))
        self.detail.setText(str(detail))
        QApplication.processEvents()


def _check_import(module: str) -> Callable[[], tuple[bool, str]]:
    def _inner() -> tuple[bool, str]:
        ok = importlib.util.find_spec(module) is not None
        return ok, "available" if ok else f"missing module: {module}"

    return _inner


def _check_project_files() -> tuple[bool, str]:
    if getattr(sys, "frozen", False):
        required = [
            resource_path("assets", "splash_loading.png"),
            resource_path("assets", "app_icon.icns"),
            resource_path("docs", "INAES_Software_Manual.pdf"),
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            return False, "missing packaged resources: " + ", ".join(missing)
        return True, str(resource_path())

    root = _project_root()
    missing = [str(p.relative_to(root)) for p in [root / "run_desktop.py", root / "src" / "inaes_desktop" / "main_window.py"] if not p.exists()]
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, str(root)


def _check_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".inaes_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(path)
    except Exception as exc:
        return False, str(exc)


def _check_temp_dir() -> tuple[bool, str]:
    return _check_writable_dir(Path(tempfile.gettempdir()))


def _startup_checks() -> list[StartupCheck]:
    return [
        StartupCheck("Locating application files", _check_project_files),
        StartupCheck("Checking Qt desktop runtime", _check_import("PySide6")),
        StartupCheck("Checking data stack", _check_import("pandas")),
        StartupCheck("Checking numeric engine", _check_import("numpy")),
        StartupCheck("Checking plotting engine", _check_import("plotly")),
        StartupCheck("Checking scientific libraries", _check_import("scipy")),
        StartupCheck("Checking machine-learning helpers", _check_import("sklearn")),
        StartupCheck("Checking kneepoint piecewise fitter", _check_import("pwlf")),
        StartupCheck("Checking session folder", lambda: _check_writable_dir(Path.home() / ".inaes_desktop")),
        StartupCheck("Checking temporary workspace", _check_temp_dir),
        StartupCheck("Checking static export engine", _check_import("kaleido"), required=False),
    ]


def run_startup_splash(app: QApplication) -> list[str]:
    global _ACTIVE_SPLASH
    splash = StartupSplash()
    _ACTIVE_SPLASH = splash
    splash.show()
    splash.raise_()
    splash.activateWindow()
    warnings: list[str] = []
    checks = _startup_checks()
    n = max(len(checks), 1)

    for i, check in enumerate(checks, start=1):
        pct = int((i - 1) * 92 / n)
        splash.set_step(pct, check.label, "Preparing check...")
        time.sleep(0.03)
        ok, detail = check.fn()
        if not ok and check.required:
            splash.set_step(100, "Startup check failed", f"{check.label}: {detail}")
            time.sleep(1.2)
            splash.close()
            raise RuntimeError(f"{check.label}: {detail}")
        if not ok:
            warnings.append(f"{check.label}: {detail}")
        suffix = "OK" if ok else "Optional warning"
        splash.set_step(int(i * 92 / n), check.label, f"{suffix}: {detail}")
        time.sleep(0.04)

    splash.set_step(96, "Loading interface", "Creating main window...")
    app.processEvents()
    return warnings


def finish_startup_splash(app: QApplication) -> None:
    global _ACTIVE_SPLASH
    if _ACTIVE_SPLASH is not None:
        _ACTIVE_SPLASH.set_step(100, "Ready", "Opening INAES...")
        time.sleep(0.12)
        _ACTIVE_SPLASH.close()
        _ACTIVE_SPLASH.deleteLater()
        _ACTIVE_SPLASH = None
    app.processEvents()
