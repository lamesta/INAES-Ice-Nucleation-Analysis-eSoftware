from __future__ import annotations

from typing import Any

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


APP_STYLESHEET_DARK = """
QMainWindow { background: #0b1020; }
QWidget {
    color: #e5e7eb;
    background: #111827;
    font-family: "SF Pro Text", "SF Pro Display", ".SF NS Text", ".SF NS Display", "Helvetica Neue", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}
QFrame#TopBarFrame {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 12px;
}
QTabWidget::pane { border: 1px solid #334155; background: #0f172a; border-radius: 12px; margin-top: 4px; }
QTabBar::tab {
    background: transparent;
    color: #93a4bd;
    border: none;
    border-radius: 9px;
    padding: 8px 16px;
    margin: 4px 3px 0 3px;
}
QTabBar::tab:hover { background: rgba(148, 163, 184, 0.08); color: #e5e7eb; }
QTabBar::tab:selected { background: rgba(37, 99, 235, 0.18); color: #bcd2ff; font-weight: 600; }
QGroupBox {
    border: 1px solid #334155;
    border-radius: 12px;
    margin-top: 12px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 6px;
    color: #93c5fd;
    font-weight: 600;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #0b1220;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px;
    min-height: 28px;
    color: #e5e7eb;
}
QListWidget {
    background: #0b1220;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 6px;
}
QPushButton {
    background: #2563eb;
    border: 1px solid #2563eb;
    border-radius: 9px;
    color: #ffffff;
    padding: 8px 12px;
    min-height: 32px;
    font-weight: 600;
}
QPushButton:hover { background: #1d4ed8; }
QPushButton:pressed { background: #1741a6; }
QPushButton:disabled {
    background: #1f2937;
    border: 1px solid #374151;
    color: #94a3b8;
}
QTableWidget {
    background: #0b1220;
    gridline-color: #334155;
    border: 1px solid #334155;
}
QHeaderView::section {
    background: #1f2937;
    color: #e5e7eb;
    border: 1px solid #334155;
    padding: 6px;
    font-weight: 600;
}
QStatusBar {
    background: #0f172a;
    color: #93c5fd;
}
QSlider::groove:horizontal {
    border: 1px solid #334155;
    height: 6px;
    background: #1f2937;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #2563eb;
    border-radius: 3px;
}
QSlider::add-page:horizontal {
    background: #334155;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #2563eb;
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QPushButton[chip="true"] {
    background: transparent;
    border: 1px solid #46566e;
    border-radius: 13px;
    color: #93a4bd;
    padding: 4px 12px;
    min-height: 22px;
    max-height: 26px;
    min-width: 0px;
    font-weight: 500;
}
QPushButton[chip="true"]:hover { border-color: #2563eb; color: #e5e7eb; }
QPushButton[chip="true"]:checked {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[chipAction="true"] {
    background: transparent;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #93a4bd;
    padding: 4px 10px;
    min-height: 24px;
    max-height: 26px;
    min-width: 0px;
    font-weight: 500;
}
QPushButton[chipAction="true"]:hover { border-color: #2563eb; color: #bcd2ff; background: rgba(37, 99, 235, 0.12); }
QGroupBox#FlatGroup { border: none; margin-top: 6px; padding-top: 4px; background: transparent; }
QLabel[caption="true"] { color: #7e90ab; font-size: 11px; }
QLabel[pageTitle="true"] { color: #eef2f7; font-size: 17px; font-weight: 650; }
QLabel[pageSubtitle="true"] { color: #93a4bd; font-size: 12px; }
"""


APP_STYLESHEET_LIGHT = """
QMainWindow { background: #f6f8fb; }
QWidget {
    color: #0f172a;
    background: #f8fafc;
    font-family: "SF Pro Text", "SF Pro Display", ".SF NS Text", ".SF NS Display", "Helvetica Neue", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}
QFrame#TopBarFrame {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QTabWidget::pane { border: 1px solid #e2e8f0; background: #ffffff; border-radius: 12px; margin-top: 4px; }
QTabBar::tab {
    background: transparent;
    color: #5b6b82;
    border: none;
    border-radius: 9px;
    padding: 8px 16px;
    margin: 4px 3px 0 3px;
}
QTabBar::tab:hover { background: rgba(37, 99, 235, 0.06); color: #1d4ed8; }
QTabBar::tab:selected { background: #e8f0fe; color: #1d4ed8; font-weight: 600; }
QGroupBox {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    margin-top: 12px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 6px;
    color: #1d4ed8;
    font-weight: 600;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #cbd7e6;
    border-radius: 8px;
    padding: 7px;
    min-height: 28px;
    color: #0f172a;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #cbd7e6;
    border-radius: 8px;
    padding: 6px;
}
QPushButton {
    background: #2563eb;
    border: 1px solid #2563eb;
    border-radius: 9px;
    color: #ffffff;
    padding: 8px 12px;
    min-height: 32px;
    font-weight: 600;
}
QPushButton:hover { background: #1d4ed8; }
QPushButton:pressed { background: #1741a6; }
QPushButton:disabled {
    background: #e2e8f0;
    border: 1px solid #cbd5e1;
    color: #64748b;
}
QTableWidget {
    background: #ffffff;
    gridline-color: #e2e8f0;
    border: 1px solid #e2e8f0;
}
QHeaderView::section {
    background: #f1f5f9;
    color: #0f172a;
    border: 1px solid #e2e8f0;
    padding: 6px;
    font-weight: 600;
}
QStatusBar {
    background: #ffffff;
    color: #1d4ed8;
}
QSlider::groove:horizontal {
    border: 1px solid #cbd7e6;
    height: 6px;
    background: #e2e8f0;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #2563eb;
    border-radius: 3px;
}
QSlider::add-page:horizontal {
    background: #cbd5e1;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #2563eb;
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QPushButton[chip="true"] {
    background: #ffffff;
    border: 1px solid #cbd7e6;
    border-radius: 13px;
    color: #5b6b82;
    padding: 4px 12px;
    min-height: 22px;
    max-height: 26px;
    min-width: 0px;
    font-weight: 500;
}
QPushButton[chip="true"]:hover { border-color: #2563eb; color: #1d4ed8; }
QPushButton[chip="true"]:checked {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[chipAction="true"] {
    background: transparent;
    border: 1px solid #cbd7e6;
    border-radius: 8px;
    color: #5b6b82;
    padding: 4px 10px;
    min-height: 24px;
    max-height: 26px;
    min-width: 0px;
    font-weight: 500;
}
QPushButton[chipAction="true"]:hover { border-color: #2563eb; color: #1d4ed8; background: rgba(37, 99, 235, 0.08); }
QGroupBox#FlatGroup { border: none; margin-top: 6px; padding-top: 4px; background: transparent; }
QLabel[caption="true"] { color: #475569; font-size: 11px; }
QLabel[pageTitle="true"] { color: #0f172a; font-size: 17px; font-weight: 650; }
QLabel[pageSubtitle="true"] { color: #5b6b82; font-size: 12px; }
"""


APP_STYLESHEET_COMPACT_PATCH = """
QGroupBox { margin-top: 8px; padding-top: 6px; border-radius: 7px; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    min-height: 24px;
    padding: 4px;
}
QPushButton { min-height: 28px; padding: 5px 8px; border-radius: 7px; }
"""

APP_STYLESHEET_GEOMETRY_PATCH = """
QTabWidget::tab-bar {
    alignment: left;
    left: 0px;
}
QTabBar::tab {
    min-width: 122px;
    min-height: 30px;
    border-radius: 9px;
}
QSplitter::handle {
    background: rgba(148,163,184,0.18);
    border-radius: 3px;
}
QSplitter::handle:horizontal { width: 8px; }
QSplitter::handle:vertical { height: 8px; }
QScrollArea { border: none; }
QComboBox {
    min-width: 196px;
    min-height: 32px;
    padding-right: 24px;
}
QComboBox QAbstractItemView {
    min-width: 240px;
    outline: none;
}
QLineEdit, QSpinBox, QDoubleSpinBox {
    min-width: 160px;
    min-height: 32px;
}
QTextEdit, QPlainTextEdit {
    min-height: 92px;
}
QListWidget {
    min-height: 132px;
}
QPushButton {
    min-height: 34px;
    min-width: 118px;
}
QProgressBar {
    min-height: 18px;
}
QTableWidget {
    alternate-background-color: rgba(148,163,184,0.08);
}
QHeaderView::section {
    min-height: 26px;
}
QGroupBox {
    padding: 10px 10px 8px 10px;
}
"""


APP_STYLESHEET = APP_STYLESHEET_DARK
_OVERLAY_BEGIN = "/* INAES_OVERLAY_BEGIN */"
_OVERLAY_END = "/* INAES_OVERLAY_END */"

BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "inaes_dark": {"label": "INAES Dark", "stylesheet": APP_STYLESHEET_DARK},
    "inaes_light": {"label": "INAES Light", "stylesheet": APP_STYLESHEET_LIGHT},
}


def _choose_preferred_font_family() -> str:
    db = QFontDatabase()
    available = {str(name) for name in db.families()}
    preferred = [
        "SF Pro Display",
        "SF Pro Text",
        ".SF NS Text",
        ".SF NS Display",
        "San Francisco",
        "Helvetica Neue",
        "Segoe UI",
    ]
    return next((name for name in preferred if name in available), "")


def _apply_preferred_app_font(app: QApplication, font_size: int = 13) -> None:
    chosen = _choose_preferred_font_family()
    if not chosen:
        return
    font = QFont(chosen)
    font.setPointSize(int(max(9, min(24, int(font_size)))))
    app.setFont(font)


def available_builtin_themes() -> list[tuple[str, str]]:
    return [(meta["label"], key) for key, meta in BUILTIN_THEMES.items()]


def apply_builtin_theme(
    app: QApplication,
    theme_key: str = "inaes_dark",
    *,
    font_size: int = 13,
    compact: bool = False,
    high_contrast: bool = False,
    control_thickness: int = 1,
) -> None:
    key = str(theme_key or "inaes_dark")
    if key not in BUILTIN_THEMES:
        key = "inaes_dark"
    base = BUILTIN_THEMES[key]["stylesheet"]
    extra = _compose_overlay(
        compact=compact,
        high_contrast=high_contrast,
        control_thickness=control_thickness,
    )
    app.setStyleSheet(base + extra)
    _apply_preferred_app_font(app, font_size=font_size)


def qt_themes_is_available() -> bool:
    try:
        import qt_themes as _qt_themes  # noqa: F401

        return True
    except Exception:
        return False


def available_qt_theme_names() -> list[str]:
    try:
        import qt_themes

        names = list(qt_themes.get_themes().keys())
        return sorted(str(n) for n in names if str(n).strip())
    except Exception:
        return []


def apply_qt_theme(
    app: QApplication,
    theme_name: str,
    *,
    font_size: int = 13,
    compact: bool = False,
    high_contrast: bool = False,
    control_thickness: int = 1,
) -> tuple[bool, str]:
    try:
        import qt_themes
    except Exception:
        return False, "qt-themes package is not installed."

    name = str(theme_name or "").strip()
    if not name:
        names = available_qt_theme_names()
        name = names[0] if names else "modern_dark"

    # Avoid stylesheet accumulation across repeated theme switches.
    # qt-themes should start from a clean app stylesheet each time.
    try:
        app.setStyleSheet("")
    except Exception:
        pass
    try:
        qt_themes.set_theme(name, style="fusion")
    except Exception as exc:
        return False, f"Failed applying qt-themes theme '{name}': {exc}"

    overlay = _compose_overlay(
        compact=compact,
        high_contrast=high_contrast,
        control_thickness=control_thickness,
    )
    existing = str(app.styleSheet() or "")
    if _OVERLAY_BEGIN in existing and _OVERLAY_END in existing:
        pre = existing.split(_OVERLAY_BEGIN, 1)[0]
        post = existing.split(_OVERLAY_END, 1)[1] if _OVERLAY_END in existing else ""
        existing = (pre + post).strip()
    if overlay:
        merged = f"{existing}\n{_OVERLAY_BEGIN}\n{overlay}\n{_OVERLAY_END}\n"
    else:
        merged = existing
    app.setStyleSheet(merged)
    _apply_preferred_app_font(app, font_size=font_size)
    return True, f"qt-themes '{name}' applied."


def _compose_overlay(*, compact: bool, high_contrast: bool, control_thickness: int) -> str:
    overlay = APP_STYLESHEET_GEOMETRY_PATCH
    if compact:
        overlay += APP_STYLESHEET_COMPACT_PATCH

    th = int(max(1, min(4, int(control_thickness))))
    overlay += (
        "\n"
        "QPushButton, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, "
        "QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QGroupBox { "
        f"border-width: {th}px; "
        "}\n"
    )

    if high_contrast:
        overlay += """
QWidget { color: #f8fafc; }
QGroupBox::title { color: #93c5fd; font-weight: 700; }
QPushButton { font-weight: 700; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QListWidget, QTableWidget, QGroupBox, QTabWidget::pane, QTabBar::tab {
    border-color: #e2e8f0;
}
"""
    return overlay


def apply_appearance(
    app: QApplication,
    *,
    engine: str,
    builtin_theme: str,
    qt_theme: str,
    font_size: int,
    compact: bool,
    high_contrast: bool = False,
    control_thickness: int = 1,
) -> tuple[bool, str]:
    engine_key = str(engine or "inaes").strip().lower()
    if engine_key == "qt_themes":
        ok, msg = apply_qt_theme(
            app,
            qt_theme,
            font_size=font_size,
            compact=compact,
            high_contrast=high_contrast,
            control_thickness=control_thickness,
        )
        if ok:
            return True, msg
        apply_builtin_theme(
            app,
            theme_key=builtin_theme or "inaes_dark",
            font_size=font_size,
            compact=compact,
            high_contrast=high_contrast,
            control_thickness=control_thickness,
        )
        return False, f"{msg} Fallback to built-in theme."
    apply_builtin_theme(
        app,
        theme_key=builtin_theme or "inaes_dark",
        font_size=font_size,
        compact=compact,
        high_contrast=high_contrast,
        control_thickness=control_thickness,
    )
    return True, f"Built-in theme '{builtin_theme or 'inaes_dark'}' applied."
