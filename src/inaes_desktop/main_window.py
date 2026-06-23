from __future__ import annotations

import html as html_lib
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import uuid
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QEvent, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QDoubleValidator, QIntValidator
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from inaes_core.boxplots import BoxplotConfig, available_group_columns, prepare_boxplot_points
from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df, suggest_curves_column_mapping
from inaes_core.compare_samples_fc import (
    CompareSamplesFilter,
    available_cmp_options,
    prepare_compare_samples_points,
)
from inaes_core.correlations import (
    CorrelationConfig,
    _coerce_numeric_series_relaxed,
    _corr_label,
    _is_nm_like_metric_name,
    available_correlation_options,
    fit_curve_with_ci,
    prepare_correlation_frame,
)
from inaes_core.freezing_curves import (
    FreezingCurvesFilter,
    MeanCIConfig,
    available_fc_options,
    compute_mean_ci_curves,
    prepare_freezing_curves_points,
)
from inaes_core.frozen_fraction import (
    FrozenFractionFilter,
    available_ff_options,
    prepare_frozen_fraction_points,
)
from inaes_core.kneepoint import (
    available_kp_options,
    filter_kp_points_for_sample,
    kneepoint_analysis,
)
from inaes_core.kneepoint_report import (
    build_kneepoint_report_preview,
    export_kneepoint_report_zip_from_preview,
    kp_parameters_df_from_sample_items,
    kp_build_full_report_figure,
    kp_build_single_sample_figure,
    kp_summary_row,
)
from inaes_core.metadata_nm import compute_metadata_with_nm
from inaes_core.raw_workflow import (
    MERGE_MAPPING_SKIP,
    RAW_ANALYZED_MERGE_FIELDS,
    RAW_VALI_METHOD_AXIS_META as RAW_WORKFLOW_AXIS_META,
    RAW_VALI_METHOD_HELP as RAW_WORKFLOW_METHOD_HELP,
    RAW_VALI_METHOD_OPTIONS as RAW_WORKFLOW_METHOD_OPTIONS,
    RawAnalyzeConfig,
    compute_analyzed_curves_from_raw,
    merge_analyzed_curve_tables,
    suggest_raw_column_mapping,
)
from inaes_core.io_universal import read_table_from_path
from inaes_desktop.long_task import LongTaskWorker
from inaes_desktop.export_manager import PlotExportConfig, default_export_manager
from inaes_desktop.session_store import (
    SESSION_SCHEMA_VERSION,
    load_session_state,
    save_session_state,
)
from inaes_desktop.resources import manual_pdf_path
from inaes_desktop.state import AppState, LoadedTable
from inaes_desktop.theme import (
    apply_appearance,
)

DEFAULT_NM_AXIS_LABEL = "nm (g^-1)"
RAW_VALI_METHOD_AXIS_META: dict[str, dict[str, str]] = {
    "mass_extraction_nm": {"label": "nm", "units": "g^-1"},
    "liquid_volume_K": {"label": "K", "units": "mL^-1"},
    "legacy_soil_default": {"label": "nm", "units": "g^-1"},
    "surface_area_direct": {"label": "n_s", "units": "m^-2"},
    "surface_area_bet": {"label": "n_s,BET", "units": "m^-2"},
    "cell_count": {"label": "n_cell", "units": "cell^-1"},
    "air_washoff": {"label": "N_INP_air", "units": "L^-1"},
    "air_drop_on": {"label": "N_INP_air", "units": "L^-1"},
    "custom_dose": {"label": "nX", "units": "X^-1"},
    # RAW workflow canonical method ids (legacy aliases above kept for compatibility).
    **RAW_WORKFLOW_AXIS_META,
}

NM_METHOD_TO_WORKFLOW_KEY: dict[str, str] = {
    "mass_extraction_nm": "mass_extraction_nm",
    "liquid_volume_K": "liquid_volume",
    "legacy_soil_default": "mass_extraction_nm",
    "surface_area_direct": "surface_area_direct",
    "surface_area_bet": "surface_area_bet_from_mass",
    "cell_count": "cell_concentration",
    "air_washoff": "air_washoff",
    "air_drop_on": "air_drop_on",
    "custom_dose": "custom_dose",
}

NM_METHOD_EXTRA_NOTE: dict[str, str] = {
    "auto": "Auto mode infers units from uploaded analyzed table metadata, when available.",
    "legacy_soil_default": "Legacy default alias of standard mass-extraction nM (soil workflow).",
}

RAW_METHOD_FORMULA_HTML: dict[str, str] = {
    "mass_concentration_nm": "<i>n</i><sub>M</sub>(T) = - (1 / <i>V</i>) · ln(1 − FF) · (<i>d</i> / <i>c</i><sub>m</sub>)",
    "liquid_volume": "<i>K</i>(T) = - (1 / <i>V</i>) · ln(1 − FF)",
    "mass_extraction_nm": "<i>n</i><sub>M</sub>(T) = -ln(1 − FF) / (30 × 10<sup>-3</sup>) × (Water_volume / Soil_mass) × Dilution.factor",
    "surface_area_direct": "<i>n</i><sub>s</sub>(T) = -ln(1 − FF) / <i>A</i><sub>droplet</sub>",
    "surface_area_bet_from_mass": "<i>n</i><sub>s,BET</sub>(T) = <i>n</i><sub>M</sub>(T) / θ",
    "cell_concentration": "<i>n</i><sub>cell</sub>(T) = - (1 / <i>V</i><sub>drop</sub>) · ln(1 − FF) · (<i>d</i> / <i>c</i><sub>cells</sub>)",
    "air_washoff": "<i>N</i><sub>INP,air</sub>(T) = -ln(1 − FF) · (<i>V</i><sub>wash</sub> / (<i>V</i><sub>drop</sub> · <i>x</i> · <i>V</i><sub>s</sub>))",
    "air_drop_on": "<i>N</i><sub>INP,air</sub>(T) = -ln(1 − FF) · (<i>A</i><sub>filter</sub> / (α · <i>V</i><sub>s</sub>))",
    "custom_dose": "<i>n</i><sub>X</sub>(T) = -ln(1 − FF) / <i>X</i>",
}

RAW_REQUIRED_PARAM_HTML: dict[str, str] = {
    "mass_conc": "<i>c</i><sub>m</sub>",
    "wash_volume": "<i>V</i><sub>wash</sub>",
    "sample_mass": "<i>m</i><sub>sample</sub>",
    "cell_conc": "<i>c</i><sub>cells</sub>",
    "area_per_drop": "<i>A</i><sub>droplet</sub>",
    "bet_area": "θ (BET)",
    "air_filter_frac": "<i>x</i>",
    "air_volume_l": "<i>V</i><sub>s</sub>",
    "filter_area": "<i>A</i><sub>filter</sub>",
    "drop_area": "α",
    "custom_dose": "<i>X</i>",
}

RUNTIME_PLOTLY_PROFILE = "balanced"
RUNTIME_BUILTIN_THEME = "inaes_dark"
RUNTIME_LOG_LEVEL = "normal"
RUNTIME_SAVE_LOG_FILE = False
RUNTIME_LOG_FILE_PATH = ""


def _should_emit_runtime_line(line: str) -> bool:
    lvl = str(RUNTIME_LOG_LEVEL or "normal").strip().lower()
    txt = str(line or "").strip().lower()
    if lvl == "verbose":
        return True
    if lvl == "quiet":
        keys = ("error", "warning", "failed", "exception", "traceback", "critical")
        return any(k in txt for k in keys)
    return bool(txt)


def _set_runtime_plotly_profile(profile: str) -> None:
    global RUNTIME_PLOTLY_PROFILE
    p = str(profile or "balanced").strip().lower()
    if p not in {"quality", "balanced", "speed"}:
        p = "balanced"
    RUNTIME_PLOTLY_PROFILE = p


def _set_runtime_builtin_theme(theme_key: str) -> None:
    global RUNTIME_BUILTIN_THEME
    key = str(theme_key or "inaes_dark").strip().lower()
    if key not in {"inaes_dark", "inaes_light"}:
        key = "inaes_dark"
    RUNTIME_BUILTIN_THEME = key


def _coerce_nm_axis_label(axis_label: Any) -> str:
    s = str(axis_label or "").strip()
    return s if s else DEFAULT_NM_AXIS_LABEL


def _split_axis_label_units(axis_label: Any) -> tuple[str, str]:
    s = str(axis_label or "").strip()
    if not s:
        return "nm", ""
    m = re.match(r"^(.+?)\s*\(([^()]*)\)\s*$", s)
    if not m:
        return s, ""
    metric_txt = str(m.group(1) or "").strip() or "nm"
    units_txt = str(m.group(2) or "").strip()
    return metric_txt, units_txt


def _axis_label_with_same_units(metric: Any, base_axis_label: Any) -> str:
    metric_txt = str(metric or "").strip() or "nm"
    _, units_txt = _split_axis_label_units(base_axis_label)
    return f"{metric_txt} ({units_txt})" if units_txt else metric_txt


def _format_metric_units_label(metric: Any, units: Any) -> str:
    metric_txt = str(metric or "").strip()
    units_txt = str(units or "").strip()
    if not metric_txt and not units_txt:
        return DEFAULT_NM_AXIS_LABEL
    if not metric_txt:
        metric_txt = "nm"
    return f"{metric_txt} ({units_txt})" if units_txt else metric_txt


def _nm_axis_label_from_method(method: Any) -> str:
    m = str(method or "mass_extraction_nm").strip()
    meta = RAW_VALI_METHOD_AXIS_META.get(m)
    if not isinstance(meta, dict):
        return DEFAULT_NM_AXIS_LABEL
    return _format_metric_units_label(meta.get("label"), meta.get("units"))


def _nm_method_detail_text(method: Any) -> str:
    m = str(method or "auto").strip()
    key = NM_METHOD_TO_WORKFLOW_KEY.get(m, m)
    formula_txt = _method_formula_html(key)
    axis_txt = _format_math_exponents(_nm_axis_label_from_method(key))
    extra_txt = str(NM_METHOD_EXTRA_NOTE.get(m, "")).strip()

    parts: list[str] = []
    parts.append(f"<b>Units:</b> {axis_txt}")
    if formula_txt:
        parts.append(f"<b>Formula:</b> {formula_txt}")
    if extra_txt:
        parts.append(f"<b>Note:</b> {html_lib.escape(extra_txt)}")
    return "<br>".join(parts)


def _extract_nm_axis_label_from_df(df: pd.DataFrame) -> str | None:
    if not isinstance(df, pd.DataFrame) or len(df.columns) == 0:
        return None
    cols = {str(c): c for c in df.columns}
    direct_candidates = ["nm_axis_label", "NM_axis_label", "axis_label_nm"]
    for c in direct_candidates:
        if c in cols:
            s = str(df[c].dropna().astype(str).iloc[0]).strip() if len(df[c].dropna()) else ""
            if s:
                return s
    label_candidates = ["normalization_label", "Normalization.label", "nm_label"]
    units_candidates = ["normalization_units", "Normalization.units", "nm_units"]
    lbl = ""
    unt = ""
    for c in label_candidates:
        if c in cols and len(df[c].dropna()) > 0:
            lbl = str(df[c].dropna().astype(str).iloc[0]).strip()
            break
    for c in units_candidates:
        if c in cols and len(df[c].dropna()) > 0:
            unt = str(df[c].dropna().astype(str).iloc[0]).strip()
            break
    if lbl or unt:
        return _format_metric_units_label(lbl or "nm", unt)
    return None


def _format_math_exponents(text: Any) -> str:
    s = str(text or "")
    if not s:
        return s
    s = re.sub(r"([A-Za-z0-9_,]+)\^\{([+-]?\d+)\}", r"\1<sup>\2</sup>", s)
    s = re.sub(r"([A-Za-z0-9_,]+)\^([+-]?\d+)", r"\1<sup>\2</sup>", s)
    return s


def _method_formula_html(method_key: Any) -> str:
    key = str(method_key or "").strip()
    pretty = RAW_METHOD_FORMULA_HTML.get(key, "")
    if pretty:
        return pretty
    raw = str(RAW_WORKFLOW_METHOD_HELP.get(key, "")).strip()
    if not raw:
        return ""
    return _format_math_exponents(html_lib.escape(raw)).replace("*", "·")


def _required_param_symbols_html(required_params: list[str]) -> str:
    if not required_params:
        return "None"
    out: list[str] = []
    for p in required_params:
        sym = RAW_REQUIRED_PARAM_HTML.get(str(p), "")
        if sym:
            out.append(sym)
        else:
            out.append(html_lib.escape(str(p)))
    return ", ".join(out)


def _render_table(table: QTableWidget, df: pd.DataFrame, max_rows: int = 60) -> None:
    # Defensive preview renderer:
    # avoids expensive full autosizing on very wide metadata tables (a known Qt crash trigger on some systems).
    MAX_PREVIEW_COLS = 220
    rows = 0
    cols = 0
    truncated_cols = 0
    safe_df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    display_cols = [str(c) for c in safe_df.columns[:MAX_PREVIEW_COLS]]
    truncated_cols = max(0, int(len(safe_df.columns) - len(display_cols)))
    rows = int(min(max(0, int(max_rows)), len(safe_df)))
    cols = int(len(display_cols) + (1 if truncated_cols > 0 else 0))

    table.setUpdatesEnabled(False)
    table.blockSignals(True)
    try:
        table.clear()
        table.setRowCount(rows)
        table.setColumnCount(cols)
        headers = display_cols[:]
        if truncated_cols > 0:
            headers.append(f"... (+{truncated_cols} cols)")
        table.setHorizontalHeaderLabels(headers)

        for r in range(rows):
            for c, _name in enumerate(display_cols):
                value = safe_df.iat[r, c]
                txt = "" if value is None else str(value)
                item = QTableWidgetItem(txt)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, c, item)
            if truncated_cols > 0:
                item = QTableWidgetItem("(truncated)")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, cols - 1, item)

        hh = table.horizontalHeader()
        if hh is not None:
            hh.setStretchLastSection(False)
            if cols <= 28:
                hh.setSectionResizeMode(QHeaderView.ResizeToContents)
                table.resizeColumnsToContents()
            else:
                hh.setSectionResizeMode(QHeaderView.Interactive)
                hh.setDefaultSectionSize(140)
                for c in range(min(cols, 20)):
                    table.resizeColumnToContents(c)
                    w = table.columnWidth(c)
                    table.setColumnWidth(c, max(80, min(360, w)))
        vh = table.verticalHeader()
        if vh is not None:
            vh.setDefaultSectionSize(max(22, int(vh.defaultSectionSize() or 22)))
    finally:
        table.blockSignals(False)
        table.setUpdatesEnabled(True)


def _default_palette() -> list[QColor]:
    return [
        QColor("#5b8ff9"),
        QColor("#5ad8a6"),
        QColor("#5d7092"),
        QColor("#f6bd16"),
        QColor("#e8684a"),
        QColor("#6dc8ec"),
        QColor("#9270ca"),
        QColor("#ff9d4d"),
        QColor("#269a99"),
        QColor("#ff99c3"),
    ]


def _palette_hex() -> list[str]:
    return [c.name() for c in _default_palette()]


def _palette_hex_named(name: Any) -> list[str]:
    key = str(name or "default").strip().lower()
    if key == "viridis":
        return list(px.colors.sequential.Viridis)
    if key == "magma":
        return list(px.colors.sequential.Magma)
    if key == "plasma":
        return list(px.colors.sequential.Plasma)
    if key == "dark2":
        return list(px.colors.qualitative.Dark2)
    if key == "set1":
        return list(px.colors.qualitative.Set1)
    if key == "set2":
        return list(px.colors.qualitative.Set2)
    if key == "set3":
        return list(px.colors.qualitative.Set3)
    if key == "turbo":
        return list(px.colors.sequential.Turbo)
    if key == "cividis":
        return list(px.colors.sequential.Cividis)
    return _palette_hex()


def _init_palette_combo(combo: QComboBox, *, include_default: bool = True, default_value: str | None = None) -> None:
    combo.clear()
    opts: list[tuple[str, str]] = []
    if include_default:
        opts.append(("Default", "default"))
    opts.extend(
        [
            ("Viridis", "viridis"),
            ("Magma", "magma"),
            ("Plasma", "plasma"),
            ("Dark2", "dark2"),
            ("Set1", "set1"),
            ("Set2", "set2"),
            ("Set3", "set3"),
            ("Turbo", "turbo"),
            ("Cividis", "cividis"),
        ]
    )
    for lbl, val in opts:
        combo.addItem(lbl, val)
    if default_value:
        idx = combo.findData(str(default_value))
        if idx >= 0:
            combo.setCurrentIndex(idx)


def _rgba_from_color(color: Any, alpha: float = 1.0) -> str:
    a = max(0.0, min(1.0, float(alpha)))
    s = str(color or "").strip()
    if s.startswith("#"):
        hx = s[1:]
        if len(hx) == 3:
            hx = "".join(ch * 2 for ch in hx)
        if len(hx) == 6:
            try:
                r = int(hx[0:2], 16)
                g = int(hx[2:4], 16)
                b = int(hx[4:6], 16)
                return f"rgba({r},{g},{b},{a})"
            except Exception:
                pass
    m = re.match(
        r"^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*([0-9.]+))?\s*\)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        try:
            r = int(float(m.group(1)))
            g = int(float(m.group(2)))
            b = int(float(m.group(3)))
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            return f"rgba({r},{g},{b},{a})"
        except Exception:
            pass
    qc = QColor(s)
    if qc.isValid():
        return f"rgba({qc.red()},{qc.green()},{qc.blue()},{a})"
    return f"rgba(91,143,249,{a})"


def _plotly_layout_base(title: str | None) -> dict:
    title_txt = str(title or "").strip()
    layout = dict(
        template="plotly_dark",
        margin=dict(l=88, r=28, t=(78 if title_txt else 44), b=84),
        plot_bgcolor="#0b1220",
        paper_bgcolor="#111827",
        font=dict(color="#e5e7eb"),
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0.0),
    )
    if title_txt:
        layout["title"] = {"text": title_txt, "x": 0.02, "y": 0.985, "yanchor": "top"}
    return layout


def _compose_optional_plot_title(main_title: Any, subtitle: Any = "") -> str | None:
    title_txt = str(main_title or "").strip()
    if not title_txt:
        return None
    subtitle_txt = str(subtitle or "").strip()
    return f"{title_txt}<br><sup>{subtitle_txt}</sup>" if subtitle_txt else title_txt


def _style_axes(
    fig: go.Figure,
    *,
    x_title: str,
    y_title: str,
    y_type: str = "linear",
    y_range: list[float] | None = None,
) -> None:
    common_x = dict(
        title_text=x_title,
        showgrid=True,
        gridcolor="rgba(148,163,184,0.18)",
        zeroline=False,
        showline=True,
        linecolor="#f8fafc",
        linewidth=1.8,
        mirror=True,
        ticks="outside",
        ticklen=8,
        tickcolor="#f8fafc",
        showticklabels=True,
        automargin=True,
        tickfont=dict(color="#f8fafc", size=12),
        title_font=dict(color="#f8fafc", size=13),
        title_standoff=12,
    )
    common_y = dict(
        title_text=_format_math_exponents(_coerce_nm_axis_label(y_title)),
        showgrid=True,
        gridcolor="rgba(148,163,184,0.18)",
        zeroline=False,
        showline=True,
        linecolor="#f8fafc",
        linewidth=1.8,
        mirror=True,
        ticks="outside",
        ticklen=8,
        tickcolor="#f8fafc",
        showticklabels=True,
        automargin=True,
        tickfont=dict(color="#f8fafc", size=12),
        title_font=dict(color="#f8fafc", size=13),
        title_standoff=12,
    )
    fig.update_xaxes(**common_x)
    if y_type == "log":
        common_y["type"] = "log"
        common_y["exponentformat"] = "power"
        common_y["showexponent"] = "all"
    else:
        common_y["type"] = "linear"
    if y_range is not None:
        common_y["range"] = y_range
    fig.update_yaxes(**common_y)


def _plot_theme_variant(bg_mode: str | None) -> str:
    mode = str(bg_mode or "white").strip().lower()
    if mode == "theme":
        return "theme_light" if str(RUNTIME_BUILTIN_THEME or "inaes_dark") == "inaes_light" else "theme_dark"
    return mode


def _contrast_text_color(background: Any, *, light: str = "#e5e7eb", dark: str = "#111827") -> str:
    qc = QColor(str(background or ""))
    if not qc.isValid():
        return dark
    luminance = (0.299 * float(qc.red()) + 0.587 * float(qc.green()) + 0.114 * float(qc.blue())) / 255.0
    return light if luminance < 0.52 else dark


def _axis_tick_color(bg_mode: str | None) -> str:
    palettes = {
        "white": "#ffffff",
        "soft_gray": "#f3f4f6",
        "ivory": "#fffdf5",
        "pale_blue": "#eef4ff",
        "theme_dark": "#0b1220",
        "theme_light": "#f8fafc",
        "night_navy": "#0b1020",
    }
    bg = palettes.get(_plot_theme_variant(bg_mode), "#ffffff")
    return _contrast_text_color(bg, light="#e2e8f0", dark="#111827")


def _apply_plot_axis_contrast(fig: go.Figure) -> None:
    layout = getattr(fig, "layout", None)
    plot_bg = str(getattr(layout, "plot_bgcolor", "") or getattr(layout, "paper_bgcolor", "") or "#ffffff")
    text_color = _contrast_text_color(plot_bg, light="#e5e7eb", dark="#111827")
    tick_color = _contrast_text_color(plot_bg, light="#e2e8f0", dark="#111827")
    axis_line_color = _rgba_from_color(text_color, 0.72)

    fig.update_layout(
        font=dict(color=text_color),
        title_font=dict(color=text_color),
        legend=dict(
            font=dict(color=text_color),
            title=dict(font=dict(color=text_color)),
        ),
    )
    fig.update_xaxes(
        tickfont=dict(color=text_color),
        title_font=dict(color=text_color),
        tickcolor=tick_color,
        linecolor=axis_line_color,
    )
    fig.update_yaxes(
        tickfont=dict(color=text_color),
        title_font=dict(color=text_color),
        tickcolor=tick_color,
        linecolor=axis_line_color,
    )


def _apply_plot_background(fig: go.Figure, bg_mode: str | None) -> None:
    mode = _plot_theme_variant(bg_mode)
    palettes = {
        "white": {
            "paper_bg": "#ffffff",
            "plot_bg": "#ffffff",
            "font_color": "#0f172a",
            "grid_color": "#d1d5db",
            "legend_bg": "rgba(255,255,255,0.88)",
        },
        "soft_gray": {
            "paper_bg": "#f3f4f6",
            "plot_bg": "#f3f4f6",
            "font_color": "#111827",
            "grid_color": "#cbd5e1",
            "legend_bg": "rgba(243,244,246,0.88)",
        },
        "ivory": {
            "paper_bg": "#fffdf5",
            "plot_bg": "#fffdf5",
            "font_color": "#1f2937",
            "grid_color": "#d6d3c7",
            "legend_bg": "rgba(255,253,245,0.88)",
        },
        "pale_blue": {
            "paper_bg": "#eef4ff",
            "plot_bg": "#eef4ff",
            "font_color": "#0f172a",
            "grid_color": "#bfdbfe",
            "legend_bg": "rgba(238,244,255,0.88)",
        },
        "theme_dark": {
            "paper_bg": "#111827",
            "plot_bg": "#0b1220",
            "font_color": "#e5e7eb",
            "grid_color": "rgba(148,163,184,0.18)",
            "legend_bg": "rgba(17,24,39,0.78)",
        },
        "theme_light": {
            "paper_bg": "#f8fafc",
            "plot_bg": "#f8fafc",
            "font_color": "#0f172a",
            "grid_color": "#cbd5e1",
            "legend_bg": "rgba(248,250,252,0.90)",
        },
        "night_navy": {
            "paper_bg": "#0b1020",
            "plot_bg": "#0b1020",
            "font_color": "#e2e8f0",
            "grid_color": "#273449",
            "legend_bg": "rgba(11,16,32,0.82)",
        },
    }
    cfg = palettes.get(mode, palettes["theme_dark"])
    grid_color = cfg["grid_color"]
    tick_color = _axis_tick_color(mode)

    fig.update_layout(
        paper_bgcolor=cfg["paper_bg"],
        plot_bgcolor=cfg["plot_bg"],
        font=dict(color=cfg["font_color"]),
        legend=dict(bgcolor=cfg["legend_bg"], bordercolor=grid_color, borderwidth=1),
    )
    fig.update_xaxes(
        gridcolor=grid_color,
        zerolinecolor=grid_color,
        ticks="outside",
        ticklen=7,
        tickwidth=1.3,
        tickcolor=tick_color,
        showline=True,
        mirror=True,
    )
    fig.update_yaxes(
        gridcolor=grid_color,
        zerolinecolor=grid_color,
        tickcolor=tick_color,
    )
    _apply_plot_axis_contrast(fig)


def _apply_y_tick_style(fig: go.Figure, tick_style: str | None, *, bg_mode: str | None, y_is_log: bool) -> None:
    tick_selected = str(tick_style or "auto")
    tick_mode = tick_selected
    if tick_mode == "auto":
        # Keep auto visually stable across panels/datasets.
        tick_mode = "standard"

    tick_color = _axis_tick_color(bg_mode)
    if tick_mode == "minimal":
        fig.update_yaxes(
            showline=True,
            mirror=True,
            ticks="outside",
            ticklen=4,
            tickwidth=1,
            tickcolor=tick_color,
            minor=dict(ticks="", showgrid=False),
        )
    elif tick_mode == "scientific":
        minor_dtick = "D1" if y_is_log else None
        fig.update_yaxes(
            showline=True,
            linewidth=1.6,
            mirror="allticks",
            ticks="outside",
            ticklen=10,
            tickwidth=2,
            tickcolor=tick_color,
            minor=dict(
                ticks="outside",
                ticklen=6,
                tickwidth=1.4,
                tickcolor=tick_color,
                showgrid=False,
                dtick=minor_dtick,
            ),
        )
    else:
        fig.update_yaxes(
            showline=True,
            mirror=True,
            ticks="outside",
            ticklen=7,
            tickwidth=1.4,
            tickcolor=tick_color,
            minor=dict(ticks="", showgrid=False),
        )


def _set_plotly_html(view: QWebEngineView, fig: go.Figure) -> None:
    _apply_plot_axis_contrast(fig)
    profile = str(RUNTIME_PLOTLY_PROFILE or "balanced").strip().lower()
    if profile == "quality":
        plot_cfg = {
            "responsive": True,
            "displaylogo": False,
            "displayModeBar": True,
            "scrollZoom": True,
            "plotGlPixelRatio": 2,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        }
    elif profile == "speed":
        plot_cfg = {
            "responsive": True,
            "displaylogo": False,
            "displayModeBar": False,
            "scrollZoom": False,
            "plotGlPixelRatio": 1,
            "modeBarButtonsToRemove": ["lasso2d", "select2d", "zoomIn2d", "zoomOut2d"],
        }
    else:
        plot_cfg = {
            "responsive": True,
            "displaylogo": False,
            "displayModeBar": True,
            "scrollZoom": True,
            "plotGlPixelRatio": 1.5,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        }
    html = fig.to_html(
        include_plotlyjs=True,
        full_html=True,
        config=plot_cfg,
    )
    bg_color = str(getattr(getattr(fig, "layout", None), "paper_bgcolor", "") or "#111827")
    # Avoid white browser body around the figure (visible when view is taller than figure).
    inject = (
        "<style>"
        f"html,body{{margin:0;padding:0;background:{bg_color};}}"
        ".plot-container,.main-svg{background:transparent !important;}"
        "</style>"
    )
    if "<head>" in html:
        html = html.replace("<head>", "<head>" + inject, 1)
    else:
        html = inject + html
    # QWebEngineView.setHtml can fail silently with large payloads (common for dense curves),
    # so we always write to a temp file and load by file URL.
    cache_dir = Path(tempfile.gettempdir()) / "inaes_desktop_plot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"plot_{uuid.uuid4().hex}.html"
    out.write_text(html, encoding="utf-8")
    view.load(QUrl.fromLocalFile(str(out)))

    # Best-effort cache pruning to avoid unbounded temp growth.
    try:
        files = sorted(cache_dir.glob("plot_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[40:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


class PlotExportBox(QGroupBox):
    def __init__(self, title: str, *, default_stem: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._default_stem = str(default_stem or "plot")
        lay = QFormLayout(self)

        self.cb_fmt = QComboBox()
        self.cb_fmt.addItems(["svg", "pdf", "png"])
        lay.addRow("Format", self.cb_fmt)

        self.sp_width = SliderNumberInput(min_value=400, max_value=8000, value=1800, decimals=0, step=50)
        lay.addRow("Width (px)", self.sp_width)

        self.sp_height = SliderNumberInput(min_value=300, max_value=8000, value=1200, decimals=0, step=50)
        lay.addRow("Height (px)", self.sp_height)

        self.sp_scale = SliderNumberInput(min_value=0.5, max_value=5.0, value=2.0, decimals=1, step=0.5)
        lay.addRow("Scale", self.sp_scale)

        self.ed_stem = QLineEdit(self._default_stem)
        lay.addRow("Filename", self.ed_stem)

        self.btn_export = QPushButton("Export plot")
        lay.addRow(self.btn_export)

    def config(self) -> PlotExportConfig:
        stem = str(self.ed_stem.text().strip() or self._default_stem)
        return PlotExportConfig(
            fmt=str(self.cb_fmt.currentText() or "svg").strip().lower(),
            width=int(self.sp_width.value()),
            height=int(self.sp_height.value()),
            scale=float(self.sp_scale.value()),
            stem=stem,
        )


def _save_plotly_figure_local(parent: QWidget, fig: go.Figure, cfg: PlotExportConfig) -> Path | None:
    return default_export_manager.save_plotly_figure(parent, fig, cfg)


def _safe_axis_range(min_v: float, max_v: float, *, log_axis: bool) -> tuple[float, float] | None:
    if not (pd.notna(min_v) and pd.notna(max_v)):
        return None
    a = float(min_v)
    b = float(max_v)
    if not (pd.notna(a) and pd.notna(b)):
        return None
    if b < a:
        a, b = b, a

    if log_axis:
        if b <= 0:
            return None
        if a <= 0:
            a = max(b * 1e-6, 1e-12)
        if not np.isfinite(a) or not np.isfinite(b) or a <= 0 or b <= 0:
            return None
        if b <= a:
            b = a * 10.0
        return a, b

    if not np.isfinite(a) or not np.isfinite(b):
        return None
    if b <= a:
        pad = max(abs(a) * 0.05, 1e-6)
        a -= pad
        b += pad
    return a, b


_METADATA_NM_LOCK = threading.RLock()


def _compute_metadata_with_nm_serialized(
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    *,
    span: float = 0.1,
    min_points: int = 10,
) -> tuple[pd.DataFrame, str]:
    # scipy/pandas internals used by metadata_with_nm can segfault when
    # executed concurrently across worker threads on some systems.
    # Serialize these calls for stability.
    with _METADATA_NM_LOCK:
        return compute_metadata_with_nm(curves_df, metadata_df, span=span, min_points=min_points)


def _compute_boxplot_payload(
    *,
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    cfg_dict: dict[str, Any],
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Boxplot run cancelled by user.")

    progress(3, "Computing metadata_with_nm...")
    _check_cancel()
    nm_df, nm_status = _compute_metadata_with_nm_serialized(curves_df, metadata_df, span=0.1, min_points=10)
    _check_cancel()
    progress(52, "Preparing boxplot points...")
    cfg = BoxplotConfig(**cfg_dict)
    d, status, ycol, group_plot_col, x_levels = prepare_boxplot_points(nm_df, cfg)
    _check_cancel()
    progress(95, "Finalizing...")
    return {
        "cfg_dict": dict(cfg_dict),
        "nm_status": str(nm_status),
        "status": str(status),
        "d": d,
        "ycol": str(ycol),
        "group_plot_col": str(group_plot_col),
        "x_levels": list(x_levels),
    }


def _compute_boxplot_options_payload(
    *,
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Boxplot options refresh cancelled.")

    # Stability-first: option lists are derived from metadata columns only.
    # metadata_with_nm remains computed at "Run" time.
    progress(3, "Reading metadata columns...")
    _check_cancel()
    meta_df = metadata_df.copy()
    meta_df.columns = [str(c) for c in meta_df.columns]
    progress(75, "Computing group-by options...")
    groups = available_group_columns(meta_df)
    has_sample = "Sample" in meta_df.columns
    nm_status = "group options derived from mapped metadata columns"
    mode = "metadata_columns"
    _check_cancel()
    progress(95, "Finalizing...")
    return {
        "groups": list(groups),
        "has_sample": bool(has_sample),
        "nm_status": str(nm_status),
        "mode": mode,
    }


def _compute_correlation_payload(
    *,
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    cfg_dict: dict[str, Any],
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Correlation run cancelled by user.")

    progress(3, "Computing metadata_with_nm...")
    _check_cancel()
    nm_df, nm_status = _compute_metadata_with_nm_serialized(curves_df, metadata_df, span=0.1, min_points=10)
    _check_cancel()
    progress(52, "Preparing correlation frame...")
    cfg = CorrelationConfig(**cfg_dict)
    d, status = prepare_correlation_frame(nm_df, cfg)
    _check_cancel()
    progress(95, "Finalizing...")
    return {
        "cfg_dict": dict(cfg_dict),
        "nm_status": str(nm_status),
        "status": str(status),
        "d": d,
    }


def _compute_correlation_options_payload(
    *,
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Correlation options refresh cancelled.")

    # Stability-first: options are derived from metadata columns only.
    # metadata_with_nm remains computed at "Run" time.
    progress(3, "Reading metadata columns...")
    _check_cancel()
    meta_df = metadata_df.copy()
    meta_df.columns = [str(c) for c in meta_df.columns]
    progress(75, "Computing variable/location options...")
    opts = available_correlation_options(meta_df)
    y_opts = [str(v) for v in list(opts.get("y", []))]
    # Keep core selectors visible even when metadata lacks precomputed nM cols.
    for y_core in ["nM10", "nM15"]:
        if y_core not in y_opts:
            y_opts.insert(0, y_core)
    # Deduplicate while preserving order.
    opts["y"] = list(dict.fromkeys(y_opts))
    nm_status = "options derived from mapped metadata columns"
    mode = "metadata_columns"
    _check_cancel()
    progress(95, "Finalizing...")
    return {"opts": dict(opts), "nm_status": str(nm_status), "mode": mode}


def _compute_freezing_curves_payload(
    *,
    curves_df: pd.DataFrame,
    filter_dict: dict[str, Any],
    mean_cfg_dict: dict[str, Any],
    nm_axis_label: Any,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Freezing Curves run cancelled by user.")

    progress(3, "Preparing filters...")
    _check_cancel()
    flt = FreezingCurvesFilter(**dict(filter_dict))
    progress(28, "Filtering curves...")
    points, status = prepare_freezing_curves_points(curves_df, flt)
    _check_cancel()
    if len(points) == 0:
        return {
            "points": points,
            "status": str(status),
            "mean_df": pd.DataFrame(),
            "mean_status": "No points after filtering.",
            "y_title": _coerce_nm_axis_label(nm_axis_label),
        }
    progress(56, "Computing mean ± CI...")
    mean_cfg = MeanCIConfig(**dict(mean_cfg_dict))
    mean_df, mean_status = compute_mean_ci_curves(points, mean_cfg)
    _check_cancel()
    progress(92, "Finalizing...")
    return {
        "points": points,
        "status": str(status),
        "mean_df": mean_df,
        "mean_status": str(mean_status),
        "y_title": _coerce_nm_axis_label(nm_axis_label),
    }


def _compute_compare_samples_payload(
    *,
    curves_df: pd.DataFrame,
    filter_dict: dict[str, Any],
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Compare Samples run cancelled by user.")

    progress(3, "Preparing filters...")
    _check_cancel()
    flt = CompareSamplesFilter(**dict(filter_dict))
    progress(35, "Filtering compare points...")
    points, status, color_by = prepare_compare_samples_points(curves_df, flt)
    _check_cancel()
    progress(92, "Finalizing...")
    return {
        "points": points,
        "status": str(status),
        "color_by": str(color_by),
    }


def _compute_frozen_fraction_payload(
    *,
    curves_df: pd.DataFrame,
    filter_dict: dict[str, Any],
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Frozen Fraction run cancelled by user.")

    progress(3, "Preparing filters...")
    _check_cancel()
    flt = FrozenFractionFilter(**dict(filter_dict))
    progress(35, "Filtering FF points...")
    points, status = prepare_frozen_fraction_points(curves_df, flt)
    _check_cancel()
    progress(92, "Finalizing...")
    return {
        "points": points,
        "status": str(status),
    }


def _compute_kneepoint_payload(
    *,
    curves_df: pd.DataFrame,
    sample: str,
    size: str,
    dilutions: list[str],
    spar: float,
    n_breaks: int,
    flat_quantile: float,
    rise_quantile: float,
    segment_selection_mode: str = "legacy",
    segment_min_internal_breaks: int = 2,
    segment_max_internal_breaks: int = 14,
    temp_min: float | None,
    temp_max: float | None,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Kneepoint run cancelled by user.")

    progress(3, "Running kneepoint analysis...")
    _check_cancel()
    res = kneepoint_analysis(
        curves_df,
        sample=sample,
        size=size,
        dilutions=dilutions,
        spar=float(spar),
        n_breaks=int(n_breaks),
        flat_quantile=float(flat_quantile),
        rise_quantile=float(rise_quantile),
        segment_selection_mode=str(segment_selection_mode or "legacy"),
        segment_min_internal_breaks=int(segment_min_internal_breaks),
        segment_max_internal_breaks=int(segment_max_internal_breaks),
        temp_min=temp_min,
        temp_max=temp_max,
        boot_R=200,
        cv_k=5,
    )
    _check_cancel()
    progress(65, "Preparing filtered points...")
    points = filter_kp_points_for_sample(
        curves_df,
        sample=sample,
        size=size,
        dilutions=dilutions,
        temp_min=temp_min,
        temp_max=temp_max,
    )
    _check_cancel()
    progress(92, "Finalizing...")
    return {
        "sample": str(sample),
        "size": str(size),
        "dilutions": list(dilutions),
        "points": points,
        "res": res,
    }


def _compute_standardize_curves_payload(
    *,
    raw_df: pd.DataFrame,
    cfg: CurvesMappingConfig,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("Curves standardization cancelled by user.")

    progress(3, "Validating mapping...")
    _check_cancel()
    progress(25, "Standardizing curves...")
    out, warnings, resolved = standardize_curves_df(raw_df, cfg)
    _check_cancel()
    progress(92, "Finalizing...")
    return {
        "out": out,
        "warnings": list(warnings),
        "resolved": dict(resolved),
    }


def _compute_raw_analyze_payload(
    *,
    raw_df: pd.DataFrame,
    cfg: RawAnalyzeConfig,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("RAW analysis cancelled by user.")

    progress(3, "Validating RAW input...")
    _check_cancel()
    progress(25, "Standardizing RAW columns and flags...")
    _check_cancel()
    out_df, status = compute_analyzed_curves_from_raw(raw_df, cfg)
    _check_cancel()
    progress(90, "Finalizing analyzed table...")
    axis_label = _extract_nm_axis_label_from_df(out_df) or _nm_axis_label_from_method(cfg.method)
    return {"df": out_df, "status": str(status), "axis_label": str(axis_label or "").strip()}


def _compute_raw_merge_payload(
    *,
    prev_df: pd.DataFrame,
    new_df: pd.DataFrame,
    raw_to_prev_map: dict[str, Any] | None = None,
    progress_callback: Any | None = None,
    cancel_requested: Any | None = None,
) -> dict[str, Any]:
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)

    def _check_cancel() -> None:
        if bool(is_cancelled()):
            raise RuntimeError("RAW merge cancelled by user.")

    progress(3, "Validating merge schema...")
    _check_cancel()
    progress(25, "Applying RAW->previous mapping...")
    _check_cancel()
    merged, status = merge_analyzed_curve_tables(
        prev_df,
        new_df,
        raw_to_prev_map=raw_to_prev_map,
    )
    _check_cancel()
    progress(90, "Finalizing merged table...")
    return {"df": merged, "status": str(status)}


RAW_METHOD_REQUIRED_PARAMS: dict[str, set[str]] = {
    "mass_concentration_nm": {"mass_conc"},
    "liquid_volume": set(),
    "mass_extraction_nm": {"wash_volume", "sample_mass"},
    "surface_area_direct": {"area_per_drop"},
    "surface_area_bet_from_mass": {"mass_conc", "bet_area"},
    "cell_concentration": {"cell_conc"},
    "air_washoff": {"wash_volume", "air_filter_frac", "air_volume_l"},
    "air_drop_on": {"air_volume_l", "filter_area", "drop_area"},
    "custom_dose": {"custom_dose"},
}


def _wrap_scroll(widget: QWidget, *, horizontal: bool = False) -> QScrollArea:
    # Layout sizing policy inspired by Qt docs / common PySide layout practice:
    # keep left control panels width driven by their content instead of over-stretching.
    lay = widget.layout()
    if lay is not None:
        lay.setSizeConstraint(QLayout.SetMinAndMaxSize)
    sc = QScrollArea()
    sc.setWidget(widget)
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if horizontal else Qt.ScrollBarAlwaysOff)
    sc.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    return sc


def _build_vertical_scroll_stack(
    panels: list[QWidget],
    *,
    min_width: int = 760,
    spacing: int = 10,
    add_stretch: bool = True,
) -> QScrollArea:
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(max(4, int(spacing)))
    for p in panels:
        if p is not None:
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            lay.addWidget(p)
    if add_stretch:
        lay.addStretch(1)
    sc = _wrap_scroll(host, horizontal=False)
    sc.setMinimumWidth(max(640, int(min_width)))
    return sc


def _build_sticky_left_panel(
    sticky_layout: QLayout,
    content_widget: QWidget,
    *,
    min_width: int,
    max_width: int,
) -> QWidget:
    shell = QWidget()
    shell_lay = QVBoxLayout(shell)
    shell_lay.setContentsMargins(0, 0, 0, 0)
    shell_lay.setSpacing(8)

    sticky = QFrame()
    sticky.setObjectName("StickyActionBar")
    sticky_lay = QVBoxLayout(sticky)
    sticky_lay.setContentsMargins(0, 0, 0, 0)
    sticky_lay.setSpacing(6)
    sticky_lay.addLayout(sticky_layout)
    sticky.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    content_scroll = _wrap_scroll(content_widget, horizontal=False)
    shell_lay.addWidget(sticky)
    shell_lay.addWidget(content_scroll, stretch=1)
    shell.setMinimumWidth(int(min_width))
    shell.setMaximumWidth(int(max_width))
    return shell


def _stop_qthread(thread: QThread | None, *, timeout_ms: int = 500) -> None:
    if thread is None:
        return
    try:
        if not thread.isRunning():
            return
        thread.requestInterruption()
        thread.quit()
        # Cooperative stop only.
        # Avoid force-terminate here: killing running Python code can crash the app
        # during rapid source/metadata switches.
        thread.wait(max(0, int(timeout_ms)))
    except Exception:
        pass


class SliderNumberInput(QWidget):
    """Numeric input rendered as slider + editable numeric field.

    Keeps a spinbox-like API subset (`value`, `setValue`) to minimize call-site changes.
    """

    _MAX_SLIDER_SPAN = 2_000_000_000

    def __init__(
        self,
        *,
        min_value: float,
        max_value: float,
        value: float,
        decimals: int = 0,
        step: float = 1.0,
        label_min_width: int = 64,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._min_value = float(min_value)
        self._max_value = float(max_value)
        if self._max_value < self._min_value:
            self._min_value, self._max_value = self._max_value, self._min_value

        requested_decimals = max(0, int(decimals))
        scale = 10 ** requested_decimals
        span = max(0.0, self._max_value - self._min_value)
        while scale > 1 and (span * float(scale)) > float(self._MAX_SLIDER_SPAN):
            scale //= 10
        self._scale = max(1, int(scale))
        self._decimals = 0
        s = self._scale
        while s > 1 and (s % 10) == 0:
            s //= 10
            self._decimals += 1

        self._slider = QSlider(Qt.Horizontal, self)
        min_raw = int(round(self._min_value * self._scale))
        max_raw = int(round(self._max_value * self._scale))
        if max_raw <= min_raw:
            max_raw = min_raw + 1
        self._slider.setRange(min_raw, max_raw)

        step_raw = int(round(float(step) * self._scale))
        step_raw = max(1, step_raw)
        self._slider.setSingleStep(step_raw)
        self._slider.setPageStep(max(step_raw * 5, 1))

        self._value_edit = QLineEdit("", self)
        self._value_edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_edit.setMinimumWidth(max(44, int(label_min_width)))
        self._value_edit.setMaximumWidth(max(72, int(label_min_width) + 24))
        if self._decimals <= 0:
            self._value_edit.setValidator(QIntValidator(int(round(self._min_value)), int(round(self._max_value)), self))
        else:
            v = QDoubleValidator(float(self._min_value), float(self._max_value), int(self._decimals), self)
            v.setNotation(QDoubleValidator.StandardNotation)
            self._value_edit.setValidator(v)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._value_edit, stretch=0)
        lay.addWidget(self._slider, stretch=1)

        self._slider.valueChanged.connect(self._sync_label)
        self._value_edit.editingFinished.connect(self._apply_edited_value)
        self.setValue(float(value))

    def _raw_to_value(self, raw: int) -> float:
        return float(raw) / float(self._scale)

    def _sync_label(self, _raw: int) -> None:
        v = self._raw_to_value(int(self._slider.value()))
        if self._decimals <= 0:
            self._value_edit.setText(str(int(round(v))))
            return
        self._value_edit.setText(f"{v:.{self._decimals}f}")

    def _apply_edited_value(self) -> None:
        txt = str(self._value_edit.text() or "").strip().replace(",", ".")
        if not txt:
            self._sync_label(int(self._slider.value()))
            return
        try:
            if self._decimals <= 0:
                raw = int(round(float(txt)))
            else:
                raw = int(round(float(txt) * float(self._scale)))
        except Exception:
            self._sync_label(int(self._slider.value()))
            return
        raw = max(int(self._slider.minimum()), min(int(self._slider.maximum()), raw))
        self._slider.setValue(raw)

    def value(self) -> float:
        v = self._raw_to_value(int(self._slider.value()))
        if self._decimals <= 0:
            return float(int(round(v)))
        return float(v)

    def setValue(self, v: float) -> None:
        try:
            val = float(v)
        except Exception:
            val = self._min_value
        val = max(self._min_value, min(self._max_value, val))
        raw = int(round(val * self._scale))
        self._slider.setValue(raw)
        self._sync_label(raw)

    def set_bounds(self, min_value: Any, max_value: Any) -> None:
        try:
            min_v = float(min_value)
        except Exception:
            min_v = self._min_value
        try:
            max_v = float(max_value)
        except Exception:
            max_v = self._max_value
        if max_v < min_v:
            min_v, max_v = max_v, min_v
        self._min_value = min_v
        self._max_value = max_v
        min_raw = int(round(self._min_value * self._scale))
        max_raw = int(round(self._max_value * self._scale))
        if max_raw <= min_raw:
            max_raw = min_raw + 1
        cur = int(self._slider.value())
        cur = max(min_raw, min(max_raw, cur))
        self._slider.blockSignals(True)
        self._slider.setRange(min_raw, max_raw)
        self._slider.setValue(cur)
        self._slider.blockSignals(False)
        self._sync_label(cur)


def _make_labeled_slider(
    *,
    min_value: int,
    max_value: int,
    value: int,
    decimals: int = 0,
    step: int = 1,
    label_min_width: int = 52,
) -> tuple[QWidget, QSlider, QLineEdit]:
    slider = QSlider(Qt.Horizontal)
    slider.setRange(int(min_value), int(max_value))
    slider.setSingleStep(max(1, int(step)))
    slider.setPageStep(max(1, int(step) * 2))
    slider.setValue(int(max(min_value, min(max_value, value))))

    value_edit = QLineEdit("")
    value_edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    value_edit.setMinimumWidth(label_min_width)
    value_edit.setMaximumWidth(max(72, int(label_min_width) + 24))
    if decimals <= 0:
        value_edit.setValidator(QIntValidator(int(min_value), int(max_value), value_edit))
    else:
        scale = 10 ** decimals
        v = QDoubleValidator(float(min_value) / float(scale), float(max_value) / float(scale), int(decimals), value_edit)
        v.setNotation(QDoubleValidator.StandardNotation)
        value_edit.setValidator(v)

    def _fmt(v: int) -> str:
        if decimals <= 0:
            return str(int(v))
        scale = 10 ** decimals
        return f"{(float(v) / float(scale)):.{decimals}f}"

    value_edit.setText(_fmt(slider.value()))
    slider.valueChanged.connect(lambda v: value_edit.setText(_fmt(int(v))))

    def _apply_edit() -> None:
        txt = str(value_edit.text() or "").strip().replace(",", ".")
        if not txt:
            value_edit.setText(_fmt(slider.value()))
            return
        try:
            if decimals <= 0:
                raw = int(round(float(txt)))
            else:
                scale = 10 ** decimals
                raw = int(round(float(txt) * float(scale)))
        except Exception:
            value_edit.setText(_fmt(slider.value()))
            return
        raw = max(int(slider.minimum()), min(int(slider.maximum()), raw))
        slider.setValue(raw)

    value_edit.editingFinished.connect(_apply_edit)

    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(value_edit, stretch=0)
    lay.addWidget(slider, stretch=1)
    return row, slider, value_edit



class MultiSelectBox(QGroupBox):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.btn_all = QPushButton("Select all")
        self.btn_none = QPushButton("Clear")
        top.addWidget(self.btn_all)
        top.addWidget(self.btn_none)
        top.addStretch(1)
        lay.addLayout(top)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list.setMinimumHeight(124)
        self.list.setMinimumWidth(240)
        lay.addWidget(self.list)

        self.lbl_count = QLabel("0 selected")
        lay.addWidget(self.lbl_count)

        self.btn_all.clicked.connect(self.select_all)
        self.btn_none.clicked.connect(self.clear_selection)
        self.list.itemSelectionChanged.connect(self._update_count)

    def set_items(self, values: list[Any], select_all: bool = True) -> None:
        self.list.clear()
        for v in values:
            it = QListWidgetItem(str(v))
            it.setData(Qt.UserRole, v)
            self.list.addItem(it)
        if select_all and values:
            self.select_all()
        else:
            self._update_count()

    def values(self) -> list[Any]:
        out: list[Any] = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            data = it.data(Qt.UserRole)
            out.append(data if data is not None else it.text())
        return out

    def selected_values(self) -> list[Any]:
        out: list[Any] = []
        for it in self.list.selectedItems():
            data = it.data(Qt.UserRole)
            out.append(data if data is not None else it.text())
        return out

    def select_all(self) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setSelected(True)
        self._update_count()

    def clear_selection(self) -> None:
        self.list.clearSelection()
        self._update_count()

    def set_selected_values(self, values: list[Any]) -> None:
        wanted = {str(v) for v in (values or [])}
        for i in range(self.list.count()):
            it = self.list.item(i)
            data = it.data(Qt.UserRole)
            key = str(data if data is not None else it.text())
            it.setSelected(key in wanted)
        self._update_count()

    def _update_count(self) -> None:
        self.lbl_count.setText(f"{len(self.selected_values())} selected")


class DataUploadTab(QWidget):
    WORKFLOW_OPTIONS: list[tuple[str, str]] = [
        ("Upload analyzed file", "analyzed_upload"),
        ("Analyze RAW -> nM", "raw_analyze"),
        ("Update existing analyzed with RAW", "raw_update_existing"),
    ]

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.raw_input: LoadedTable | None = None
        self.prev_analyzed_input: LoadedTable | None = None
        self.raw_analyzed_df: pd.DataFrame | None = None
        self.raw_merged_df: pd.DataFrame | None = None
        self._metadata_upload_raw: LoadedTable | None = None
        self._meta_map_applying: bool = False
        self._std_thread: QThread | None = None
        self._std_worker: LongTaskWorker | None = None
        self._raw_analyze_thread: QThread | None = None
        self._raw_analyze_worker: LongTaskWorker | None = None
        self._raw_merge_thread: QThread | None = None
        self._raw_merge_worker: LongTaskWorker | None = None
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_curves_changed)
        self.state.curves_standardized_changed.connect(self._on_state_curves_changed)
        self.state.metadata_changed.connect(self._on_state_metadata_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left_panel = QWidget()
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)

        mode_box = QGroupBox("Workflow Mode")
        mode_lay = QFormLayout(mode_box)
        mode_lay.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        mode_lay.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cb_workflow_mode = QComboBox()
        self.cb_workflow_mode.setMinimumWidth(320)
        for lbl, val in self.WORKFLOW_OPTIONS:
            self.cb_workflow_mode.addItem(lbl, val)
        self.cb_workflow_mode.currentIndexChanged.connect(self._on_workflow_mode_changed)
        mode_lay.addRow("Mode", self.cb_workflow_mode)
        self.lbl_workflow_hint = QLabel("")
        self.lbl_workflow_hint.setWordWrap(True)
        self.lbl_workflow_hint.setMinimumHeight(46)
        self.lbl_workflow_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        mode_lay.addRow("Hint", self.lbl_workflow_hint)
        left.addWidget(mode_box)

        box = QGroupBox("Data Upload")
        self.upload_box = box
        box_layout = QVBoxLayout(box)

        self.btn_curves = QPushButton("Load curves file")
        self.btn_curves.clicked.connect(self._load_curves)
        box_layout.addWidget(self.btn_curves)

        self.lbl_curves = QLabel("Curves: not loaded")
        self.lbl_curves.setWordWrap(True)
        box_layout.addWidget(self.lbl_curves)

        self.btn_metadata = QPushButton("Load metadata file")
        self.btn_metadata.clicked.connect(self._load_metadata)
        box_layout.addWidget(self.btn_metadata)

        self.lbl_metadata = QLabel("Metadata: not loaded")
        self.lbl_metadata.setWordWrap(True)
        box_layout.addWidget(self.lbl_metadata)

        self.lbl_active_source = QLabel("Active curves source for panels: none")
        self.lbl_active_source.setWordWrap(True)
        box_layout.addWidget(self.lbl_active_source)

        left.addWidget(box)

        std_box = QGroupBox("Curves Standardization (Legacy Parity)")
        self.std_box = std_box
        std_layout = QVBoxLayout(std_box)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        def _map_label_with_help(text: str, tooltip: str) -> QWidget:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            lbl = QLabel(text)
            tip = QToolButton()
            tip.setText("?")
            tip.setToolTip(tooltip)
            tip.setCursor(Qt.PointingHandCursor)
            tip.setAutoRaise(True)
            tip.setFocusPolicy(Qt.NoFocus)
            tip.setStyleSheet(
                "QToolButton{font-weight:700;color:#1d4ed8;border:1px solid #94a3b8;border-radius:9px;padding:0px;background:transparent;}"
                "QToolButton:hover{background:rgba(59,130,246,0.10);}"
            )
            tip.setFixedSize(18, 18)
            tip.clicked.connect(
                lambda _=False, btn=tip, tt=tooltip: QToolTip.showText(
                    btn.mapToGlobal(btn.rect().bottomLeft()), tt, btn
                )
            )
            lay.addWidget(lbl)
            lay.addWidget(tip)
            lay.addStretch(1)
            return row

        self.map_sample = QComboBox()
        self.map_size = QComboBox()
        self.map_location = QComboBox()
        self.map_temp = QComboBox()
        self.map_nm = QComboBox()
        self.map_control = QComboBox()
        self.map_dilution = QComboBox()
        self.map_ff = QComboBox()
        for fld in (
            self.map_sample,
            self.map_size,
            self.map_location,
            self.map_temp,
            self.map_nm,
            self.map_control,
            self.map_dilution,
            self.map_ff,
        ):
            fld.setMinimumWidth(240)
            fld.addItem("Auto / infer", "")

        form.addRow(
            _map_label_with_help(
                "Map Sample",
                "Sample identifier column used to group rows into curves and to match metadata.",
            ),
            self.map_sample,
        )
        form.addRow(
            _map_label_with_help(
                "Map Size",
                "Particle-size grouping column. If missing, fallback/auto-inference is used.",
            ),
            self.map_size,
        )
        form.addRow(
            _map_label_with_help(
                "Map Location",
                "Location grouping column used in FC/FF/Correlation comparisons.",
            ),
            self.map_location,
        )
        form.addRow(
            _map_label_with_help(
                "Map Freezing.temperature",
                "Temperature column (°C), used as X-axis for freezing curves and frozen fraction plots.",
            ),
            self.map_temp,
        )
        form.addRow(
            _map_label_with_help(
                "Map nm",
                "nM concentration column used for FC/Kneepoint/Boxplots/Correlations.",
            ),
            self.map_nm,
        )
        form.addRow(
            _map_label_with_help(
                "Map Control",
                "Control flag column (Yes/No). If missing, control can be inferred from keywords.",
            ),
            self.map_control,
        )
        form.addRow(
            _map_label_with_help(
                "Map Dilution.factor",
                "Dilution column (e.g., 1, 10, 100...). If missing, trailing dilution can be parsed from Sample.",
            ),
            self.map_dilution,
        )
        form.addRow(
            _map_label_with_help(
                "Map FF",
                "Frozen fraction column in [0,1], used by Frozen Fraction panel and RAW/Vali logic.",
            ),
            self.map_ff,
        )
        std_layout.addLayout(form)

        map_actions = QHBoxLayout()
        self.btn_auto_map = QPushButton("Auto-map from columns")
        self.btn_auto_map.clicked.connect(self._auto_fill_mapping)
        map_actions.addWidget(self.btn_auto_map)
        self.btn_clear_map = QPushButton("Clear mapping fields")
        self.btn_clear_map.clicked.connect(self._clear_mapping_fields)
        map_actions.addWidget(self.btn_clear_map)
        std_layout.addLayout(map_actions)

        grid = QGridLayout()
        self.chk_size_grouping = QCheckBox("Use size grouping")
        self.chk_size_grouping.setChecked(True)
        grid.addWidget(self.chk_size_grouping, 0, 0)
        self.in_manual_size = QLineEdit("b_5_m")
        grid.addWidget(QLabel("Manual size fallback"), 1, 0)
        grid.addWidget(self.in_manual_size, 1, 1)

        self.chk_location_grouping = QCheckBox("Use location grouping")
        self.chk_location_grouping.setChecked(True)
        grid.addWidget(self.chk_location_grouping, 2, 0)
        self.in_manual_location = QLineEdit("(unknown)")
        grid.addWidget(QLabel("Manual location fallback"), 3, 0)
        grid.addWidget(self.in_manual_location, 3, 1)

        self.chk_auto_dil = QCheckBox("Auto-detect dilution from Sample")
        self.chk_auto_dil.setChecked(True)
        grid.addWidget(self.chk_auto_dil, 4, 0, 1, 2)

        self.chk_auto_ctrl = QCheckBox("Auto-detect control from keywords")
        self.chk_auto_ctrl.setChecked(True)
        grid.addWidget(self.chk_auto_ctrl, 5, 0, 1, 2)

        self.in_control_keywords = QLineEdit("MilliQ,milli-q,blank,control,ctrl,mq")
        grid.addWidget(QLabel("Control keywords"), 6, 0)
        grid.addWidget(self.in_control_keywords, 6, 1)

        std_layout.addLayout(grid)

        std_actions = QHBoxLayout()
        self.btn_standardize = QPushButton("Standardize curves (legacy parity)")
        self.btn_standardize.clicked.connect(self._standardize_curves)
        std_actions.addWidget(self.btn_standardize)
        self.btn_std_cancel = QPushButton("Cancel")
        self.btn_std_cancel.setEnabled(False)
        self.btn_std_cancel.clicked.connect(self._cancel_standardize)
        std_actions.addWidget(self.btn_std_cancel)
        std_layout.addLayout(std_actions)

        self.pb_standardize = QProgressBar()
        self.pb_standardize.setRange(0, 100)
        self.pb_standardize.setValue(0)
        std_layout.addWidget(self.pb_standardize)

        self.btn_reset_session = QPushButton("Reset loaded data")
        self.btn_reset_session.clicked.connect(self._reset_session)
        std_layout.addWidget(self.btn_reset_session)

        self.lbl_standardized = QLabel("Standardized curves: not generated")
        self.lbl_standardized.setWordWrap(True)
        std_layout.addWidget(self.lbl_standardized)

        left.addWidget(std_box)

        raw_box = QGroupBox("RAW Workflow (analyze + merge)")
        self.raw_box = raw_box
        raw_lay = QVBoxLayout(raw_box)

        raw_files_lay = QGridLayout()
        raw_files_lay.setColumnStretch(0, 1)
        raw_files_lay.setColumnStretch(1, 1)
        self.btn_raw_load = QPushButton("Load RAW file")
        self.btn_raw_load.clicked.connect(self._load_raw_input)
        raw_files_lay.addWidget(self.btn_raw_load, 0, 0, 1, 2)
        self.lbl_raw_file = QLabel("RAW input: not loaded")
        self.lbl_raw_file.setWordWrap(True)
        raw_files_lay.addWidget(self.lbl_raw_file, 1, 0, 1, 2)
        self.btn_prev_analyzed_load = QPushButton("Load previous analyzed file")
        self.btn_prev_analyzed_load.clicked.connect(self._load_previous_analyzed_input)
        raw_files_lay.addWidget(self.btn_prev_analyzed_load, 2, 0, 1, 2)
        self.lbl_prev_analyzed_file = QLabel("Previous analyzed: not loaded")
        self.lbl_prev_analyzed_file.setWordWrap(True)
        raw_files_lay.addWidget(self.lbl_prev_analyzed_file, 3, 0, 1, 2)
        raw_lay.addLayout(raw_files_lay)

        raw_map_form = QFormLayout()
        raw_map_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        raw_map_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.raw_map_sample = QLineEdit()
        self.raw_map_temp = QLineEdit()
        self.raw_map_ff = QLineEdit()
        self.raw_map_size = QLineEdit()
        self.raw_map_location = QLineEdit()
        self.raw_map_control = QLineEdit()
        self.raw_map_dilution = QLineEdit()
        for fld in (
            self.raw_map_sample,
            self.raw_map_temp,
            self.raw_map_ff,
            self.raw_map_size,
            self.raw_map_location,
            self.raw_map_control,
            self.raw_map_dilution,
        ):
            fld.setMinimumWidth(240)
        raw_map_form.addRow("Map Sample.name (RAW Content)", self.raw_map_sample)
        raw_map_form.addRow("Map Freezing.temperature", self.raw_map_temp)
        raw_map_form.addRow("Map FF", self.raw_map_ff)
        raw_map_form.addRow("Map Size (optional)", self.raw_map_size)
        raw_map_form.addRow("Map Location (optional)", self.raw_map_location)
        raw_map_form.addRow("Map Control (optional)", self.raw_map_control)
        raw_map_form.addRow("Map Dilution.factor (optional)", self.raw_map_dilution)
        raw_lay.addLayout(raw_map_form)

        raw_map_btns = QHBoxLayout()
        self.btn_raw_auto_map = QPushButton("Auto-map RAW columns")
        self.btn_raw_auto_map.clicked.connect(self._auto_fill_raw_mapping)
        raw_map_btns.addWidget(self.btn_raw_auto_map)
        self.btn_raw_clear_map = QPushButton("Clear RAW mapping")
        self.btn_raw_clear_map.clicked.connect(self._clear_raw_mapping_fields)
        raw_map_btns.addWidget(self.btn_raw_clear_map)
        raw_lay.addLayout(raw_map_btns)

        raw_opts_grid = QGridLayout()
        self.chk_raw_use_size = QCheckBox("Use size grouping")
        self.chk_raw_use_size.setChecked(True)
        raw_opts_grid.addWidget(self.chk_raw_use_size, 0, 0)
        self.in_raw_size_single = QLineEdit("NoSizeGroup")
        raw_opts_grid.addWidget(QLabel("Size single-group label"), 1, 0)
        raw_opts_grid.addWidget(self.in_raw_size_single, 1, 1)
        self.in_raw_manual_size = QLineEdit("")
        raw_opts_grid.addWidget(QLabel("Manual Size fallback"), 2, 0)
        raw_opts_grid.addWidget(self.in_raw_manual_size, 2, 1)

        self.chk_raw_use_location = QCheckBox("Use location grouping")
        self.chk_raw_use_location.setChecked(True)
        raw_opts_grid.addWidget(self.chk_raw_use_location, 3, 0)
        self.in_raw_location_single = QLineEdit("(single_location)")
        raw_opts_grid.addWidget(QLabel("Location single-group label"), 4, 0)
        raw_opts_grid.addWidget(self.in_raw_location_single, 4, 1)
        self.in_raw_manual_location = QLineEdit("")
        raw_opts_grid.addWidget(QLabel("Manual Location fallback"), 5, 0)
        raw_opts_grid.addWidget(self.in_raw_manual_location, 5, 1)

        self.chk_raw_auto_dil = QCheckBox("Auto-detect trailing dilution from Sample.name")
        self.chk_raw_auto_dil.setChecked(True)
        raw_opts_grid.addWidget(self.chk_raw_auto_dil, 6, 0, 1, 2)
        self.chk_raw_auto_ctrl = QCheckBox("Auto-detect control from keywords")
        self.chk_raw_auto_ctrl.setChecked(True)
        raw_opts_grid.addWidget(self.chk_raw_auto_ctrl, 7, 0, 1, 2)
        self.in_raw_control_keywords = QLineEdit("MilliQ,milli-q,blank,control,ctrl,mq")
        raw_opts_grid.addWidget(QLabel("Control keywords"), 8, 0)
        raw_opts_grid.addWidget(self.in_raw_control_keywords, 8, 1)
        raw_lay.addLayout(raw_opts_grid)

        raw_method_box = QGroupBox("RAW normalization settings")
        self.raw_method_form = QFormLayout(raw_method_box)
        self.raw_method_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.raw_method_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cb_raw_platform = QComboBox()
        self.cb_raw_platform.addItem("Micro-PINGUIN defaults", "micro_pinguin")
        self.cb_raw_platform.addItem("Custom setup", "custom")
        self.cb_raw_platform.currentIndexChanged.connect(self._apply_raw_platform_defaults)
        self.raw_method_form.addRow("Platform setup", self.cb_raw_platform)

        self.cb_raw_method = QComboBox()
        for lbl, val in RAW_WORKFLOW_METHOD_OPTIONS:
            self.cb_raw_method.addItem(lbl, val)
        idx_default = self.cb_raw_method.findData("mass_extraction_nm")
        self.cb_raw_method.setCurrentIndex(idx_default if idx_default >= 0 else 0)
        self.cb_raw_method.currentIndexChanged.connect(self._on_raw_method_changed)
        self.raw_method_form.addRow("Method", self.cb_raw_method)
        self.lbl_raw_method_help = QLabel("")
        self.lbl_raw_method_help.setWordWrap(True)
        self.raw_method_form.addRow("Method help", self.lbl_raw_method_help)
        self.btn_raw_method_ref = QPushButton("Open RAW method reference")
        self.btn_raw_method_ref.clicked.connect(self._open_raw_method_reference)
        self.raw_method_form.addRow("Reference", self.btn_raw_method_ref)

        self.sp_raw_n0 = SliderNumberInput(min_value=1, max_value=100000, value=384, decimals=0, step=1)
        self.raw_method_form.addRow("N0", self.sp_raw_n0)
        self.sp_raw_drop_ul = SliderNumberInput(
            min_value=0.001,
            max_value=100000.0,
            value=30.0,
            decimals=3,
            step=0.1,
        )
        self.raw_method_form.addRow("Droplet volume (µL)", self.sp_raw_drop_ul)
        self.sp_raw_wash_ml = SliderNumberInput(
            min_value=0.001,
            max_value=1_000_000.0,
            value=400.0,
            decimals=3,
            step=1.0,
        )
        self.raw_method_form.addRow("Water/Wash volume (mL)", self.sp_raw_wash_ml)
        self.sp_raw_sample_mass_g = SliderNumberInput(
            min_value=0.0001,
            max_value=1_000_000.0,
            value=10.0,
            decimals=4,
            step=0.1,
        )
        self.raw_method_form.addRow("Sample/Soil mass (g)", self.sp_raw_sample_mass_g)

        self.sp_raw_extra_dilution = SliderNumberInput(
            min_value=0.001,
            max_value=1_000_000.0,
            value=1.0,
            decimals=3,
            step=0.1,
        )
        self.raw_method_form.addRow("Extra dilution factor", self.sp_raw_extra_dilution)
        self.sp_raw_mass_conc = SliderNumberInput(
            min_value=0.000001,
            max_value=1_000_000.0,
            value=1.0,
            decimals=6,
            step=0.1,
        )
        self.raw_method_form.addRow("Mass concentration c_m (g/mL)", self.sp_raw_mass_conc)
        self.sp_raw_cell_conc = SliderNumberInput(
            min_value=1.0,
            max_value=1_000_000_000.0,
            value=1_000_000.0,
            decimals=0,
            step=1_000.0,
        )
        self.raw_method_form.addRow("Cell concentration (cells/mL)", self.sp_raw_cell_conc)
        self.sp_raw_area_drop = SliderNumberInput(
            min_value=0.000000001,
            max_value=1_000.0,
            value=0.001,
            decimals=9,
            step=0.000001,
        )
        self.raw_method_form.addRow("Area per droplet (m²/drop)", self.sp_raw_area_drop)
        self.sp_raw_bet_area = SliderNumberInput(
            min_value=0.000001,
            max_value=1_000_000.0,
            value=10.0,
            decimals=6,
            step=0.1,
        )
        self.raw_method_form.addRow("BET area theta (m²/g)", self.sp_raw_bet_area)
        self.sp_raw_air_filter_frac = SliderNumberInput(
            min_value=0.0001,
            max_value=1.0,
            value=1.0,
            decimals=4,
            step=0.01,
        )
        self.raw_method_form.addRow("Filter fraction x (0-1)", self.sp_raw_air_filter_frac)
        self.sp_raw_air_volume_l = SliderNumberInput(
            min_value=0.001,
            max_value=1_000_000_000.0,
            value=1.0,
            decimals=3,
            step=1.0,
        )
        self.raw_method_form.addRow("Sampled air volume Vs (L)", self.sp_raw_air_volume_l)
        self.sp_raw_filter_area = SliderNumberInput(
            min_value=0.000001,
            max_value=1_000_000.0,
            value=1.0,
            decimals=6,
            step=0.1,
        )
        self.raw_method_form.addRow("Exposed filter area", self.sp_raw_filter_area)
        self.sp_raw_drop_area = SliderNumberInput(
            min_value=0.000001,
            max_value=1_000_000.0,
            value=1.0,
            decimals=6,
            step=0.1,
        )
        self.raw_method_form.addRow("Droplet footprint area alpha", self.sp_raw_drop_area)
        self.sp_raw_custom_dose = SliderNumberInput(
            min_value=0.000001,
            max_value=1_000_000_000.0,
            value=1.0,
            decimals=6,
            step=0.1,
        )
        self.raw_method_form.addRow("Custom dose per droplet X", self.sp_raw_custom_dose)
        self.lbl_raw_platform_note = QLabel(
            "Micro-PINGUIN defaults applied (N0=384, V=30 µL). You can still edit values if needed."
        )
        self.lbl_raw_platform_note.setWordWrap(True)
        self.raw_method_form.addRow("Platform note", self.lbl_raw_platform_note)
        raw_lay.addWidget(raw_method_box)

        raw_actions_top = QGridLayout()
        self.btn_raw_analyze = QPushButton("Analyze RAW (Vali)")
        self.btn_raw_analyze.clicked.connect(self._analyze_raw)
        raw_actions_top.addWidget(self.btn_raw_analyze, 0, 0)
        self.btn_raw_cancel = QPushButton("Cancel")
        self.btn_raw_cancel.setEnabled(False)
        self.btn_raw_cancel.clicked.connect(self._cancel_raw_analyze)
        raw_actions_top.addWidget(self.btn_raw_cancel, 0, 1)
        self.btn_raw_use = QPushButton("Use analyzed file")
        self.btn_raw_use.clicked.connect(self._use_raw_analyzed_as_active_curves)
        self.btn_raw_use.setEnabled(False)
        raw_actions_top.addWidget(self.btn_raw_use, 1, 0)
        self.btn_raw_save = QPushButton("Save analyzed CSV")
        self.btn_raw_save.clicked.connect(self._save_raw_analyzed_csv)
        self.btn_raw_save.setEnabled(False)
        raw_actions_top.addWidget(self.btn_raw_save, 1, 1)
        raw_lay.addLayout(raw_actions_top)
        self.pb_raw_analyze = QProgressBar()
        self.pb_raw_analyze.setRange(0, 100)
        self.pb_raw_analyze.setValue(0)
        raw_lay.addWidget(self.pb_raw_analyze)

        merge_map_group = QGroupBox("Merge mapping (RAW -> previous analyzed schema)")
        self.merge_map_group = merge_map_group
        merge_map_form = QFormLayout(merge_map_group)
        merge_map_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        merge_map_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.merge_map_boxes: dict[str, QComboBox] = {}
        for src in RAW_ANALYZED_MERGE_FIELDS:
            cb = QComboBox()
            cb.addItem("Auto", "")
            cb.addItem("Do not import", MERGE_MAPPING_SKIP)
            cb.setMinimumWidth(240)
            merge_map_form.addRow(src, cb)
            self.merge_map_boxes[src] = cb
        raw_lay.addWidget(merge_map_group)

        raw_actions_bottom = QGridLayout()
        self.btn_merge_auto_map = QPushButton("Auto-map merge targets")
        self.btn_merge_auto_map.clicked.connect(self._auto_fill_merge_mapping)
        self.btn_merge_auto_map.setEnabled(False)
        raw_actions_bottom.addWidget(self.btn_merge_auto_map, 0, 0)
        self.btn_raw_merge = QPushButton("Merge into previous analyzed")
        self.btn_raw_merge.clicked.connect(self._merge_raw_into_previous)
        self.btn_raw_merge.setEnabled(False)
        raw_actions_bottom.addWidget(self.btn_raw_merge, 0, 1)
        self.btn_raw_merged_use = QPushButton("Use merged file")
        self.btn_raw_merged_use.clicked.connect(self._use_raw_merged_as_active_curves)
        self.btn_raw_merged_use.setEnabled(False)
        raw_actions_bottom.addWidget(self.btn_raw_merged_use, 1, 0)
        self.btn_raw_merged_save = QPushButton("Save merged CSV")
        self.btn_raw_merged_save.clicked.connect(self._save_raw_merged_csv)
        self.btn_raw_merged_save.setEnabled(False)
        raw_actions_bottom.addWidget(self.btn_raw_merged_save, 1, 1)
        self.btn_raw_merge_cancel = QPushButton("Cancel merge")
        self.btn_raw_merge_cancel.clicked.connect(self._cancel_raw_merge)
        self.btn_raw_merge_cancel.setEnabled(False)
        raw_actions_bottom.addWidget(self.btn_raw_merge_cancel, 2, 0, 1, 2)
        raw_lay.addLayout(raw_actions_bottom)
        self.pb_raw_merge = QProgressBar()
        self.pb_raw_merge.setRange(0, 100)
        self.pb_raw_merge.setValue(0)
        raw_lay.addWidget(self.pb_raw_merge)

        self.lbl_raw_status = QLabel("RAW status: waiting")
        self.lbl_raw_status.setWordWrap(True)
        raw_lay.addWidget(self.lbl_raw_status)
        self.lbl_merge_status = QLabel("Merge status: waiting")
        self.lbl_merge_status.setWordWrap(True)
        raw_lay.addWidget(self.lbl_merge_status)

        left.addWidget(raw_box)

        meta_box = QGroupBox("Metadata Mapping")
        self.meta_box = meta_box
        meta_lay = QVBoxLayout(meta_box)
        meta_form = QFormLayout()
        meta_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        meta_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cb_meta_sample = QComboBox()
        self.cb_meta_sample.setMinimumWidth(240)
        self.cb_meta_sample.setMinimumContentsLength(24)
        self.cb_meta_sample.currentTextChanged.connect(self._mark_metadata_mapping_dirty)
        meta_form.addRow("Metadata Sample column", self.cb_meta_sample)
        meta_lay.addLayout(meta_form)

        self.meta_cols_box = MultiSelectBox("Metadata columns to keep")
        self.meta_cols_box.list.itemSelectionChanged.connect(self._mark_metadata_mapping_dirty)
        meta_lay.addWidget(self.meta_cols_box)

        meta_btns = QHBoxLayout()
        self.btn_meta_auto = QPushButton("Auto-map metadata")
        self.btn_meta_auto.clicked.connect(self._auto_fill_metadata_mapping)
        meta_btns.addWidget(self.btn_meta_auto)
        self.btn_meta_apply = QPushButton("Apply metadata mapping")
        self.btn_meta_apply.clicked.connect(self._apply_metadata_mapping)
        meta_btns.addWidget(self.btn_meta_apply)
        meta_lay.addLayout(meta_btns)

        self.lbl_meta_map_status = QLabel("Metadata mapping: waiting")
        self.lbl_meta_map_status.setWordWrap(True)
        meta_lay.addWidget(self.lbl_meta_map_status)
        left.addWidget(meta_box)

        nm_box = QGroupBox("nM Equation / Axis Units")
        self.nm_box = nm_box
        nm_lay = QVBoxLayout(nm_box)
        self.cb_nm_method = QComboBox()
        nm_options = [
            ("Auto (infer from analyzed file metadata if available)", "auto"),
            ("Mass extraction (nm g^-1)", "mass_extraction_nm"),
            ("Liquid volume K(T) (mL^-1)", "liquid_volume_K"),
            ("Legacy soil default (nm g^-1)", "legacy_soil_default"),
            ("Surface area direct (n_s m^-2)", "surface_area_direct"),
            ("Surface area BET (n_s,BET m^-2)", "surface_area_bet"),
            ("Cell count (n_cell cell^-1)", "cell_count"),
            ("Air wash-off (N_INP_air L^-1)", "air_washoff"),
            ("Air drop-on (N_INP_air L^-1)", "air_drop_on"),
            ("Custom dose (nX X^-1)", "custom_dose"),
        ]
        for lbl, val in nm_options:
            self.cb_nm_method.addItem(lbl, val)
        self.cb_nm_method.currentIndexChanged.connect(self._apply_nm_method_selection)
        nm_lay.addWidget(self.cb_nm_method)

        self.lbl_nm_axis = QLabel(f"Current Y-axis label: {self.state.nm_axis_label}")
        self.lbl_nm_axis.setWordWrap(True)
        nm_lay.addWidget(self.lbl_nm_axis)

        self.lbl_nm_method_detail = QLabel("")
        self.lbl_nm_method_detail.setWordWrap(True)
        self.lbl_nm_method_detail.setTextFormat(Qt.RichText)
        nm_lay.addWidget(self.lbl_nm_method_detail)
        self.btn_nm_method_ref = QPushButton("Open equation reference")
        self.btn_nm_method_ref.clicked.connect(self._open_nm_method_reference)
        nm_lay.addWidget(self.btn_nm_method_ref)
        left.addWidget(nm_box)

        info_box = QGroupBox("Ingestion Log")
        info_layout = QVBoxLayout(info_box)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        info_layout.addWidget(self.log)
        left.addWidget(info_box, stretch=1)
        left.addStretch(1)

        left_scroll = _wrap_scroll(left_panel, horizontal=False)
        left_scroll.setMinimumWidth(560)
        left_scroll.setMaximumWidth(760)

        right_panel = QWidget()
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QTableWidget()
        preview_layout.addWidget(self.preview)
        right.addWidget(preview_box, stretch=1)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 1080])
        self._refresh_merge_mapping_controls()
        self._apply_raw_platform_defaults()
        self._on_raw_method_changed()
        self._apply_workflow_mode_ui()

    def _pick_file(self, title: str) -> Path | None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            "Data files (*.csv *.tsv *.txt *.xls *.xlsx);;All files (*.*)",
        )
        if not selected:
            return None
        return Path(selected)

    def _workflow_mode(self) -> str:
        return str(self.cb_workflow_mode.currentData() or "analyzed_upload")

    def _on_workflow_mode_changed(self) -> None:
        self._apply_workflow_mode_ui()
        self._refresh_merge_mapping_controls()
        mode = self._workflow_mode()
        self.log.append(f"[workflow] mode switched -> {mode}")

    def _apply_workflow_mode_ui(self) -> None:
        mode = self._workflow_mode()
        is_analyzed = mode == "analyzed_upload"
        is_raw_analyze = mode == "raw_analyze"

        self.std_box.setVisible(is_analyzed)
        self.raw_box.setVisible(not is_analyzed)
        self.meta_box.setVisible(True)
        self.nm_box.setVisible(True)

        self.btn_curves.setEnabled(is_analyzed)
        self.btn_curves.setText("Load analyzed file" if is_analyzed else "Load curves file (legacy)")

        if is_analyzed:
            self.lbl_workflow_hint.setText(
                "Use this mode when your file is already analyzed (has nm/FF/temperature columns). "
                "Map columns and standardize to activate all panels."
            )
        elif is_raw_analyze:
            self.lbl_workflow_hint.setText(
                "Use this mode to analyze RAW into a new analyzed table. "
                "Merge controls are hidden because no previous analyzed file is required."
            )
        else:
            self.lbl_workflow_hint.setText(
                "Use this mode to analyze RAW and merge results into a previous analyzed file "
                "with strict schema preservation."
            )
        self._refresh_standardize_controls()
        self._refresh_raw_action_controls()

    def _raw_analyze_running(self) -> bool:
        return self._raw_analyze_thread is not None and self._raw_analyze_thread.isRunning()

    def _raw_merge_running(self) -> bool:
        return self._raw_merge_thread is not None and self._raw_merge_thread.isRunning()

    def _std_running(self) -> bool:
        return self._std_thread is not None and self._std_thread.isRunning()

    def _refresh_standardize_controls(self) -> None:
        mode = self._workflow_mode()
        is_analyzed = mode == "analyzed_upload"
        running = self._std_running()
        has_raw = self.state.curves_raw is not None
        self.btn_standardize.setEnabled(is_analyzed and has_raw and not running)
        self.btn_std_cancel.setEnabled(is_analyzed and running)
        self.btn_auto_map.setEnabled(is_analyzed and has_raw and not running)
        self.btn_clear_map.setEnabled(is_analyzed and not running)
        if is_analyzed:
            self.btn_curves.setEnabled(not running)

    def _refresh_raw_action_controls(self) -> None:
        mode = self._workflow_mode()
        is_raw_update = mode == "raw_update_existing"
        analyze_running = self._raw_analyze_running()
        merge_running = self._raw_merge_running()
        any_running = analyze_running or merge_running

        has_raw_input = self.raw_input is not None
        has_prev = self.prev_analyzed_input is not None
        has_raw_analyzed = self.raw_analyzed_df is not None
        has_raw_merged = self.raw_merged_df is not None

        self.btn_raw_load.setEnabled(not any_running)
        self.btn_raw_auto_map.setEnabled(has_raw_input and not any_running)
        self.btn_raw_clear_map.setEnabled(not any_running)
        self.btn_raw_analyze.setEnabled(has_raw_input and not any_running)
        self.btn_raw_cancel.setEnabled(analyze_running)
        self.btn_raw_use.setEnabled(has_raw_analyzed and not any_running)
        self.btn_raw_save.setEnabled(has_raw_analyzed and not any_running)

        self.btn_prev_analyzed_load.setEnabled(is_raw_update and not any_running)
        self.merge_map_group.setEnabled(is_raw_update and has_prev and not any_running)
        self.btn_merge_auto_map.setEnabled(is_raw_update and has_prev and not any_running)
        self.btn_raw_merge.setEnabled(is_raw_update and has_prev and has_raw_analyzed and not any_running)
        self.btn_raw_merged_use.setEnabled(is_raw_update and has_raw_merged and not any_running)
        self.btn_raw_merged_save.setEnabled(is_raw_update and has_raw_merged and not any_running)
        self.btn_raw_merge_cancel.setEnabled(merge_running)
        self.lbl_prev_analyzed_file.setEnabled(is_raw_update)
        self.lbl_merge_status.setEnabled(is_raw_update)

    def _load_curves(self) -> None:
        path = self._pick_file("Select curves file")
        if path is None:
            return
        self._load_curves_from_path(path, show_errors=True)

    def _load_metadata(self) -> None:
        path = self._pick_file("Select metadata file")
        if path is None:
            return
        self._load_metadata_from_path(path, show_errors=True)

    def _load_any_table(self, path: Path, *, kind: str, show_errors: bool = True) -> bool:
        try:
            df = read_table_from_path(path)
        except Exception as exc:
            if show_errors:
                QMessageBox.critical(self, "Read error", f"Could not read file:\n{path}\n\n{exc}")
            self.log.append(f"[{kind}] read error for '{path}': {exc}")
            return False

        loaded = LoadedTable(path=path, df=df)
        if kind == "curves":
            self.state.set_curves_raw(loaded)
            self.lbl_standardized.setText("Standardized curves: not generated")
            self._render_preview(df)
            # Pre-fill mapping fields to make upload workflow less manual.
            self._auto_fill_mapping()
            if self.cb_nm_method.currentData() == "auto":
                self._apply_nm_method_selection()
        else:
            # Keep raw uploaded metadata as source-of-truth for mapping,
            # then publish mapped metadata into shared app state.
            self._meta_map_applying = False
            self._metadata_upload_raw = loaded
            self._render_preview(df)
            try:
                self._auto_fill_metadata_mapping()
            except Exception as exc:
                txt = str(exc)
                self.lbl_meta_map_status.setText(f"Metadata mapping error: {txt}")
                self.log.append(f"[metadata-mapping] auto-map ERROR: {txt}")

        col_preview = ", ".join([str(c) for c in df.columns[:12]])
        if len(df.columns) > 12:
            col_preview += ", ..."
        self.log.append(
            f"[{kind}] loaded '{path.name}' | rows={len(df)} cols={len(df.columns)}\n"
            f"columns: {col_preview}\n"
        )
        return True

    def _load_curves_from_path(self, path: Path, *, show_errors: bool = True) -> bool:
        return self._load_any_table(Path(path), kind="curves", show_errors=show_errors)

    def _load_metadata_from_path(self, path: Path, *, show_errors: bool = True) -> bool:
        return self._load_any_table(Path(path), kind="metadata", show_errors=show_errors)

    def _raw_mapping_fields(self) -> dict[str, QLineEdit]:
        return {
            "Sample": self.raw_map_sample,
            "Freezing.temperature": self.raw_map_temp,
            "FF": self.raw_map_ff,
            "Size": self.raw_map_size,
            "Location": self.raw_map_location,
            "Control": self.raw_map_control,
            "Dilution.factor": self.raw_map_dilution,
        }

    def _clear_raw_mapping_fields(self) -> None:
        for field in self._raw_mapping_fields().values():
            field.clear()
        self.log.append("[raw-mapping] mapping fields cleared.")

    def _load_raw_input(self) -> None:
        path = self._pick_file("Select RAW file")
        if path is None:
            return
        self._load_raw_input_from_path(path, show_errors=True)

    def _load_raw_input_from_path(self, path: Path, *, show_errors: bool = True) -> bool:
        try:
            df = read_table_from_path(path)
        except Exception as exc:
            if show_errors:
                QMessageBox.critical(self, "RAW read error", f"Could not read RAW file:\n{path}\n\n{exc}")
            self.log.append(f"[raw] read error for '{path}': {exc}")
            return False
        self.raw_input = LoadedTable(path=path, df=df)
        self.raw_analyzed_df = None
        self.raw_merged_df = None
        self.lbl_raw_file.setText(f"RAW input: {path.name} | rows={len(df)} cols={len(df.columns)}")
        self.lbl_raw_status.setText("RAW status: file loaded, configure mapping/method and run Analyze RAW.")
        self.pb_raw_analyze.setValue(0)
        self.pb_raw_merge.setValue(0)
        self._render_preview(df)
        self._auto_fill_raw_mapping()
        self._refresh_merge_mapping_controls()
        self.log.append(f"[raw] loaded '{path.name}' | rows={len(df)} cols={len(df.columns)}")
        return True

    def _load_previous_analyzed_input(self) -> None:
        if self._workflow_mode() != "raw_update_existing":
            QMessageBox.information(
                self,
                "Workflow mode",
                "Previous analyzed file is used only in 'Update existing analyzed with RAW' mode.",
            )
            return
        path = self._pick_file("Select previous analyzed file")
        if path is None:
            return
        self._load_previous_analyzed_from_path(path, show_errors=True)

    def _load_previous_analyzed_from_path(self, path: Path, *, show_errors: bool = True) -> bool:
        try:
            df = read_table_from_path(path)
        except Exception as exc:
            if show_errors:
                QMessageBox.critical(self, "Read error", f"Could not read previous analyzed file:\n{path}\n\n{exc}")
            self.log.append(f"[raw-merge] previous analyzed read error for '{path}': {exc}")
            return False
        self.prev_analyzed_input = LoadedTable(path=path, df=df)
        self.lbl_prev_analyzed_file.setText(f"Previous analyzed: {path.name} | rows={len(df)} cols={len(df.columns)}")
        self._refresh_merge_mapping_controls()
        self._auto_fill_merge_mapping()
        self.log.append(f"[raw-merge] previous analyzed loaded '{path.name}' | rows={len(df)} cols={len(df.columns)}")
        return True

    def _auto_fill_raw_mapping(self) -> None:
        if self.raw_input is None:
            QMessageBox.warning(self, "Missing RAW input", "Load a RAW file first.")
            return
        suggestions = suggest_raw_column_mapping(self.raw_input.df)
        applied: list[str] = []
        for expected, field in self._raw_mapping_fields().items():
            src = suggestions.get(expected)
            if src:
                field.setText(str(src))
                applied.append(f"{expected}->{src}")
        self.lbl_raw_status.setText(
            "RAW status: mapping suggestions applied."
            if applied
            else "RAW status: no mapping suggestion found; map required fields manually."
        )
        self.log.append("[raw-mapping] auto-map | " + (", ".join(applied) if applied else "no suggestions"))

    def _parse_optional_float(self, value: Any) -> float | None:
        txt = str(value or "").strip()
        if not txt:
            return None
        txt = txt.replace(" ", "")
        if "," in txt and "." not in txt:
            txt = txt.replace(",", ".")
        try:
            return float(txt)
        except Exception:
            return None

    def _sync_nm_axis_with_raw_method(self, *_args: Any) -> None:
        method = str(self.cb_raw_method.currentData() or "mass_extraction_nm")
        label = _nm_axis_label_from_method(method)
        self.state.set_nm_axis_label(label)

    def _raw_param_widgets(self) -> dict[str, QWidget]:
        return {
            "mass_conc": self.sp_raw_mass_conc,
            "wash_volume": self.sp_raw_wash_ml,
            "sample_mass": self.sp_raw_sample_mass_g,
            "cell_conc": self.sp_raw_cell_conc,
            "area_per_drop": self.sp_raw_area_drop,
            "bet_area": self.sp_raw_bet_area,
            "air_filter_frac": self.sp_raw_air_filter_frac,
            "air_volume_l": self.sp_raw_air_volume_l,
            "filter_area": self.sp_raw_filter_area,
            "drop_area": self.sp_raw_drop_area,
            "custom_dose": self.sp_raw_custom_dose,
            "extra_dilution": self.sp_raw_extra_dilution,
        }

    def _set_param_widget_style(self, widget: QWidget, *, enabled: bool, required: bool) -> None:
        widget.setEnabled(bool(enabled))
        if required and enabled:
            widget.setStyleSheet("border: 1px solid #f59e0b;")
        elif enabled:
            widget.setStyleSheet("border: 1px solid #334155;")
        else:
            widget.setStyleSheet("border: 1px solid #1f2937; color: #64748b;")

    def _on_raw_method_changed(self, *_args: Any) -> None:
        method = str(self.cb_raw_method.currentData() or "mass_extraction_nm")
        required = set(RAW_METHOD_REQUIRED_PARAMS.get(method, set()))
        widgets = self._raw_param_widgets()
        # extra_dilution is currently reserved for future method extensions.
        for key, widget in widgets.items():
            enabled = key in required
            req = key in required
            if key == "extra_dilution":
                enabled = False
                req = False
            label_widget = None
            if hasattr(self, "raw_method_form"):
                label_widget = self.raw_method_form.labelForField(widget)
            widget.setVisible(bool(enabled))
            if label_widget is not None:
                label_widget.setVisible(bool(enabled))
            self._set_param_widget_style(widget, enabled=enabled, required=req)
        pretty_formula = _method_formula_html(method)
        if pretty_formula:
            self.lbl_raw_method_help.setText(
                f"<span style='font-family: Times New Roman, serif; font-size: 15px;'>{pretty_formula}</span>"
            )
        else:
            self.lbl_raw_method_help.setText("Select a normalization method.")
        self._sync_nm_axis_with_raw_method()
        self.log.append(f"[raw-method] method={method} | required={sorted(required)}")

    def _apply_raw_platform_defaults(self, *_args: Any) -> None:
        platform_key = str(self.cb_raw_platform.currentData() or "micro_pinguin")
        if platform_key == "micro_pinguin":
            self.sp_raw_n0.setValue(384)
            self.sp_raw_drop_ul.setValue(30.0)
            self.lbl_raw_platform_note.setText(
                "Micro-PINGUIN defaults applied (N0=384, V=30 µL). You can still edit values if needed."
            )
        else:
            self.lbl_raw_platform_note.setText(
                "Custom setup selected. Set N0 and droplet volume V according to your experiment."
            )
        self.log.append(f"[raw-platform] selected={platform_key}")

    def _refresh_merge_mapping_controls(self) -> None:
        prev_cols = []
        if self.prev_analyzed_input is not None:
            prev_cols = [str(c) for c in self.prev_analyzed_input.df.columns]

        for src, cb in self.merge_map_boxes.items():
            old = str(cb.currentData() or "")
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("Auto", "")
            cb.addItem("Do not import", MERGE_MAPPING_SKIP)
            for c in prev_cols:
                cb.addItem(c, c)
            idx = cb.findData(old)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self._refresh_raw_action_controls()

    def _auto_fill_merge_mapping(self) -> None:
        if self._workflow_mode() != "raw_update_existing":
            self.lbl_merge_status.setText("Merge status: switch workflow mode to 'Update existing analyzed with RAW'.")
            return
        if self.prev_analyzed_input is None:
            self.lbl_merge_status.setText("Merge status: load previous analyzed file first.")
            return
        prev_cols = [str(c) for c in self.prev_analyzed_input.df.columns]
        alias_defaults: dict[str, list[str]] = {
            "Sample.name": ["Sample.name", "Sample_Name", "SampleName", "Content"],
            "Sample_ID": ["Sample_ID", "SampleID", "sample_id"],
            "nm": ["nm", "nM", "NM"],
            "FF": ["FF", "Frozen Fraction", "Frozen.fraction", "Frozen_fraction"],
            "Freezing.temperature": ["Freezing.temperature", "Freeze Temp", "Freezing_temperature", "temperature"],
            "Control": ["Control", "control"],
            "Dilution.factor": ["Dilution.factor", "Dilution", "dilution", "dilution_factor"],
            "Location": ["Location", "location", "Site"],
            "Size": ["Size", "size"],
            "Sample": ["Sample", "sample"],
        }

        applied: list[str] = []
        for src in RAW_ANALYZED_MERGE_FIELDS:
            cb = self.merge_map_boxes[src]
            target = MERGE_MAPPING_SKIP
            for cand in [src] + alias_defaults.get(src, []):
                if cand in prev_cols:
                    target = cand
                    break
            idx = cb.findData(target)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            applied.append(f"{src}->{target}")
        self.lbl_merge_status.setText("Merge status: auto-mapping filled.")
        self.log.append("[raw-merge] auto-map | " + "; ".join(applied))

    def _raw_merge_mapping_payload(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for src in RAW_ANALYZED_MERGE_FIELDS:
            cb = self.merge_map_boxes[src]
            out[src] = str(cb.currentData() or "")
        return out

    def _analyze_raw(self) -> None:
        if self._raw_analyze_running() or self._raw_merge_running():
            QMessageBox.information(self, "RAW workflow", "A RAW task is already running.")
            return
        if self.raw_input is None:
            QMessageBox.warning(self, "Missing RAW input", "Load a RAW file first.")
            return

        method = str(self.cb_raw_method.currentData() or "mass_extraction_nm")
        required = set(RAW_METHOD_REQUIRED_PARAMS.get(method, set()))
        raw_vals = {
            "mass_conc": float(self.sp_raw_mass_conc.value()),
            "cell_conc": float(self.sp_raw_cell_conc.value()),
            "area_per_drop": float(self.sp_raw_area_drop.value()),
            "bet_area": float(self.sp_raw_bet_area.value()),
            "air_filter_frac": float(self.sp_raw_air_filter_frac.value()),
            "air_volume_l": float(self.sp_raw_air_volume_l.value()),
            "filter_area": float(self.sp_raw_filter_area.value()),
            "drop_area": float(self.sp_raw_drop_area.value()),
            "custom_dose": float(self.sp_raw_custom_dose.value()),
        }
        missing_required = sorted([k for k in required if k in raw_vals and (raw_vals.get(k) is None or raw_vals.get(k) <= 0)])
        if missing_required:
            msg = "Missing required RAW method parameters: " + ", ".join(missing_required)
            QMessageBox.warning(self, "RAW method input", msg)
            self.lbl_raw_status.setText(f"RAW status: {msg}")
            self.log.append(f"[raw] {msg}")
            return

        cfg = RawAnalyzeConfig(
            map_sample=self._mapping_value(self.raw_map_sample),
            map_temp=self._mapping_value(self.raw_map_temp),
            map_ff=self._mapping_value(self.raw_map_ff),
            map_size=self._mapping_value(self.raw_map_size),
            map_location=self._mapping_value(self.raw_map_location),
            map_control=self._mapping_value(self.raw_map_control),
            map_dilution=self._mapping_value(self.raw_map_dilution),
            auto_dilution_from_sample=self.chk_raw_auto_dil.isChecked(),
            use_size_grouping=self.chk_raw_use_size.isChecked(),
            size_single_label=self.in_raw_size_single.text().strip(),
            manual_size_value=self.in_raw_manual_size.text().strip(),
            use_location_grouping=self.chk_raw_use_location.isChecked(),
            location_single_label=self.in_raw_location_single.text().strip(),
            manual_location_value=self.in_raw_manual_location.text().strip(),
            auto_control_from_sample=self.chk_raw_auto_ctrl.isChecked(),
            control_detection_keywords=self.in_raw_control_keywords.text().strip(),
            method=method,
            n0=int(self.sp_raw_n0.value()),
            droplet_volume_ul=float(self.sp_raw_drop_ul.value()),
            wash_volume_ml=float(self.sp_raw_wash_ml.value()),
            sample_mass_g=float(self.sp_raw_sample_mass_g.value()),
            extra_dilution_factor=float(self.sp_raw_extra_dilution.value()),
            mass_conc_g_per_ml=raw_vals["mass_conc"],
            cell_conc_per_ml=raw_vals["cell_conc"],
            area_per_drop_m2=raw_vals["area_per_drop"],
            bet_area_m2_per_g=raw_vals["bet_area"],
            air_filter_fraction_x=raw_vals["air_filter_frac"],
            air_sampled_volume_L=raw_vals["air_volume_l"],
            filter_exposed_area=raw_vals["filter_area"],
            droplet_footprint_area=raw_vals["drop_area"],
            custom_dose_per_drop=raw_vals["custom_dose"],
        )

        self.pb_raw_analyze.setValue(0)
        self.lbl_raw_status.setText("RAW status: RUNNING | 0% | starting...")
        self.log.append("[raw] started")
        self._refresh_raw_action_controls()

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_raw_analyze_payload,
            kwargs={"raw_df": self.raw_input.df.copy(), "cfg": cfg},
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_raw_analyze_progress)
        worker.succeeded.connect(self._on_raw_analyze_succeeded)
        worker.failed.connect(self._on_raw_analyze_failed)
        worker.cancelled.connect(self._on_raw_analyze_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_raw_analyze_thread_finished)

        self._raw_analyze_thread = thread
        self._raw_analyze_worker = worker
        thread.start()

    def _on_raw_analyze_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._raw_analyze_worker:
            return
        self.pb_raw_analyze.setValue(int(max(0, min(100, pct))))
        self.lbl_raw_status.setText(f"RAW status: RUNNING | {pct}% | {msg}")

    def _on_raw_analyze_succeeded(self, payload: object) -> None:
        self.pb_raw_analyze.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid RAW analyze payload.")
            out_df = payload.get("df")
            if not isinstance(out_df, pd.DataFrame):
                raise ValueError("RAW analyze result missing dataframe.")
            status = str(payload.get("status") or "")
            axis_label = str(payload.get("axis_label") or "").strip()

            self.raw_analyzed_df = out_df
            self.raw_merged_df = None
            self._refresh_merge_mapping_controls()
            self._render_preview(out_df, max_rows=40)
            self.lbl_raw_status.setText(f"RAW status: {status}")
            self.log.append(f"[raw] {status}")
            if axis_label:
                self.state.set_nm_axis_label(axis_label)
        except Exception as exc:
            self._on_raw_analyze_failed(str(exc))

    def _on_raw_analyze_failed(self, msg: str) -> None:
        self.pb_raw_analyze.setValue(0)
        txt = str(msg or "Unknown error.")
        QMessageBox.critical(self, "RAW analyze error", txt)
        self.lbl_raw_status.setText(f"RAW status: ERROR | {txt}")
        self.log.append(f"[raw] ERROR: {txt}")

    def _on_raw_analyze_cancelled(self, msg: str) -> None:
        self.pb_raw_analyze.setValue(0)
        txt = str(msg or "Cancelled.")
        self.lbl_raw_status.setText(f"RAW status: CANCELLED | {txt}")
        self.log.append(f"[raw] CANCELLED: {txt}")

    def _on_raw_analyze_thread_finished(self) -> None:
        self._raw_analyze_worker = None
        self._raw_analyze_thread = None
        self._refresh_raw_action_controls()

    def _cancel_raw_analyze(self) -> None:
        if self._raw_analyze_worker is None:
            return
        try:
            self._raw_analyze_worker.request_cancel()
            self.lbl_raw_status.setText("RAW status: cancel requested...")
            self.log.append("[raw] cancel requested")
        except Exception as exc:
            self.log.append(f"[raw] cancel request error: {exc}")

    def _use_raw_analyzed_as_active_curves(self) -> None:
        if self.raw_analyzed_df is None:
            QMessageBox.warning(self, "No analyzed RAW data", "Run RAW analysis first.")
            return
        name = "raw_analyzed_vali.csv"
        if self.raw_input is not None:
            name = f"{self.raw_input.path.stem}__analyzed_vali.csv"
        p = Path(name)
        self.state.set_curves_standardized(LoadedTable(path=p, df=self.raw_analyzed_df.copy()))
        self.lbl_raw_status.setText("RAW status: analyzed table set as active curves source.")
        self.log.append("[raw] analyzed table sent to active curves source.")

    def _save_raw_analyzed_csv(self) -> None:
        if self.raw_analyzed_df is None:
            QMessageBox.warning(self, "No analyzed RAW data", "Run RAW analysis first.")
            return
        default_name = "raw_analyzed_vali.csv"
        if self.raw_input is not None:
            default_name = f"{self.raw_input.path.stem}__analyzed_vali.csv"
        target, _ = QFileDialog.getSaveFileName(self, "Save analyzed RAW CSV", default_name, "CSV files (*.csv)")
        if not target:
            return
        self.raw_analyzed_df.to_csv(target, index=False)
        self.log.append(f"[raw] analyzed CSV saved: {target}")

    def _merge_raw_into_previous(self) -> None:
        if self._raw_analyze_running() or self._raw_merge_running():
            QMessageBox.information(self, "RAW workflow", "A RAW task is already running.")
            return
        if self._workflow_mode() != "raw_update_existing":
            QMessageBox.information(
                self,
                "Workflow mode",
                "Merge is available only in 'Update existing analyzed with RAW' mode.",
            )
            return
        if self.raw_analyzed_df is None:
            QMessageBox.warning(self, "Missing RAW analysis", "Run RAW analysis first.")
            return
        if self.prev_analyzed_input is None:
            QMessageBox.warning(self, "Missing previous analyzed file", "Load the previous analyzed file first.")
            return
        merge_map = self._raw_merge_mapping_payload()
        self.pb_raw_merge.setValue(0)
        self.lbl_merge_status.setText("Merge status: RUNNING | 0% | starting...")
        self.log.append("[raw-merge] started")
        self._refresh_raw_action_controls()

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_raw_merge_payload,
            kwargs={
                "prev_df": self.prev_analyzed_input.df.copy(),
                "new_df": self.raw_analyzed_df.copy(),
                "raw_to_prev_map": merge_map,
            },
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_raw_merge_progress)
        worker.succeeded.connect(self._on_raw_merge_succeeded)
        worker.failed.connect(self._on_raw_merge_failed)
        worker.cancelled.connect(self._on_raw_merge_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_raw_merge_thread_finished)

        self._raw_merge_thread = thread
        self._raw_merge_worker = worker
        thread.start()

    def _on_raw_merge_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._raw_merge_worker:
            return
        self.pb_raw_merge.setValue(int(max(0, min(100, pct))))
        self.lbl_merge_status.setText(f"Merge status: RUNNING | {pct}% | {msg}")

    def _on_raw_merge_succeeded(self, payload: object) -> None:
        self.pb_raw_merge.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid RAW merge payload.")
            merged = payload.get("df")
            if not isinstance(merged, pd.DataFrame):
                raise ValueError("RAW merge result missing dataframe.")
            msg = str(payload.get("status") or "")
            self.raw_merged_df = merged
            self._render_preview(merged, max_rows=40)
            self.lbl_merge_status.setText(f"Merge status: {msg}")
            self.log.append(f"[raw-merge] {msg}")
        except Exception as exc:
            self._on_raw_merge_failed(str(exc))

    def _on_raw_merge_failed(self, msg: str) -> None:
        self.pb_raw_merge.setValue(0)
        txt = str(msg or "Unknown error.")
        QMessageBox.critical(self, "RAW merge error", txt)
        self.lbl_merge_status.setText(f"Merge status: ERROR | {txt}")
        self.log.append(f"[raw-merge] ERROR: {txt}")

    def _on_raw_merge_cancelled(self, msg: str) -> None:
        self.pb_raw_merge.setValue(0)
        txt = str(msg or "Cancelled.")
        self.lbl_merge_status.setText(f"Merge status: CANCELLED | {txt}")
        self.log.append(f"[raw-merge] CANCELLED: {txt}")

    def _on_raw_merge_thread_finished(self) -> None:
        self._raw_merge_worker = None
        self._raw_merge_thread = None
        self._refresh_raw_action_controls()

    def _cancel_raw_merge(self) -> None:
        if self._raw_merge_worker is None:
            return
        try:
            self._raw_merge_worker.request_cancel()
            self.lbl_merge_status.setText("Merge status: cancel requested...")
            self.log.append("[raw-merge] cancel requested")
        except Exception as exc:
            self.log.append(f"[raw-merge] cancel request error: {exc}")

    def _use_raw_merged_as_active_curves(self) -> None:
        if self._workflow_mode() != "raw_update_existing":
            QMessageBox.information(
                self,
                "Workflow mode",
                "Merged output is available only in 'Update existing analyzed with RAW' mode.",
            )
            return
        if self.raw_merged_df is None:
            QMessageBox.warning(self, "No merged table", "Run merge first.")
            return
        name = "merged_analyzed_curves.csv"
        if self.prev_analyzed_input is not None and self.raw_input is not None:
            name = f"{self.prev_analyzed_input.path.stem}__updated_with__{self.raw_input.path.stem}.csv"
        self.state.set_curves_standardized(LoadedTable(path=Path(name), df=self.raw_merged_df.copy()))
        self.lbl_merge_status.setText("Merge status: merged table set as active curves source.")
        self.log.append("[raw-merge] merged table sent to active curves source.")

    def _save_raw_merged_csv(self) -> None:
        if self._workflow_mode() != "raw_update_existing":
            QMessageBox.information(
                self,
                "Workflow mode",
                "Merged CSV save is available only in 'Update existing analyzed with RAW' mode.",
            )
            return
        if self.raw_merged_df is None:
            QMessageBox.warning(self, "No merged table", "Run merge first.")
            return
        default_name = "merged_analyzed_curves.csv"
        if self.prev_analyzed_input is not None and self.raw_input is not None:
            default_name = f"{self.prev_analyzed_input.path.stem}__updated_with__{self.raw_input.path.stem}.csv"
        target, _ = QFileDialog.getSaveFileName(self, "Save merged analyzed CSV", default_name, "CSV files (*.csv)")
        if not target:
            return
        self.raw_merged_df.to_csv(target, index=False)
        self.log.append(f"[raw-merge] merged CSV saved: {target}")

    def _mapping_value(self, field: QWidget) -> str | None:
        if isinstance(field, QComboBox):
            v = str(field.currentData() or field.currentText() or "").strip()
            return v if v else None
        if isinstance(field, QLineEdit):
            v = field.text().strip()
            return v if v else None
        return None

    def _set_mapping_field_value(self, field: QWidget, value: Any) -> None:
        txt = str(value or "").strip()
        if isinstance(field, QComboBox):
            if not txt:
                field.setCurrentIndex(0 if field.count() > 0 else -1)
                return
            idx = field.findData(txt)
            if idx < 0:
                idx = field.findText(txt)
            if idx < 0:
                field.addItem(txt, txt)
                idx = field.findData(txt)
            if idx >= 0:
                field.setCurrentIndex(idx)
            return
        if isinstance(field, QLineEdit):
            field.setText(txt)

    def _refresh_curves_mapping_dropdowns(self) -> None:
        cols: list[str] = []
        if self.state.curves_raw is not None:
            cols = [str(c) for c in self.state.curves_raw.df.columns]
        for field in self._mapping_fields().values():
            if not isinstance(field, QComboBox):
                continue
            current = self._mapping_value(field) or ""
            field.blockSignals(True)
            field.clear()
            field.addItem("Auto / infer", "")
            for c in cols:
                field.addItem(c, c)
            if current:
                idx = field.findData(current)
                if idx < 0:
                    field.addItem(current, current)
                    idx = field.findData(current)
                if idx >= 0:
                    field.setCurrentIndex(idx)
            else:
                field.setCurrentIndex(0)
            field.blockSignals(False)

    def _mapping_fields(self) -> dict[str, QComboBox]:
        return {
            "Sample": self.map_sample,
            "Size": self.map_size,
            "Location": self.map_location,
            "Freezing.temperature": self.map_temp,
            "nm": self.map_nm,
            "Control": self.map_control,
            "Dilution.factor": self.map_dilution,
            "FF": self.map_ff,
        }

    def _auto_fill_mapping(self) -> None:
        if self.state.curves_raw is None:
            QMessageBox.warning(self, "Missing input", "Load a curves file first.")
            return
        self._refresh_curves_mapping_dropdowns()
        suggestions = suggest_curves_column_mapping(self.state.curves_raw.df)
        fields = self._mapping_fields()
        applied: list[str] = []
        for expected, field in fields.items():
            src = suggestions.get(expected)
            if src:
                self._set_mapping_field_value(field, src)
                applied.append(f"{expected}->{src}")
        self.log.append(
            "[curves-mapping] auto-map applied | "
            + (", ".join(applied) if applied else "no suggestions found")
        )

    def _clear_mapping_fields(self) -> None:
        for field in self._mapping_fields().values():
            if isinstance(field, QComboBox):
                field.setCurrentIndex(0 if field.count() > 0 else -1)
            else:
                field.clear()
        self.log.append("[curves-mapping] mapping fields cleared.")

    def _suggest_metadata_sample_col(self, df: pd.DataFrame) -> str | None:
        cols = [str(c) for c in df.columns]
        if "Sample" in cols:
            return "Sample"
        canon = {re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()): str(c) for c in cols}
        for cand in ["sample", "sample_name", "sample_id", "sample_name_id"]:
            if cand in canon:
                return canon[cand]
        return cols[0] if cols else None

    def _metadata_mapping_source(self) -> LoadedTable | None:
        if self._metadata_upload_raw is not None:
            return self._metadata_upload_raw
        return self.state.metadata

    def _mark_metadata_mapping_dirty(self) -> None:
        # Keep mapping changes explicit/stable: user confirms with "Apply metadata mapping".
        self.lbl_meta_map_status.setText("Metadata mapping changed. Click 'Apply metadata mapping'.")

    def _queue_metadata_mapping_apply(self) -> None:
        # Backward-compatible entrypoint used by older call-sites.
        self._mark_metadata_mapping_dirty()

    def _auto_fill_metadata_mapping(self) -> None:
        try:
            source = self._metadata_mapping_source()
            if source is None:
                self.lbl_meta_map_status.setText("Metadata mapping: load metadata first.")
                return
            df = source.df
            cols = [str(c) for c in df.columns]
            if len(cols) == 0:
                self.lbl_meta_map_status.setText("Metadata mapping: metadata has no columns.")
                return
            self.cb_meta_sample.blockSignals(True)
            self.meta_cols_box.list.blockSignals(True)
            self.cb_meta_sample.clear()
            self.cb_meta_sample.addItems(cols)
            guess = self._suggest_metadata_sample_col(df)
            if guess:
                idx = self.cb_meta_sample.findText(guess)
                if idx >= 0:
                    self.cb_meta_sample.setCurrentIndex(idx)
            self.meta_cols_box.set_items(cols, select_all=True)
            self.cb_meta_sample.blockSignals(False)
            self.meta_cols_box.list.blockSignals(False)
            self.lbl_meta_map_status.setText(
                f"Metadata mapping: suggested Sample='{guess or '(none)'}' | columns={len(cols)}"
            )
            self.log.append(f"[metadata-mapping] auto-map suggested Sample='{guess}'")
            # Apply once immediately after auto-suggestion to mirror app.py's
            # upload->mapping->publish sequence and avoid stale metadata state.
            self._apply_metadata_mapping()
        except Exception as exc:
            txt = str(exc)
            self.lbl_meta_map_status.setText(f"Metadata mapping error: {txt}")
            self.log.append(f"[metadata-mapping] auto-map ERROR: {txt}")

    def _apply_metadata_mapping(self) -> None:
        if self._meta_map_applying:
            return
        source = self._metadata_mapping_source()
        if source is None:
            self.lbl_meta_map_status.setText("Metadata mapping: load metadata first.")
            return
        df = source.df.copy()
        if len(df.columns) == 0:
            self.lbl_meta_map_status.setText("Metadata mapping: metadata has no columns.")
            return

        try:
            self._meta_map_applying = True
            cols_all = [str(c) for c in df.columns]
            sample_src = self.cb_meta_sample.currentText().strip()
            if not sample_src or sample_src not in cols_all:
                guess = self._suggest_metadata_sample_col(df)
                if guess and guess in cols_all:
                    sample_src = guess
                    idx = self.cb_meta_sample.findText(sample_src)
                    if idx >= 0:
                        self.cb_meta_sample.blockSignals(True)
                        self.cb_meta_sample.setCurrentIndex(idx)
                        self.cb_meta_sample.blockSignals(False)
                else:
                    raise ValueError("Select a valid metadata Sample column.")

            selected_raw = self.meta_cols_box.selected_values()
            if selected_raw is None or len(selected_raw) == 0:
                selected_raw = cols_all[:]
            else:
                selected_raw = [str(c) for c in list(selected_raw) if str(c) in cols_all]

            cols_keep = list(dict.fromkeys([str(c) for c in selected_raw]))
            if sample_src in cols_all and sample_src not in cols_keep:
                cols_keep.append(sample_src)

            df_for_mapping = df[cols_keep].copy() if len(cols_keep) > 0 else df.iloc[:, 0:0].copy()
            if sample_src != "Sample":
                df_for_mapping["Sample"] = df[sample_src]

            keep_cols: list[str] = ["Sample"]
            for c in cols_keep:
                mapped_c = "Sample" if c == sample_src else c
                if mapped_c in df_for_mapping.columns and mapped_c not in keep_cols:
                    keep_cols.append(mapped_c)

            df2 = df_for_mapping[keep_cols].copy()
            df2 = df2.loc[:, ~df2.columns.duplicated()]
            # Avoid re-entrant heavy refresh chains while Data Upload is still
            # finalizing mapping UI. Publish metadata object first, then emit
            # metadata_changed in the next event-loop tick.
            self.state.metadata = LoadedTable(path=source.path, df=df2)
            QTimer.singleShot(0, self.state.metadata_changed.emit)
            self._render_preview(df2)
            self.lbl_meta_map_status.setText(
                f"Metadata mapping applied | Sample<={sample_src} | selected_metadata_cols={max(len(df2.columns) - 1, 0)}"
            )
            self.log.append(
                f"[metadata-mapping] applied | sample_src={sample_src} | cols={list(df2.columns)}"
            )
        except Exception as exc:
            txt = str(exc)
            self.lbl_meta_map_status.setText(f"Metadata mapping error: {txt}")
            self.log.append(f"[metadata-mapping] ERROR: {txt}")
        finally:
            self._meta_map_applying = False

    def _apply_nm_method_selection(self) -> None:
        method = str(self.cb_nm_method.currentData() or "auto")
        label = None
        if method == "auto":
            table = self.state.curves_standardized if self.state.curves_standardized is not None else self.state.curves_raw
            if table is not None:
                label = _extract_nm_axis_label_from_df(table.df)
        if not label:
            label = _nm_axis_label_from_method(method if method != "auto" else "mass_extraction_nm")
        label = _coerce_nm_axis_label(label)
        self.state.set_nm_axis_label(label)
        self.lbl_nm_axis.setText(f"Current Y-axis label: {_format_math_exponents(label)}")
        self.lbl_nm_method_detail.setText(_nm_method_detail_text(method))
        self.log.append(f"[nm-axis] method={method} -> label='{label}'")

    def _build_equation_reference_html(self, *, selected_nm_method: str, selected_raw_method: str) -> str:
        rows: list[str] = []
        rows.append("<h2>nM / RAW Equation Reference</h2>")
        rows.append(
            "<p>This reference mirrors the normalization methods used by the desktop migration backend. "
            "Units and formulas below are the same ones used to compute axis labels and RAW analysis outputs.</p>"
        )
        rows.append("<h3>nM axis methods</h3>")
        for i in range(self.cb_nm_method.count()):
            lbl = str(self.cb_nm_method.itemText(i))
            m = str(self.cb_nm_method.itemData(i) or "")
            key = NM_METHOD_TO_WORKFLOW_KEY.get(m, m)
            units = _format_math_exponents(_nm_axis_label_from_method(key if key else m))
            formula = _method_formula_html(key)
            note = str(NM_METHOD_EXTRA_NOTE.get(m, "")).strip()
            selected_tag = " <b>[selected]</b>" if m == selected_nm_method else ""
            rows.append(f"<p><b>{html_lib.escape(lbl)}</b>{selected_tag}<br><b>Units:</b> {units}</p>")
            if formula:
                rows.append(f"<p><b>Formula:</b> <span style='font-family: Times New Roman, serif; font-size: 15px;'>{formula}</span></p>")
            if note:
                rows.append(f"<p><i>{html_lib.escape(note)}</i></p>")
        rows.append("<h3>RAW normalization methods</h3>")
        for lbl, method in RAW_WORKFLOW_METHOD_OPTIONS:
            req = sorted(RAW_METHOD_REQUIRED_PARAMS.get(str(method), set()))
            req_txt = _required_param_symbols_html(req)
            formula = _method_formula_html(str(method))
            units = _format_math_exponents(_nm_axis_label_from_method(str(method)))
            selected_tag = " <b>[selected]</b>" if str(method) == selected_raw_method else ""
            rows.append(
                f"<p><b>{html_lib.escape(lbl)}</b>{selected_tag}<br>"
                f"<b>Units:</b> {units}<br>"
                f"<b>Required parameters:</b> {req_txt}</p>"
            )
            if formula:
                rows.append(f"<p><b>Formula:</b> <span style='font-family: Times New Roman, serif; font-size: 15px;'>{formula}</span></p>")
        return "\n".join(rows)

    def _show_reference_dialog(self, title: str, html: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(920, 700)
        lay = QVBoxLayout(dlg)
        txt = QTextEdit(dlg)
        txt.setReadOnly(True)
        txt.setHtml(str(html))
        lay.addWidget(txt)
        btn_close = QPushButton("Close", dlg)
        btn_close.clicked.connect(dlg.accept)
        lay.addWidget(btn_close)
        dlg.exec()

    def _open_nm_method_reference(self) -> None:
        nm_method = str(self.cb_nm_method.currentData() or "auto")
        raw_method = str(self.cb_raw_method.currentData() or "mass_extraction_nm")
        html = self._build_equation_reference_html(
            selected_nm_method=nm_method,
            selected_raw_method=raw_method,
        )
        self._show_reference_dialog("nM Equation Reference", html)

    def _open_raw_method_reference(self) -> None:
        nm_method = str(self.cb_nm_method.currentData() or "auto")
        raw_method = str(self.cb_raw_method.currentData() or "mass_extraction_nm")
        html = self._build_equation_reference_html(
            selected_nm_method=nm_method,
            selected_raw_method=raw_method,
        )
        self._show_reference_dialog("RAW Method Reference", html)

    def _reset_session(self) -> None:
        self._shutdown_background_threads()
        self.state.set_curves_raw(None)
        self.state.set_metadata(None)
        self.state.set_nm_axis_label(DEFAULT_NM_AXIS_LABEL)
        self.raw_input = None
        self.prev_analyzed_input = None
        self.raw_analyzed_df = None
        self.raw_merged_df = None
        self._metadata_upload_raw = None
        self.pb_standardize.setValue(0)
        self.pb_raw_analyze.setValue(0)
        self.pb_raw_merge.setValue(0)
        self._render_preview(pd.DataFrame(), max_rows=20)
        self._clear_mapping_fields()
        self._clear_raw_mapping_fields()
        self.lbl_raw_file.setText("RAW input: not loaded")
        self.lbl_prev_analyzed_file.setText("Previous analyzed: not loaded")
        self.lbl_raw_status.setText("RAW status: waiting")
        self.lbl_merge_status.setText("Merge status: waiting")
        self._refresh_merge_mapping_controls()
        self.cb_meta_sample.clear()
        self.meta_cols_box.set_items([], select_all=False)
        self.lbl_meta_map_status.setText("Metadata mapping: waiting")
        self._apply_workflow_mode_ui()
        self.log.append("[session] reset complete (curves, standardized curves, metadata cleared).")

    def _standardize_curves(self) -> None:
        if self._std_running():
            QMessageBox.information(self, "Run in progress", "Standardization is already running.")
            return
        if self.state.curves_raw is None:
            QMessageBox.warning(self, "Missing input", "Load a curves file first.")
            return

        cfg = CurvesMappingConfig(
            map_sample=self._mapping_value(self.map_sample),
            map_size=self._mapping_value(self.map_size),
            map_location=self._mapping_value(self.map_location),
            map_temp=self._mapping_value(self.map_temp),
            map_nm=self._mapping_value(self.map_nm),
            map_control=self._mapping_value(self.map_control),
            map_dilution=self._mapping_value(self.map_dilution),
            map_ff=self._mapping_value(self.map_ff),
            use_size_grouping=self.chk_size_grouping.isChecked(),
            manual_size_value=self.in_manual_size.text().strip(),
            use_location_grouping=self.chk_location_grouping.isChecked(),
            manual_location_value=self.in_manual_location.text().strip(),
            auto_dilution_from_sample=self.chk_auto_dil.isChecked(),
            auto_control_from_sample=self.chk_auto_ctrl.isChecked(),
            control_detection_keywords=self.in_control_keywords.text().strip(),
        )

        kwargs = dict(raw_df=self.state.curves_raw.df.copy(), cfg=cfg)
        self.pb_standardize.setValue(0)
        self.lbl_standardized.setText("Standardized curves: running...")
        self._refresh_standardize_controls()
        self.log.append("[curves-standardize] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_standardize_curves_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_standardize_progress)
        worker.succeeded.connect(self._on_standardize_succeeded)
        worker.failed.connect(self._on_standardize_failed)
        worker.cancelled.connect(self._on_standardize_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_standardize_thread_finished)

        self._std_thread = thread
        self._std_worker = worker
        thread.start()

    def _on_standardize_progress(self, pct: int, _msg: str) -> None:
        if self.sender() is not self._std_worker:
            return
        self.pb_standardize.setValue(int(max(0, min(100, pct))))

    def _on_standardize_succeeded(self, payload: object) -> None:
        self.pb_standardize.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid standardization payload.")
            out = payload.get("out")
            warnings = payload.get("warnings")
            resolved = payload.get("resolved")
            if not isinstance(out, pd.DataFrame):
                raise ValueError("Standardization output dataframe missing.")
            if not isinstance(warnings, list):
                warnings = []
            if not isinstance(resolved, dict):
                resolved = {}

            if self.state.curves_raw is None:
                raise ValueError("Curves source changed during standardization.")
            std_path = self.state.curves_raw.path.with_suffix("")
            self.state.set_curves_standardized(LoadedTable(path=std_path, df=out))
            self._render_preview(out)

            self.log.append(
                "[curves-standardize] ok | "
                f"rows={len(out)} cols={len(out.columns)} | resolved={resolved}\n"
                + (f"warnings={len(warnings)}: {' ; '.join([str(w) for w in warnings[:3]])}" if warnings else "warnings=0")
                + "\n"
            )
        except Exception as exc:
            self._on_standardize_failed(str(exc))

    def _on_standardize_failed(self, msg: str) -> None:
        txt = str(msg or "Unknown error.")
        self.pb_standardize.setValue(0)
        QMessageBox.critical(self, "Standardization error", txt)
        self.lbl_standardized.setText(f"Standardized curves: ERROR | {txt}")
        self.log.append(f"[curves-standardize] ERROR: {txt}\n")

    def _on_standardize_cancelled(self, msg: str) -> None:
        txt = str(msg or "Cancelled.")
        self.pb_standardize.setValue(0)
        self.lbl_standardized.setText(f"Standardized curves: CANCELLED | {txt}")
        self.log.append(f"[curves-standardize] CANCELLED: {txt}")

    def _on_standardize_thread_finished(self) -> None:
        self._std_worker = None
        self._std_thread = None
        self._refresh_standardize_controls()

    def _cancel_standardize(self) -> None:
        if self._std_worker is None:
            return
        try:
            self._std_worker.request_cancel()
            self.log.append("[curves-standardize] cancel requested")
        except Exception as exc:
            self.log.append(f"[curves-standardize] cancel request error: {exc}")

    def _render_preview(self, df: pd.DataFrame, max_rows: int = 25) -> None:
        _render_table(self.preview, df, max_rows=max_rows)

    def _on_state_curves_changed(self) -> None:
        raw = self.state.curves_raw
        std = self.state.curves_standardized
        self._refresh_curves_mapping_dropdowns()
        if raw is None:
            self.lbl_curves.setText("Curves: not loaded")
        else:
            self.lbl_curves.setText(
                f"Curves: {raw.path.name} | rows={len(raw.df)} cols={len(raw.df.columns)}"
            )
        if std is None:
            self.lbl_standardized.setText("Standardized curves: not generated")
            if raw is None:
                self.lbl_active_source.setText("Active curves source for panels: none")
            else:
                self.lbl_active_source.setText("Active curves source for panels: raw curves")
        else:
            self.lbl_standardized.setText(
                f"Standardized curves: rows={len(std.df)} cols={len(std.df.columns)}"
            )
            self.lbl_active_source.setText("Active curves source for panels: standardized curves")
        self._refresh_standardize_controls()
        if self.cb_nm_method.currentData() == "auto":
            self._apply_nm_method_selection()

    def _on_state_metadata_changed(self) -> None:
        meta = self.state.metadata
        if meta is None:
            self.lbl_metadata.setText("Metadata: not loaded")
            if self._metadata_upload_raw is None:
                self.cb_meta_sample.clear()
                self.meta_cols_box.set_items([], select_all=False)
        else:
            self.lbl_metadata.setText(
                f"Metadata: {meta.path.name} | rows={len(meta.df)} cols={len(meta.df.columns)}"
            )

    def _on_nm_axis_label_changed(self) -> None:
        self.lbl_nm_axis.setText(f"Current Y-axis label: {_format_math_exponents(self.state.nm_axis_label)}")

    def _shutdown_background_threads(self) -> None:
        self._meta_map_applying = False
        try:
            if self._std_worker is not None:
                self._std_worker.request_cancel()
        except Exception:
            pass
        try:
            if self._raw_analyze_worker is not None:
                self._raw_analyze_worker.request_cancel()
        except Exception:
            pass
        try:
            if self._raw_merge_worker is not None:
                self._raw_merge_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._std_thread)
        _stop_qthread(self._raw_analyze_thread)
        _stop_qthread(self._raw_merge_thread)
        self._std_worker = None
        self._std_thread = None
        self._raw_analyze_worker = None
        self._raw_analyze_thread = None
        self._raw_merge_worker = None
        self._raw_merge_thread = None
        self._refresh_standardize_controls()
        self._refresh_raw_action_controls()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)

    def _set_combo_by_data(self, combo: QComboBox, value: Any) -> None:
        target = str(value or "").strip()
        if not target:
            return
        idx = combo.findData(target)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def export_session_state(self) -> dict[str, Any]:
        mappings = {k: str(self._mapping_value(v) or "").strip() for k, v in self._mapping_fields().items()}
        raw_mappings = {k: v.text().strip() for k, v in self._raw_mapping_fields().items()}
        merge_map = {k: str(cb.currentData() or "").strip() for k, cb in self.merge_map_boxes.items()}
        selected_meta_cols = [str(v) for v in self.meta_cols_box.selected_values()]

        return {
            "workflow_mode": self._workflow_mode(),
            "files": {
                "curves_path": str(self.state.curves_raw.path) if self.state.curves_raw is not None else "",
                "metadata_path": str(self.state.metadata.path) if self.state.metadata is not None else "",
                "raw_input_path": str(self.raw_input.path) if self.raw_input is not None else "",
                "previous_analyzed_path": str(self.prev_analyzed_input.path) if self.prev_analyzed_input is not None else "",
            },
            "curves_mapping": mappings,
            "curves_options": {
                "use_size_grouping": bool(self.chk_size_grouping.isChecked()),
                "manual_size": self.in_manual_size.text().strip(),
                "use_location_grouping": bool(self.chk_location_grouping.isChecked()),
                "manual_location": self.in_manual_location.text().strip(),
                "auto_dilution": bool(self.chk_auto_dil.isChecked()),
                "auto_control": bool(self.chk_auto_ctrl.isChecked()),
                "control_keywords": self.in_control_keywords.text().strip(),
            },
            "raw_mapping": raw_mappings,
            "raw_options": {
                "use_size_grouping": bool(self.chk_raw_use_size.isChecked()),
                "size_single_label": self.in_raw_size_single.text().strip(),
                "manual_size": self.in_raw_manual_size.text().strip(),
                "use_location_grouping": bool(self.chk_raw_use_location.isChecked()),
                "location_single_label": self.in_raw_location_single.text().strip(),
                "manual_location": self.in_raw_manual_location.text().strip(),
                "auto_dilution": bool(self.chk_raw_auto_dil.isChecked()),
                "auto_control": bool(self.chk_raw_auto_ctrl.isChecked()),
                "control_keywords": self.in_raw_control_keywords.text().strip(),
            },
            "raw_method": {
                "platform": str(self.cb_raw_platform.currentData() or ""),
                "method": str(self.cb_raw_method.currentData() or ""),
                "n0": int(self.sp_raw_n0.value()),
                "droplet_ul": float(self.sp_raw_drop_ul.value()),
                "wash_ml": float(self.sp_raw_wash_ml.value()),
                "sample_mass_g": float(self.sp_raw_sample_mass_g.value()),
                "extra_dilution": float(self.sp_raw_extra_dilution.value()),
                "mass_conc": float(self.sp_raw_mass_conc.value()),
                "cell_conc": float(self.sp_raw_cell_conc.value()),
                "area_drop": float(self.sp_raw_area_drop.value()),
                "bet_area": float(self.sp_raw_bet_area.value()),
                "air_filter_frac": float(self.sp_raw_air_filter_frac.value()),
                "air_volume_l": float(self.sp_raw_air_volume_l.value()),
                "filter_area": float(self.sp_raw_filter_area.value()),
                "drop_area": float(self.sp_raw_drop_area.value()),
                "custom_dose": float(self.sp_raw_custom_dose.value()),
            },
            "merge_mapping": merge_map,
            "metadata_mapping": {
                "sample_col": self.cb_meta_sample.currentText().strip(),
                "selected_cols": selected_meta_cols,
                "apply_on_restore": bool(len(selected_meta_cols) > 0),
            },
            "nm_method": str(self.cb_nm_method.currentData() or "auto"),
            "restore_standardized": bool(self.state.curves_standardized is not None),
        }

    def restore_session_state(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        def _as_bool(v: Any, default: bool = False) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            txt = str(v or "").strip().lower()
            if txt in {"1", "true", "yes", "y", "on"}:
                return True
            if txt in {"0", "false", "no", "n", "off"}:
                return False
            return default

        def _as_int(v: Any, default: int) -> int:
            try:
                return int(float(v))
            except Exception:
                return int(default)

        def _as_float(v: Any, default: float) -> float:
            try:
                return float(v)
            except Exception:
                return float(default)

        mode = str(payload.get("workflow_mode", "")).strip()
        if mode:
            self._set_combo_by_data(self.cb_workflow_mode, mode)
            self._apply_workflow_mode_ui()

        # Intentionally do not auto-load files on startup restore:
        # users should explicitly load datasets every session.

        mappings = payload.get("curves_mapping", {}) if isinstance(payload.get("curves_mapping"), dict) else {}
        for k, field in self._mapping_fields().items():
            self._set_mapping_field_value(field, mappings.get(k, ""))

        copt = payload.get("curves_options", {}) if isinstance(payload.get("curves_options"), dict) else {}
        self.chk_size_grouping.setChecked(_as_bool(copt.get("use_size_grouping"), True))
        self.in_manual_size.setText(str(copt.get("manual_size", self.in_manual_size.text())).strip())
        self.chk_location_grouping.setChecked(_as_bool(copt.get("use_location_grouping"), True))
        self.in_manual_location.setText(str(copt.get("manual_location", self.in_manual_location.text())).strip())
        self.chk_auto_dil.setChecked(_as_bool(copt.get("auto_dilution"), True))
        self.chk_auto_ctrl.setChecked(_as_bool(copt.get("auto_control"), True))
        self.in_control_keywords.setText(str(copt.get("control_keywords", self.in_control_keywords.text())).strip())

        raw_map = payload.get("raw_mapping", {}) if isinstance(payload.get("raw_mapping"), dict) else {}
        for k, field in self._raw_mapping_fields().items():
            field.setText(str(raw_map.get(k, "")).strip())

        ropt = payload.get("raw_options", {}) if isinstance(payload.get("raw_options"), dict) else {}
        self.chk_raw_use_size.setChecked(_as_bool(ropt.get("use_size_grouping"), True))
        self.in_raw_size_single.setText(str(ropt.get("size_single_label", self.in_raw_size_single.text())).strip())
        self.in_raw_manual_size.setText(str(ropt.get("manual_size", self.in_raw_manual_size.text())).strip())
        self.chk_raw_use_location.setChecked(_as_bool(ropt.get("use_location_grouping"), True))
        self.in_raw_location_single.setText(str(ropt.get("location_single_label", self.in_raw_location_single.text())).strip())
        self.in_raw_manual_location.setText(str(ropt.get("manual_location", self.in_raw_manual_location.text())).strip())
        self.chk_raw_auto_dil.setChecked(_as_bool(ropt.get("auto_dilution"), True))
        self.chk_raw_auto_ctrl.setChecked(_as_bool(ropt.get("auto_control"), True))
        self.in_raw_control_keywords.setText(str(ropt.get("control_keywords", self.in_raw_control_keywords.text())).strip())

        rmethod = payload.get("raw_method", {}) if isinstance(payload.get("raw_method"), dict) else {}
        self._set_combo_by_data(self.cb_raw_platform, rmethod.get("platform"))
        self._set_combo_by_data(self.cb_raw_method, rmethod.get("method"))
        self.sp_raw_n0.setValue(max(1, _as_int(rmethod.get("n0"), int(self.sp_raw_n0.value()))))
        self.sp_raw_drop_ul.setValue(max(0.001, _as_float(rmethod.get("droplet_ul"), float(self.sp_raw_drop_ul.value()))))
        self.sp_raw_wash_ml.setValue(max(0.001, _as_float(rmethod.get("wash_ml"), float(self.sp_raw_wash_ml.value()))))
        self.sp_raw_sample_mass_g.setValue(
            max(0.0001, _as_float(rmethod.get("sample_mass_g"), float(self.sp_raw_sample_mass_g.value())))
        )
        self.sp_raw_extra_dilution.setValue(max(0.001, _as_float(rmethod.get("extra_dilution"), float(self.sp_raw_extra_dilution.value()))))
        self.sp_raw_mass_conc.setValue(max(0.000001, _as_float(rmethod.get("mass_conc"), float(self.sp_raw_mass_conc.value()))))
        self.sp_raw_cell_conc.setValue(max(1.0, _as_float(rmethod.get("cell_conc"), float(self.sp_raw_cell_conc.value()))))
        self.sp_raw_area_drop.setValue(max(0.000000001, _as_float(rmethod.get("area_drop"), float(self.sp_raw_area_drop.value()))))
        self.sp_raw_bet_area.setValue(max(0.000001, _as_float(rmethod.get("bet_area"), float(self.sp_raw_bet_area.value()))))
        self.sp_raw_air_filter_frac.setValue(max(0.0001, _as_float(rmethod.get("air_filter_frac"), float(self.sp_raw_air_filter_frac.value()))))
        self.sp_raw_air_volume_l.setValue(max(0.001, _as_float(rmethod.get("air_volume_l"), float(self.sp_raw_air_volume_l.value()))))
        self.sp_raw_filter_area.setValue(max(0.000001, _as_float(rmethod.get("filter_area"), float(self.sp_raw_filter_area.value()))))
        self.sp_raw_drop_area.setValue(max(0.000001, _as_float(rmethod.get("drop_area"), float(self.sp_raw_drop_area.value()))))
        self.sp_raw_custom_dose.setValue(max(0.000001, _as_float(rmethod.get("custom_dose"), float(self.sp_raw_custom_dose.value()))))
        self._on_raw_method_changed()

        merge_map = payload.get("merge_mapping", {}) if isinstance(payload.get("merge_mapping"), dict) else {}
        if merge_map:
            for src, cb in self.merge_map_boxes.items():
                want = str(merge_map.get(src, "")).strip()
                idx = cb.findData(want)
                if idx >= 0:
                    cb.setCurrentIndex(idx)

        mmap = payload.get("metadata_mapping", {}) if isinstance(payload.get("metadata_mapping"), dict) else {}
        sample_col = str(mmap.get("sample_col", "")).strip()
        if sample_col:
            idx = self.cb_meta_sample.findText(sample_col)
            if idx >= 0:
                self.cb_meta_sample.setCurrentIndex(idx)
        selected_cols = mmap.get("selected_cols", [])
        if isinstance(selected_cols, list) and len(selected_cols) > 0:
            self.meta_cols_box.set_selected_values([str(c) for c in selected_cols])
        if _as_bool(mmap.get("apply_on_restore"), False) and self.state.metadata is not None:
            try:
                self._apply_metadata_mapping()
            except Exception as exc:
                self.log.append(f"[session-restore] metadata mapping apply skipped: {exc}")

        nm_method = str(payload.get("nm_method", "")).strip()
        if nm_method:
            self._set_combo_by_data(self.cb_nm_method, nm_method)
        self._apply_nm_method_selection()

        restore_standardized = _as_bool(payload.get("restore_standardized"), False)
        if restore_standardized and self.state.curves_raw is not None and self.state.curves_standardized is None:
            try:
                self._standardize_curves()
            except Exception as exc:
                self.log.append(f"[session-restore] standardization replay skipped: {exc}")

        self.log.append("[session-restore] Data Upload state restored.")


class FreezingCurvesTab(QWidget):
    REQUIRED_COLUMNS = [
        "Sample",
        "Size",
        "Location",
        "Control",
        "Dilution.factor",
        "Freezing.temperature",
        "nm",
    ]

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._last_main_fig: go.Figure | None = None
        self._last_mean_fig: go.Figure | None = None
        self._fc_thread: QThread | None = None
        self._fc_worker: LongTaskWorker | None = None
        self._shape_boxes: dict[str, QComboBox] = {}
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)
        self._on_state_changed()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)
        self.lbl_missing = QLabel("")
        self.lbl_missing.setWordWrap(True)
        left_lay.addWidget(self.lbl_missing)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Update Curves")
        self.btn_run.clicked.connect(self._run_curves)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_fc_run)
        run_row.addWidget(self.btn_cancel)

        self.size_box = MultiSelectBox("Size")
        self.dil_box = MultiSelectBox("Dilution.factor")
        self.loc_box = MultiSelectBox("Location")
        self.loc_box.list.itemSelectionChanged.connect(self._on_location_selection_changed)
        left_lay.addWidget(self.size_box)
        left_lay.addWidget(self.dil_box)
        left_lay.addWidget(self.loc_box)

        self.shape_group = QGroupBox("Location shapes")
        self.shape_form = QFormLayout(self.shape_group)
        left_lay.addWidget(self.shape_group)

        self.chk_include_controls = QCheckBox("Include control curves")
        self.chk_include_controls.setChecked(False)
        left_lay.addWidget(self.chk_include_controls)

        style_box = QGroupBox("Plot style")
        style_lay = QFormLayout(style_box)
        self.cb_palette = QComboBox()
        _init_palette_combo(self.cb_palette, include_default=True, default_value="set1")
        style_lay.addRow("Palette", self.cb_palette)

        row_ps, self.sl_point_size, _ = _make_labeled_slider(min_value=2, max_value=16, value=5, decimals=0, step=1)
        style_lay.addRow("Point size", row_ps)

        row_bw, self.sl_point_border, _ = _make_labeled_slider(
            min_value=0, max_value=300, value=30, decimals=2, step=5
        )
        style_lay.addRow("Point border width", row_bw)

        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_lay.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_lay.addRow("Y-axis tick style", self.cb_tick_style)

        self.sp_fc_main_height = SliderNumberInput(min_value=320, max_value=1200, value=520, decimals=0, step=20)
        style_lay.addRow("Main plot height", self.sp_fc_main_height)

        self.sp_fc_mean_height = SliderNumberInput(min_value=260, max_value=1000, value=430, decimals=0, step=20)
        style_lay.addRow("Mean plot height", self.sp_fc_mean_height)

        self.in_plot_title = QLineEdit("Freezing Curves")
        style_lay.addRow("Main title", self.in_plot_title)
        self.in_plot_subtitle = QLineEdit("")
        style_lay.addRow("Main subtitle", self.in_plot_subtitle)
        left_lay.addWidget(style_box)

        cfg_box = QGroupBox("Mean ± CI settings")
        cfg_lay = QFormLayout(cfg_box)

        self.cb_group = QComboBox()
        self.cb_group.addItems(["Location", "Size", "Dilution.plot"])
        cfg_lay.addRow("Group by", self.cb_group)

        self.cb_ci_method = QComboBox()
        self.cb_ci_method.addItem("Legacy (mean log ± 1.96×SE)", "legacy")
        self.cb_ci_method.addItem("Kaplan–Meier (Whale et al., 2026)", "kaplan_meier")
        cfg_lay.addRow("CI method", self.cb_ci_method)

        self.sp_step = SliderNumberInput(min_value=0.05, max_value=5.0, value=0.25, decimals=2, step=0.05)
        cfg_lay.addRow("Temp step (°C)", self.sp_step)

        self.sp_smooth = SliderNumberInput(min_value=0.0, max_value=1.0, value=0.35, decimals=2, step=0.05)
        cfg_lay.addRow("Smooth (0..1)", self.sp_smooth)

        self.sp_min_curves = SliderNumberInput(min_value=1, max_value=20, value=2, decimals=0, step=1)
        cfg_lay.addRow("Min curves / group", self.sp_min_curves)

        left_lay.addWidget(cfg_box)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_main = PlotExportBox("Main curves export", default_stem="freezing_curves")
        self.export_main.btn_export.clicked.connect(self._export_main_plot)
        left_lay.addWidget(self.export_main)

        self.export_mean = PlotExportBox("Mean ± CI export", default_stem="freezing_curves_mean_ci")
        self.export_mean.btn_export.clicked.connect(self._export_mean_plot)
        left_lay.addWidget(self.export_mean)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        main_panel = QWidget()
        main_panel_lay = QVBoxLayout(main_panel)
        main_panel_lay.setContentsMargins(0, 0, 0, 0)
        main_panel_lay.setSpacing(6)
        self.main_plot = QWebEngineView()
        self.main_plot.setMinimumHeight(int(self.sp_fc_main_height.value()))
        main_panel_lay.addWidget(QLabel("Freezing curves (nm vs Freezing.temperature)"))
        main_panel_lay.addWidget(self.main_plot, stretch=1)

        mean_panel = QWidget()
        mean_panel_lay = QVBoxLayout(mean_panel)
        mean_panel_lay.setContentsMargins(0, 0, 0, 0)
        mean_panel_lay.setSpacing(6)
        self.mean_plot = QWebEngineView()
        self.mean_plot.setMinimumHeight(int(self.sp_fc_mean_height.value()))
        mean_panel_lay.addWidget(QLabel("Mean curves ± 95% CI"))
        mean_panel_lay.addWidget(self.mean_plot, stretch=1)

        right_scroll = _build_vertical_scroll_stack(
            [main_panel, mean_panel],
            min_width=780,
            spacing=10,
            add_stretch=True,
        )

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=360,
            max_width=520,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1230])

    def _shape_symbol_options(self) -> list[tuple[str, str]]:
        return [
            ("circle", "circle"),
            ("square", "square"),
            ("diamond", "diamond"),
            ("x", "x"),
            ("triangle-up", "triangle-up"),
            ("triangle-down", "triangle-down"),
            ("cross", "cross"),
            ("star", "star"),
            ("hexagon", "hexagon"),
            ("pentagon", "pentagon"),
        ]

    def _refresh_location_shape_controls(self, locations: list[str]) -> None:
        previous = {loc: str(cb.currentData() or "") for loc, cb in self._shape_boxes.items()}
        while self.shape_form.rowCount() > 0:
            self.shape_form.removeRow(0)
        self._shape_boxes = {}

        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, loc in enumerate(locations):
            cb = QComboBox()
            for lbl, val in options:
                cb.addItem(lbl, val)
            desired = previous.get(str(loc), default_symbols[i % len(default_symbols)])
            idx = cb.findData(desired)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            self.shape_form.addRow(str(loc), cb)
            self._shape_boxes[str(loc)] = cb
        self.shape_group.setVisible(len(locations) > 0)

    def _current_location_shape_map(self, locations: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, loc in enumerate(locations):
            key = str(loc)
            cb = self._shape_boxes.get(key)
            if cb is not None:
                out[key] = str(cb.currentData() or default_symbols[i % len(default_symbols)])
            else:
                out[key] = default_symbols[i % len(default_symbols)]
        return out

    def _on_location_selection_changed(self) -> None:
        selected = self.loc_box.selected_values()
        self._refresh_location_shape_controls(selected)

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _fc_run_running(self) -> bool:
        return self._fc_thread is not None and self._fc_thread.isRunning()

    def _set_fc_busy(self, busy: bool) -> None:
        running = self._fc_run_running()
        if busy or running:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.export_main.setEnabled(False)
            self.export_mean.setEnabled(False)
            return
        table = self._active_curves_table()
        has_ready = False
        if table is not None:
            missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
            has_ready = len(missing) == 0
        self.btn_run.setEnabled(has_ready)
        self.btn_cancel.setEnabled(False)
        self.export_main.setEnabled(has_ready)
        self.export_mean.setEnabled(has_ready)

    def _clear_fc_outputs(self) -> None:
        self._last_main_fig = None
        self._last_mean_fig = None
        self.main_plot.setHtml("")
        self.mean_plot.setHtml("")

    def _on_state_changed(self) -> None:
        if self._fc_run_running():
            self.log.append("[fc] source changed while run active: cancelling previous run.")
            self._cancel_fc_run()
            _stop_qthread(self._fc_thread)
            self._fc_worker = None
            self._fc_thread = None
        table = self._active_curves_table()
        if table is None:
            self.lbl_source.setText("Source: none (load curves first)")
            self.size_box.set_items([], select_all=False)
            self.dil_box.set_items([], select_all=False)
            self.loc_box.set_items([], select_all=False)
            self._refresh_location_shape_controls([])
            self.lbl_missing.setText("")
            self._set_fc_busy(False)
            return

        source_kind = "standardized curves" if self.state.curves_standardized is not None else "raw curves"
        self.lbl_source.setText(
            f"Source: {source_kind} | {table.path.name or '(in-memory)'} | "
            f"rows={len(table.df)} cols={len(table.df.columns)}"
        )

        missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
        if missing:
            self.lbl_missing.setText(
                "Missing required columns for Freezing Curves: "
                f"{missing}. Run standardization/mapping in Data Upload tab."
            )
        else:
            self.lbl_missing.setText("")
        self._set_fc_busy(False)

        opts = available_fc_options(table.df)
        self.size_box.set_items(opts.get("sizes", []), select_all=True)
        self.dil_box.set_items(opts.get("dilutions", []), select_all=True)
        self.loc_box.set_items(opts.get("locations", []), select_all=True)
        self._refresh_location_shape_controls(self.loc_box.selected_values())

    def _run_curves(self) -> None:
        if self._fc_run_running():
            QMessageBox.information(self, "Run in progress", "Freezing Curves is already running.")
            return
        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return

        selected_sizes = self.size_box.selected_values() or self.size_box.values()
        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        selected_locs = self.loc_box.selected_values() or self.loc_box.values()

        group_col = self.cb_group.currentText().strip() or "Location"
        filter_dict = dict(
            selected_sizes=selected_sizes,
            selected_dilutions=selected_dils,
            selected_locations=selected_locs,
            include_controls=self.chk_include_controls.isChecked(),
        )
        mean_cfg_dict = dict(
            group_col=group_col,
            ci_method=str(self.cb_ci_method.currentData() or "legacy"),
            temp_step=float(self.sp_step.value()),
            smooth=float(self.sp_smooth.value()),
            min_curves_per_group=int(self.sp_min_curves.value()),
        )
        kwargs = dict(
            curves_df=table.df.copy(),
            filter_dict=filter_dict,
            mean_cfg_dict=mean_cfg_dict,
            nm_axis_label=self.state.nm_axis_label,
        )

        self._set_fc_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append("[fc] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_freezing_curves_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_fc_progress)
        worker.succeeded.connect(self._on_fc_succeeded)
        worker.failed.connect(self._on_fc_failed)
        worker.cancelled.connect(self._on_fc_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_fc_thread_finished)

        self._fc_thread = thread
        self._fc_worker = worker
        thread.start()

    def _on_fc_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._fc_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _apply_fc_payload(self, payload: dict[str, Any]) -> None:
        points = payload.get("points")
        if not isinstance(points, pd.DataFrame):
            raise ValueError("Invalid Freezing Curves payload: missing points.")
        status = str(payload.get("status") or "")
        mean_df = payload.get("mean_df")
        if not isinstance(mean_df, pd.DataFrame):
            mean_df = pd.DataFrame()
        mean_status = str(payload.get("mean_status") or "")
        y_title = _coerce_nm_axis_label(payload.get("y_title") or self.state.nm_axis_label)

        if len(points) == 0:
            self.lbl_status.setText(f"Status: No points after filtering | {status}")
            self.log.append(f"[fc] no points | {status}")
            self._clear_fc_outputs()
            return

        color_by = self._draw_main_curves_plotly(points, y_title=y_title)
        group_col = self.cb_group.currentText().strip() or "Location"
        self._draw_mean_plotly(mean_df, group_col=group_col, y_title=y_title)

        final_status = f"{status} | colored_by={color_by} | mean_ci: {mean_status}"
        self.lbl_status.setText(f"Status: OK | {final_status}")
        self.log.append(f"[fc] {final_status}")

    def _on_fc_succeeded(self, payload: object) -> None:
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid Freezing Curves worker payload.")
            self._apply_fc_payload(payload)
        except Exception as exc:
            self._on_fc_failed(str(exc))

    def _on_fc_failed(self, msg: str) -> None:
        txt = str(msg or "Unknown error.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: ERROR rendering curves | {txt}")
        self.log.append(f"[fc] ERROR rendering: {txt}")
        self._clear_fc_outputs()

    def _on_fc_cancelled(self, msg: str) -> None:
        txt = str(msg or "Cancelled.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[fc] CANCELLED: {txt}")

    def _on_fc_thread_finished(self) -> None:
        self._fc_worker = None
        self._fc_thread = None
        self._set_fc_busy(False)

    def _cancel_fc_run(self) -> None:
        if self._fc_worker is None:
            return
        try:
            self._fc_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[fc] cancel requested")
        except Exception as exc:
            self.log.append(f"[fc] cancel request error: {exc}")

    def _draw_main_curves_plotly(self, points: pd.DataFrame, *, y_title: str) -> str:
        title_html = _compose_optional_plot_title(self.in_plot_title.text(), self.in_plot_subtitle.text())
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(title_html))
        plot_h = int(max(320, min(1200, int(self.sp_fc_main_height.value()))))
        self.main_plot.setMinimumHeight(plot_h)
        fig.update_layout(height=plot_h, legend_title_text="")

        palette = _palette_hex_named(self.cb_palette.currentData() or "default")
        locations = sorted(points["Location"].astype(str).unique().tolist())
        symbol_map = self._current_location_shape_map(locations)
        border_w = float(self.sl_point_border.value()) / 100.0
        point_size = int(self.sl_point_size.value())

        sizes_u = sorted(points["Size"].astype(str).unique().tolist())
        samples_u = sorted(points["Sample"].astype(str).unique().tolist())
        if (len(locations) <= 1) and (len(sizes_u) <= 1) and (len(samples_u) > 1):
            color_by = "Sample"
        elif (len(locations) == 1) and (len(sizes_u) > 1):
            color_by = "Size"
        else:
            color_by = "Location"

        color_levels = sorted(points[color_by].astype(str).unique().tolist())
        color_map = {lvl: palette[i % len(palette)] for i, lvl in enumerate(color_levels)}

        for col_key in color_levels:
            g = points[points[color_by].astype(str) == str(col_key)]
            if len(g) == 0:
                continue
            for loc, gg in g.groupby("Location", sort=False):
                sym = symbol_map.get(str(loc), "circle")
                name = str(col_key) if color_by == "Location" else f"{col_key} | {loc}"
                fig.add_trace(
                    go.Scattergl(
                        x=gg["Freezing.temperature"],
                        y=gg["nm"],
                        mode="markers",
                        name=name,
                        marker=dict(
                            color=color_map.get(str(col_key), palette[0]),
                            symbol=sym,
                            size=point_size,
                            opacity=0.75,
                            line=dict(color="#111827", width=border_w),
                        ),
                        customdata=np.stack(
                            [
                                gg["Sample"].astype(str).to_numpy(),
                                gg["Size"].astype(str).to_numpy(),
                                gg["Dilution.factor"].astype(str).to_numpy(),
                                gg["Location"].astype(str).to_numpy(),
                            ],
                            axis=1,
                        ),
                        hovertemplate=(
                            "Sample=%{customdata[0]}<br>Size=%{customdata[1]}<br>Dilution=%{customdata[2]}<br>"
                            "Location=%{customdata[3]}<br>nm=%{y:.3g}<br>T=%{x:.2f}°C<extra></extra>"
                        ),
                    )
                )

        _style_axes(fig, x_title="Temperature (°C)", y_title=y_title, y_type="log")
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=True,
        )
        self._last_main_fig = go.Figure(fig)
        _set_plotly_html(self.main_plot, fig)
        return color_by

    def _draw_mean_plotly(self, mean_df: pd.DataFrame, *, group_col: str, y_title: str) -> None:
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(f"Mean Curves ±95% CI by {group_col}"))
        plot_h = int(max(260, min(1000, int(self.sp_fc_mean_height.value()))))
        self.mean_plot.setMinimumHeight(plot_h)
        fig.update_layout(height=plot_h, legend_title_text=str(group_col))

        if len(mean_df) == 0:
            fig.add_annotation(
                text="No valid mean summary for current filters.",
                xref="paper",
                yref="paper",
                x=0.01,
                y=0.95,
                showarrow=False,
            )
            self._last_mean_fig = go.Figure(fig)
            _set_plotly_html(self.mean_plot, fig)
            return

        palette = _palette_hex_named(self.cb_palette.currentData() or "default")
        groups = sorted(mean_df["group"].astype(str).unique().tolist())
        color_map = {g: palette[i % len(palette)] for i, g in enumerate(groups)}
        ci_method_values = set(mean_df.get("ci_method", pd.Series(dtype=str)).astype(str).str.lower().unique().tolist())
        km_style = any(v in {"kaplan_meier", "kaplan-meier", "km", "article"} for v in ci_method_values)

        for grp, d in mean_df.groupby("group", dropna=False):
            s = d.sort_values("Freezing.temperature", ascending=True).copy()
            if len(s) < 2:
                continue
            col = color_map.get(str(grp), palette[0])
            x = s["Freezing.temperature"].to_numpy(dtype=float)
            y = s["mean_nm"].to_numpy(dtype=float)
            lo = s["low_nm"].to_numpy(dtype=float)
            up = s["up_nm"].to_numpy(dtype=float)

            if km_style:
                # Paper-like KM view: step curve + CI bounds (log-log KM interval transformed to nm).
                fig.add_trace(
                    go.Scatter(
                        x=np.concatenate([x, x[::-1]]),
                        y=np.concatenate([up, lo[::-1]]),
                        fill="toself",
                        fillcolor=_rgba_from_color(col, 0.10),
                        line=dict(color="rgba(0,0,0,0)"),
                        hoverinfo="skip",
                        showlegend=False,
                        name=str(grp),
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=up,
                        mode="lines",
                        line=dict(color=_rgba_from_color(col, 0.65), width=1.2, dash="dot"),
                        line_shape="hv",
                        name=f"{grp} CI upper",
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=lo,
                        mode="lines",
                        line=dict(color=_rgba_from_color(col, 0.65), width=1.2, dash="dot"),
                        line_shape="hv",
                        name=f"{grp} CI lower",
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=y,
                        mode="lines",
                        line=dict(color=col, width=2.2),
                        line_shape="hv",
                        name=str(grp),
                        hovertemplate=(
                            f"group={grp}<br>T=%{{x:.2f}}°C<br>KM mean nm=%{{y:.3g}}"
                            "<extra></extra>"
                        ),
                    )
                )
            else:
                # Legacy view: smooth central trend + continuous ribbon.
                fig.add_trace(
                    go.Scatter(
                        x=np.concatenate([x, x[::-1]]),
                        y=np.concatenate([up, lo[::-1]]),
                        fill="toself",
                        fillcolor=_rgba_from_color(col, 0.16),
                        line=dict(color="rgba(0,0,0,0)"),
                        hoverinfo="skip",
                        showlegend=False,
                        name=str(grp),
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=y,
                        mode="lines",
                        line=dict(color=col, width=2.5),
                        name=str(grp),
                        hovertemplate=f"group={grp}<br>T=%{{x:.2f}}°C<br>mean nm=%{{y:.3g}}<extra></extra>",
                    )
                )

        _style_axes(fig, x_title="Temperature (°C)", y_title=y_title, y_type="log")
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=True,
        )
        self._last_mean_fig = go.Figure(fig)
        _set_plotly_html(self.mean_plot, fig)

    def _on_nm_axis_label_changed(self) -> None:
        self.log.append(f"[fc] nm axis label updated: {self.state.nm_axis_label}")

    def _export_main_plot(self) -> None:
        if self._last_main_fig is None:
            QMessageBox.information(self, "No plot", "Run Freezing Curves first to generate the main plot.")
            return
        try:
            cfg = self.export_main.config()
            saved = _save_plotly_figure_local(self, self._last_main_fig, cfg)
            if saved is not None:
                self.log.append(f"[fc] main export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported main plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[fc] main export error: {exc}")

    def _export_mean_plot(self) -> None:
        if self._last_mean_fig is None:
            QMessageBox.information(self, "No plot", "Run Freezing Curves first to generate the mean ± CI plot.")
            return
        try:
            cfg = self.export_mean.config()
            saved = _save_plotly_figure_local(self, self._last_mean_fig, cfg)
            if saved is not None:
                self.log.append(f"[fc] mean export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported mean ± CI plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[fc] mean export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._fc_worker is not None:
                self._fc_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._fc_thread)
        self._fc_worker = None
        self._fc_thread = None
        self._set_fc_busy(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


class CompareSamplesTab(QWidget):
    REQUIRED_COLUMNS = [
        "Sample",
        "Size",
        "Freezing.temperature",
        "nm",
        "Control",
        "Dilution.factor",
    ]

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._last_plot_fig: go.Figure | None = None
        self._cmp_thread: QThread | None = None
        self._cmp_worker: LongTaskWorker | None = None
        self._shape_boxes: dict[str, QComboBox] = {}
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)
        self._on_state_changed()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)

        self.lbl_missing = QLabel("")
        self.lbl_missing.setWordWrap(True)
        left_lay.addWidget(self.lbl_missing)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Update Compare FC")
        self.btn_run.clicked.connect(self._run_compare)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_cmp_run)
        run_row.addWidget(self.btn_cancel)

        self.sample_box = MultiSelectBox("Sample")
        self.size_box = MultiSelectBox("Size")
        self.dil_box = MultiSelectBox("Dilution.factor")
        self.sample_box.list.itemSelectionChanged.connect(self._on_sample_selection_changed)
        left_lay.addWidget(self.sample_box)
        left_lay.addWidget(self.size_box)
        left_lay.addWidget(self.dil_box)

        self.shape_group = QGroupBox("Sample shapes")
        self.shape_form = QFormLayout(self.shape_group)
        left_lay.addWidget(self.shape_group)

        style_box = QGroupBox("Plot style")
        style_lay = QFormLayout(style_box)
        self.cb_palette = QComboBox()
        _init_palette_combo(self.cb_palette, include_default=True, default_value="set1")
        style_lay.addRow("Palette", self.cb_palette)

        row_ps, self.sl_point_size, _ = _make_labeled_slider(min_value=2, max_value=16, value=7, decimals=0, step=1)
        style_lay.addRow("Point size", row_ps)

        row_bw, self.sl_point_border, _ = _make_labeled_slider(
            min_value=0, max_value=300, value=30, decimals=2, step=5
        )
        style_lay.addRow("Point border width", row_bw)

        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(True)
        style_lay.addRow("Grid", self.chk_grid)

        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_lay.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_lay.addRow("Y-axis tick style", self.cb_tick_style)

        self.in_plot_title = QLineEdit("Compare Samples FC")
        style_lay.addRow("Main title", self.in_plot_title)
        self.in_plot_subtitle = QLineEdit("")
        style_lay.addRow("Main subtitle", self.in_plot_subtitle)
        left_lay.addWidget(style_box)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_plot = PlotExportBox("Compare plot export", default_stem="compare_samples_fc")
        self.export_plot.btn_export.clicked.connect(self._export_plot)
        left_lay.addWidget(self.export_plot)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        plot_panel = QWidget()
        plot_panel_lay = QVBoxLayout(plot_panel)
        plot_panel_lay.setContentsMargins(0, 0, 0, 0)
        plot_panel_lay.setSpacing(6)
        plot_panel_lay.addWidget(QLabel("Compare Samples FC (nm vs Freezing.temperature)"))
        self.plot_view = QWebEngineView()
        self.plot_view.setMinimumHeight(460)
        plot_panel_lay.addWidget(self.plot_view, stretch=1)

        right_scroll = _build_vertical_scroll_stack(
            [plot_panel],
            min_width=760,
            spacing=10,
            add_stretch=True,
        )

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=360,
            max_width=520,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1230])

    def _shape_symbol_options(self) -> list[tuple[str, str]]:
        return [
            ("circle", "circle"),
            ("square", "square"),
            ("diamond", "diamond"),
            ("x", "x"),
            ("triangle-up", "triangle-up"),
            ("triangle-down", "triangle-down"),
            ("cross", "cross"),
            ("star", "star"),
            ("hexagon", "hexagon"),
            ("pentagon", "pentagon"),
        ]

    def _refresh_sample_shape_controls(self, samples: list[str]) -> None:
        previous = {sample: str(cb.currentData() or "") for sample, cb in self._shape_boxes.items()}
        while self.shape_form.rowCount() > 0:
            self.shape_form.removeRow(0)
        self._shape_boxes = {}

        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, sample in enumerate(samples):
            cb = QComboBox()
            for lbl, val in options:
                cb.addItem(lbl, val)
            desired = previous.get(str(sample), default_symbols[i % len(default_symbols)])
            idx = cb.findData(desired)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            self.shape_form.addRow(str(sample), cb)
            self._shape_boxes[str(sample)] = cb

        self.shape_group.setVisible(len(samples) > 0)

    def _current_sample_shape_map(self, samples: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, sample in enumerate(samples):
            s = str(sample)
            cb = self._shape_boxes.get(s)
            if cb is not None:
                out[s] = str(cb.currentData() or default_symbols[i % len(default_symbols)])
            else:
                out[s] = default_symbols[i % len(default_symbols)]
        return out

    def _on_sample_selection_changed(self) -> None:
        selected = self.sample_box.selected_values()
        self._refresh_sample_shape_controls(selected)

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _cmp_run_running(self) -> bool:
        return self._cmp_thread is not None and self._cmp_thread.isRunning()

    def _set_cmp_busy(self, busy: bool) -> None:
        running = self._cmp_run_running()
        if busy or running:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.export_plot.setEnabled(False)
            return
        table = self._active_curves_table()
        has_ready = False
        if table is not None:
            missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
            has_ready = len(missing) == 0
        self.btn_run.setEnabled(has_ready)
        self.btn_cancel.setEnabled(False)
        self.export_plot.setEnabled(has_ready)

    def _clear_cmp_outputs(self) -> None:
        self._last_plot_fig = None
        self.plot_view.setHtml("")

    def _on_state_changed(self) -> None:
        if self._cmp_run_running():
            self.log.append("[cmp] source changed while run active: cancelling previous run.")
            self._cancel_cmp_run()
            _stop_qthread(self._cmp_thread)
            self._cmp_worker = None
            self._cmp_thread = None
        table = self._active_curves_table()
        if table is None:
            self.lbl_source.setText("Source: none (load curves first)")
            self.sample_box.set_items([], select_all=False)
            self.size_box.set_items([], select_all=False)
            self.dil_box.set_items([], select_all=False)
            self._refresh_sample_shape_controls([])
            self.lbl_missing.setText("")
            self._set_cmp_busy(False)
            return

        source_kind = "standardized curves" if self.state.curves_standardized is not None else "raw curves"
        self.lbl_source.setText(
            f"Source: {source_kind} | {table.path.name or '(in-memory)'} | "
            f"rows={len(table.df)} cols={len(table.df.columns)}"
        )

        missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
        if missing:
            self.lbl_missing.setText(
                "Missing required columns for Compare Samples FC: "
                f"{missing}. Run standardization/mapping in Data Upload tab."
            )
        else:
            self.lbl_missing.setText("")
        self._set_cmp_busy(False)

        opts = available_cmp_options(table.df)
        self.sample_box.set_items(opts.get("samples", []), select_all=False)
        self.size_box.set_items(opts.get("sizes", []), select_all=True)
        self.dil_box.set_items(opts.get("dilutions", []), select_all=True)
        self._refresh_sample_shape_controls(self.sample_box.selected_values())

    def _run_compare(self) -> None:
        if self._cmp_run_running():
            QMessageBox.information(self, "Run in progress", "Compare Samples FC is already running.")
            return
        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return

        selected_samples = self.sample_box.selected_values()
        selected_sizes = self.size_box.selected_values() or self.size_box.values()
        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        flt_dict = dict(
            selected_samples=selected_samples,
            selected_sizes=selected_sizes,
            selected_dilutions=selected_dils,
        )
        kwargs = dict(curves_df=table.df.copy(), filter_dict=flt_dict)

        self._set_cmp_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append("[cmp] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_compare_samples_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_cmp_progress)
        worker.succeeded.connect(self._on_cmp_succeeded)
        worker.failed.connect(self._on_cmp_failed)
        worker.cancelled.connect(self._on_cmp_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_cmp_thread_finished)

        self._cmp_thread = thread
        self._cmp_worker = worker
        thread.start()

    def _on_cmp_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._cmp_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _apply_cmp_payload(self, payload: dict[str, Any]) -> None:
        points = payload.get("points")
        if not isinstance(points, pd.DataFrame):
            raise ValueError("Invalid Compare Samples payload: missing points.")
        status = str(payload.get("status") or "")
        color_by = str(payload.get("color_by") or "Sample")
        y_title = _coerce_nm_axis_label(self.state.nm_axis_label)

        if len(points) == 0:
            self._draw_compare_chart(points, color_by=color_by, y_title=y_title)
            self.lbl_status.setText(f"Status: No points after filtering | {status}")
            self.log.append(f"[cmp] no points | {status}")
            return

        self._draw_compare_chart(points, color_by=color_by, y_title=y_title)

        self.lbl_status.setText(f"Status: OK | {status}")
        self.log.append(f"[cmp] {status}")

    def _on_cmp_succeeded(self, payload: object) -> None:
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid Compare Samples worker payload.")
            self._apply_cmp_payload(payload)
        except Exception as exc:
            self._on_cmp_failed(str(exc))

    def _on_cmp_failed(self, msg: str) -> None:
        txt = str(msg or "Unknown error.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: ERROR rendering compare plot | {txt}")
        self.log.append(f"[cmp] ERROR rendering: {txt}")
        self._clear_cmp_outputs()

    def _on_cmp_cancelled(self, msg: str) -> None:
        txt = str(msg or "Cancelled.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[cmp] CANCELLED: {txt}")

    def _on_cmp_thread_finished(self) -> None:
        self._cmp_worker = None
        self._cmp_thread = None
        self._set_cmp_busy(False)

    def _cancel_cmp_run(self) -> None:
        if self._cmp_worker is None:
            return
        try:
            self._cmp_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[cmp] cancel requested")
        except Exception as exc:
            self.log.append(f"[cmp] cancel request error: {exc}")

    def _draw_compare_chart(self, points: pd.DataFrame, *, color_by: str, y_title: str) -> None:
        title_html = _compose_optional_plot_title(self.in_plot_title.text(), self.in_plot_subtitle.text())
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(title_html))
        fig.update_layout(height=500, legend_title_text=color_by)

        if len(points) == 0:
            self._last_plot_fig = go.Figure(fig)
            _set_plotly_html(self.plot_view, fig)
            return

        palette = _palette_hex_named(self.cb_palette.currentData() or "default")
        color_levels = sorted(points[color_by].astype(str).unique().tolist())
        color_map = {k: palette[i % len(palette)] for i, k in enumerate(color_levels)}
        samples = sorted(points["Sample"].astype(str).unique().tolist())
        sample_symbol = self._current_sample_shape_map(samples)
        point_size = int(self.sl_point_size.value())
        border_w = float(self.sl_point_border.value()) / 100.0
        show_grid = bool(self.chk_grid.isChecked())

        for level in color_levels:
            g = points[points[color_by].astype(str) == str(level)]
            for sample, gg in g.groupby("Sample", sort=False):
                if len(gg) == 0:
                    continue
                name = str(level) if color_by == "Sample" else f"{level} | {sample}"
                fig.add_trace(
                    go.Scattergl(
                        x=gg["Freezing.temperature"],
                        y=gg["nm"],
                        mode="markers",
                        name=name,
                        marker=dict(
                            color=color_map.get(str(level), palette[0]),
                            symbol=sample_symbol.get(str(sample), "circle"),
                            size=point_size,
                            opacity=0.82,
                            line=dict(color="#111827", width=border_w),
                        ),
                        customdata=np.stack(
                            [
                                gg["Sample"].astype(str).to_numpy(),
                                gg["Size"].astype(str).to_numpy(),
                                gg["Dilution.plot"].astype(str).to_numpy(),
                                gg["Location"].astype(str).to_numpy(),
                            ],
                            axis=1,
                        ),
                        hovertemplate=(
                            "Sample=%{customdata[0]}<br>Size=%{customdata[1]}<br>Dilution=%{customdata[2]}<br>"
                            "Location=%{customdata[3]}<br>nm=%{y:.3g}<br>T=%{x:.2f}°C<extra></extra>"
                        ),
                    )
                )

        _style_axes(fig, x_title="Temperature (°C)", y_title=y_title, y_type="log")
        fig.update_xaxes(showgrid=show_grid)
        fig.update_yaxes(showgrid=show_grid)
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=True,
        )
        self._last_plot_fig = go.Figure(fig)
        _set_plotly_html(self.plot_view, fig)

    def _on_nm_axis_label_changed(self) -> None:
        self.log.append(f"[cmp] nm axis label updated: {self.state.nm_axis_label}")

    def _export_plot(self) -> None:
        if self._last_plot_fig is None:
            QMessageBox.information(self, "No plot", "Run Compare Samples FC first.")
            return
        try:
            cfg = self.export_plot.config()
            saved = _save_plotly_figure_local(self, self._last_plot_fig, cfg)
            if saved is not None:
                self.log.append(f"[cmp] export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported compare plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[cmp] export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._cmp_worker is not None:
                self._cmp_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._cmp_thread)
        self._cmp_worker = None
        self._cmp_thread = None
        self._set_cmp_busy(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


class FrozenFractionTab(QWidget):
    REQUIRED_COLUMNS = ["Sample", "Size", "Freezing.temperature", "FF", "Control", "Dilution.factor"]

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._last_plot_fig: go.Figure | None = None
        self._ff_thread: QThread | None = None
        self._ff_worker: LongTaskWorker | None = None
        self._ff_run_mode: str = "multiple"
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)
        self._on_state_changed()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)

        self.lbl_missing = QLabel("")
        self.lbl_missing.setWordWrap(True)
        left_lay.addWidget(self.lbl_missing)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Update Frozen Fraction")
        self.btn_run.clicked.connect(self._run_ff)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_ff_run)
        run_row.addWidget(self.btn_cancel)

        self.sample_box = MultiSelectBox("Sample")
        self.size_box = MultiSelectBox("Size")
        self.dil_box = MultiSelectBox("Dilution.factor")

        left_lay.addWidget(self.sample_box)
        left_lay.addWidget(self.size_box)
        left_lay.addWidget(self.dil_box)

        self.cb_mode = QComboBox()
        self.cb_mode.addItem("Single sample file (combine all dilutions)", "single")
        self.cb_mode.addItem("Multiple samples in file (select sample + dilutions)", "multiple")
        left_lay.addWidget(QLabel("File interpretation (Frozen Fraction)"))
        left_lay.addWidget(self.cb_mode)

        self.chk_show_control = QCheckBox("Show control points")
        self.chk_show_control.setChecked(True)
        left_lay.addWidget(self.chk_show_control)

        style_box = QGroupBox("Plot style")
        style_lay = QFormLayout(style_box)
        self.cb_palette = QComboBox()
        _init_palette_combo(self.cb_palette, include_default=True, default_value="set1")
        style_lay.addRow("Palette", self.cb_palette)

        row_ps, self.sl_point_size, _ = _make_labeled_slider(min_value=2, max_value=16, value=7, decimals=0, step=1)
        style_lay.addRow("Point size", row_ps)

        row_bw, self.sl_point_border, _ = _make_labeled_slider(
            min_value=0, max_value=300, value=50, decimals=2, step=5
        )
        style_lay.addRow("Point border width", row_bw)

        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(True)
        style_lay.addRow("Grid", self.chk_grid)

        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_lay.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_lay.addRow("Y-axis tick style", self.cb_tick_style)

        ff_symbols = [
            ("circle", "circle"),
            ("square", "square"),
            ("diamond", "diamond"),
            ("x", "x"),
            ("triangle-up", "triangle-up"),
            ("triangle-down", "triangle-down"),
            ("square-open", "square-open"),
            ("circle-open", "circle-open"),
            ("diamond-open", "diamond-open"),
        ]
        self.cb_symbol_sample = QComboBox()
        self.cb_symbol_control = QComboBox()
        for lbl, val in ff_symbols:
            self.cb_symbol_sample.addItem(lbl, val)
            self.cb_symbol_control.addItem(lbl, val)
        i_sample = self.cb_symbol_sample.findData("circle")
        i_ctrl = self.cb_symbol_control.findData("square-open")
        self.cb_symbol_sample.setCurrentIndex(i_sample if i_sample >= 0 else 0)
        self.cb_symbol_control.setCurrentIndex(i_ctrl if i_ctrl >= 0 else 0)
        style_lay.addRow("Symbol (sample)", self.cb_symbol_sample)
        style_lay.addRow("Symbol (control)", self.cb_symbol_control)

        self.in_plot_title = QLineEdit("Frozen Fraction")
        style_lay.addRow("Main title", self.in_plot_title)
        self.in_plot_subtitle = QLineEdit("")
        style_lay.addRow("Main subtitle", self.in_plot_subtitle)
        left_lay.addWidget(style_box)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_plot = PlotExportBox("Frozen Fraction export", default_stem="frozen_fraction")
        self.export_plot.btn_export.clicked.connect(self._export_plot)
        left_lay.addWidget(self.export_plot)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        plot_panel = QWidget()
        plot_panel_lay = QVBoxLayout(plot_panel)
        plot_panel_lay.setContentsMargins(0, 0, 0, 0)
        plot_panel_lay.setSpacing(6)
        plot_panel_lay.addWidget(QLabel("Frozen Fraction (FF vs Freezing.temperature)"))
        self.plot_view = QWebEngineView()
        self.plot_view.setMinimumHeight(460)
        plot_panel_lay.addWidget(self.plot_view, stretch=1)

        right_scroll = _build_vertical_scroll_stack(
            [plot_panel],
            min_width=760,
            spacing=10,
            add_stretch=True,
        )

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=360,
            max_width=520,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1230])

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _ff_run_running(self) -> bool:
        return self._ff_thread is not None and self._ff_thread.isRunning()

    def _set_ff_busy(self, busy: bool) -> None:
        running = self._ff_run_running()
        if busy or running:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.export_plot.setEnabled(False)
            return
        table = self._active_curves_table()
        has_ready = False
        if table is not None:
            missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
            has_ready = len(missing) == 0
        self.btn_run.setEnabled(has_ready)
        self.btn_cancel.setEnabled(False)
        self.export_plot.setEnabled(has_ready)

    def _clear_ff_outputs(self) -> None:
        self._last_plot_fig = None
        self.plot_view.setHtml("")

    def _on_state_changed(self) -> None:
        if self._ff_run_running():
            self.log.append("[ff] source changed while run active: cancelling previous run.")
            self._cancel_ff_run()
            _stop_qthread(self._ff_thread)
            self._ff_worker = None
            self._ff_thread = None
        table = self._active_curves_table()
        if table is None:
            self.lbl_source.setText("Source: none (load curves first)")
            self.sample_box.set_items([], select_all=False)
            self.size_box.set_items([], select_all=False)
            self.dil_box.set_items([], select_all=False)
            self.lbl_missing.setText("")
            self._set_ff_busy(False)
            return

        source_kind = "standardized curves" if self.state.curves_standardized is not None else "raw curves"
        self.lbl_source.setText(
            f"Source: {source_kind} | {table.path.name or '(in-memory)'} | "
            f"rows={len(table.df)} cols={len(table.df.columns)}"
        )

        missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
        if missing:
            self.lbl_missing.setText(
                "Missing required columns for FF: "
                f"{missing}. Run standardization/mapping in Data Upload tab."
            )
        else:
            self.lbl_missing.setText("")
        self._set_ff_busy(False)

        opts = available_ff_options(table.df)
        self.sample_box.set_items(opts.get("samples", []), select_all=True)
        self.size_box.set_items(opts.get("sizes", []), select_all=True)
        self.dil_box.set_items(opts.get("dilutions", []), select_all=True)

    def _run_ff(self) -> None:
        if self._ff_run_running():
            QMessageBox.information(self, "Run in progress", "Frozen Fraction is already running.")
            return
        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return

        selected_samples = self.sample_box.selected_values() or self.sample_box.values()
        selected_sizes = self.size_box.selected_values() or self.size_box.values()
        selected_dilutions = self.dil_box.selected_values() or self.dil_box.values()
        ff_mode = str(self.cb_mode.currentData() or "multiple")
        if ff_mode == "single" and len(selected_samples) > 1:
            selected_samples = selected_samples[:1]
            self.log.append("[ff] single-sample mode: using first selected sample only.")
        self._ff_run_mode = ff_mode

        flt_dict = dict(
            selected_samples=selected_samples,
            selected_sizes=selected_sizes,
            selected_dilutions=selected_dilutions,
            show_control=self.chk_show_control.isChecked(),
        )
        kwargs = dict(curves_df=table.df.copy(), filter_dict=flt_dict)

        self._set_ff_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append("[ff] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_frozen_fraction_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_ff_progress)
        worker.succeeded.connect(self._on_ff_succeeded)
        worker.failed.connect(self._on_ff_failed)
        worker.cancelled.connect(self._on_ff_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_ff_thread_finished)

        self._ff_thread = thread
        self._ff_worker = worker
        thread.start()

    def _on_ff_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._ff_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _apply_ff_payload(self, payload: dict[str, Any]) -> None:
        points = payload.get("points")
        if not isinstance(points, pd.DataFrame):
            raise ValueError("Invalid Frozen Fraction payload: missing points.")
        status = str(payload.get("status") or "")
        ff_mode = str(self._ff_run_mode or "multiple")

        if len(points) == 0:
            self.lbl_status.setText(f"Status: No points after filtering | mode={ff_mode} | {status}")
            self.log.append(f"[ff] no points | {status}")
            self._clear_ff_outputs()
            return

        self._draw_ff_plotly(points)
        self.lbl_status.setText(f"Status: OK | mode={ff_mode} | {status}")
        self.log.append(f"[ff] {status}")

    def _on_ff_succeeded(self, payload: object) -> None:
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid Frozen Fraction worker payload.")
            self._apply_ff_payload(payload)
        except Exception as exc:
            self._on_ff_failed(str(exc))

    def _on_ff_failed(self, msg: str) -> None:
        txt = str(msg or "Unknown error.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: ERROR rendering FF plot | {txt}")
        self.log.append(f"[ff] ERROR rendering: {txt}")
        self._clear_ff_outputs()

    def _on_ff_cancelled(self, msg: str) -> None:
        txt = str(msg or "Cancelled.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[ff] CANCELLED: {txt}")

    def _on_ff_thread_finished(self) -> None:
        self._ff_worker = None
        self._ff_thread = None
        self._set_ff_busy(False)

    def _cancel_ff_run(self) -> None:
        if self._ff_worker is None:
            return
        try:
            self._ff_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[ff] cancel requested")
        except Exception as exc:
            self.log.append(f"[ff] cancel request error: {exc}")

    def _draw_ff_plotly(self, points: pd.DataFrame) -> None:
        title_html = _compose_optional_plot_title(self.in_plot_title.text(), self.in_plot_subtitle.text())
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(title_html))
        fig.update_layout(height=500, legend_title_text="Dilution.factor")

        if len(points) == 0:
            self._last_plot_fig = go.Figure(fig)
            _set_plotly_html(self.plot_view, fig)
            return

        palette = _palette_hex_named(self.cb_palette.currentData() or "default")
        dilutions = sorted(
            points["Dilution.plot"].astype(str).unique().tolist(),
            key=lambda x: (0, float(x), x) if x.replace(".", "", 1).isdigit() else (1, x),
        )
        color_map = {d: palette[i % len(palette)] for i, d in enumerate(dilutions)}
        sample_symbol = str(self.cb_symbol_sample.currentData() or "circle")
        control_symbol = str(self.cb_symbol_control.currentData() or "square-open")
        point_size = int(self.sl_point_size.value())
        border_w = float(self.sl_point_border.value()) / 100.0
        show_grid = bool(self.chk_grid.isChecked())

        # Split traces by dilution + control to keep symbols and legend clean.
        for dil in dilutions:
            dd = points[points["Dilution.plot"].astype(str) == str(dil)]
            for ctrl in ["No", "Yes"]:
                g = dd[dd["Control_norm"].astype(str) == ctrl]
                if len(g) == 0:
                    continue
                symbol = control_symbol if ctrl == "Yes" else sample_symbol
                name = str(dil) if ctrl == "No" else f"{dil} (control)"
                fig.add_trace(
                    go.Scatter(
                        x=g["Freezing.temperature"],
                        y=g["FF"],
                        mode="markers",
                        name=name,
                        marker=dict(
                            color=color_map.get(str(dil), palette[0]),
                            symbol=symbol,
                            size=point_size,
                            opacity=0.9,
                            line=dict(color="#111827", width=border_w),
                        ),
                        customdata=np.stack(
                            [
                                g["Sample"].astype(str).to_numpy(),
                                g["Size"].astype(str).to_numpy(),
                                g["Dilution.plot"].astype(str).to_numpy(),
                                g["Control_norm"].astype(str).to_numpy(),
                            ],
                            axis=1,
                        ),
                        hovertemplate=(
                            "Sample=%{customdata[0]}<br>Size=%{customdata[1]}<br>Dilution=%{customdata[2]}<br>"
                            "Control=%{customdata[3]}<br>FF=%{y:.3f}<br>T=%{x:.2f}°C<extra></extra>"
                        ),
                    )
                )

        _style_axes(
            fig,
            x_title="Temperature (°C)",
            y_title="Frozen Fraction",
            y_type="linear",
            y_range=[0, 1],
        )
        fig.update_xaxes(showgrid=show_grid)
        fig.update_yaxes(showgrid=show_grid)
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=False,
        )
        self._last_plot_fig = go.Figure(fig)
        _set_plotly_html(self.plot_view, fig)

    def _on_nm_axis_label_changed(self) -> None:
        # No nm axis in FF, but keep the signal connection to avoid stale handlers.
        self.log.append(f"[ff] nm axis label changed: {self.state.nm_axis_label}")

    def _export_plot(self) -> None:
        if self._last_plot_fig is None:
            QMessageBox.information(self, "No plot", "Run Frozen Fraction first.")
            return
        try:
            cfg = self.export_plot.config()
            saved = _save_plotly_figure_local(self, self._last_plot_fig, cfg)
            if saved is not None:
                self.log.append(f"[ff] export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported Frozen Fraction plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[ff] export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._ff_worker is not None:
                self._ff_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._ff_thread)
        self._ff_worker = None
        self._ff_thread = None
        self._set_ff_busy(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


class ClickablePlotView(QWebEngineView):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._click_filters_installed = False
        self._last_click_ms = 0
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.loadFinished.connect(lambda _ok: QTimer.singleShot(0, self._install_click_filters))

    def _install_click_filters(self) -> None:
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            try:
                w.installEventFilter(self)
            except Exception:
                pass
        self._click_filters_installed = True

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._click_filters_installed:
            QTimer.singleShot(0, self._install_click_filters)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        try:
            if event is not None and event.type() == QEvent.MouseButtonRelease:
                if getattr(event, "button", lambda: None)() == Qt.LeftButton:
                    now = int(datetime.now().timestamp() * 1000)
                    if (now - int(self._last_click_ms)) > 260:
                        self._last_click_ms = now
                        self.clicked.emit()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        super().mousePressEvent(event)


class KneepointSampleEditorDialog(QDialog):
    def __init__(
        self,
        *,
        parent: QWidget,
        curves_df: pd.DataFrame,
        sample: str,
        size: str,
        dilution_values: list[Any],
        initial_dilutions: list[Any],
        defaults: dict[str, Any],
        nm_axis_label: str,
        bg_mode: str,
        tick_style: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Kneepoint editor - {sample}")
        # Keep closing deterministic: dialog should close only via explicit buttons.
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        scr = QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            self.resize(max(1200, int(geo.width() * 0.82)), max(820, int(geo.height() * 0.82)))
        else:
            self.resize(1500, 940)
        self.setMinimumSize(1180, 800)
        self._curves_df = curves_df
        self._sample = str(sample or "").strip()
        self._size = str(size or "").strip()
        self._nm_axis_label = _coerce_nm_axis_label(nm_axis_label)
        self._bg_mode = str(bg_mode or "white")
        self._tick_style = str(tick_style or "auto")
        self._last_payload: dict[str, Any] | None = None
        self._allow_early_close: bool = False

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QWidget(self)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)
        left.setMinimumWidth(380)
        left.setMaximumWidth(520)

        info = QLabel(f"Sample: {self._sample}\nSize: {self._size}")
        info.setWordWrap(True)
        left_lay.addWidget(info)

        self.dil_box = MultiSelectBox("Dilution.factor")
        self.dil_box.set_items(list(dilution_values), select_all=False)
        self.dil_box.set_selected_values(list(initial_dilutions))
        if len(self.dil_box.selected_values()) == 0:
            self.dil_box.select_all()
        left_lay.addWidget(self.dil_box)

        temp_box = QGroupBox("Temperature range (°C)")
        temp_lay = QFormLayout(temp_box)
        self.chk_temp_range = QCheckBox("Enable temperature range")
        self.chk_temp_range.setChecked(bool(defaults.get("temp_range_enabled", False)))
        self.chk_temp_range.toggled.connect(self._set_temp_enabled)
        temp_lay.addRow("", self.chk_temp_range)
        tmin_raw = pd.to_numeric(pd.Series([defaults.get("temp_min", -35.0)]), errors="coerce").iloc[0]
        tmax_raw = pd.to_numeric(pd.Series([defaults.get("temp_max", 0.0)]), errors="coerce").iloc[0]
        tmin_default = float(tmin_raw) if np.isfinite(tmin_raw) else -35.0
        tmax_default = float(tmax_raw) if np.isfinite(tmax_raw) else 0.0
        t_series = pd.to_numeric(self._curves_df.get("Freezing.temperature"), errors="coerce")
        t_min_bound = float(np.nanmin(t_series)) if len(t_series) else -40.0
        t_max_bound = float(np.nanmax(t_series)) if len(t_series) else 5.0
        if not np.isfinite(t_min_bound):
            t_min_bound = -40.0
        if not np.isfinite(t_max_bound):
            t_max_bound = 5.0
        if t_max_bound <= t_min_bound:
            t_max_bound = t_min_bound + 0.1
        self.sp_temp_min = SliderNumberInput(min_value=t_min_bound, max_value=t_max_bound, value=tmin_default, decimals=2, step=0.1)
        self.sp_temp_max = SliderNumberInput(min_value=t_min_bound, max_value=t_max_bound, value=tmax_default, decimals=2, step=0.1)
        temp_lay.addRow("Min", self.sp_temp_min)
        temp_lay.addRow("Max", self.sp_temp_max)
        left_lay.addWidget(temp_box)
        self._set_temp_enabled()

        cfg_box = QGroupBox("Kneepoint parameters")
        cfg_lay = QFormLayout(cfg_box)
        row_spar, self.sl_spar, _ = _make_labeled_slider(
            min_value=0,
            max_value=100,
            value=int(round(float(defaults.get("spar", 0.4)) * 100)),
            decimals=2,
            step=5,
        )
        cfg_lay.addRow("spar-like (0..1)", row_spar)
        row_nbreaks, self.sl_nbreaks, _ = _make_labeled_slider(
            min_value=1,
            max_value=8,
            value=int(defaults.get("n_breaks", 2)),
            decimals=0,
            step=1,
        )
        cfg_lay.addRow("n breakpoints", row_nbreaks)
        row_flat, self.sl_flat_q, _ = _make_labeled_slider(
            min_value=0,
            max_value=100,
            value=int(round(float(defaults.get("flat_quantile", 0.35)) * 100)),
            decimals=2,
            step=5,
        )
        cfg_lay.addRow("Flat quantile", row_flat)
        row_rise, self.sl_rise_q, _ = _make_labeled_slider(
            min_value=0,
            max_value=100,
            value=int(round(float(defaults.get("rise_quantile", 0.70)) * 100)),
            decimals=2,
            step=5,
        )
        cfg_lay.addRow("Rise quantile", row_rise)
        self.cb_segment_mode = QComboBox()
        self.cb_segment_mode.addItem("Legacy (fixed internal segments)", "legacy")
        self.cb_segment_mode.addItem("BIC (auto segments)", "bic")
        self.cb_segment_mode.addItem("CV (auto segments)", "cv")
        self.cb_segment_mode.addItem("CV + BIC (hybrid)", "cv+bic")
        seg_mode_default = str(defaults.get("segment_selection_mode", "legacy") or "legacy").strip().lower()
        idx_seg = self.cb_segment_mode.findData(seg_mode_default)
        if idx_seg >= 0:
            self.cb_segment_mode.setCurrentIndex(idx_seg)
        cfg_lay.addRow("Segment mode", self.cb_segment_mode)
        row_seg_min, self.sl_seg_min, _ = _make_labeled_slider(
            min_value=1,
            max_value=20,
            value=max(1, int(defaults.get("segment_min_internal_breaks", 2))),
            decimals=0,
            step=1,
        )
        cfg_lay.addRow("Min internal segments", row_seg_min)
        row_seg_max, self.sl_seg_max, _ = _make_labeled_slider(
            min_value=2,
            max_value=25,
            value=max(2, int(defaults.get("segment_max_internal_breaks", 14))),
            decimals=0,
            step=1,
        )
        cfg_lay.addRow("Max internal segments", row_seg_max)
        row_pt, self.sl_point_size, _ = _make_labeled_slider(
            min_value=2,
            max_value=18,
            value=int(defaults.get("point_size", 6)),
            decimals=0,
            step=1,
        )
        cfg_lay.addRow("Point size", row_pt)
        row_lw, self.sl_line_width, _ = _make_labeled_slider(
            min_value=5,
            max_value=60,
            value=int(round(float(defaults.get("line_width", 2.0)) * 10)),
            decimals=1,
            step=2,
        )
        cfg_lay.addRow("Line width", row_lw)
        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(bool(defaults.get("show_grid", True)))
        cfg_lay.addRow("", self.chk_grid)
        self.chk_show_breakpoints = QCheckBox("Show piecewise segments (breakpoints)")
        self.chk_show_breakpoints.setChecked(bool(defaults.get("show_breakpoints", True)))
        cfg_lay.addRow("", self.chk_show_breakpoints)
        left_lay.addWidget(cfg_box)

        action_row = QHBoxLayout()
        self.btn_run = QPushButton("Run preview")
        self.btn_run.clicked.connect(self._run_preview)
        action_row.addWidget(self.btn_run)
        self.btn_apply = QPushButton("Apply to report")
        self.btn_apply.setAutoDefault(False)
        self.btn_apply.setDefault(False)
        self.btn_apply.clicked.connect(self._apply_and_close)
        action_row.addWidget(self.btn_apply)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.setDefault(False)
        self.btn_cancel.clicked.connect(self._cancel_clicked)
        action_row.addWidget(self.btn_cancel)
        left_lay.addLayout(action_row)

        self.lbl_status = QLabel("Ready. Run preview to validate edits for this sample.")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)
        left_lay.addStretch(1)

        right = QWidget(self)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        right_lay.addWidget(QLabel("Sample preview"))
        self.plot_view = QWebEngineView(self)
        self.plot_view.setMinimumHeight(560)
        right_lay.addWidget(self.plot_view, stretch=1)
        right_lay.addWidget(QLabel("Detected knees"))
        self.table_bp = QTableWidget(self)
        self.table_bp.setMinimumHeight(230)
        right_lay.addWidget(self.table_bp)
        left_scroll = _wrap_scroll(left, horizontal=False)
        left_scroll.setMinimumWidth(420)
        left_scroll.setMaximumWidth(620)
        right_scroll = _wrap_scroll(right, horizontal=False)
        right_scroll.setMinimumWidth(680)

        split = QSplitter(Qt.Horizontal, self)
        split.setChildrenCollapsible(False)
        split.addWidget(left_scroll)
        split.addWidget(right_scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([460, 980])
        root.addWidget(split)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._allow_early_close = False

    def reject(self) -> None:  # type: ignore[override]
        # Ignore implicit reject paths; close only via explicit Cancel/Apply.
        if not self._allow_early_close:
            return
        super().reject()

    def done(self, r: int) -> None:  # type: ignore[override]
        if int(r) == int(QDialog.Rejected) and not self._allow_early_close:
            return
        super().done(r)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._allow_early_close:
            try:
                event.ignore()
            except Exception:
                pass
            return
        super().closeEvent(event)

    def _cancel_clicked(self) -> None:
        self._allow_early_close = True
        self.done(int(QDialog.Rejected))

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        try:
            k = int(getattr(event, "key", lambda: 0)())
            if (not self._allow_early_close) and k in (int(Qt.Key_Escape), int(Qt.Key_Return), int(Qt.Key_Enter)):
                event.ignore()
                return
        except Exception:
            pass
        super().keyPressEvent(event)

    def _set_temp_enabled(self, *_args: Any) -> None:
        enabled = bool(self.chk_temp_range.isChecked())
        self.sp_temp_min.setEnabled(enabled)
        self.sp_temp_max.setEnabled(enabled)

    def _current_params(self) -> dict[str, Any]:
        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        if len(selected_dils) == 0:
            raise ValueError("Select at least one dilution for this sample.")
        tmin: float | None = None
        tmax: float | None = None
        if self.chk_temp_range.isChecked():
            tmin = float(self.sp_temp_min.value())
            tmax = float(self.sp_temp_max.value())
            if tmin > tmax:
                tmin, tmax = tmax, tmin
        return {
            "dilutions": list(selected_dils),
            "spar": float(self.sl_spar.value()) / 100.0,
            "n_breaks": int(self.sl_nbreaks.value()),
            "flat_quantile": float(self.sl_flat_q.value()) / 100.0,
            "rise_quantile": float(self.sl_rise_q.value()) / 100.0,
            "segment_selection_mode": str(self.cb_segment_mode.currentData() or "legacy"),
            "segment_min_internal_breaks": max(1, int(self.sl_seg_min.value())),
            "segment_max_internal_breaks": max(1, int(self.sl_seg_max.value())),
            "temp_min": tmin,
            "temp_max": tmax,
            "temp_range_enabled": bool(self.chk_temp_range.isChecked()),
            "point_size": int(self.sl_point_size.value()),
            "line_width": float(self.sl_line_width.value()) / 10.0,
            "show_breakpoints": bool(self.chk_show_breakpoints.isChecked()),
            "show_grid": bool(self.chk_grid.isChecked()),
        }

    def _run_preview(self) -> bool:
        try:
            params = self._current_params()
            payload = _compute_kneepoint_payload(
                curves_df=self._curves_df,
                sample=self._sample,
                size=self._size,
                dilutions=params["dilutions"],
                spar=params["spar"],
                n_breaks=params["n_breaks"],
                flat_quantile=params["flat_quantile"],
                rise_quantile=params["rise_quantile"],
                segment_selection_mode=params["segment_selection_mode"],
                segment_min_internal_breaks=params["segment_min_internal_breaks"],
                segment_max_internal_breaks=max(
                    int(params["segment_min_internal_breaks"]),
                    int(params["segment_max_internal_breaks"]),
                ),
                temp_min=params["temp_min"],
                temp_max=params["temp_max"],
            )
            points = payload.get("points")
            res = payload.get("res")
            if not isinstance(points, pd.DataFrame) or res is None:
                raise ValueError("Invalid kneepoint payload while editing sample.")
            fig, bp_rows = kp_build_single_sample_figure(
                self._sample,
                points,
                res,
                point_size=int(params["point_size"]),
                line_width=float(params["line_width"]),
                show_breakpoints=bool(params.get("show_breakpoints", True)),
                show_grid=bool(params["show_grid"]),
                nm_axis_label=self._nm_axis_label,
            )
            fig_preview = go.Figure(fig)
            _apply_plot_background(fig_preview, self._bg_mode)
            _apply_y_tick_style(fig_preview, self._tick_style, bg_mode=self._bg_mode, y_is_log=True)
            _set_plotly_html(self.plot_view, fig_preview)
            _render_table(self.table_bp, pd.DataFrame(bp_rows), max_rows=30)
            self._last_payload = {
                "sample": self._sample,
                "size": self._size,
                "points": points,
                "res": res,
                "figure": fig,
                "params": params,
            }
            self.lbl_status.setText(
                f"Preview OK | knees={len(res.breakpoints)} | "
                f"T={np.round(res.breakpoints, 3).tolist()}"
            )
            return True
        except Exception as exc:
            self._last_payload = None
            self.lbl_status.setText(f"Preview error: {exc}")
            QMessageBox.warning(self, "Kneepoint sample editor", str(exc))
            return False

    def _apply_and_close(self) -> None:
        if not self._run_preview():
            return
        self._allow_early_close = True
        self.accept()

    def applied_payload(self) -> dict[str, Any] | None:
        return self._last_payload


class KneepointTab(QWidget):
    REQUIRED_COLUMNS = [
        "Sample",
        "Size",
        "Control",
        "Dilution.factor",
        "Freezing.temperature",
        "nm",
    ]

    def __init__(self, state: AppState, parent: QWidget | None = None, *, test_mode: bool = False) -> None:
        super().__init__(parent)
        self.state = state
        self._test_mode = bool(test_mode)
        self._kp_thread: QThread | None = None
        self._kp_worker: LongTaskWorker | None = None
        self._kp_report_thread: QThread | None = None
        self._kp_report_worker: LongTaskWorker | None = None
        self._kp_report_job_kind: str = ""
        self._kp_report_preview: dict[str, Any] | None = None
        self._kp_report_card_views: list[QWebEngineView] = []
        self._kp_active_editor: KneepointSampleEditorDialog | None = None
        self._kp_active_editor_sample: str = ""
        self._kp_pending_editor_sample: str = ""
        self._kp_last_payload: dict[str, Any] | None = None
        self._last_plot_fig: go.Figure | None = None
        self._kp_report_loaded_overrides: dict[str, dict[str, Any]] = {}
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self._on_state_changed()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)

        self.lbl_missing = QLabel("")
        self.lbl_missing.setWordWrap(True)
        left_lay.addWidget(self.lbl_missing)

        run_row = QVBoxLayout()
        self.btn_run = QPushButton("Run Kneepoint")
        self.btn_run.clicked.connect(self._run_kp)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_kp_run)
        run_row.addWidget(self.btn_cancel)

        self.cb_sample = QComboBox()
        self.cb_size = QComboBox()
        form_top = QFormLayout()
        form_top.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_top.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form_top.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form_top.addRow("Sample", self.cb_sample)
        form_top.addRow("Size", self.cb_size)
        left_lay.addLayout(form_top)

        self.dil_box = MultiSelectBox("Dilution.factor")
        left_lay.addWidget(self.dil_box)

        temp_box = QGroupBox("Temperature range (°C)")
        temp_lay = QFormLayout(temp_box)
        temp_lay.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        temp_lay.setRowWrapPolicy(QFormLayout.WrapLongRows)
        temp_lay.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.chk_temp_range = QCheckBox("Enable temperature range")
        self.chk_temp_range.setChecked(False)
        self.chk_temp_range.toggled.connect(self._set_kp_temp_range_enabled)
        temp_lay.addRow("", self.chk_temp_range)
        self.sp_temp_min = SliderNumberInput(min_value=-40.0, max_value=5.0, value=-35.0, decimals=2, step=0.1)
        self.sp_temp_max = SliderNumberInput(min_value=-40.0, max_value=5.0, value=0.0, decimals=2, step=0.1)
        temp_lay.addRow("Min", self.sp_temp_min)
        temp_lay.addRow("Max", self.sp_temp_max)
        left_lay.addWidget(temp_box)

        cfg_box = QGroupBox("Kneepoint settings")
        cfg_lay = QFormLayout(cfg_box)
        cfg_lay.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        cfg_lay.setRowWrapPolicy(QFormLayout.WrapLongRows)
        cfg_lay.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        row_spar, self.sl_spar, _ = _make_labeled_slider(min_value=0, max_value=100, value=40, decimals=2, step=5)
        cfg_lay.addRow("spar-like (0..1)", row_spar)

        row_nbreaks, self.sl_nbreaks, _ = _make_labeled_slider(min_value=1, max_value=8, value=2, decimals=0, step=1)
        cfg_lay.addRow("n breakpoints", row_nbreaks)

        row_flat, self.sl_flat_q, _ = _make_labeled_slider(min_value=0, max_value=100, value=35, decimals=2, step=5)
        cfg_lay.addRow("Flat quantile", row_flat)

        row_rise, self.sl_rise_q, _ = _make_labeled_slider(min_value=0, max_value=100, value=70, decimals=2, step=5)
        cfg_lay.addRow("Rise quantile", row_rise)

        self.cb_segment_mode = QComboBox()
        self.cb_segment_mode.addItem("Legacy (fixed internal segments)", "legacy")
        self.cb_segment_mode.addItem("BIC (auto segments)", "bic")
        self.cb_segment_mode.addItem("CV (auto segments)", "cv")
        self.cb_segment_mode.addItem("CV + BIC (hybrid)", "cv+bic")
        self.lbl_segment_mode = QLabel("Segment mode")
        cfg_lay.addRow(self.lbl_segment_mode, self.cb_segment_mode)

        self.row_seg_min, self.sl_seg_min, _ = _make_labeled_slider(min_value=1, max_value=20, value=2, decimals=0, step=1)
        self.lbl_seg_min = QLabel("Min internal segments")
        cfg_lay.addRow(self.lbl_seg_min, self.row_seg_min)
        self.row_seg_max, self.sl_seg_max, _ = _make_labeled_slider(min_value=2, max_value=25, value=14, decimals=0, step=1)
        self.lbl_seg_max = QLabel("Max internal segments")
        cfg_lay.addRow(self.lbl_seg_max, self.row_seg_max)

        row_pt, self.sl_point_size, _ = _make_labeled_slider(min_value=2, max_value=18, value=6, decimals=0, step=1)
        cfg_lay.addRow("Point size", row_pt)

        row_lw, self.sl_line_width, _ = _make_labeled_slider(min_value=5, max_value=60, value=20, decimals=1, step=2)
        cfg_lay.addRow("Line width", row_lw)

        left_lay.addWidget(cfg_box)

        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(True)
        left_lay.addWidget(self.chk_grid)
        self.chk_show_breakpoints = QCheckBox("Show piecewise segments (breakpoints)")
        self.chk_show_breakpoints.setChecked(True)
        left_lay.addWidget(self.chk_show_breakpoints)
        self.chk_grid.toggled.connect(self._refresh_kp_plot_from_last)
        self.chk_show_breakpoints.toggled.connect(self._refresh_kp_plot_from_last)

        style_box = QGroupBox("Plot style")
        style_lay = QFormLayout(style_box)
        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_lay.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_lay.addRow("Y-axis tick style", self.cb_tick_style)
        left_lay.addWidget(style_box)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_plot = PlotExportBox("Kneepoint plot export", default_stem="kneepoint")
        self.export_plot.btn_export.clicked.connect(self._export_plot)
        left_lay.addWidget(self.export_plot)

        report_box = QGroupBox("Create Report (batch)")
        report_lay = QVBoxLayout(report_box)
        report_intro = QLabel(
            "Report sample selection is separate from live sample selection. "
            "Choose multiple samples (or Select All), then generate a report package."
        )
        report_intro.setWordWrap(True)
        report_lay.addWidget(report_intro)
        self.kp_report_samples = QListWidget()
        self.kp_report_samples.setSelectionMode(QAbstractItemView.MultiSelection)
        self.kp_report_samples.setMinimumHeight(140)
        self.kp_report_samples.itemSelectionChanged.connect(self._on_kp_report_selection_changed)
        report_lay.addWidget(self.kp_report_samples)

        report_sel_row = QHBoxLayout()
        self.btn_kp_report_select_all = QPushButton("Select All")
        self.btn_kp_report_select_all.clicked.connect(self._kp_report_select_all)
        report_sel_row.addWidget(self.btn_kp_report_select_all)
        self.btn_kp_report_clear = QPushButton("Clear")
        self.btn_kp_report_clear.clicked.connect(self._kp_report_clear_selection)
        report_sel_row.addWidget(self.btn_kp_report_clear)
        report_sel_row.addStretch(1)
        report_lay.addLayout(report_sel_row)

        report_form = QFormLayout()
        report_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        report_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        report_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.in_kp_report_dir = QLineEdit(str(Path.home() / "Desktop"))
        report_form.addRow("Save folder", self.in_kp_report_dir)
        self.btn_kp_report_browse = QPushButton("Browse...")
        self.btn_kp_report_browse.clicked.connect(self._browse_kp_report_dir)
        report_form.addRow("", self.btn_kp_report_browse)
        self.in_kp_report_prefix = QLineEdit("kneepoint_report")
        report_form.addRow("File prefix", self.in_kp_report_prefix)
        report_lay.addLayout(report_form)

        report_actions = QHBoxLayout()
        self.btn_kp_report_create = QPushButton("Build Preview")
        self.btn_kp_report_create.clicked.connect(self._create_kp_report)
        report_actions.addWidget(self.btn_kp_report_create)
        self.btn_kp_report_download = QPushButton("Download Report (.zip)")
        self.btn_kp_report_download.clicked.connect(self._download_kp_report)
        self.btn_kp_report_download.setEnabled(False)
        report_actions.addWidget(self.btn_kp_report_download)
        self.btn_kp_report_cancel = QPushButton("Cancel Report")
        self.btn_kp_report_cancel.clicked.connect(self._cancel_kp_report)
        self.btn_kp_report_cancel.setEnabled(False)
        report_actions.addWidget(self.btn_kp_report_cancel)
        report_lay.addLayout(report_actions)

        report_preset_row = QHBoxLayout()
        self.btn_kp_report_save_preset = QPushButton("Save Session (.inaeskpr)")
        self.btn_kp_report_save_preset.clicked.connect(self._save_kp_report_session)
        report_preset_row.addWidget(self.btn_kp_report_save_preset)
        self.btn_kp_report_load_preset = QPushButton("Load Session (.inaeskpr)")
        self.btn_kp_report_load_preset.clicked.connect(self._load_kp_report_session)
        report_preset_row.addWidget(self.btn_kp_report_load_preset)
        report_lay.addLayout(report_preset_row)

        self.pb_kp_report = QProgressBar()
        self.pb_kp_report.setRange(0, 100)
        self.pb_kp_report.setValue(0)
        self.pb_kp_report.setFormat("%p%")
        report_lay.addWidget(self.pb_kp_report)

        self.lbl_kp_report_status = QLabel("Report status: waiting")
        self.lbl_kp_report_status.setWordWrap(True)
        report_lay.addWidget(self.lbl_kp_report_status)

        report_lay.addWidget(QLabel("Report artifacts"))
        self.kp_report_artifacts = QTextEdit()
        self.kp_report_artifacts.setReadOnly(True)
        self.kp_report_artifacts.setMinimumHeight(120)
        self.kp_report_artifacts.setPlainText("No report generated yet.")
        report_lay.addWidget(self.kp_report_artifacts)
        left_lay.addWidget(report_box)
        if self._test_mode:
            report_box.setVisible(False)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        plot_panel = QWidget()
        plot_panel_lay = QVBoxLayout(plot_panel)
        plot_panel_lay.setContentsMargins(0, 0, 0, 0)
        plot_panel_lay.setSpacing(6)
        plot_panel_lay.addWidget(QLabel("Kneepoint plot (spline + points)"))
        self.plot_view = QWebEngineView()
        self.plot_view.setMinimumHeight(460)
        plot_panel_lay.addWidget(self.plot_view, stretch=1)

        bp_panel = QWidget()
        bp_panel_lay = QVBoxLayout(bp_panel)
        bp_panel_lay.setContentsMargins(0, 0, 0, 0)
        bp_panel_lay.setSpacing(6)
        bp_panel_lay.addWidget(QLabel("Kneepoint results"))
        self.table_bp = QTableWidget()
        self.table_bp.setMinimumHeight(170)
        bp_panel_lay.addWidget(self.table_bp, stretch=1)

        report_preview_panel = QWidget()
        report_preview_lay = QVBoxLayout(report_preview_panel)
        report_preview_lay.setContentsMargins(0, 0, 0, 0)
        report_preview_lay.setSpacing(6)
        report_preview_lay.addWidget(QLabel("Report preview (live before download)"))
        self.lbl_kp_preview_state = QLabel(
            "Build preview from selected samples, then click a sample plot below to open its dedicated editor."
        )
        self.lbl_kp_preview_state.setWordWrap(True)
        report_preview_lay.addWidget(self.lbl_kp_preview_state)
        self.table_kp_report_summary = QTableWidget()
        self.table_kp_report_summary.setMinimumHeight(180)
        report_preview_lay.addWidget(self.table_kp_report_summary, stretch=1)
        self.kp_report_preview_view = QWebEngineView()
        self.kp_report_preview_view.setMinimumHeight(820)
        report_preview_lay.addWidget(self.kp_report_preview_view, stretch=2)
        report_preview_lay.addWidget(QLabel("Sample plots (click plot or Edit button to customize one sample)"))
        cards_host = QWidget()
        self.kp_report_cards_layout = QGridLayout(cards_host)
        self.kp_report_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.kp_report_cards_layout.setHorizontalSpacing(10)
        self.kp_report_cards_layout.setVerticalSpacing(10)
        self._kp_report_cards_columns = 2
        self.kp_report_cards_scroll = _wrap_scroll(cards_host, horizontal=False)
        self.kp_report_cards_scroll.setMinimumHeight(700)
        report_preview_lay.addWidget(self.kp_report_cards_scroll, stretch=2)

        # Robust layout for dense Kneepoint report outputs:
        # use vertical scrolling on the whole right side instead of forcing
        # multiple fixed split panes that can compress and hide content.
        right_content = QWidget()
        right_content_lay = QVBoxLayout(right_content)
        right_content_lay.setContentsMargins(0, 0, 0, 0)
        right_content_lay.setSpacing(10)
        right_content_lay.addWidget(plot_panel)
        right_content_lay.addWidget(bp_panel)
        right_content_lay.addWidget(report_preview_panel)
        right_content_lay.addStretch(1)
        right_scroll = _wrap_scroll(right_content, horizontal=False)
        right_scroll.setMinimumWidth(760)

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=400,
            max_width=580,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1240])
        self._set_kp_temp_range_enabled()

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _parse_optional_float(self, txt: str) -> float | None:
        s = str(txt or "").strip()
        if not s:
            return None
        v = pd.to_numeric(pd.Series([s]), errors="coerce").iloc[0]
        if np.isfinite(v):
            return float(v)
        return None

    def _set_kp_temp_range_enabled(self, *_args: Any) -> None:
        enabled = bool(self.chk_temp_range.isChecked())
        self.sp_temp_min.setEnabled(enabled)
        self.sp_temp_max.setEnabled(enabled)

    def _refresh_kp_temp_bounds(self, df: pd.DataFrame | None) -> None:
        if df is None or "Freezing.temperature" not in df.columns:
            self.sp_temp_min.set_bounds(-40.0, 5.0)
            self.sp_temp_max.set_bounds(-40.0, 5.0)
            return
        t = pd.to_numeric(df["Freezing.temperature"], errors="coerce")
        t = t[np.isfinite(t)]
        if len(t) == 0:
            self.sp_temp_min.set_bounds(-40.0, 5.0)
            self.sp_temp_max.set_bounds(-40.0, 5.0)
            return
        tmin = float(np.nanmin(t))
        tmax = float(np.nanmax(t))
        if not np.isfinite(tmin) or not np.isfinite(tmax):
            tmin, tmax = -40.0, 5.0
        if tmax <= tmin:
            tmax = tmin + 0.1
        self.sp_temp_min.set_bounds(tmin, tmax)
        self.sp_temp_max.set_bounds(tmin, tmax)
        self.sp_temp_min.setValue(tmin)
        self.sp_temp_max.setValue(tmax)

    def _kp_temp_range_values(self) -> tuple[float | None, float | None]:
        if not self.chk_temp_range.isChecked():
            return None, None
        tmin = float(self.sp_temp_min.value())
        tmax = float(self.sp_temp_max.value())
        if tmin > tmax:
            tmin, tmax = tmax, tmin
        return tmin, tmax

    def _kp_spar_value(self) -> float:
        return float(self.sl_spar.value()) / 100.0

    def _kp_nbreaks_value(self) -> int:
        return int(self.sl_nbreaks.value())

    def _kp_flat_q_value(self) -> float:
        return float(self.sl_flat_q.value()) / 100.0

    def _kp_rise_q_value(self) -> float:
        return float(self.sl_rise_q.value()) / 100.0

    def _kp_point_size_value(self) -> int:
        return int(self.sl_point_size.value())

    def _kp_line_width_value(self) -> float:
        return float(self.sl_line_width.value()) / 10.0

    def _kp_segment_mode_value(self) -> str:
        mode = str(self.cb_segment_mode.currentData() or "legacy").strip().lower()
        if mode not in {"legacy", "bic", "cv", "cv+bic"}:
            mode = "legacy"
        return mode

    def _kp_segment_bounds(self) -> tuple[int, int]:
        mn = max(int(self.sl_seg_min.value()), 1)
        mx = max(int(self.sl_seg_max.value()), mn)
        return mn, mx

    def _kp_run_running(self) -> bool:
        return self._kp_thread is not None and self._kp_thread.isRunning()

    def _set_kp_busy(self, busy: bool) -> None:
        running = self._kp_run_running()
        if busy or running:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            return
        table = self._active_curves_table()
        has_ready = False
        if table is not None:
            missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
            has_ready = len(missing) == 0
        self.btn_run.setEnabled(has_ready)
        self.btn_cancel.setEnabled(False)

    def _clear_kp_outputs(self) -> None:
        self.plot_view.setHtml("")
        _render_table(self.table_bp, pd.DataFrame(), max_rows=20)
        self._kp_last_payload = None
        self._last_plot_fig = None

    def _refresh_kp_plot_from_last(self) -> None:
        payload = self._kp_last_payload
        if not isinstance(payload, dict):
            return
        points = payload.get("points")
        res = payload.get("res")
        sample = str(payload.get("sample") or "")
        if not isinstance(points, pd.DataFrame) or res is None or (not sample):
            return
        try:
            self._draw_kp_plotly(
                sample=sample,
                points=points,
                res=res,
                point_size=self._kp_point_size_value(),
                line_width=self._kp_line_width_value(),
                show_breakpoints=self.chk_show_breakpoints.isChecked(),
                show_grid=self.chk_grid.isChecked(),
                y_title=_coerce_nm_axis_label(self.state.nm_axis_label),
            )
        except Exception as exc:
            self.log.append(f"[kp] refresh plot failed: {exc}")

    @staticmethod
    def _kp_breakpoint_pairs(res: Any) -> list[tuple[float, float]]:
        bp_arr = pd.to_numeric(pd.Series(getattr(res, "breakpoints", [])), errors="coerce").to_numpy(dtype=float)
        nm_arr = pd.to_numeric(pd.Series(getattr(res, "nm_at_breakpoints", [])), errors="coerce").to_numpy(dtype=float)
        sg = getattr(res, "spline_grid", None)
        sg_t = np.asarray([], dtype=float)
        sg_log = np.asarray([], dtype=float)
        if isinstance(sg, pd.DataFrame) and ("Freezing.temperature" in sg.columns) and ("log_nm_spline" in sg.columns):
            sg_t = pd.to_numeric(sg["Freezing.temperature"], errors="coerce").to_numpy(dtype=float)
            sg_log = pd.to_numeric(sg["log_nm_spline"], errors="coerce").to_numpy(dtype=float)
            ok = np.isfinite(sg_t) & np.isfinite(sg_log)
            sg_t = sg_t[ok]
            sg_log = sg_log[ok]
            if sg_t.size > 1:
                order = np.argsort(sg_t)
                sg_t = sg_t[order]
                sg_log = sg_log[order]

        out: list[tuple[float, float]] = []
        for i, bp in enumerate(bp_arr):
            if not np.isfinite(bp):
                continue
            nm_bp = float(nm_arr[i]) if i < len(nm_arr) and np.isfinite(nm_arr[i]) else np.nan
            if (not np.isfinite(nm_bp)) or (nm_bp <= 0):
                if sg_t.size > 1:
                    x = float(np.clip(bp, float(np.nanmin(sg_t)), float(np.nanmax(sg_t))))
                    log_nm = float(np.interp(x, sg_t, sg_log))
                    nm_bp = float(np.power(10.0, log_nm)) if np.isfinite(log_nm) else np.nan
            out.append((float(bp), float(nm_bp) if np.isfinite(nm_bp) else np.nan))
        return out

    @staticmethod
    def _kp_piecewise_overlay_data(res: Any) -> tuple[np.ndarray, np.ndarray, list[float]]:
        pw = getattr(res, "piecewise_params", {}) if isinstance(getattr(res, "piecewise_params", {}), dict) else {}
        x_pw = pd.to_numeric(pd.Series(pw.get("piecewise_x_grid", [])), errors="coerce").to_numpy(dtype=float)
        y_log_pw = pd.to_numeric(pd.Series(pw.get("piecewise_log_nm", [])), errors="coerce").to_numpy(dtype=float)
        if x_pw.size > 0 and y_log_pw.size == x_pw.size:
            ok = np.isfinite(x_pw) & np.isfinite(y_log_pw)
            x_pw = x_pw[ok]
            y_log_pw = y_log_pw[ok]
        else:
            sg = getattr(res, "spline_grid", None)
            if isinstance(sg, pd.DataFrame) and ("Freezing.temperature" in sg.columns) and ("log_nm_spline" in sg.columns):
                x_s = pd.to_numeric(sg["Freezing.temperature"], errors="coerce").to_numpy(dtype=float)
                y_s = pd.to_numeric(sg["log_nm_spline"], errors="coerce").to_numpy(dtype=float)
                ok = np.isfinite(x_s) & np.isfinite(y_s)
                x_s = x_s[ok]
                y_s = y_s[ok]
                if x_s.size > 1:
                    order = np.argsort(x_s)
                    x_s = x_s[order]
                    y_s = y_s[order]
                    breaks_all = pd.to_numeric(pd.Series(pw.get("breaks_all", [])), errors="coerce").dropna().astype(float).tolist()
                    if len(breaks_all) >= 2:
                        bx = np.array(sorted(breaks_all), dtype=float)
                        by = np.interp(bx, x_s, y_s)
                        x_segments: list[float] = []
                        y_segments: list[float] = []
                        for i in range(len(bx) - 1):
                            x0, x1 = float(bx[i]), float(bx[i + 1])
                            y0, y1 = float(by[i]), float(by[i + 1])
                            x_segments.extend([x0, x1, np.nan])
                            y_segments.extend([y0, y1, np.nan])
                        x_pw = np.asarray(x_segments, dtype=float)
                        y_log_pw = np.asarray(y_segments, dtype=float)
        y_pw = np.power(10.0, y_log_pw) if y_log_pw.size > 0 else np.asarray([], dtype=float)
        breaks_all = pd.to_numeric(pd.Series(pw.get("breaks_all", [])), errors="coerce").dropna().astype(float).tolist()
        breaks_internal = list(np.array(sorted(breaks_all), dtype=float)[1:-1]) if len(breaks_all) >= 2 else []
        return x_pw, y_pw, breaks_internal

    def _on_state_changed(self) -> None:
        if self._kp_run_running():
            self.log.append("[kp] source changed while run active: cancelling previous run.")
            self._cancel_kp_run()
            _stop_qthread(self._kp_thread)
            self._kp_worker = None
            self._kp_thread = None
        if self._kp_report_thread is not None and self._kp_report_thread.isRunning():
            self.log.append("[kp-report] source changed while report active: cancelling previous run.")
            self._cancel_kp_report()
            _stop_qthread(self._kp_report_thread)
            self._kp_report_worker = None
            self._kp_report_thread = None
            self._set_kp_report_busy(False)
        table = self._active_curves_table()
        if table is None:
            self.lbl_source.setText("Source: none (load curves first)")
            self.lbl_missing.setText("")
            self.cb_sample.clear()
            self.cb_size.clear()
            self.dil_box.set_items([], select_all=False)
            self._refresh_kp_temp_bounds(None)
            self._set_kp_busy(False)
            self.kp_report_samples.clear()
            self.btn_kp_report_create.setEnabled(False)
            self.btn_kp_report_download.setEnabled(False)
            self.btn_kp_report_cancel.setEnabled(False)
            self._kp_report_loaded_overrides = {}
            self._invalidate_report_preview("Build preview from selected samples after loading curves.")
            return

        source_kind = "standardized curves" if self.state.curves_standardized is not None else "raw curves"
        self.lbl_source.setText(
            f"Source: {source_kind} | {table.path.name or '(in-memory)'} | "
            f"rows={len(table.df)} cols={len(table.df.columns)}"
        )

        missing = [c for c in self.REQUIRED_COLUMNS if c not in table.df.columns]
        if missing:
            self.lbl_missing.setText(
                "Missing required columns for Kneepoint: "
                f"{missing}. Run standardization/mapping in Data Upload tab."
            )
            self._set_kp_busy(False)
            self.kp_report_samples.clear()
            self.btn_kp_report_create.setEnabled(False)
            self.btn_kp_report_download.setEnabled(False)
            self.btn_kp_report_cancel.setEnabled(False)
            self._kp_report_loaded_overrides = {}
            self._invalidate_report_preview("Preview unavailable: required kneepoint columns are missing.")
            return

        self.lbl_missing.setText("")
        self._set_kp_busy(False)
        self.btn_kp_report_create.setEnabled(True)
        self._refresh_kp_temp_bounds(table.df)

        opts = available_kp_options(table.df)
        old_sample = self.cb_sample.currentText().strip()
        old_size = self.cb_size.currentText().strip()
        self.cb_sample.clear()
        self.cb_sample.addItems(opts.get("samples", []))
        self.cb_size.clear()
        self.cb_size.addItems(opts.get("sizes", []))
        if old_sample:
            idx = self.cb_sample.findText(old_sample)
            if idx >= 0:
                self.cb_sample.setCurrentIndex(idx)
        if old_size:
            idx = self.cb_size.findText(old_size)
            if idx >= 0:
                self.cb_size.setCurrentIndex(idx)
        self.dil_box.set_items(opts.get("dilutions", []), select_all=True)
        self._refresh_kp_report_samples(opts.get("samples", []))

    def _refresh_kp_report_samples(self, samples: list[str]) -> None:
        old = {it.text() for it in self.kp_report_samples.selectedItems()}
        self.kp_report_samples.clear()
        for s in samples:
            item = QListWidgetItem(str(s))
            self.kp_report_samples.addItem(item)
            if str(s) in old:
                item.setSelected(True)
        self._invalidate_report_preview("Report preview invalidated: sample pool changed.")

    def _on_kp_report_selection_changed(self) -> None:
        self._invalidate_report_preview("Report preview invalidated: sample selection changed.")

    def _clear_layout_widgets(self, lay: QLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            child_lay = item.layout()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif child_lay is not None:
                while child_lay.count():
                    sub_item = child_lay.takeAt(0)
                    sub_w = sub_item.widget()
                    if sub_w is not None:
                        sub_w.setParent(None)
                        sub_w.deleteLater()

    def _invalidate_report_preview(self, reason: str | None = None) -> None:
        self._kp_report_preview = None
        self.btn_kp_report_download.setEnabled(False)
        self.table_kp_report_summary.clear()
        self.table_kp_report_summary.setRowCount(0)
        self.table_kp_report_summary.setColumnCount(0)
        self.kp_report_preview_view.setHtml("")
        if hasattr(self, "kp_report_cards_layout"):
            self._clear_layout_widgets(self.kp_report_cards_layout)
        self._kp_report_card_views = []
        if reason:
            self.lbl_kp_preview_state.setText(reason)

    def _request_kp_report_sample_editor(self, sample: str) -> None:
        s = str(sample or "").strip()
        if not s:
            return
        active = self._kp_active_editor
        if active is not None:
            try:
                if active.isVisible():
                    try:
                        active.raise_()
                        active.activateWindow()
                    except Exception:
                        pass
                    if str(self._kp_active_editor_sample or "").strip() == s:
                        self.log.append(f"[kp-report] edit ignored (already open): {s}")
                    else:
                        self.log.append(
                            f"[kp-report] edit ignored (editor already open for {self._kp_active_editor_sample or '<unknown>'})"
                        )
                    return
            except Exception:
                # Stale/deleted Qt wrapper; reset defensive state.
                self._kp_active_editor = None
                self._kp_active_editor_sample = ""
        pending = str(self._kp_pending_editor_sample or "").strip()
        if pending:
            if pending == s:
                self.log.append(f"[kp-report] edit ignored (already queued): {s}")
            else:
                self.log.append(f"[kp-report] edit ignored (editor opening for {pending})")
            return
        self.log.append(f"[kp-report] edit requested for sample={s}")
        self._kp_pending_editor_sample = s
        QTimer.singleShot(0, self._flush_kp_report_sample_editor_request)

    def _flush_kp_report_sample_editor_request(self) -> None:
        s = str(self._kp_pending_editor_sample or "").strip()
        self._kp_pending_editor_sample = ""
        if not s:
            return
        try:
            self._open_kp_report_sample_editor(s)
        except Exception as exc:
            txt = str(exc)
            self.log.append(f"[kp-report] edit open ERROR for sample={s}: {txt}")
            QMessageBox.warning(self, "Kneepoint report editor", txt)
            self._kp_active_editor = None
            self._kp_active_editor_sample = ""

    def _kp_report_select_all(self) -> None:
        for i in range(self.kp_report_samples.count()):
            self.kp_report_samples.item(i).setSelected(True)

    def _kp_report_clear_selection(self) -> None:
        self.kp_report_samples.clearSelection()

    def _browse_kp_report_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select report output folder", self.in_kp_report_dir.text().strip() or str(Path.home()))
        if d:
            self.in_kp_report_dir.setText(str(d))

    def _report_selected_samples(self) -> list[str]:
        return [it.text().strip() for it in self.kp_report_samples.selectedItems() if it.text().strip()]

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): KneepointTab._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [KneepointTab._json_safe(v) for v in value]
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            v = float(value)
            return v if np.isfinite(v) else None
        if isinstance(value, float):
            return value if np.isfinite(value) else None
        if isinstance(value, (int, str, bool)) or value is None:
            return value
        return str(value)

    def _collect_kp_report_session_payload(self) -> dict[str, Any]:
        table = self._active_curves_table()
        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        tmin, tmax = self._kp_temp_range_values()
        seg_min, seg_max = self._kp_segment_bounds()
        sample_overrides: dict[str, dict[str, Any]] = {}
        if isinstance(self._kp_report_preview, dict):
            for it in self._kp_report_preview.get("sample_items", []):
                if not isinstance(it, dict):
                    continue
                s = str(it.get("sample", "")).strip()
                p = it.get("params")
                if s and isinstance(p, dict):
                    sample_overrides[s] = self._json_safe(p)
        for s, p in self._kp_report_loaded_overrides.items():
            if s not in sample_overrides and isinstance(p, dict):
                sample_overrides[str(s)] = self._json_safe(p)

        source_info = {
            "path": str(table.path) if isinstance(table, LoadedTable) else "",
            "rows": int(len(table.df)) if isinstance(table, LoadedTable) else 0,
            "cols": int(len(table.df.columns)) if isinstance(table, LoadedTable) else 0,
        }
        payload = {
            "format": "INAES_KNEEPOINT_REPORT_SESSION",
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": source_info,
            "settings": {
                "size": str(self.cb_size.currentText().strip()),
                "dilutions": list(selected_dils),
                "temp_range_enabled": bool(self.chk_temp_range.isChecked()),
                "temp_min": tmin,
                "temp_max": tmax,
                "spar": float(self._kp_spar_value()),
                "n_breaks": int(self._kp_nbreaks_value()),
                "flat_quantile": float(self._kp_flat_q_value()),
                "rise_quantile": float(self._kp_rise_q_value()),
                "point_size": int(self._kp_point_size_value()),
                "line_width": float(self._kp_line_width_value()),
                "show_breakpoints": bool(self.chk_show_breakpoints.isChecked()),
                "show_grid": bool(self.chk_grid.isChecked()),
                "segment_selection_mode": str(self._kp_segment_mode_value()),
                "segment_min_internal_breaks": int(seg_min),
                "segment_max_internal_breaks": int(seg_max),
                "nm_axis_label": _coerce_nm_axis_label(self.state.nm_axis_label),
            },
            "report": {
                "selected_samples": self._report_selected_samples(),
                "save_dir": str(self.in_kp_report_dir.text().strip()),
                "file_prefix": str(self.in_kp_report_prefix.text().strip() or "kneepoint_report"),
            },
            "sample_overrides": sample_overrides,
        }
        return self._json_safe(payload)

    def _save_kp_report_session(self) -> None:
        payload = self._collect_kp_report_session_payload()
        default_dir = self.in_kp_report_dir.text().strip() or str(Path.home() / "Desktop")
        default_name = f"{self.in_kp_report_prefix.text().strip() or 'kneepoint_report'}_session.inaeskpr"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save kneepoint report session",
            str(Path(default_dir) / default_name),
            "INAES Kneepoint Session (*.inaeskpr)",
        )
        if not out_path:
            return
        p = Path(out_path)
        if p.suffix.lower() != ".inaeskpr":
            p = p.with_suffix(".inaeskpr")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.kp_report_artifacts.setPlainText(f"Session saved:\n{p}")
            self.lbl_kp_report_status.setText(f"Report status: session saved | {p}")
            self.log.append(f"[kp-report] session saved: {p}")
        except Exception as exc:
            QMessageBox.critical(self, "Save session error", str(exc))
            self.log.append(f"[kp-report] session save ERROR: {exc}")

    def _load_kp_report_session(self) -> None:
        in_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load kneepoint report session",
            str(Path.home()),
            "INAES Kneepoint Session (*.inaeskpr);;JSON (*.json);;All files (*.*)",
        )
        if not in_path:
            return
        p = Path(in_path)
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Load session error", f"Could not read session file:\n{exc}")
            self.log.append(f"[kp-report] session load ERROR: {exc}")
            return

        if not isinstance(obj, dict):
            QMessageBox.warning(self, "Invalid session", "Session file has invalid content.")
            return
        if str(obj.get("format", "")) != "INAES_KNEEPOINT_REPORT_SESSION":
            QMessageBox.warning(self, "Invalid session", "File is not an INAES kneepoint report session.")
            return

        settings = obj.get("settings", {}) if isinstance(obj.get("settings", {}), dict) else {}
        report = obj.get("report", {}) if isinstance(obj.get("report", {}), dict) else {}
        self._kp_report_loaded_overrides = {
            str(k): dict(v)
            for k, v in (obj.get("sample_overrides", {}) if isinstance(obj.get("sample_overrides", {}), dict) else {}).items()
            if isinstance(v, dict)
        }

        size_txt = str(settings.get("size", "")).strip()
        if size_txt:
            idx = self.cb_size.findText(size_txt)
            if idx >= 0:
                self.cb_size.setCurrentIndex(idx)
        dils = settings.get("dilutions", [])
        if isinstance(dils, (list, tuple, set)):
            self.dil_box.set_selected_values(list(dils))

        tr_enabled = bool(settings.get("temp_range_enabled", False))
        self.chk_temp_range.setChecked(tr_enabled)
        tmin = pd.to_numeric(pd.Series([settings.get("temp_min")]), errors="coerce").iloc[0]
        tmax = pd.to_numeric(pd.Series([settings.get("temp_max")]), errors="coerce").iloc[0]
        if np.isfinite(tmin):
            self.sp_temp_min.setValue(float(tmin))
        if np.isfinite(tmax):
            self.sp_temp_max.setValue(float(tmax))

        spar = pd.to_numeric(pd.Series([settings.get("spar")]), errors="coerce").iloc[0]
        if np.isfinite(spar):
            self.sl_spar.setValue(int(round(float(np.clip(float(spar), 0.0, 1.0)) * 100)))
        n_breaks = pd.to_numeric(pd.Series([settings.get("n_breaks")]), errors="coerce").iloc[0]
        if np.isfinite(n_breaks):
            self.sl_nbreaks.setValue(int(max(1, round(float(n_breaks)))))
        flat_q = pd.to_numeric(pd.Series([settings.get("flat_quantile")]), errors="coerce").iloc[0]
        if np.isfinite(flat_q):
            self.sl_flat_q.setValue(int(round(float(np.clip(float(flat_q), 0.0, 1.0)) * 100)))
        rise_q = pd.to_numeric(pd.Series([settings.get("rise_quantile")]), errors="coerce").iloc[0]
        if np.isfinite(rise_q):
            self.sl_rise_q.setValue(int(round(float(np.clip(float(rise_q), 0.0, 1.0)) * 100)))
        pt_size = pd.to_numeric(pd.Series([settings.get("point_size")]), errors="coerce").iloc[0]
        if np.isfinite(pt_size):
            self.sl_point_size.setValue(int(max(2, round(float(pt_size)))))
        line_w = pd.to_numeric(pd.Series([settings.get("line_width")]), errors="coerce").iloc[0]
        if np.isfinite(line_w):
            self.sl_line_width.setValue(int(max(1, round(float(line_w) * 10.0))))
        self.chk_show_breakpoints.setChecked(bool(settings.get("show_breakpoints", self.chk_show_breakpoints.isChecked())))
        self.chk_grid.setChecked(bool(settings.get("show_grid", self.chk_grid.isChecked())))

        seg_mode = str(settings.get("segment_selection_mode", "legacy") or "legacy").strip().lower()
        idx_seg = self.cb_segment_mode.findData(seg_mode)
        if idx_seg >= 0:
            self.cb_segment_mode.setCurrentIndex(idx_seg)
        seg_min = pd.to_numeric(pd.Series([settings.get("segment_min_internal_breaks")]), errors="coerce").iloc[0]
        seg_max = pd.to_numeric(pd.Series([settings.get("segment_max_internal_breaks")]), errors="coerce").iloc[0]
        if np.isfinite(seg_min):
            self.sl_seg_min.setValue(int(max(1, round(float(seg_min)))))
        if np.isfinite(seg_max):
            self.sl_seg_max.setValue(int(max(2, round(float(seg_max)))))

        save_dir = str(report.get("save_dir", "")).strip()
        if save_dir:
            self.in_kp_report_dir.setText(save_dir)
        prefix = str(report.get("file_prefix", "")).strip()
        if prefix:
            self.in_kp_report_prefix.setText(prefix)

        selected_samples = report.get("selected_samples", [])
        selected_set = {str(s).strip() for s in selected_samples} if isinstance(selected_samples, (list, tuple, set)) else set()
        if selected_set:
            for i in range(self.kp_report_samples.count()):
                item = self.kp_report_samples.item(i)
                item.setSelected(item.text().strip() in selected_set)

        self.log.append(f"[kp-report] session loaded: {p}")
        self.lbl_kp_report_status.setText(f"Report status: session loaded | {p.name}")
        self.kp_report_artifacts.setPlainText(
            "Session loaded.\n"
            "Rebuilding report preview with saved settings and sample overrides..."
        )

        if len(self._report_selected_samples()) > 0:
            self._create_kp_report()

    def _set_kp_report_busy(self, busy: bool) -> None:
        self.btn_kp_report_create.setEnabled(not busy)
        self.btn_kp_report_download.setEnabled((not busy) and (self._kp_report_preview is not None))
        self.btn_kp_report_cancel.setEnabled(busy)
        self.btn_kp_report_save_preset.setEnabled(not busy)
        self.btn_kp_report_load_preset.setEnabled(not busy)
        self.btn_kp_report_select_all.setEnabled(not busy)
        self.btn_kp_report_clear.setEnabled(not busy)
        self.btn_kp_report_browse.setEnabled(not busy)
        self.in_kp_report_dir.setEnabled(not busy)
        self.in_kp_report_prefix.setEnabled(not busy)
        self.kp_report_samples.setEnabled(not busy)
        if busy:
            self.pb_kp_report.setRange(0, 100)
            self.pb_kp_report.setValue(0)
            self.pb_kp_report.setFormat("%p%")

    def _on_kp_report_progress(self, pct: int, msg: str) -> None:
        p = int(max(0, min(100, int(pct))))
        self.pb_kp_report.setRange(0, 100)
        self.pb_kp_report.setValue(p)
        self.lbl_kp_report_status.setText(f"Report status: {p}% | {msg}")

    def _kp_report_defaults_for_editor(self, sample_item: dict[str, Any]) -> dict[str, Any]:
        params = sample_item.get("params") if isinstance(sample_item.get("params"), dict) else {}
        res = sample_item.get("result")
        n_breaks_fallback = self._kp_nbreaks_value()
        if res is not None and hasattr(res, "breakpoints"):
            try:
                n_breaks_fallback = max(int(len(res.breakpoints)), 1)
            except Exception:
                pass

        def _safe_float(v: Any, default: float) -> float:
            try:
                x = float(v)
                if np.isfinite(x):
                    return x
            except Exception:
                pass
            return float(default)

        def _safe_int(v: Any, default: int) -> int:
            try:
                x = int(round(float(v)))
                return x
            except Exception:
                return int(default)

        return {
            "dilutions": list(params.get("dilutions", self.dil_box.selected_values() or self.dil_box.values())),
            "spar": _safe_float(params.get("spar"), self._kp_spar_value()),
            "n_breaks": max(1, _safe_int(params.get("n_breaks"), n_breaks_fallback)),
            "flat_quantile": _safe_float(params.get("flat_quantile"), self._kp_flat_q_value()),
            "rise_quantile": _safe_float(params.get("rise_quantile"), self._kp_rise_q_value()),
            "segment_selection_mode": str(params.get("segment_selection_mode", self._kp_segment_mode_value()) or "legacy"),
            "segment_min_internal_breaks": max(1, _safe_int(params.get("segment_min_internal_breaks"), self._kp_segment_bounds()[0])),
            "segment_max_internal_breaks": max(1, _safe_int(params.get("segment_max_internal_breaks"), self._kp_segment_bounds()[1])),
            "temp_min": params.get("temp_min"),
            "temp_max": params.get("temp_max"),
            "temp_range_enabled": bool(params.get("temp_range_enabled", params.get("temp_min") is not None or params.get("temp_max") is not None)),
            "point_size": max(2, _safe_int(params.get("point_size"), self._kp_point_size_value())),
            "line_width": max(0.1, _safe_float(params.get("line_width"), self._kp_line_width_value())),
            "show_breakpoints": bool(params.get("show_breakpoints", self.chk_show_breakpoints.isChecked())),
            "show_grid": bool(params.get("show_grid", self.chk_grid.isChecked())),
        }

    def _kp_summary_for_display(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if out.empty:
            return out
        for c in out.columns:
            if str(c).strip() == "Sample":
                out[c] = out[c].astype(str)
                continue
            num = pd.to_numeric(out[c], errors="coerce")
            out[c] = num.map(lambda v: "" if not np.isfinite(v) else f"{float(v):.6g}")
        return out

    def _rebuild_kp_report_summary_from_items(self) -> None:
        if not isinstance(self._kp_report_preview, dict):
            return
        sample_items = self._kp_report_preview.get("sample_items")
        if not isinstance(sample_items, list):
            return
        req = int(max(1, int(self._kp_report_preview.get("requested_breaks", 1))))
        rows: list[dict[str, Any]] = []
        for it in sample_items:
            if not isinstance(it, dict):
                continue
            sample = str(it.get("sample", "")).strip()
            res = it.get("result")
            if not sample or res is None:
                continue
            if hasattr(res, "breakpoints"):
                try:
                    req = max(req, int(len(res.breakpoints)))
                except Exception:
                    pass
            rows.append(kp_summary_row(sample, res, req))
        summary_df = pd.DataFrame(rows)
        ordered = ["Sample"]
        for i in range(1, req + 1):
            ordered.extend([f"Kneepoint{i}_T", f"Kneepoint{i}_nm"])
        for c in ordered:
            if c not in summary_df.columns:
                summary_df[c] = np.nan
        summary_df = summary_df[ordered].reset_index(drop=True)
        self._kp_report_preview["requested_breaks"] = req
        self._kp_report_preview["summary_df"] = summary_df
        self._kp_report_preview["parameters_df"] = kp_parameters_df_from_sample_items(sample_items)

    def _render_kp_report_preview(self, preview: dict[str, Any]) -> None:
        summary_df = preview.get("summary_df")
        sample_items = preview.get("sample_items")
        if not isinstance(summary_df, pd.DataFrame) or not isinstance(sample_items, list):
            self._invalidate_report_preview("Preview payload invalid.")
            return

        summary_view_df = self._kp_summary_for_display(summary_df)
        _render_table(self.table_kp_report_summary, summary_view_df, max_rows=300)
        _render_table(self.table_bp, summary_view_df, max_rows=200)

        show_grid = bool(preview.get("show_grid", self.chk_grid.isChecked()))
        show_breakpoints = bool(preview.get("show_breakpoints", self.chk_show_breakpoints.isChecked()))
        nm_axis_label = _coerce_nm_axis_label(preview.get("nm_axis_label") or self.state.nm_axis_label)
        fig = kp_build_full_report_figure(
            summary_df,
            [it for it in sample_items if isinstance(it, dict)],
            ncols=2,
            show_breakpoints=show_breakpoints,
            show_grid=show_grid,
            nm_axis_label=nm_axis_label,
        )
        bg_mode = str(self.cb_bg_mode.currentData() or "white")
        tick_style = str(self.cb_tick_style.currentData() or "auto")
        _apply_plot_background(fig, bg_mode)
        _apply_y_tick_style(fig, tick_style, bg_mode=bg_mode, y_is_log=True)
        _set_plotly_html(self.kp_report_preview_view, fig)

        self._clear_layout_widgets(self.kp_report_cards_layout)
        self._kp_report_card_views = []
        card_index = 0
        for it in sample_items:
            if not isinstance(it, dict):
                continue
            sample_name = str(it.get("sample", "")).strip()
            if not sample_name:
                continue
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(6, 6, 6, 6)
            card_lay.setSpacing(6)
            edited_tag = " [edited]" if bool(it.get("edited", False)) else ""
            row_hdr = QHBoxLayout()
            row_hdr.setContentsMargins(0, 0, 0, 0)
            row_hdr.setSpacing(6)
            btn_title = QPushButton(f"{sample_name}{edited_tag}")
            btn_title.setToolTip("Open editor for this sample")
            btn_title.clicked.connect(lambda _checked=False, s=sample_name: self._request_kp_report_sample_editor(s))
            row_hdr.addWidget(btn_title, 1)
            btn_edit = QPushButton("Edit")
            btn_edit.setToolTip("Open editor for this sample")
            btn_edit.clicked.connect(lambda _checked=False, s=sample_name: self._request_kp_report_sample_editor(s))
            row_hdr.addWidget(btn_edit, 0)
            card_lay.addLayout(row_hdr)

            fig_single = it.get("figure")
            if not isinstance(fig_single, go.Figure):
                pts = it.get("points")
                res = it.get("result")
                if isinstance(pts, pd.DataFrame) and res is not None:
                    fig_single, _ = kp_build_single_sample_figure(
                        sample_name,
                        pts,
                        res,
                        point_size=int(preview.get("point_size", self._kp_point_size_value())),
                        line_width=float(preview.get("line_width", self._kp_line_width_value())),
                        show_breakpoints=show_breakpoints,
                        show_grid=show_grid,
                        nm_axis_label=nm_axis_label,
                    )
                    it["figure"] = fig_single
            if isinstance(fig_single, go.Figure):
                fig_card = go.Figure(fig_single)
                _apply_plot_background(fig_card, bg_mode)
                _apply_y_tick_style(fig_card, tick_style, bg_mode=bg_mode, y_is_log=True)
                sample_view = ClickablePlotView()
                sample_view.setMinimumHeight(500)
                _set_plotly_html(sample_view, fig_card)
                sample_view.clicked.connect(lambda s=sample_name: self._request_kp_report_sample_editor(s))
                card_lay.addWidget(sample_view)
                self._kp_report_card_views.append(sample_view)
            cols = max(int(getattr(self, "_kp_report_cards_columns", 2)), 1)
            rr = card_index // cols
            cc = card_index % cols
            self.kp_report_cards_layout.addWidget(card, rr, cc)
            card_index += 1
        cols = max(int(getattr(self, "_kp_report_cards_columns", 2)), 1)
        for cc in range(cols):
            self.kp_report_cards_layout.setColumnStretch(cc, 1)
        self.btn_kp_report_download.setEnabled(True)
        self.lbl_kp_preview_state.setText(
            "Preview ready. Click a sample plot (or Edit button) to tune parameters for that sample before download."
        )

    def _open_kp_report_sample_editor(self, sample: str) -> None:
        if not isinstance(self._kp_report_preview, dict):
            QMessageBox.information(self, "Report preview", "Build report preview first.")
            return
        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return
        sample_items = self._kp_report_preview.get("sample_items")
        if not isinstance(sample_items, list):
            return
        target: dict[str, Any] | None = None
        for it in sample_items:
            if isinstance(it, dict) and str(it.get("sample", "")).strip() == str(sample).strip():
                target = it
                break
        if target is None:
            QMessageBox.warning(self, "Sample not found", f"Sample '{sample}' not found in current preview.")
            return

        defaults = self._kp_report_defaults_for_editor(target)
        preview_dils = self._kp_report_preview.get("dilutions")
        dil_values = list(preview_dils) if isinstance(preview_dils, (list, tuple, set)) else list(self.dil_box.values())
        initial_dils = list(defaults.get("dilutions", dil_values))
        parent_widget = self.window() if isinstance(self.window(), QWidget) else self
        dlg = KneepointSampleEditorDialog(
            parent=parent_widget,
            curves_df=table.df.copy(),
            sample=str(target.get("sample", "")),
            size=str(target.get("size", self.cb_size.currentText().strip() or self._kp_report_preview.get("size", ""))),
            dilution_values=dil_values,
            initial_dilutions=initial_dils,
            defaults=defaults,
            nm_axis_label=_coerce_nm_axis_label(self.state.nm_axis_label),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            tick_style=str(self.cb_tick_style.currentData() or "auto"),
        )
        self._kp_active_editor = dlg
        self._kp_active_editor_sample = str(sample).strip()
        dlg.setModal(True)
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.finished.connect(lambda code, s=str(sample).strip(): self._on_kp_report_sample_editor_finished(s, int(code)))
        self.log.append(f"[kp-report] editor opened for sample={sample}")
        dlg.show()
        try:
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            pass

    def _on_kp_report_sample_editor_finished(self, sample: str, code: int) -> None:
        dlg = self._kp_active_editor
        self.log.append(f"[kp-report] editor closed for sample={sample} | code={int(code)}")
        try:
            if int(code) != int(QDialog.Accepted):
                self.log.append(f"[kp-report] editor closed without apply: {sample}")
                return
            if not isinstance(self._kp_report_preview, dict):
                self.log.append(f"[kp-report] editor apply ignored (preview missing): {sample}")
                return
            sample_items = self._kp_report_preview.get("sample_items")
            if not isinstance(sample_items, list):
                self.log.append(f"[kp-report] editor apply ignored (sample list missing): {sample}")
                return
            target: dict[str, Any] | None = None
            for it in sample_items:
                if isinstance(it, dict) and str(it.get("sample", "")).strip() == str(sample).strip():
                    target = it
                    break
            if target is None:
                self.log.append(f"[kp-report] editor apply ignored (sample not found): {sample}")
                return
            payload = dlg.applied_payload() if isinstance(dlg, KneepointSampleEditorDialog) else None
            if not isinstance(payload, dict):
                self.log.append(f"[kp-report] editor returned empty payload: {sample}")
                return
            target["points"] = payload.get("points")
            target["result"] = payload.get("res")
            target["figure"] = payload.get("figure")
            target["params"] = payload.get("params", {})
            target["size"] = payload.get("size", target.get("size"))
            target["edited"] = True
            pmap = target.get("params")
            if isinstance(pmap, dict):
                self._kp_report_loaded_overrides[str(sample)] = dict(pmap)
            self._rebuild_kp_report_summary_from_items()
            self._render_kp_report_preview(self._kp_report_preview)
            self.lbl_kp_report_status.setText(f"Report status: preview updated for sample {sample}")
            self.log.append(f"[kp-report] sample override applied: {sample}")
        finally:
            self._kp_active_editor = None
            self._kp_active_editor_sample = ""
            try:
                if isinstance(dlg, QWidget):
                    dlg.deleteLater()
            except Exception:
                pass

    def _on_kp_report_preview_ready(self, result: object) -> None:
        self._set_kp_report_busy(False)
        self.pb_kp_report.setValue(100)
        if not isinstance(result, dict):
            raise ValueError("Invalid report preview payload.")
        self._kp_report_preview = result
        self._kp_report_loaded_overrides = {
            str(it.get("sample", "")).strip(): dict(it.get("params", {}))
            for it in result.get("sample_items", [])
            if isinstance(it, dict) and str(it.get("sample", "")).strip() and isinstance(it.get("params", {}), dict)
        }
        self._rebuild_kp_report_summary_from_items()
        self._render_kp_report_preview(self._kp_report_preview)
        status = str(result.get("status", "Preview created"))
        skipped = result.get("skipped")
        lines: list[str] = [status]
        if isinstance(skipped, list) and len(skipped) > 0:
            lines.append("")
            lines.append("Skipped samples:")
            for s in skipped:
                lines.append(f"- {s}")
        lines.append("")
        lines.append("Ready to download. Click 'Download Report (.zip)' when satisfied.")
        self.lbl_kp_report_status.setText(f"Report status: {status}")
        self.kp_report_artifacts.setPlainText("\n".join(lines))
        self.log.append(f"[kp-report] {status}")

    def _on_kp_report_finished(self, result: object) -> None:
        self._set_kp_report_busy(False)
        self.pb_kp_report.setValue(100)
        artifact_txt = "No artifacts."
        if isinstance(result, dict):
            status = str(result.get("status", "Report created"))
            out_path = Path(str(result.get("output_path", "")).strip()) if result.get("output_path") else None
            lines: list[str] = []
            if out_path is not None and str(out_path):
                lines.append(f"ZIP: {out_path}")
                if out_path.exists():
                    lines.append("")
                    lines.append("Contents:")
                    try:
                        with zipfile.ZipFile(out_path, "r") as zf:
                            names = sorted(zf.namelist())
                        for nm in names:
                            lines.append(f"- {nm}")
                    except Exception as exc:
                        lines.append(f"(Could not list ZIP contents: {exc})")
            skipped = result.get("skipped")
            if isinstance(skipped, list) and len(skipped) > 0:
                lines.append("")
                lines.append("Skipped samples:")
                for s in skipped:
                    lines.append(f"- {s}")
            if lines:
                artifact_txt = "\n".join(lines)
        else:
            status = "Report created"
        self.lbl_kp_report_status.setText(f"Report status: {status}")
        self.kp_report_artifacts.setPlainText(artifact_txt)
        self.log.append(f"[kp-report] {status}")

    def _on_kp_report_cancelled(self, msg: str) -> None:
        self._set_kp_report_busy(False)
        self.pb_kp_report.setValue(0)
        txt = str(msg or "Report cancelled by user.")
        self.lbl_kp_report_status.setText(f"Report status: CANCELLED | {txt}")
        self.kp_report_artifacts.setPlainText(f"Report cancelled.\n\n{txt}")
        self.log.append(f"[kp-report] CANCELLED: {txt}")

    def _on_kp_report_failed(self, msg: str) -> None:
        self._set_kp_report_busy(False)
        self.pb_kp_report.setValue(0)
        QMessageBox.critical(self, "Report error", str(msg))
        self.lbl_kp_report_status.setText(f"Report status: ERROR | {msg}")
        self.kp_report_artifacts.setPlainText(f"Report failed.\n\n{msg}")
        self.log.append(f"[kp-report] ERROR: {msg}")

    def _on_kp_report_thread_finished(self) -> None:
        self._kp_report_worker = None
        self._kp_report_thread = None
        self._kp_report_job_kind = ""

    def _cancel_kp_report(self) -> None:
        if self._kp_report_worker is None:
            return
        try:
            self._kp_report_worker.request_cancel()
            self.lbl_kp_report_status.setText("Report status: cancel requested...")
            self.log.append("[kp-report] cancel requested")
        except Exception as exc:
            self.log.append(f"[kp-report] cancel request error: {exc}")

    def _start_kp_report_worker(self, *, fn: Any, kwargs: dict[str, Any], mode: str, start_msg: str) -> None:
        self._kp_report_job_kind = str(mode or "")
        self._set_kp_report_busy(True)
        self.lbl_kp_report_status.setText(f"Report status: 0% | {start_msg}")
        self.kp_report_artifacts.setPlainText(start_msg)
        self.log.append(f"[kp-report] started | mode={mode}")

        thread = QThread(self)
        worker = LongTaskWorker(
            fn,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_kp_report_progress)
        worker.succeeded.connect(self._on_kp_report_worker_succeeded)
        worker.failed.connect(self._on_kp_report_failed)
        worker.cancelled.connect(self._on_kp_report_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_kp_report_thread_finished)

        self._kp_report_thread = thread
        self._kp_report_worker = worker
        thread.start()

    def _on_kp_report_worker_succeeded(self, result: object) -> None:
        mode = str(self._kp_report_job_kind or "")
        if mode == "preview":
            try:
                self._on_kp_report_preview_ready(result)
            except Exception as exc:
                self._on_kp_report_failed(str(exc))
            return
        self._on_kp_report_finished(result)

    def _create_kp_report(self) -> None:
        if self._kp_report_thread is not None and self._kp_report_thread.isRunning():
            QMessageBox.information(self, "Report running", "A report generation is already running.")
            return

        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return
        report_samples = self._report_selected_samples()
        if len(report_samples) == 0:
            QMessageBox.warning(self, "Missing selection", "Select at least one report sample.")
            return
        size = self.cb_size.currentText().strip()
        if not size:
            QMessageBox.warning(self, "Missing selection", "Select Size in Kneepoint settings.")
            return
        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        if len(selected_dils) == 0:
            QMessageBox.warning(self, "Missing selection", "Select at least one Dilution.factor.")
            return

        tmin, tmax = self._kp_temp_range_values()
        seg_min, seg_max = self._kp_segment_bounds()

        kwargs = dict(
            curves_df=table.df.copy(),
            report_samples=report_samples,
            size=size,
            dilutions=selected_dils,
            temp_min=tmin,
            temp_max=tmax,
            spar=self._kp_spar_value(),
            nbreaks=self._kp_nbreaks_value(),
            flat_q=self._kp_flat_q_value(),
            rise_q=self._kp_rise_q_value(),
            point_size=self._kp_point_size_value(),
            line_width=self._kp_line_width_value(),
            show_breakpoints=self.chk_show_breakpoints.isChecked(),
            show_grid=self.chk_grid.isChecked(),
            nm_axis_label=_coerce_nm_axis_label(self.state.nm_axis_label),
            segment_selection_mode=self._kp_segment_mode_value(),
            segment_min_internal_breaks=seg_min,
            segment_max_internal_breaks=seg_max,
            sample_overrides=dict(self._kp_report_loaded_overrides),
        )
        self._invalidate_report_preview("Generating report preview...")
        self._start_kp_report_worker(
            fn=build_kneepoint_report_preview,
            kwargs=kwargs,
            mode="preview",
            start_msg="Building report preview...",
        )

    def _download_kp_report(self) -> None:
        if self._kp_report_thread is not None and self._kp_report_thread.isRunning():
            QMessageBox.information(self, "Report running", "A report task is already running.")
            return
        if not isinstance(self._kp_report_preview, dict):
            QMessageBox.information(self, "Missing preview", "Build report preview first.")
            return
        out_dir = Path(self.in_kp_report_dir.text().strip() or str(Path.home() / "Desktop"))
        file_prefix = self.in_kp_report_prefix.text().strip() or "kneepoint_report"
        kwargs = dict(
            preview=self._kp_report_preview,
            output_dir=out_dir,
            file_prefix=file_prefix,
            show_breakpoints=self.chk_show_breakpoints.isChecked(),
            show_grid=self.chk_grid.isChecked(),
            nm_axis_label=_coerce_nm_axis_label(self.state.nm_axis_label),
        )
        self._start_kp_report_worker(
            fn=export_kneepoint_report_zip_from_preview,
            kwargs=kwargs,
            mode="download",
            start_msg="Exporting report ZIP...",
        )

    def _run_kp(self) -> None:
        if self._kp_run_running():
            QMessageBox.information(self, "Run in progress", "Kneepoint is already running.")
            return
        table = self._active_curves_table()
        if table is None:
            QMessageBox.warning(self, "Missing input", "Load curves first.")
            return

        sample = self.cb_sample.currentText().strip()
        size = self.cb_size.currentText().strip()
        if not sample:
            QMessageBox.warning(self, "Missing selection", "Select a sample.")
            return
        if not size:
            QMessageBox.warning(self, "Missing selection", "Select a size.")
            return

        selected_dils = self.dil_box.selected_values() or self.dil_box.values()
        if len(selected_dils) == 0:
            QMessageBox.warning(self, "Missing selection", "Select at least one dilution.")
            return

        tmin, tmax = self._kp_temp_range_values()
        seg_min, seg_max = self._kp_segment_bounds()

        kwargs = dict(
            curves_df=table.df.copy(),
            sample=sample,
            size=size,
            dilutions=list(selected_dils),
            spar=self._kp_spar_value(),
            n_breaks=self._kp_nbreaks_value(),
            flat_quantile=self._kp_flat_q_value(),
            rise_quantile=self._kp_rise_q_value(),
            segment_selection_mode=self._kp_segment_mode_value(),
            segment_min_internal_breaks=seg_min,
            segment_max_internal_breaks=seg_max,
            temp_min=tmin,
            temp_max=tmax,
        )

        self._set_kp_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append(f"[kp] started | sample={sample} size={size}")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_kneepoint_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_kp_progress)
        worker.succeeded.connect(self._on_kp_succeeded)
        worker.failed.connect(self._on_kp_failed)
        worker.cancelled.connect(self._on_kp_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_kp_thread_finished)

        self._kp_thread = thread
        self._kp_worker = worker
        thread.start()

    def _on_kp_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._kp_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _apply_kp_payload(self, payload: dict[str, Any]) -> None:
        sample = str(payload.get("sample") or "")
        size = str(payload.get("size") or "")
        points = payload.get("points")
        res = payload.get("res")
        if not isinstance(points, pd.DataFrame):
            raise ValueError("Invalid Kneepoint payload: missing points.")
        if res is None:
            raise ValueError("Invalid Kneepoint payload: missing analysis result.")

        self._draw_kp_plotly(
            sample=sample,
            points=points,
            res=res,
            point_size=self._kp_point_size_value(),
            line_width=self._kp_line_width_value(),
            show_breakpoints=self.chk_show_breakpoints.isChecked(),
            show_grid=self.chk_grid.isChecked(),
            y_title=_coerce_nm_axis_label(self.state.nm_axis_label),
        )

        bp_rows = []
        bp_pairs = self._kp_breakpoint_pairs(res)
        for i, (bp, nm_bp) in enumerate(bp_pairs, start=1):
            y_bp_log = float(np.log10(nm_bp)) if nm_bp > 0 else np.nan
            bp_rows.append({"knee": i, "T_C": float(bp), "nm": float(nm_bp), "log10_nm": y_bp_log})
        bp_df = pd.DataFrame(bp_rows)
        _render_table(self.table_bp, bp_df, max_rows=40)

        all_bp_count = int(res.piecewise_params.get("n_segments", 1)) - 1
        candidate_bp_count = int(res.piecewise_params.get("n_knees_candidates_plus_to_minus", len(res.breakpoints)))
        kept_bp_count = int(res.piecewise_params.get("n_knees_selected", len(res.breakpoints)))
        req_breaks = int(res.piecewise_params.get("n_breaks_requested", self._kp_nbreaks_value()))
        int_breaks = int(res.piecewise_params.get("n_breaks_internal", all_bp_count))
        seg_mode = str(res.piecewise_params.get("segment_selection_mode", self._kp_segment_mode_value()))
        seg_by = str(res.piecewise_params.get("segment_selection_selected_by", "legacy"))
        status = (
            f"Breakpoints selected(+->-)={kept_bp_count} | candidates(+->-)={candidate_bp_count} | "
            f"non_knee={max(all_bp_count - candidate_bp_count, 0)} | requested={req_breaks} internal_fit={int_breaks} | "
            f"segment_mode={seg_mode} ({seg_by}) | "
            f"Breakpoints (T)={np.round(res.breakpoints, 3).tolist()} | "
            f"Bootstrap ok={res.bootstrap.get('n_boot_ok', 0)} | "
            f"CV mse_mean={res.cv.get('mse_mean', float('nan')):.3g}"
        )
        self.lbl_status.setText(f"Status: OK | {status}")
        self.log.append(f"[kp] sample={sample} size={size} rows={len(points)} | {status}")
        self._kp_last_payload = {
            "sample": sample,
            "size": size,
            "points": points.copy(),
            "res": res,
        }

    def _on_kp_succeeded(self, payload: object) -> None:
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid Kneepoint worker payload.")
            self._apply_kp_payload(payload)
        except Exception as exc:
            self._on_kp_failed(str(exc))

    def _on_kp_failed(self, msg: str) -> None:
        txt = str(msg or "Unknown error.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: ERROR | {txt}")
        self.log.append(f"[kp] ERROR: {txt}")
        self._clear_kp_outputs()

    def _on_kp_cancelled(self, msg: str) -> None:
        txt = str(msg or "Cancelled.")
        self.pb_run.setValue(0)
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[kp] CANCELLED: {txt}")

    def _on_kp_thread_finished(self) -> None:
        self._kp_worker = None
        self._kp_thread = None
        self._set_kp_busy(False)

    def _cancel_kp_run(self) -> None:
        if self._kp_worker is None:
            return
        try:
            self._kp_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[kp] cancel requested")
        except Exception as exc:
            self.log.append(f"[kp] cancel request error: {exc}")

    def _draw_kp_plotly(
        self,
        *,
        sample: str,
        points: pd.DataFrame,
        res: Any,
        point_size: int,
        line_width: float,
        show_breakpoints: bool,
        show_grid: bool,
        y_title: str,
    ) -> None:
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(f"Kneepoint - {sample}"))
        fig.update_layout(height=520, legend_title_text="")

        if len(points) == 0:
            fig.add_annotation(
                text="No points available after filtering.",
                xref="paper",
                yref="paper",
                x=0.01,
                y=0.95,
                showarrow=False,
            )
            self._last_plot_fig = go.Figure(fig)
            _set_plotly_html(self.plot_view, fig)
            return

        loc_arr = (
            points["Location"].astype(str).to_numpy()
            if "Location" in points.columns
            else np.repeat("(no Location)", len(points))
        )
        fig.add_trace(
            go.Scatter(
                x=points["Freezing.temperature"],
                y=points["nm"],
                mode="markers",
                name="Raw points",
                marker=dict(
                    size=int(point_size),
                    color="#93c5fd",
                    line=dict(color="#111827", width=0.5),
                    opacity=0.85,
                ),
                customdata=np.stack([np.repeat(str(sample), len(points)), loc_arr], axis=1),
                hovertemplate=(
                    "Sample=%{customdata[0]}<br>Location=%{customdata[1]}<br>T=%{x:.2f}°C<br>nm=%{y:.3e}<extra></extra>"
                ),
            )
        )

        g = res.spline_grid
        spline_nm = np.power(10.0, pd.to_numeric(g["log_nm_spline"], errors="coerce").to_numpy(dtype=float))
        fig.add_trace(
            go.Scatter(
                x=g["Freezing.temperature"],
                y=spline_nm,
                mode="lines",
                name="Smoothing spline",
                line=dict(color="#60a5fa", width=float(line_width)),
                hovertemplate=f"Sample={sample}<br>T=%{{x:.2f}}°C<br>nm=%{{y:.3e}}<extra></extra>",
            )
        )

        for i, (bp, nm_bp) in enumerate(self._kp_breakpoint_pairs(res), start=1):
            fig.add_vline(x=float(bp), line_width=float(line_width), line_dash="dash", line_color="#f59e0b")
            y_bp = float(nm_bp) if nm_bp > 0 else np.nan
            fig.add_trace(
                go.Scatter(
                    x=[float(bp)],
                    y=[y_bp],
                    mode="markers+text",
                    name=f"Knee {i}",
                    text=[f"K{i}: {bp:.2f}°C\n{nm_bp:.2e}"],
                    textposition="top center",
                    marker=dict(size=int(point_size) + 2, color="#f59e0b", line=dict(color="#111827", width=0.6)),
                    hovertemplate=f"Sample={sample}<br>Knee {i}<br>T={bp:.2f}°C<br>nm={nm_bp:.3e}<extra></extra>",
                )
            )

        if bool(show_breakpoints):
            x_pw, y_pw, _bps_internal = self._kp_piecewise_overlay_data(res)
            if x_pw.size > 0 and y_pw.size == x_pw.size:
                fig.add_trace(
                    go.Scatter(
                        x=x_pw,
                        y=y_pw,
                        mode="lines",
                        name="Piecewise segments",
                        line=dict(color="#7c3aed", width=max(1.0, float(line_width) - 0.2), dash="dot"),
                        hovertemplate=f"Sample={sample}<br>T=%{{x:.2f}}°C<br>piecewise nm=%{{y:.3e}}<extra></extra>",
                    )
                )

        y_all = np.concatenate([points["nm"].to_numpy(dtype=float), spline_nm])
        y_all = y_all[np.isfinite(y_all) & (y_all > 0)]
        if y_all.size > 0:
            exp_min = int(np.floor(np.log10(float(np.nanmin(y_all)))))
            exp_max = int(np.ceil(np.log10(float(np.nanmax(y_all)))))
            exps = [e for e in range(exp_min, exp_max + 1) if -300 <= int(e) <= 300]
            if len(exps) == 0:
                exps = [int(np.clip(exp_min, -300, 300)), int(np.clip(exp_max, -300, 300))]
                exps = sorted(set(exps))
            fig.update_yaxes(
                type="log",
                tickvals=[float(np.power(10.0, float(e))) for e in exps],
                ticktext=[f"10<sup>{e}</sup>" for e in exps],
                showgrid=bool(show_grid),
                title_text=_format_math_exponents(y_title),
                exponentformat="power",
                showexponent="all",
            )
        else:
            fig.update_yaxes(
                type="log",
                showgrid=bool(show_grid),
                title_text=_format_math_exponents(y_title),
                exponentformat="power",
                showexponent="all",
            )
        fig.update_xaxes(
            title_text="Freezing.temperature",
            showgrid=bool(show_grid),
            zeroline=False,
            showline=True,
            linecolor="#f8fafc",
            linewidth=1.8,
            mirror=True,
            ticks="outside",
            ticklen=8,
            tickcolor="#f8fafc",
            showticklabels=True,
            automargin=True,
            tickfont=dict(color="#f8fafc", size=12),
            title_font=dict(color="#f8fafc", size=13),
            title_standoff=12,
        )
        fig.update_yaxes(
            showline=True,
            linecolor="#f8fafc",
            linewidth=1.8,
            mirror=True,
            ticks="outside",
            ticklen=8,
            tickcolor="#f8fafc",
            showticklabels=True,
            automargin=True,
            tickfont=dict(color="#f8fafc", size=12),
            title_font=dict(color="#f8fafc", size=13),
            title_standoff=12,
            gridcolor="rgba(148,163,184,0.18)",
        )
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=True,
        )

        self._last_plot_fig = go.Figure(fig)
        _set_plotly_html(self.plot_view, fig)

    def _on_nm_axis_label_changed(self) -> None:
        self.log.append(f"[kp] nm axis label updated: {self.state.nm_axis_label}")

    def _export_plot(self) -> None:
        if self._last_plot_fig is None:
            QMessageBox.information(self, "No plot", "Run Kneepoint first.")
            return
        try:
            cfg = self.export_plot.config()
            saved = _save_plotly_figure_local(self, self._last_plot_fig, cfg)
            if saved is not None:
                self.log.append(f"[kp] export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported kneepoint plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[kp] export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._kp_worker is not None:
                self._kp_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._kp_thread)
        self._kp_worker = None
        self._kp_thread = None
        self._set_kp_busy(False)
        try:
            if self._kp_report_worker is not None:
                self._kp_report_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._kp_report_thread)
        self._kp_report_worker = None
        self._kp_report_thread = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


class BoxplotsTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._last_plot_fig: go.Figure | None = None
        self._box_thread: QThread | None = None
        self._box_worker: LongTaskWorker | None = None
        self._box_state_thread: QThread | None = None
        self._box_state_worker: LongTaskWorker | None = None
        self._box_pending_refresh: tuple[pd.DataFrame, pd.DataFrame] | None = None
        self._box_state_token: int = 0
        self._box_run_queued: bool = False
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self.state.metadata_changed.connect(self._on_state_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)
        self._on_state_changed()

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: curves=none | metadata=none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Update Boxplot")
        self.btn_run.clicked.connect(self._run_boxplot_async)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_boxplot_run)
        self.btn_cancel.setVisible(True)
        run_row.addWidget(self.btn_cancel)

        form = QFormLayout()
        self.cb_y = QComboBox()
        self.cb_y.addItems(["nM10", "nM15"])
        form.addRow("Response (y)", self.cb_y)

        self.cb_size = QComboBox()
        self.cb_size.addItems(["b_5_m", "b_02_m"])
        form.addRow("Size", self.cb_size)

        self.cb_group = QComboBox()
        form.addRow("Group by", self.cb_group)
        left_lay.addLayout(form)

        self.chk_bin = QCheckBox("Bin numeric Group by into ranges")
        self.chk_bin.setChecked(False)
        left_lay.addWidget(self.chk_bin)

        bin_form = QFormLayout()
        self.cb_bin_mode = QComboBox()
        self.cb_bin_mode.addItems(["count", "width"])
        bin_form.addRow("Range mode", self.cb_bin_mode)

        self.sp_bin_count = SliderNumberInput(min_value=2, max_value=200, value=6, decimals=0, step=1)
        bin_form.addRow("Number of ranges", self.sp_bin_count)

        self.sp_bin_width = SliderNumberInput(min_value=1e-6, max_value=1e6, value=1.0, decimals=3, step=0.1)
        bin_form.addRow("Range width", self.sp_bin_width)
        left_lay.addLayout(bin_form)

        vis_form = QFormLayout()
        self.cb_scale = QComboBox()
        self.cb_scale.addItems(["log10", "linear"])
        vis_form.addRow("Y-axis scale", self.cb_scale)

        self.chk_points = QCheckBox("Show points (jitter)")
        self.chk_points.setChecked(True)
        left_lay.addWidget(self.chk_points)

        self.sp_stroke = SliderNumberInput(min_value=0.1, max_value=4.0, value=0.9, decimals=2, step=0.05)
        vis_form.addRow("Border width", self.sp_stroke)

        self.sp_pt_size = SliderNumberInput(min_value=2, max_value=14, value=5, decimals=0, step=1)
        vis_form.addRow("Point size", self.sp_pt_size)
        left_lay.addLayout(vis_form)

        style_form = QFormLayout()
        self.cb_palette = QComboBox()
        _init_palette_combo(self.cb_palette, include_default=True, default_value="set1")
        style_form.addRow("Palette", self.cb_palette)

        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(True)
        style_form.addRow("Grid", self.chk_grid)

        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_form.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_form.addRow("Y-axis tick style", self.cb_tick_style)

        self.in_plot_title = QLineEdit("Boxplots")
        style_form.addRow("Main title", self.in_plot_title)
        self.in_plot_subtitle = QLineEdit("")
        style_form.addRow("Main subtitle", self.in_plot_subtitle)
        left_lay.addLayout(style_form)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_plot = PlotExportBox("Boxplot export", default_stem="boxplots")
        self.export_plot.btn_export.clicked.connect(self._export_plot)
        left_lay.addWidget(self.export_plot)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        plot_panel = QWidget()
        plot_lay = QVBoxLayout(plot_panel)
        plot_lay.setContentsMargins(0, 0, 0, 0)
        plot_lay.setSpacing(6)
        plot_lay.addWidget(QLabel("Boxplots (nM10 / nM15)"))
        self.plot_view = QWebEngineView()
        self.plot_view.setMinimumHeight(460)
        plot_lay.addWidget(self.plot_view, stretch=1)

        right_scroll = _build_vertical_scroll_stack(
            [plot_panel],
            min_width=760,
            spacing=10,
            add_stretch=True,
        )

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=360,
            max_width=540,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1230])

    def _build_metadata_with_nm(self) -> tuple[pd.DataFrame, str]:
        curves = self._active_curves_table()
        meta = self.state.metadata
        if curves is None:
            raise ValueError("Curves not loaded.")
        if meta is None:
            raise ValueError("Metadata not loaded.")
        return _compute_metadata_with_nm_serialized(curves.df, meta.df, span=0.1, min_points=10)

    def _shape_symbol_options(self) -> list[tuple[str, str]]:
        return [
            ("circle", "circle"),
            ("square", "square"),
            ("diamond", "diamond"),
            ("x", "x"),
            ("triangle-up", "triangle-up"),
            ("triangle-down", "triangle-down"),
            ("cross", "cross"),
            ("star", "star"),
        ]

    def _refresh_location_shape_controls(self, locations: list[str]) -> None:
        previous = {loc: str(cb.currentData() or "") for loc, cb in self._shape_boxes.items()}
        while self.shape_form.rowCount() > 0:
            self.shape_form.removeRow(0)
        self._shape_boxes = {}
        options = self._shape_symbol_options()
        for i, loc in enumerate(locations):
            cb = QComboBox()
            for lbl, val in options:
                cb.addItem(lbl, val)
            default_symbol = previous.get(str(loc), options[i % len(options)][1])
            idx = cb.findData(default_symbol)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            self.shape_form.addRow(str(loc), cb)
            self._shape_boxes[str(loc)] = cb
        self.shape_group.setVisible(len(locations) > 0)

    def _current_location_shape_map(self, loc_levels: list[str]) -> dict[str, str]:
        options = self._shape_symbol_options()
        fallback = {str(loc): options[i % len(options)][1] for i, loc in enumerate(loc_levels)}
        out = dict(fallback)
        for loc, cb in self._shape_boxes.items():
            out[str(loc)] = str(cb.currentData() or out.get(str(loc), "circle"))
        return out

    def _on_state_changed(self) -> None:
        curves = self._active_curves_table()
        meta = self.state.metadata
        src_curves = "none" if curves is None else f"{curves.path.name} ({len(curves.df)} rows)"
        src_meta = "none" if meta is None else f"{meta.path.name} ({len(meta.df)} rows)"
        self.lbl_source.setText(f"Source: curves={src_curves} | metadata={src_meta}")
        if self._box_thread is not None and self._box_thread.isRunning():
            self.log.append("[box] source changed while run active: cancelling previous run.")
            self._cancel_boxplot_run()
            _stop_qthread(self._box_thread)
            self._box_worker = None
            self._box_thread = None
        self._cancel_box_state_refresh()
        enable = curves is not None and meta is not None
        if not enable:
            self._box_run_queued = False
            self.cb_group.clear()
            self._set_box_busy(False)
            return
        self._start_box_state_refresh(curves.df, meta.df)

    def _set_box_busy(self, busy: bool) -> None:
        run_busy = self._box_thread is not None and self._box_thread.isRunning()
        state_busy = self._box_state_thread is not None and self._box_state_thread.isRunning()
        if busy or run_busy:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.export_plot.setEnabled(False)
            return
        if state_busy:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(False)
            self.export_plot.setEnabled(False)
            return
        enable = (self._active_curves_table() is not None) and (self.state.metadata is not None)
        self.btn_run.setEnabled(enable)
        self.btn_cancel.setEnabled(False)
        self.export_plot.setEnabled(enable)

    def _start_box_state_refresh(self, curves_df: pd.DataFrame, metadata_df: pd.DataFrame) -> None:
        if self._box_state_thread is not None and self._box_state_thread.isRunning():
            # Serialize refreshes: keep only latest request and let current worker
            # exit cooperatively to avoid unsafe thread churn during rapid file changes.
            self._cancel_box_state_refresh()
            self._box_pending_refresh = (curves_df.copy(), metadata_df.copy())
            self.log.append("[box] queued options refresh (worker busy).")
            return

        self._box_state_token += 1
        self.lbl_status.setText("Status: Loading boxplot options...")
        self.pb_run.setValue(0)
        self.btn_run.setEnabled(False)
        self.export_plot.setEnabled(False)

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_boxplot_options_payload,
            kwargs=dict(curves_df=curves_df.copy(), metadata_df=metadata_df.copy()),
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_box_state_progress)
        worker.succeeded.connect(self._on_box_state_succeeded)
        worker.failed.connect(self._on_box_state_failed)
        worker.cancelled.connect(self._on_box_state_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_box_state_thread_finished)

        self._box_state_thread = thread
        self._box_state_worker = worker
        thread.start()

    def _cancel_box_state_refresh(self) -> None:
        if self._box_state_worker is None:
            return
        try:
            self._box_state_worker.request_cancel()
        except Exception:
            pass

    def _on_box_state_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._box_state_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: Loading options | {pct}% | {msg}")

    def _on_box_state_succeeded(self, payload: object) -> None:
        if self.sender() is not self._box_state_worker:
            return
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid boxplot options payload.")
            groups = list(payload.get("groups") or [])
            has_sample = bool(payload.get("has_sample"))
            nm_status = str(payload.get("nm_status") or "")
            mode = str(payload.get("mode") or "metadata_with_nm")
            old = self.cb_group.currentText().strip()
            self.cb_group.clear()
            self.cb_group.addItems(groups)
            if old:
                idx = self.cb_group.findText(old)
                if idx >= 0:
                    self.cb_group.setCurrentIndex(idx)
            if self.cb_group.currentIndex() < 0 and self.cb_group.count() > 0:
                if self.cb_group.findText("Location") >= 0:
                    self.cb_group.setCurrentText("Location")
                elif self.cb_group.findText("Sample") >= 0:
                    self.cb_group.setCurrentText("Sample")
            if self.cb_group.count() == 0 and has_sample:
                self.cb_group.addItem("Sample")
            if self.cb_group.count() == 0:
                self.lbl_status.setText("Status: Ready (no Group by options found)")
            else:
                self.lbl_status.setText("Status: Ready")
            self.log.append(f"[box] options ready ({mode}) | {nm_status}")
            self.pb_run.setValue(0)
        except Exception as exc:
            self.cb_group.clear()
            self.log.append(f"[box] metadata_with_nm error: {exc}")
            self.lbl_status.setText(f"Status: ERROR | {exc}")
        finally:
            self._set_box_busy(False)

    def _on_box_state_failed(self, msg: str) -> None:
        if self.sender() is not self._box_state_worker:
            return
        txt = str(msg or "Unknown error.")
        self.cb_group.clear()
        self.log.append(f"[box] metadata_with_nm error: {txt}")
        self.lbl_status.setText(f"Status: ERROR | {txt}")
        self.pb_run.setValue(0)
        self._set_box_busy(False)

    def _on_box_state_cancelled(self, msg: str) -> None:
        if self.sender() is not self._box_state_worker:
            return
        txt = str(msg or "Cancelled.")
        self.log.append(f"[box] options refresh cancelled: {txt}")
        self.lbl_status.setText("Status: waiting")
        self.pb_run.setValue(0)
        self._set_box_busy(False)

    def _on_box_state_thread_finished(self) -> None:
        self._box_state_worker = None
        self._box_state_thread = None
        pending = self._box_pending_refresh
        self._box_pending_refresh = None
        if pending is not None:
            curves_df, metadata_df = pending
            self._start_box_state_refresh(curves_df, metadata_df)
            return
        # Safety re-sync: after async state refresh completion, recompute enabled
        # state from current app data to avoid sticky-disabled Run button when
        # rapid source switches reorder worker signals.
        self._set_box_busy(False)
        if self._box_run_queued:
            self._box_run_queued = False
            QTimer.singleShot(0, self._run_boxplot_async)

    def _on_box_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._box_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _on_box_cancelled(self, msg: str) -> None:
        if self.sender() is not self._box_worker:
            return
        self._set_box_busy(False)
        self.pb_run.setValue(0)
        txt = str(msg or "Cancelled.")
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[box] cancelled: {txt}")

    def _on_box_failed(self, msg: str) -> None:
        if self.sender() is not self._box_worker:
            return
        self._set_box_busy(False)
        self.pb_run.setValue(0)
        txt = str(msg or "Unknown error.")
        self.lbl_status.setText(f"Status: ERROR | {txt}")
        self.log.append(f"[box] ERROR: {txt}")
        self._last_plot_fig = None
        self.plot_view.setHtml("")

    def _on_box_thread_finished(self) -> None:
        self._box_worker = None
        self._box_thread = None
        # Re-evaluate enabled state after run thread fully exits.
        self._set_box_busy(False)

    def _cancel_boxplot_run(self) -> None:
        if self._box_worker is None:
            return
        try:
            self._box_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[box] cancel requested")
        except Exception as exc:
            self.log.append(f"[box] cancel request error: {exc}")

    def _apply_boxplot_payload(self, payload: dict[str, Any]) -> None:
        cfg_dict = dict(payload.get("cfg_dict") or {})
        cfg = BoxplotConfig(**cfg_dict)
        d = payload["d"]
        ycol = str(payload.get("ycol") or "")
        group_plot_col = str(payload.get("group_plot_col") or "")
        x_levels = list(payload.get("x_levels") or [])
        status = str(payload.get("status") or "")
        nm_status = str(payload.get("nm_status") or "")
        self._draw_boxplot(
            d=d,
            ycol=ycol,
            group_col=cfg.group_col,
            group_plot_col=group_plot_col,
            x_levels=x_levels,
            cfg=cfg,
        )
        self.lbl_status.setText(f"Status: OK | {status}")
        self.log.append(f"[box] {status} | {nm_status}")

    def _on_box_succeeded(self, payload: object) -> None:
        if self.sender() is not self._box_worker:
            return
        self._set_box_busy(False)
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid boxplot worker payload.")
            self._apply_boxplot_payload(payload)
        except Exception as exc:
            self._on_box_failed(str(exc))

    def _run_boxplot_async(self) -> None:
        if self._box_thread is not None and self._box_thread.isRunning():
            QMessageBox.information(self, "Run in progress", "Boxplot run is already in progress.")
            return
        if self._box_state_thread is not None and self._box_state_thread.isRunning():
            self._box_run_queued = True
            self.lbl_status.setText("Status: options still loading... run queued.")
            self.log.append("[box] run queued while options refresh is in progress.")
            return
        curves = self._active_curves_table()
        meta = self.state.metadata
        if curves is None:
            QMessageBox.warning(self, "Missing input", "Curves not loaded.")
            return
        if meta is None:
            QMessageBox.warning(self, "Missing input", "Metadata not loaded.")
            return
        group_col = str(self.cb_group.currentText() or "").strip()
        if not group_col:
            msg = "Group by options are not ready yet. Wait for options load, then run again."
            self.lbl_status.setText(f"Status: ERROR | {msg}")
            self.log.append(f"[box] ERROR: {msg}")
            QMessageBox.warning(self, "Boxplot options not ready", msg)
            return

        cfg_dict = dict(
            y_metric=str(self.cb_y.currentText() or "nM10"),
            size_choice=str(self.cb_size.currentText() or "b_5_m"),
            group_col=group_col,
            use_numeric_ranges=self.chk_bin.isChecked(),
            bin_mode=str(self.cb_bin_mode.currentText() or "count"),
            bin_count=int(self.sp_bin_count.value()),
            bin_width=float(self.sp_bin_width.value()),
            scale=str(self.cb_scale.currentText() or "log10"),
            show_points=self.chk_points.isChecked(),
        )
        kwargs = dict(curves_df=curves.df.copy(), metadata_df=meta.df.copy(), cfg_dict=cfg_dict)

        self._set_box_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append("[box] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_boxplot_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_box_progress)
        worker.succeeded.connect(self._on_box_succeeded)
        worker.failed.connect(self._on_box_failed)
        worker.cancelled.connect(self._on_box_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_box_thread_finished)

        self._box_thread = thread
        self._box_worker = worker
        thread.start()

    def _run_boxplot(self) -> None:
        try:
            if self._box_state_thread is not None and self._box_state_thread.isRunning():
                raise ValueError("Boxplot options are still loading. Try again in a moment.")
            curves = self._active_curves_table()
            meta = self.state.metadata
            if curves is None:
                raise ValueError("Curves not loaded.")
            if meta is None:
                raise ValueError("Metadata not loaded.")
            cfg = BoxplotConfig(
                y_metric=str(self.cb_y.currentText() or "nM10"),
                size_choice=str(self.cb_size.currentText() or "b_5_m"),
                group_col=str(self.cb_group.currentText() or "Sample"),
                use_numeric_ranges=self.chk_bin.isChecked(),
                bin_mode=str(self.cb_bin_mode.currentText() or "count"),
                bin_count=int(self.sp_bin_count.value()),
                bin_width=float(self.sp_bin_width.value()),
                scale=str(self.cb_scale.currentText() or "log10"),
                show_points=self.chk_points.isChecked(),
            )
            payload = _compute_boxplot_payload(
                curves_df=curves.df,
                metadata_df=meta.df,
                cfg_dict={
                    "y_metric": cfg.y_metric,
                    "size_choice": cfg.size_choice,
                    "group_col": cfg.group_col,
                    "use_numeric_ranges": cfg.use_numeric_ranges,
                    "bin_mode": cfg.bin_mode,
                    "bin_count": cfg.bin_count,
                    "bin_width": cfg.bin_width,
                    "scale": cfg.scale,
                    "show_points": cfg.show_points,
                },
            )
            self._apply_boxplot_payload(payload)
        except Exception as exc:
            self.lbl_status.setText(f"Status: ERROR | {exc}")
            self.log.append(f"[box] ERROR: {exc}")
            self._last_plot_fig = None
            self.plot_view.setHtml("")

    def _draw_boxplot(
        self,
        *,
        d: pd.DataFrame,
        ycol: str,
        group_col: str,
        group_plot_col: str,
        x_levels: list[str],
        cfg: BoxplotConfig,
    ) -> None:
        title_html = _compose_optional_plot_title(self.in_plot_title.text(), self.in_plot_subtitle.text())
        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base(title_html))
        fig.update_layout(height=500, legend_title_text="")

        palette = _palette_hex_named(self.cb_palette.currentData() or "default")
        color_map = {lvl: palette[i % len(palette)] for i, lvl in enumerate(x_levels)}
        show_points = "all" if cfg.show_points else False
        show_grid = bool(self.chk_grid.isChecked())

        for lvl in x_levels:
            g = d[d[group_plot_col].astype(str) == str(lvl)]
            if len(g) == 0:
                continue
            sample_arr = (
                g["Sample"].astype(str).to_numpy()
                if "Sample" in g.columns
                else np.repeat("(n/a)", len(g))
            )
            location_arr = (
                g["Location"].astype(str).to_numpy()
                if "Location" in g.columns
                else np.repeat("(no Location)", len(g))
            )
            raw_group_arr = g[group_col].astype(str).to_numpy() if group_col in g.columns else np.repeat("(n/a)", len(g))
            fig.add_trace(
                go.Box(
                    x=g[group_plot_col].astype(str),
                    y=g[ycol],
                    name=str(lvl),
                    boxpoints=show_points,
                    jitter=0.35,
                    pointpos=0,
                    marker=dict(
                        size=int(self.sp_pt_size.value()),
                        opacity=0.82,
                        color=color_map.get(lvl, palette[0]),
                        line=dict(color="#111827", width=float(self.sp_stroke.value())),
                    ),
                    line=dict(width=float(self.sp_stroke.value()), color=color_map.get(lvl, palette[0])),
                    fillcolor=_rgba_from_color(color_map.get(lvl, palette[0]), 0.22),
                    customdata=np.stack([sample_arr, location_arr, raw_group_arr], axis=1),
                    hovertemplate=(
                        "Sample=%{customdata[0]}<br>Location=%{customdata[1]}<br>"
                        f"{group_col}=%{{customdata[2]}}<br>{ycol}=%{{y:.3g}}<extra></extra>"
                    ),
                    showlegend=False,
                )
            )

        y_title = (
            f"{_axis_label_with_same_units(self.cb_y.currentText(), self.state.nm_axis_label)} "
            f"[{self.cb_size.currentText()}]"
        )
        _style_axes(
            fig,
            x_title=(f"{group_col} ranges" if group_plot_col == "_box_group_range" else str(group_col)),
            y_title=y_title,
            y_type=("log" if cfg.scale == "log10" else "linear"),
        )
        fig.update_xaxes(categoryorder="array", categoryarray=x_levels, tickangle=35, showgrid=show_grid)
        fig.update_yaxes(showgrid=show_grid)
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=(cfg.scale == "log10"),
        )
        self._last_plot_fig = go.Figure(fig)
        _set_plotly_html(self.plot_view, fig)

    def _on_nm_axis_label_changed(self) -> None:
        self.log.append(f"[box] nm axis label updated: {self.state.nm_axis_label}")

    def _export_plot(self) -> None:
        if self._last_plot_fig is None:
            QMessageBox.information(self, "No plot", "Run Boxplots first.")
            return
        try:
            cfg = self.export_plot.config()
            saved = _save_plotly_figure_local(self, self._last_plot_fig, cfg)
            if saved is not None:
                self.log.append(f"[box] export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported boxplot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[box] export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._box_worker is not None:
                self._box_worker.request_cancel()
        except Exception:
            pass
        try:
            if self._box_state_worker is not None:
                self._box_state_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._box_thread)
        _stop_qthread(self._box_state_thread)
        self._box_worker = None
        self._box_thread = None
        self._box_state_worker = None
        self._box_state_thread = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


class CorrelationsTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._last_plot_fig: go.Figure | None = None
        self._shape_boxes: dict[str, QComboBox] = {}
        self._cor_thread: QThread | None = None
        self._cor_worker: LongTaskWorker | None = None
        self._cor_state_thread: QThread | None = None
        self._cor_state_worker: LongTaskWorker | None = None
        self._cor_pending_refresh: tuple[pd.DataFrame, pd.DataFrame] | None = None
        self._cor_state_token: int = 0
        self._cor_run_queued: bool = False
        self._build_ui()
        self.state.curves_raw_changed.connect(self._on_state_changed)
        self.state.curves_standardized_changed.connect(self._on_state_changed)
        self.state.metadata_changed.connect(self._on_state_changed)
        self.state.nm_axis_label_changed.connect(self._on_nm_axis_label_changed)
        self._on_state_changed()

    def _active_curves_table(self) -> LoadedTable | None:
        if self.state.curves_standardized is not None:
            return self.state.curves_standardized
        return self.state.curves_raw

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self.lbl_source = QLabel("Source: curves=none | metadata=none")
        self.lbl_source.setWordWrap(True)
        left_lay.addWidget(self.lbl_source)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Run Correlation")
        self.btn_run.clicked.connect(self._run_correlation_async)
        run_row.addWidget(self.btn_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_correlation_run)
        self.btn_cancel.setVisible(True)
        run_row.addWidget(self.btn_cancel)

        form = QFormLayout()
        self.cb_method = QComboBox()
        self.cb_method.addItems(["Spearman", "Pearson", "Quadratic Fit", "GAM"])
        form.addRow("Method", self.cb_method)

        self.cb_x = QComboBox()
        form.addRow("X variable", self.cb_x)

        self.cb_y = QComboBox()
        form.addRow("Y variable", self.cb_y)
        left_lay.addLayout(form)

        self.loc_box = MultiSelectBox("Locations")
        self.loc_box.list.itemSelectionChanged.connect(self._on_location_selection_changed)
        left_lay.addWidget(self.loc_box)

        vis = QFormLayout()
        self.sp_pt_size = SliderNumberInput(min_value=2, max_value=14, value=6, decimals=0, step=1)
        vis.addRow("Point size", self.sp_pt_size)

        self.sp_stroke = SliderNumberInput(min_value=0.1, max_value=4.0, value=0.3, decimals=2, step=0.05)
        vis.addRow("Border width", self.sp_stroke)
        left_lay.addLayout(vis)

        style_form = QFormLayout()
        self.cb_palette = QComboBox()
        _init_palette_combo(self.cb_palette, include_default=True, default_value="set1")
        style_form.addRow("Palette", self.cb_palette)

        self.in_title_prefix = QLineEdit("Correlation")
        style_form.addRow("Title prefix", self.in_title_prefix)

        self.chk_grid = QCheckBox("Show grid")
        self.chk_grid.setChecked(True)
        style_form.addRow("Grid", self.chk_grid)

        self.cb_bg_mode = QComboBox()
        self.cb_bg_mode.addItem("Theme", "theme")
        self.cb_bg_mode.addItem("White", "white")
        self.cb_bg_mode.addItem("Soft gray", "soft_gray")
        self.cb_bg_mode.addItem("Warm ivory", "ivory")
        self.cb_bg_mode.addItem("Pale blue", "pale_blue")
        self.cb_bg_mode.addItem("Night navy", "night_navy")
        idx_bg_white = self.cb_bg_mode.findData("white")
        if idx_bg_white >= 0:
            self.cb_bg_mode.setCurrentIndex(idx_bg_white)
        style_form.addRow("Plot background", self.cb_bg_mode)

        self.cb_tick_style = QComboBox()
        self.cb_tick_style.addItem("Auto", "auto")
        self.cb_tick_style.addItem("Standard", "standard")
        self.cb_tick_style.addItem("Scientific", "scientific")
        self.cb_tick_style.addItem("Minimal", "minimal")
        style_form.addRow("Y-axis tick style", self.cb_tick_style)

        self.cb_y_axis_mode = QComboBox()
        self.cb_y_axis_mode.addItem("Auto", "auto")
        self.cb_y_axis_mode.addItem("Log10", "log")
        self.cb_y_axis_mode.addItem("Linear", "linear")
        style_form.addRow("Y-axis mode", self.cb_y_axis_mode)

        self.sp_cor_plot_height = SliderNumberInput(min_value=360, max_value=1200, value=560, decimals=0, step=20)
        style_form.addRow("Plot height", self.sp_cor_plot_height)
        left_lay.addLayout(style_form)

        self.shape_group = QGroupBox("Location shapes")
        self.shape_form = QFormLayout(self.shape_group)
        left_lay.addWidget(self.shape_group)

        self.pb_run = QProgressBar()
        self.pb_run.setRange(0, 100)
        self.pb_run.setValue(0)
        left_lay.addWidget(self.pb_run)

        self.export_plot = PlotExportBox("Correlation export", default_stem="correlations")
        self.export_plot.btn_export.clicked.connect(self._export_plot)
        left_lay.addWidget(self.export_plot)

        self.lbl_status = QLabel("Status: waiting")
        self.lbl_status.setWordWrap(True)
        left_lay.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        left_lay.addWidget(self.log, stretch=1)
        left_lay.addStretch(1)

        plot_panel = QWidget()
        plot_lay = QVBoxLayout(plot_panel)
        plot_lay.setContentsMargins(0, 0, 0, 0)
        plot_lay.setSpacing(6)
        plot_lay.addWidget(QLabel("Correlation Analysis (nM10 / nM15)"))
        self.plot_view = QWebEngineView()
        self.plot_view.setMinimumHeight(int(self.sp_cor_plot_height.value()))
        plot_lay.addWidget(self.plot_view, stretch=1)

        right_scroll = _build_vertical_scroll_stack(
            [plot_panel],
            min_width=760,
            spacing=10,
            add_stretch=True,
        )

        left_panel_sticky = _build_sticky_left_panel(
            run_row,
            left,
            min_width=360,
            max_width=540,
        )

        splitter.addWidget(left_panel_sticky)
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1230])

    def _shape_symbol_options(self) -> list[tuple[str, str]]:
        return [
            ("circle", "circle"),
            ("square", "square"),
            ("diamond", "diamond"),
            ("x", "x"),
            ("triangle-up", "triangle-up"),
            ("triangle-down", "triangle-down"),
            ("cross", "cross"),
            ("star", "star"),
            ("hexagon", "hexagon"),
            ("pentagon", "pentagon"),
        ]

    def _refresh_location_shape_controls(self, locations: list[str]) -> None:
        previous = {loc: str(cb.currentData() or "") for loc, cb in self._shape_boxes.items()}
        while self.shape_form.rowCount() > 0:
            self.shape_form.removeRow(0)
        self._shape_boxes = {}

        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, loc in enumerate(locations):
            cb = QComboBox()
            for lbl, val in options:
                cb.addItem(lbl, val)
            desired = previous.get(str(loc), default_symbols[i % len(default_symbols)])
            idx = cb.findData(desired)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            self.shape_form.addRow(str(loc), cb)
            self._shape_boxes[str(loc)] = cb
        self.shape_group.setVisible(len(locations) > 0)

    def _current_location_shape_map(self, loc_levels: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        options = self._shape_symbol_options()
        default_symbols = [opt[1] for opt in options]
        for i, loc in enumerate(loc_levels):
            key = str(loc)
            cb = self._shape_boxes.get(key)
            if cb is not None:
                out[key] = str(cb.currentData() or default_symbols[i % len(default_symbols)])
            else:
                out[key] = default_symbols[i % len(default_symbols)]
        return out

    def _on_location_selection_changed(self) -> None:
        selected = self.loc_box.selected_values()
        self._refresh_location_shape_controls(selected)

    def _build_metadata_with_nm(self) -> tuple[pd.DataFrame, str]:
        curves = self._active_curves_table()
        meta = self.state.metadata
        if curves is None:
            raise ValueError("Curves not loaded.")
        if meta is None:
            raise ValueError("Metadata not loaded.")
        return _compute_metadata_with_nm_serialized(curves.df, meta.df, span=0.1, min_points=10)

    def _on_state_changed(self) -> None:
        curves = self._active_curves_table()
        meta = self.state.metadata
        src_curves = "none" if curves is None else f"{curves.path.name} ({len(curves.df)} rows)"
        src_meta = "none" if meta is None else f"{meta.path.name} ({len(meta.df)} rows)"
        self.lbl_source.setText(f"Source: curves={src_curves} | metadata={src_meta}")
        if self._cor_thread is not None and self._cor_thread.isRunning():
            self.log.append("[cor] source changed while run active: cancelling previous run.")
            self._cancel_correlation_run()
            _stop_qthread(self._cor_thread)
            self._cor_worker = None
            self._cor_thread = None
        self._cancel_cor_state_refresh()
        enable = curves is not None and meta is not None
        if not enable:
            self._cor_run_queued = False
            self.cb_x.clear()
            self.cb_y.clear()
            self.loc_box.set_items([], select_all=False)
            self._refresh_location_shape_controls([])
            self._set_cor_busy(False)
            return
        self._start_cor_state_refresh(curves.df, meta.df)

    def _set_cor_busy(self, busy: bool) -> None:
        run_busy = self._cor_thread is not None and self._cor_thread.isRunning()
        state_busy = self._cor_state_thread is not None and self._cor_state_thread.isRunning()
        if busy or run_busy:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.export_plot.setEnabled(False)
            return
        if state_busy:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(False)
            self.export_plot.setEnabled(False)
            return
        enable = (self._active_curves_table() is not None) and (self.state.metadata is not None)
        self.btn_run.setEnabled(enable)
        self.btn_cancel.setEnabled(False)
        self.export_plot.setEnabled(enable)

    def _start_cor_state_refresh(self, curves_df: pd.DataFrame, metadata_df: pd.DataFrame) -> None:
        if self._cor_state_thread is not None and self._cor_state_thread.isRunning():
            # Serialize refreshes: keep only latest request and let current worker
            # exit cooperatively to avoid unsafe thread churn during rapid file changes.
            self._cancel_cor_state_refresh()
            self._cor_pending_refresh = (curves_df.copy(), metadata_df.copy())
            self.log.append("[cor] queued options refresh (worker busy).")
            return

        self._cor_state_token += 1
        self.lbl_status.setText("Status: Loading correlation options...")
        self.pb_run.setValue(0)
        self.btn_run.setEnabled(False)
        self.export_plot.setEnabled(False)

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_correlation_options_payload,
            kwargs=dict(curves_df=curves_df.copy(), metadata_df=metadata_df.copy()),
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_cor_state_progress)
        worker.succeeded.connect(self._on_cor_state_succeeded)
        worker.failed.connect(self._on_cor_state_failed)
        worker.cancelled.connect(self._on_cor_state_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_cor_state_thread_finished)

        self._cor_state_thread = thread
        self._cor_state_worker = worker
        thread.start()

    def _cancel_cor_state_refresh(self) -> None:
        if self._cor_state_worker is None:
            return
        try:
            self._cor_state_worker.request_cancel()
        except Exception:
            pass

    def _on_cor_state_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._cor_state_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: Loading options | {pct}% | {msg}")

    def _on_cor_state_succeeded(self, payload: object) -> None:
        if self.sender() is not self._cor_state_worker:
            return
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid correlation options payload.")
            opts = dict(payload.get("opts") or {})
            nm_status = str(payload.get("nm_status") or "")
            mode = str(payload.get("mode") or "metadata_with_nm")
            old_x = self.cb_x.currentText().strip()
            old_y = self.cb_y.currentText().strip()
            self.cb_x.clear()
            self.cb_x.addItems(opts.get("x", []))
            self.cb_y.clear()
            self.cb_y.addItems(opts.get("y", []))
            if old_x:
                ix = self.cb_x.findText(old_x)
                if ix >= 0:
                    self.cb_x.setCurrentIndex(ix)
            elif self.cb_x.findText("GenLatitude") >= 0:
                self.cb_x.setCurrentText("GenLatitude")
            if old_y:
                iy = self.cb_y.findText(old_y)
                if iy >= 0:
                    self.cb_y.setCurrentIndex(iy)
            elif self.cb_y.findText("nM10") >= 0:
                self.cb_y.setCurrentText("nM10")
            locations = opts.get("locations", [])
            self.loc_box.set_items(locations, select_all=True)
            self._refresh_location_shape_controls(locations)
            self.log.append(f"[cor] options ready ({mode}) | {nm_status}")
            if self.cb_x.count() == 0 or self.cb_y.count() == 0:
                self.lbl_status.setText("Status: Ready (no X/Y options found)")
            else:
                self.lbl_status.setText("Status: Ready")
            self.pb_run.setValue(0)
        except Exception as exc:
            self.log.append(f"[cor] metadata_with_nm error: {exc}")
            self.cb_x.clear()
            self.cb_y.clear()
            self.loc_box.set_items([], select_all=False)
            self._refresh_location_shape_controls([])
            self.lbl_status.setText(f"Status: ERROR | {exc}")
        finally:
            self._set_cor_busy(False)

    def _on_cor_state_failed(self, msg: str) -> None:
        if self.sender() is not self._cor_state_worker:
            return
        txt = str(msg or "Unknown error.")
        self.log.append(f"[cor] metadata_with_nm error: {txt}")
        self.cb_x.clear()
        self.cb_y.clear()
        self.loc_box.set_items([], select_all=False)
        self._refresh_location_shape_controls([])
        self.lbl_status.setText(f"Status: ERROR | {txt}")
        self.pb_run.setValue(0)
        self._set_cor_busy(False)

    def _on_cor_state_cancelled(self, msg: str) -> None:
        if self.sender() is not self._cor_state_worker:
            return
        txt = str(msg or "Cancelled.")
        self.log.append(f"[cor] options refresh cancelled: {txt}")
        self.lbl_status.setText("Status: waiting")
        self.pb_run.setValue(0)
        self._set_cor_busy(False)

    def _on_cor_state_thread_finished(self) -> None:
        self._cor_state_worker = None
        self._cor_state_thread = None
        pending = self._cor_pending_refresh
        self._cor_pending_refresh = None
        if pending is not None:
            curves_df, metadata_df = pending
            self._start_cor_state_refresh(curves_df, metadata_df)
            return
        # Safety re-sync: after async state refresh completion, recompute enabled
        # state from current app data to avoid sticky-disabled Run button when
        # rapid source switches reorder worker signals.
        self._set_cor_busy(False)
        if self._cor_run_queued:
            self._cor_run_queued = False
            QTimer.singleShot(0, self._run_correlation_async)

    def _on_cor_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not self._cor_worker:
            return
        self.pb_run.setValue(int(max(0, min(100, pct))))
        self.lbl_status.setText(f"Status: RUNNING | {pct}% | {msg}")

    def _on_cor_cancelled(self, msg: str) -> None:
        if self.sender() is not self._cor_worker:
            return
        self._set_cor_busy(False)
        self.pb_run.setValue(0)
        txt = str(msg or "Cancelled.")
        self.lbl_status.setText(f"Status: CANCELLED | {txt}")
        self.log.append(f"[cor] cancelled: {txt}")

    def _on_cor_failed(self, msg: str) -> None:
        if self.sender() is not self._cor_worker:
            return
        self._set_cor_busy(False)
        self.pb_run.setValue(0)
        txt = str(msg or "Unknown error.")
        self.lbl_status.setText(f"Status: ERROR | {txt}")
        self.log.append(f"[cor] ERROR: {txt}")
        self._last_plot_fig = None
        self.plot_view.setHtml("")

    def _on_cor_thread_finished(self) -> None:
        self._cor_worker = None
        self._cor_thread = None
        # Re-evaluate enabled state after run thread fully exits.
        self._set_cor_busy(False)

    def _cancel_correlation_run(self) -> None:
        if self._cor_worker is None:
            return
        try:
            self._cor_worker.request_cancel()
            self.lbl_status.setText("Status: cancel requested...")
            self.log.append("[cor] cancel requested")
        except Exception as exc:
            self.log.append(f"[cor] cancel request error: {exc}")

    def _apply_correlation_payload(self, payload: dict[str, Any]) -> None:
        cfg_dict = dict(payload.get("cfg_dict") or {})
        cfg = CorrelationConfig(**cfg_dict)
        d = payload["d"]
        status = str(payload.get("status") or "")
        nm_status = str(payload.get("nm_status") or "")
        self._draw_correlation(d=d, cfg=cfg)
        self.lbl_status.setText(f"Status: OK | {status}")
        self.log.append(f"[cor] {status} | {nm_status}")

    def _on_cor_succeeded(self, payload: object) -> None:
        if self.sender() is not self._cor_worker:
            return
        self._set_cor_busy(False)
        self.pb_run.setValue(100)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid correlation worker payload.")
            self._apply_correlation_payload(payload)
        except Exception as exc:
            self._on_cor_failed(str(exc))

    def _run_correlation_async(self) -> None:
        if self._cor_thread is not None and self._cor_thread.isRunning():
            QMessageBox.information(self, "Run in progress", "Correlation run is already in progress.")
            return
        if self._cor_state_thread is not None and self._cor_state_thread.isRunning():
            self._cor_run_queued = True
            self.lbl_status.setText("Status: options still loading... run queued.")
            self.log.append("[cor] run queued while options refresh is in progress.")
            return
        curves = self._active_curves_table()
        meta = self.state.metadata
        if curves is None:
            QMessageBox.warning(self, "Missing input", "Curves not loaded.")
            return
        if meta is None:
            QMessageBox.warning(self, "Missing input", "Metadata not loaded.")
            return
        x_col = str(self.cb_x.currentText() or "").strip()
        y_choice = str(self.cb_y.currentText() or "").strip()
        if not x_col or not y_choice:
            msg = "X/Y options are not ready yet. Wait for options load, then run again."
            self.lbl_status.setText(f"Status: ERROR | {msg}")
            self.log.append(f"[cor] ERROR: {msg}")
            QMessageBox.warning(self, "Correlation options not ready", msg)
            return

        cfg_dict = dict(
            method=str(self.cb_method.currentText() or "Spearman"),
            x_col=x_col,
            y_choice=y_choice,
            selected_locations=self.loc_box.selected_values() or self.loc_box.values(),
        )
        kwargs = dict(curves_df=curves.df.copy(), metadata_df=meta.df.copy(), cfg_dict=cfg_dict)

        self._set_cor_busy(True)
        self.pb_run.setValue(0)
        self.lbl_status.setText("Status: RUNNING | 0% | starting...")
        self.log.append("[cor] started")

        thread = QThread(self)
        worker = LongTaskWorker(
            _compute_correlation_payload,
            kwargs=kwargs,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_requested",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_cor_progress)
        worker.succeeded.connect(self._on_cor_succeeded)
        worker.failed.connect(self._on_cor_failed)
        worker.cancelled.connect(self._on_cor_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_cor_thread_finished)

        self._cor_thread = thread
        self._cor_worker = worker
        thread.start()

    def _run_correlation(self) -> None:
        try:
            if self._cor_state_thread is not None and self._cor_state_thread.isRunning():
                raise ValueError("Correlation options are still loading. Try again in a moment.")
            curves = self._active_curves_table()
            meta = self.state.metadata
            if curves is None:
                raise ValueError("Curves not loaded.")
            if meta is None:
                raise ValueError("Metadata not loaded.")
            cfg = CorrelationConfig(
                method=str(self.cb_method.currentText() or "Spearman"),
                x_col=str(self.cb_x.currentText() or ""),
                y_choice=str(self.cb_y.currentText() or "nM10"),
                selected_locations=self.loc_box.selected_values() or self.loc_box.values(),
            )
            payload = _compute_correlation_payload(
                curves_df=curves.df,
                metadata_df=meta.df,
                cfg_dict={
                    "method": cfg.method,
                    "x_col": cfg.x_col,
                    "y_choice": cfg.y_choice,
                    "selected_locations": list(cfg.selected_locations),
                },
            )
            self._apply_correlation_payload(payload)
        except Exception as exc:
            self.lbl_status.setText(f"Status: ERROR | {exc}")
            self.log.append(f"[cor] ERROR: {exc}")
            self._last_plot_fig = None
            self.plot_view.setHtml("")

    def _draw_correlation(self, *, d: pd.DataFrame, cfg: CorrelationConfig) -> None:
        xvar = cfg.x_col
        method = cfg.method
        y_choice = cfg.y_choice
        d = d.copy()
        d[xvar] = _coerce_numeric_series_relaxed(d[xvar])
        y_axis_mode = str(self.cb_y_axis_mode.currentData() or "auto")
        plot_h = int(max(360, min(1200, int(self.sp_cor_plot_height.value()))))
        self.plot_view.setMinimumHeight(plot_h)

        loc_levels = sorted(d["Location"].astype(str).unique().tolist())
        pal = _palette_hex_named(self.cb_palette.currentData() or "default")
        color_map = {lvl: pal[i % len(pal)] for i, lvl in enumerate(loc_levels)}
        symbol_map = self._current_location_shape_map(loc_levels)
        show_grid = bool(self.chk_grid.isChecked())
        title_prefix = str(self.in_title_prefix.text().strip() or "Correlation")
        title_text = f"{title_prefix}: {method} of {xvar} vs {y_choice}" if title_prefix else f"{method} of {xvar} vs {y_choice}"

        if y_choice in ["nM10", "nM15"]:
            col_b5 = "nM10_b5" if y_choice == "nM10" else "nM15_b5"
            col_b02 = "nM10_b02" if y_choice == "nM10" else "nM15_b02"
            parts = []
            if col_b5 in d.columns:
                parts.append(
                    pd.DataFrame(
                        {
                            "x": d[xvar],
                            "y": _coerce_numeric_series_relaxed(d[col_b5]),
                            "Size": "b_5_m",
                            "Location": d["Location"].astype(str),
                            "Sample": d["Sample"].astype(str),
                        }
                    )
                )
            if col_b02 in d.columns:
                parts.append(
                    pd.DataFrame(
                        {
                            "x": d[xvar],
                            "y": _coerce_numeric_series_relaxed(d[col_b02]),
                            "Size": "b_02_m",
                            "Location": d["Location"].astype(str),
                            "Sample": d["Sample"].astype(str),
                        }
                    )
                )
            dat = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["x", "y", "Size", "Location", "Sample"])
            dat = dat.dropna(subset=["x", "y"]).copy()
            y_is_log = y_axis_mode != "linear"
            dat = dat[np.isfinite(dat["x"]) & np.isfinite(dat["y"])].copy()
            if y_is_log:
                dat = dat[dat["y"] > 0].copy()

            fig = make_subplots(rows=1, cols=2, subplot_titles=["b_5_m", "b_02_m"], shared_yaxes=True)
            sizes_order = ["b_5_m", "b_02_m"]
            for j, sz in enumerate(sizes_order, start=1):
                sub = dat[dat["Size"] == sz]
                if len(sub) == 0:
                    continue
                lbl = _corr_label(method, sub["x"].to_numpy(dtype=float), sub["y"].to_numpy(dtype=float))
                fig.layout.annotations[j - 1]["text"] = f"{sz}: {lbl}"
                for loc in loc_levels:
                    g = sub[sub["Location"] == str(loc)]
                    if len(g) == 0:
                        continue
                    fig.add_trace(
                        go.Scatter(
                            x=g["x"],
                            y=g["y"],
                            mode="markers",
                            name=str(loc),
                            marker=dict(
                                color=color_map.get(str(loc), pal[0]),
                                symbol=symbol_map.get(str(loc), "circle"),
                                size=int(self.sp_pt_size.value()),
                                opacity=0.85,
                                line=dict(color="#111827", width=float(self.sp_stroke.value())),
                            ),
                            showlegend=(j == 1),
                            customdata=np.stack([g["Location"].astype(str).to_numpy(), g["Sample"].astype(str).to_numpy()], axis=1),
                            hovertemplate=(
                                "Sample=%{customdata[1]}<br>Location=%{customdata[0]}<br>"
                                f"{xvar}=%{{x:.3g}}<br>{y_choice}=%{{y:.3g}}<extra></extra>"
                            ),
                        ),
                        row=1,
                        col=j,
                    )
                x_arr = sub["x"].to_numpy(dtype=float)
                y_arr = sub["y"].to_numpy(dtype=float)
                if len(x_arr) >= 4:
                    x_grid = np.linspace(float(np.nanmin(x_arr)), float(np.nanmax(x_arr)), 200)
                    yhat, ylo, yhi = fit_curve_with_ci(
                        method,
                        x_arr,
                        y_arr,
                        x_grid,
                        fit_log_y=(y_is_log and method in ["Spearman", "Pearson"]),
                    )
                    if yhat is not None:
                        fig.add_trace(
                            go.Scatter(x=x_grid, y=yhat, mode="lines", line=dict(color="black", width=2), showlegend=False, hoverinfo="skip"),
                            row=1,
                            col=j,
                        )
                        if ylo is not None and yhi is not None:
                            if ylo.shape == yhat.shape and yhi.shape == yhat.shape:
                                fig.add_trace(
                                    go.Scatter(
                                        x=np.concatenate([x_grid, x_grid[::-1]]),
                                        y=np.concatenate([yhi, ylo[::-1]]),
                                        fill="toself",
                                        fillcolor="rgba(0,0,0,0.12)",
                                        line=dict(color="rgba(0,0,0,0)"),
                                        showlegend=False,
                                        hoverinfo="skip",
                                    ),
                                    row=1,
                                    col=j,
                                )
                fig.update_xaxes(title_text=xvar, row=1, col=j, showgrid=show_grid, gridcolor="rgba(148,163,184,0.18)")
                fig.update_yaxes(type=("log" if y_is_log else "linear"), row=1, col=j, showgrid=show_grid, gridcolor="rgba(148,163,184,0.18)")

            fig.update_layout(**_plotly_layout_base("Correlation"))
            # Keep correlation facets readable: avoid title/legend/subplot-title overlap.
            fig.update_layout(
                height=plot_h,
                title={"text": title_text, "x": 0.02, "y": 0.985, "yanchor": "top"},
                legend_title_text="Location",
                legend=dict(orientation="v", x=1.01, xanchor="left", y=1.0, yanchor="top"),
                margin=dict(l=86, r=165, t=80, b=54),
            )
            if fig.layout.annotations:
                for ann in fig.layout.annotations:
                    ann.y = 0.955
            y_title = _coerce_nm_axis_label(self.state.nm_axis_label)
            _style_axes(fig, x_title=xvar, y_title=y_title, y_type=("log" if y_is_log else "linear"))
            fig.update_xaxes(title_text=xvar, row=1, col=1, showgrid=show_grid)
            fig.update_xaxes(title_text=xvar, row=1, col=2, showgrid=show_grid)
            fig.update_yaxes(title_text=_format_math_exponents(y_title), row=1, col=1, showgrid=show_grid)
            fig.update_yaxes(showgrid=show_grid, row=1, col=2)
            fig.update_yaxes(matches="y", row=1, col=2)
            _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
            _apply_y_tick_style(
                fig,
                str(self.cb_tick_style.currentData() or "auto"),
                bg_mode=str(self.cb_bg_mode.currentData() or "white"),
                y_is_log=bool(y_is_log),
            )
            self._last_plot_fig = go.Figure(fig)
            _set_plotly_html(self.plot_view, fig)
            return

        if y_choice not in d.columns:
            raise ValueError(f"Y column not found: {y_choice}")
        dat = pd.DataFrame(
            {
                "x": _coerce_numeric_series_relaxed(d[xvar]),
                "y": _coerce_numeric_series_relaxed(d[y_choice]),
                "Location": d["Location"].astype(str),
                "Sample": d["Sample"].astype(str),
            }
        )
        dat = dat.dropna(subset=["x", "y"]).copy()
        dat = dat[np.isfinite(dat["x"]) & np.isfinite(dat["y"])].copy()
        y_is_nm = _is_nm_like_metric_name(y_choice)
        if y_axis_mode == "log":
            y_is_log = True
        elif y_axis_mode == "linear":
            y_is_log = False
        else:
            y_is_log = bool(y_is_nm)
        if y_is_log:
            dat = dat[dat["y"] > 0].copy()
        if len(dat) == 0:
            raise ValueError("No finite rows after correlation filters.")

        fig = go.Figure()
        fig.update_layout(**_plotly_layout_base("Correlation"))
        fig.update_layout(
            height=plot_h,
            title={"text": title_text, "x": 0.02, "y": 0.985, "yanchor": "top"},
            legend_title_text="Location",
            legend=dict(orientation="v", x=1.01, xanchor="left", y=1.0, yanchor="top"),
            margin=dict(l=86, r=165, t=80, b=54),
        )
        for loc in loc_levels:
            g = dat[dat["Location"] == str(loc)]
            if len(g) == 0:
                continue
            fig.add_trace(
                go.Scatter(
                    x=g["x"],
                    y=g["y"],
                    mode="markers",
                    name=str(loc),
                    marker=dict(
                        color=color_map.get(str(loc), pal[0]),
                        symbol=symbol_map.get(str(loc), "circle"),
                        size=int(self.sp_pt_size.value()),
                        opacity=0.85,
                        line=dict(color="#111827", width=float(self.sp_stroke.value())),
                    ),
                    customdata=np.stack([g["Location"].astype(str).to_numpy(), g["Sample"].astype(str).to_numpy()], axis=1),
                    hovertemplate=(
                        "Sample=%{customdata[1]}<br>Location=%{customdata[0]}<br>"
                        f"{xvar}=%{{x:.3g}}<br>{y_choice}=%{{y:.3g}}<extra></extra>"
                    ),
                )
            )

        x_arr = dat["x"].to_numpy(dtype=float)
        y_arr = dat["y"].to_numpy(dtype=float)
        if len(x_arr) >= 4:
            x_grid = np.linspace(float(np.nanmin(x_arr)), float(np.nanmax(x_arr)), 200)
            yhat, ylo, yhi = fit_curve_with_ci(
                method,
                x_arr,
                y_arr,
                x_grid,
                fit_log_y=(bool(y_is_log) and method in ["Spearman", "Pearson"]),
            )
            if yhat is not None:
                fig.add_trace(go.Scatter(x=x_grid, y=yhat, mode="lines", line=dict(color="black", width=2), showlegend=False, hoverinfo="skip"))
                if ylo is not None and yhi is not None:
                    if ylo.shape == yhat.shape and yhi.shape == yhat.shape:
                        fig.add_trace(
                            go.Scatter(
                                x=np.concatenate([x_grid, x_grid[::-1]]),
                                y=np.concatenate([yhi, ylo[::-1]]),
                                fill="toself",
                                fillcolor="rgba(0,0,0,0.12)",
                                line=dict(color="rgba(0,0,0,0)"),
                                hoverinfo="skip",
                                showlegend=False,
                            )
                        )
        y_title = _coerce_nm_axis_label(self.state.nm_axis_label) if y_is_nm else y_choice
        _style_axes(fig, x_title=xvar, y_title=y_title, y_type=("log" if y_is_log else "linear"))
        fig.update_xaxes(showgrid=show_grid)
        fig.update_yaxes(showgrid=show_grid)
        _apply_plot_background(fig, str(self.cb_bg_mode.currentData() or "white"))
        _apply_y_tick_style(
            fig,
            str(self.cb_tick_style.currentData() or "auto"),
            bg_mode=str(self.cb_bg_mode.currentData() or "white"),
            y_is_log=bool(y_is_log),
        )
        self._last_plot_fig = go.Figure(fig)
        _set_plotly_html(self.plot_view, fig)

    def _on_nm_axis_label_changed(self) -> None:
        self.log.append(f"[cor] nm axis label updated: {self.state.nm_axis_label}")

    def _export_plot(self) -> None:
        if self._last_plot_fig is None:
            QMessageBox.information(self, "No plot", "Run Correlation first.")
            return
        try:
            cfg = self.export_plot.config()
            saved = _save_plotly_figure_local(self, self._last_plot_fig, cfg)
            if saved is not None:
                self.log.append(f"[cor] export saved: {saved}")
                self.lbl_status.setText(f"Status: Exported correlation plot -> {saved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Export error", str(exc))
            self.log.append(f"[cor] export error: {exc}")

    def _shutdown_background_threads(self) -> None:
        try:
            if self._cor_worker is not None:
                self._cor_worker.request_cancel()
        except Exception:
            pass
        try:
            if self._cor_state_worker is not None:
                self._cor_state_worker.request_cancel()
        except Exception:
            pass
        _stop_qthread(self._cor_thread)
        _stop_qthread(self._cor_state_thread)
        self._cor_worker = None
        self._cor_thread = None
        self._cor_state_worker = None
        self._cor_state_thread = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_background_threads()
        super().closeEvent(event)


def _make_placeholder(text: str) -> QWidget:
    box = QFrame()
    lay = QVBoxLayout(box)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    lay.addStretch(1)
    return box


class SettingsDialog(QDialog):
    request_open_terminal = Signal()
    request_reboot = Signal()
    request_open_manual = Signal(str)

    def __init__(self, current: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(760, 780)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        tabs = QTabWidget()
        tabs.setMovable(False)
        tabs.tabBar().setExpanding(False)
        lay.addWidget(tabs, 1)

        # Appearance tab
        tab_appearance = QWidget()
        tab_appearance_lay = QVBoxLayout(tab_appearance)
        tab_appearance_lay.setContentsMargins(8, 8, 8, 8)
        tab_appearance_lay.setSpacing(10)
        ap_box = QGroupBox("Appearance")
        ap_form = QFormLayout(ap_box)

        self.btn_theme_mode = QPushButton()
        self.btn_theme_mode.setCheckable(True)
        self.btn_theme_mode.clicked.connect(self._sync_enable_state)
        ap_form.addRow("Theme mode", self.btn_theme_mode)

        self.sp_font_size = SliderNumberInput(min_value=10, max_value=18, value=13, decimals=0, step=1)
        ap_form.addRow("Base font size", self.sp_font_size)

        self.chk_compact = QCheckBox("Compact controls")
        ap_form.addRow("", self.chk_compact)
        self.chk_high_contrast = QCheckBox("High contrast")
        ap_form.addRow("", self.chk_high_contrast)
        self.sp_control_thickness = SliderNumberInput(min_value=1, max_value=4, value=1, decimals=0, step=1)
        ap_form.addRow("Control thickness", self.sp_control_thickness)
        tab_appearance_lay.addWidget(ap_box)
        tab_appearance_lay.addStretch(1)
        tabs.addTab(tab_appearance, "Appearance")

        # Export tab
        tab_export = QWidget()
        tab_export_lay = QVBoxLayout(tab_export)
        tab_export_lay.setContentsMargins(8, 8, 8, 8)
        tab_export_lay.setSpacing(10)
        export_box = QGroupBox("Default Export")
        export_form = QFormLayout(export_box)
        self.cb_export_fmt = QComboBox()
        self.cb_export_fmt.addItems(["svg", "pdf", "png"])
        export_form.addRow("Format", self.cb_export_fmt)
        self.sp_export_width = SliderNumberInput(min_value=400, max_value=8000, value=1800, decimals=0, step=50)
        export_form.addRow("Width (px)", self.sp_export_width)
        self.sp_export_height = SliderNumberInput(min_value=300, max_value=8000, value=1200, decimals=0, step=50)
        export_form.addRow("Height (px)", self.sp_export_height)
        self.sp_export_scale = SliderNumberInput(min_value=0.5, max_value=5.0, value=2.0, decimals=1, step=0.5)
        export_form.addRow("DPI/scale", self.sp_export_scale)
        row_export_dir = QHBoxLayout()
        self.ed_export_dir = QLineEdit("")
        self.btn_export_dir = QPushButton("Browse")
        self.btn_export_dir.clicked.connect(self._pick_export_dir)
        row_export_dir.addWidget(self.ed_export_dir, 1)
        row_export_dir.addWidget(self.btn_export_dir, 0)
        export_form.addRow("Default folder", row_export_dir)
        tab_export_lay.addWidget(export_box)

        perf_box = QGroupBox("Performance / Rendering")
        perf_form = QFormLayout(perf_box)
        self.cb_perf_profile = QComboBox()
        self.cb_perf_profile.addItem("Quality (best rendering)", "quality")
        self.cb_perf_profile.addItem("Balanced", "balanced")
        self.cb_perf_profile.addItem("Speed (lighter interactivity)", "speed")
        perf_form.addRow("Plotly profile", self.cb_perf_profile)
        tab_export_lay.addWidget(perf_box)
        tab_export_lay.addStretch(1)
        tabs.addTab(tab_export, "Export")

        # Runtime tab
        tab_runtime = QWidget()
        tab_runtime_lay = QVBoxLayout(tab_runtime)
        tab_runtime_lay.setContentsMargins(8, 8, 8, 8)
        tab_runtime_lay.setSpacing(10)
        session_box = QGroupBox("Session")
        session_form = QFormLayout(session_box)
        self.chk_session_restore = QCheckBox("Restore previous session at startup")
        self.chk_session_autosave = QCheckBox("Autosave session on close")
        session_form.addRow("", self.chk_session_restore)
        session_form.addRow("", self.chk_session_autosave)
        tab_runtime_lay.addWidget(session_box)

        preflight_box = QGroupBox("Preflight Policy")
        preflight_form = QFormLayout(preflight_box)
        self.cb_preflight = QComboBox()
        self.cb_preflight.addItem("Strict (block on failures)", "strict")
        self.cb_preflight.addItem("Warn (show warnings, continue)", "warn")
        self.cb_preflight.addItem("Skip (no startup checks)", "skip")
        preflight_form.addRow("Startup policy", self.cb_preflight)
        tab_runtime_lay.addWidget(preflight_box)
        tab_runtime_lay.addStretch(1)
        tabs.addTab(tab_runtime, "Runtime")

        # Diagnostics tab
        tab_diag = QWidget()
        tab_diag_lay = QVBoxLayout(tab_diag)
        tab_diag_lay.setContentsMargins(8, 8, 8, 8)
        tab_diag_lay.setSpacing(10)
        diag_box = QGroupBox("Diagnostics")
        diag_form = QFormLayout(diag_box)
        self.cb_log_level = QComboBox()
        self.cb_log_level.addItem("Quiet (warnings/errors only)", "quiet")
        self.cb_log_level.addItem("Normal", "normal")
        self.cb_log_level.addItem("Verbose", "verbose")
        diag_form.addRow("Log level", self.cb_log_level)
        self.chk_log_to_file = QCheckBox("Save runtime log to file")
        diag_form.addRow("", self.chk_log_to_file)
        row_log = QHBoxLayout()
        self.ed_log_file = QLineEdit("")
        self.btn_log_file = QPushButton("Browse")
        self.btn_log_file.clicked.connect(self._pick_log_file)
        row_log.addWidget(self.ed_log_file, 1)
        row_log.addWidget(self.btn_log_file, 0)
        diag_form.addRow("Log file path", row_log)
        tab_diag_lay.addWidget(diag_box)

        gen_box = QGroupBox("General")
        gen_lay = QVBoxLayout(gen_box)
        self.lbl_manual_note = QLabel(
            "The Manual button opens the local PDF included with INAES."
        )
        self.lbl_manual_note.setWordWrap(True)
        gen_lay.addWidget(self.lbl_manual_note)
        self.lbl_general_note = QLabel(
            "Appearance/settings do not change analysis logic.\n"
            "Language selector is temporarily disabled in this build."
        )
        self.lbl_general_note.setWordWrap(True)
        gen_lay.addWidget(self.lbl_general_note)
        tab_diag_lay.addWidget(gen_box)
        tab_diag_lay.addStretch(1)
        tabs.addTab(tab_diag, "Diagnostics")

        tools_row = QHBoxLayout()
        self.btn_terminal = QPushButton("Open terminal")
        self.btn_terminal.clicked.connect(self.request_open_terminal.emit)
        tools_row.addWidget(self.btn_terminal)
        self.btn_manual = QPushButton("Open manual")
        self.btn_manual.clicked.connect(self._emit_manual_request)
        tools_row.addWidget(self.btn_manual)
        self.btn_reboot = QPushButton("Reboot session")
        self.btn_reboot.clicked.connect(self.request_reboot.emit)
        tools_row.addWidget(self.btn_reboot)
        tools_row.addStretch(1)
        lay.addLayout(tools_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton("Apply")
        self.btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_ok)
        lay.addLayout(btn_row)

        self.chk_log_to_file.toggled.connect(self._sync_enable_state)
        self._load(current)

    def _load(self, current: dict[str, Any]) -> None:
        builtin_theme = str(current.get("builtin_theme", "inaes_dark") or "inaes_dark")
        font_size = int(float(current.get("font_size", 13) or 13))
        compact = bool(current.get("compact", False))
        high_contrast = bool(current.get("high_contrast", False))
        control_thickness = int(float(current.get("control_thickness", 1) or 1))
        perf_profile = str(current.get("performance_profile", "balanced") or "balanced")
        export_fmt = str(current.get("default_export_format", "svg") or "svg")
        export_w = int(float(current.get("default_export_width", 1800) or 1800))
        export_h = int(float(current.get("default_export_height", 1200) or 1200))
        export_scale = float(current.get("default_export_scale", 2.0) or 2.0)
        export_dir = str(current.get("default_export_dir", "") or "")
        restore_on = bool(current.get("session_restore_on_startup", True))
        autosave_on = bool(current.get("session_autosave_on_close", True))
        log_level = str(current.get("diagnostics_level", "normal") or "normal")
        log_to_file = bool(current.get("save_log_file", False))
        log_path = str(current.get("log_file_path", "") or "")
        preflight = str(current.get("preflight_policy", "warn") or "warn")

        self.btn_theme_mode.setChecked(builtin_theme == "inaes_light")
        self._refresh_theme_toggle_text()

        self.sp_font_size.setValue(float(max(10, min(18, font_size))))
        self.chk_compact.setChecked(compact)
        self.chk_high_contrast.setChecked(high_contrast)
        self.sp_control_thickness.setValue(float(max(1, min(4, control_thickness))))

        idx = self.cb_perf_profile.findData(perf_profile)
        self.cb_perf_profile.setCurrentIndex(idx if idx >= 0 else 1)

        idx = self.cb_export_fmt.findText(export_fmt)
        self.cb_export_fmt.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_export_width.setValue(float(max(400, min(8000, export_w))))
        self.sp_export_height.setValue(float(max(300, min(8000, export_h))))
        self.sp_export_scale.setValue(float(max(0.5, min(5.0, export_scale))))
        self.ed_export_dir.setText(export_dir)

        self.chk_session_restore.setChecked(restore_on)
        self.chk_session_autosave.setChecked(autosave_on)

        idx = self.cb_log_level.findData(log_level)
        self.cb_log_level.setCurrentIndex(idx if idx >= 0 else 1)
        self.chk_log_to_file.setChecked(log_to_file)
        self.ed_log_file.setText(log_path)

        idx = self.cb_preflight.findData(preflight)
        self.cb_preflight.setCurrentIndex(idx if idx >= 0 else 1)
        self._sync_enable_state()

    def _sync_enable_state(self) -> None:
        self._refresh_theme_toggle_text()
        self.lbl_general_note.setText(
            "Appearance/settings do not change analysis logic.\n"
            "Language selector is temporarily disabled in this build."
        )

        log_file_enabled = bool(self.chk_log_to_file.isChecked())
        self.ed_log_file.setEnabled(log_file_enabled)
        self.btn_log_file.setEnabled(log_file_enabled)

    def _refresh_theme_toggle_text(self) -> None:
        if bool(self.btn_theme_mode.isChecked()):
            self.btn_theme_mode.setText("☀ Light")
        else:
            self.btn_theme_mode.setText("☾ Dark")

    def values(self) -> dict[str, Any]:
        return {
            "engine": "inaes",
            "builtin_theme": ("inaes_light" if bool(self.btn_theme_mode.isChecked()) else "inaes_dark"),
            "qt_theme": "",
            "font_size": int(round(float(self.sp_font_size.value()))),
            "compact": bool(self.chk_compact.isChecked()),
            "high_contrast": bool(self.chk_high_contrast.isChecked()),
            "control_thickness": int(round(float(self.sp_control_thickness.value()))),
            "ui_language": "en",
            "performance_profile": str(self.cb_perf_profile.currentData() or "balanced"),
            "default_export_format": str(self.cb_export_fmt.currentText() or "svg").strip().lower(),
            "default_export_width": int(round(float(self.sp_export_width.value()))),
            "default_export_height": int(round(float(self.sp_export_height.value()))),
            "default_export_scale": float(self.sp_export_scale.value()),
            "default_export_dir": str(self.ed_export_dir.text().strip()),
            "session_restore_on_startup": bool(self.chk_session_restore.isChecked()),
            "session_autosave_on_close": bool(self.chk_session_autosave.isChecked()),
            "diagnostics_level": str(self.cb_log_level.currentData() or "normal"),
            "save_log_file": bool(self.chk_log_to_file.isChecked()),
            "log_file_path": str(self.ed_log_file.text().strip()),
            "preflight_policy": str(self.cb_preflight.currentData() or "warn"),
            "manual_url": "",
        }

    def _pick_export_dir(self) -> None:
        base = str(self.ed_export_dir.text().strip() or "")
        path = QFileDialog.getExistingDirectory(self, "Select default export folder", base)
        if path:
            self.ed_export_dir.setText(path)

    def _pick_log_file(self) -> None:
        base = str(self.ed_log_file.text().strip() or "inaes_desktop_runtime.log")
        path, _ = QFileDialog.getSaveFileName(self, "Select log file", base, "Log files (*.log *.txt)")
        if path:
            self.ed_log_file.setText(path)

    def _emit_manual_request(self) -> None:
        self.request_open_manual.emit("")


class LiveTerminalDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("INAES Runtime Terminal")
        self.resize(980, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.out, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.out.clear)
        row.addWidget(btn_clear)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_close)
        lay.addLayout(row)

    def append_line(self, line: str) -> None:
        self.out.appendPlainText(str(line))
        bar = self.out.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("INAES")
        self._apply_screen_based_window_geometry()
        self.state = AppState()
        self._ui_settings: dict[str, Any] = self._default_ui_settings()
        self._terminal_dialog: LiveTerminalDialog | None = None
        self._tab_log_sizes: dict[int, int] = {}
        self.tabs: QTabWidget | None = None
        self.tab_data_upload: DataUploadTab | None = None
        self.btn_settings: QPushButton | None = None
        self._build_ui()
        self.restore_session_state_now()
        self._run_preflight_on_startup()

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.setMovable(False)
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setUsesScrollButtons(True)
        tabs.tabBar().setElideMode(Qt.ElideRight)
        self.tab_data_upload = DataUploadTab(self.state)
        tabs.addTab(self.tab_data_upload, "Data Upload & nM")
        tabs.addTab(FreezingCurvesTab(self.state), "Freezing Curves")
        tabs.addTab(CompareSamplesTab(self.state), "Compare Samples FC")
        tabs.addTab(FrozenFractionTab(self.state), "Frozen Fraction")
        tabs.addTab(KneepointTab(self.state, test_mode=False), "Kneepoint")
        tabs.addTab(BoxplotsTab(self.state), "Boxplots")
        tabs.addTab(CorrelationsTab(self.state), "Correlations")
        self.tabs = tabs

        corner = QWidget()
        corner.setObjectName("TopBarFrame")
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 0, 0)
        corner_lay.setSpacing(0)
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setToolTip("Open application settings")
        self.btn_settings.setFixedSize(44, 44)
        self.btn_settings.setStyleSheet(
            "font-size: 24px; font-weight: 700; padding: 0px; "
            "min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px;"
        )
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        corner_lay.addWidget(self.btn_settings, 0, Qt.AlignCenter)
        corner.setFixedSize(46, 46)
        tabs.setCornerWidget(corner, Qt.TopLeftCorner)

        self.setCentralWidget(tabs)
        tabs.setCurrentIndex(0)

        status = QStatusBar()
        status.showMessage("INAES ready.")
        self.setStatusBar(status)
        self._apply_ui_settings(show_status=False)
        self._apply_global_geometry_tuning()
        self._apply_splitter_size_hints()
        self._hook_tab_logs()

    def _apply_screen_based_window_geometry(self) -> None:
        # QScreen-based startup sizing: adapt to real desktop work area.
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                self.resize(1500, 920)
                self.setMinimumSize(1180, 760)
                return
            geo = screen.availableGeometry()
            target_w = int(max(1180, min(2200, round(float(geo.width()) * 0.90))))
            target_h = int(max(760, min(1400, round(float(geo.height()) * 0.88))))
            self.resize(target_w, target_h)
            self.setMinimumSize(1180, 760)
        except Exception:
            self.resize(1500, 920)
            self.setMinimumSize(1180, 760)

    def _apply_splitter_size_hints(self) -> None:
        # Keep left controls readable and right plotting area dominant.
        # This avoids over-compressed panels on some monitors / DPI setups.
        try:
            for splitter in self.findChildren(QSplitter):
                count = int(splitter.count())
                if count < 2:
                    continue
                if splitter.orientation() == Qt.Horizontal and count == 2:
                    left = splitter.widget(0)
                    total = int(max(splitter.width(), self.width(), 1200))
                    left_hint = int(left.sizeHint().width()) if left is not None else 380
                    left_target = int(min(max(left_hint + 18, 340), total * 0.40))
                    right_target = int(max(640, total - left_target))
                    splitter.setSizes([left_target, right_target])
                elif splitter.orientation() == Qt.Vertical:
                    total = int(max(splitter.height(), self.height(), 760))
                    weights = [3] + [2] * (count - 1)
                    ws = float(sum(weights))
                    sizes = [int(max(120, round(total * (w / ws)))) for w in weights]
                    splitter.setSizes(sizes)
        except Exception:
            pass

    @staticmethod
    def _default_ui_settings() -> dict[str, Any]:
        return {
            "engine": "inaes",
            "builtin_theme": "inaes_dark",
            "qt_theme": "",
            "font_size": 13,
            "compact": False,
            "high_contrast": False,
            "control_thickness": 1,
            "ui_language": "en",
            "performance_profile": "balanced",
            "default_export_format": "svg",
            "default_export_width": 1800,
            "default_export_height": 1200,
            "default_export_scale": 2.0,
            "default_export_dir": "",
            "session_restore_on_startup": True,
            "session_autosave_on_close": True,
            "diagnostics_level": "normal",
            "save_log_file": False,
            "log_file_path": "",
            "preflight_policy": "warn",
            "manual_url": "",
        }

    def _sanitize_ui_settings(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(self._default_ui_settings())
        if isinstance(raw, dict):
            out.update(raw)
        out["engine"] = "inaes"
        builtin_theme = str(out.get("builtin_theme", "inaes_dark") or "inaes_dark").strip().lower()
        if builtin_theme not in {"inaes_dark", "inaes_light"}:
            builtin_theme = "inaes_dark"
        out["builtin_theme"] = builtin_theme
        out["qt_theme"] = ""
        try:
            out["font_size"] = int(max(10, min(18, int(float(out.get("font_size", 13))))))
        except Exception:
            out["font_size"] = 13
        out["compact"] = bool(out.get("compact", False))
        out["high_contrast"] = bool(out.get("high_contrast", False))
        try:
            out["control_thickness"] = int(max(1, min(4, int(float(out.get("control_thickness", 1))))))
        except Exception:
            out["control_thickness"] = 1
        out["ui_language"] = "en"
        perf = str(out.get("performance_profile", "balanced") or "balanced").strip().lower()
        out["performance_profile"] = perf if perf in {"quality", "balanced", "speed"} else "balanced"
        fmt = str(out.get("default_export_format", "svg") or "svg").strip().lower()
        out["default_export_format"] = fmt if fmt in {"svg", "pdf", "png"} else "svg"
        try:
            out["default_export_width"] = int(max(400, min(8000, int(float(out.get("default_export_width", 1800))))))
        except Exception:
            out["default_export_width"] = 1800
        try:
            out["default_export_height"] = int(
                max(300, min(8000, int(float(out.get("default_export_height", 1200))))
            ))
        except Exception:
            out["default_export_height"] = 1200
        try:
            out["default_export_scale"] = float(max(0.5, min(5.0, float(out.get("default_export_scale", 2.0)))))
        except Exception:
            out["default_export_scale"] = 2.0
        out["default_export_dir"] = str(out.get("default_export_dir", "") or "").strip()
        out["session_restore_on_startup"] = bool(out.get("session_restore_on_startup", True))
        out["session_autosave_on_close"] = bool(out.get("session_autosave_on_close", True))
        diag = str(out.get("diagnostics_level", "normal") or "normal").strip().lower()
        out["diagnostics_level"] = diag if diag in {"quiet", "normal", "verbose"} else "normal"
        out["save_log_file"] = bool(out.get("save_log_file", False))
        out["log_file_path"] = str(out.get("log_file_path", "") or "").strip()
        ppol = str(out.get("preflight_policy", "warn") or "warn").strip().lower()
        out["preflight_policy"] = ppol if ppol in {"strict", "warn", "skip"} else "warn"
        out["manual_url"] = str(out.get("manual_url", "") or "").strip()
        return out

    def _apply_ui_settings(self, *, show_status: bool = True) -> None:
        global RUNTIME_LOG_LEVEL, RUNTIME_SAVE_LOG_FILE, RUNTIME_LOG_FILE_PATH
        self._ui_settings = self._sanitize_ui_settings(self._ui_settings)
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        _set_runtime_plotly_profile(str(self._ui_settings.get("performance_profile", "balanced")))
        _set_runtime_builtin_theme(str(self._ui_settings.get("builtin_theme", "inaes_dark")))
        RUNTIME_LOG_LEVEL = str(self._ui_settings.get("diagnostics_level", "normal"))
        RUNTIME_SAVE_LOG_FILE = bool(self._ui_settings.get("save_log_file", False))
        RUNTIME_LOG_FILE_PATH = str(self._ui_settings.get("log_file_path", "") or "").strip()

        ok, msg = apply_appearance(
            app,
            engine="inaes",
            builtin_theme=str(self._ui_settings.get("builtin_theme", "inaes_dark")),
            qt_theme="",
            font_size=int(self._ui_settings.get("font_size", 13)),
            compact=bool(self._ui_settings.get("compact", False)),
            high_contrast=bool(self._ui_settings.get("high_contrast", False)),
            control_thickness=int(self._ui_settings.get("control_thickness", 1)),
        )
        default_export_manager.set_default_export_dir(str(self._ui_settings.get("default_export_dir", "") or ""))
        self._apply_export_defaults_to_boxes()
        if self.btn_settings is not None:
            self.btn_settings.setText("⚙")
        self._apply_global_geometry_tuning()
        self._append_runtime_log(
            f"UI settings applied | theme={self._ui_settings.get('builtin_theme')} | profile={RUNTIME_PLOTLY_PROFILE}",
            level="info",
        )
        if show_status and self.statusBar() is not None:
            level = "Appearance updated" if ok else "Appearance updated with fallback"
            self.statusBar().showMessage(f"{level}: {msg}", 5000)

    def _apply_export_defaults_to_boxes(self) -> None:
        fmt = str(self._ui_settings.get("default_export_format", "svg") or "svg")
        width = int(self._ui_settings.get("default_export_width", 1800) or 1800)
        height = int(self._ui_settings.get("default_export_height", 1200) or 1200)
        scale = float(self._ui_settings.get("default_export_scale", 2.0) or 2.0)
        for box in self.findChildren(PlotExportBox):
            idx = box.cb_fmt.findText(fmt)
            if idx >= 0:
                box.cb_fmt.setCurrentIndex(idx)
            box.sp_width.setValue(float(width))
            box.sp_height.setValue(float(height))
            box.sp_scale.setValue(float(scale))

    def _append_runtime_log(self, message: str, *, level: str = "info") -> None:
        msg = str(message or "").strip()
        if not msg:
            return
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {level.upper()} {msg}"
        if _should_emit_runtime_line(line):
            if self._terminal_dialog is not None:
                self._terminal_dialog.append_line(line)
            if RUNTIME_SAVE_LOG_FILE and RUNTIME_LOG_FILE_PATH:
                try:
                    p = Path(RUNTIME_LOG_FILE_PATH).expanduser()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with p.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                except Exception:
                    pass
        logging.getLogger("inaes_desktop").info(line)

    def _hook_tab_logs(self) -> None:
        if not isinstance(self.tabs, QTabWidget):
            return
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            log_widget = getattr(tab, "log", None)
            if isinstance(log_widget, QTextEdit):
                key = id(log_widget)
                if key in self._tab_log_sizes:
                    continue
                self._tab_log_sizes[key] = len(log_widget.toPlainText())
                log_widget.textChanged.connect(lambda w=log_widget: self._on_tab_log_changed(w))

    def _on_tab_log_changed(self, widget: QTextEdit) -> None:
        key = id(widget)
        prev = int(self._tab_log_sizes.get(key, 0))
        txt = widget.toPlainText()
        cur = len(txt)
        if cur <= prev:
            self._tab_log_sizes[key] = cur
            return
        delta = txt[prev:cur]
        self._tab_log_sizes[key] = cur
        for line in delta.splitlines():
            if line.strip():
                self._append_runtime_log(line, level="info")

    def _apply_global_geometry_tuning(self) -> None:
        # UI geometry harmonization across all tabs/panels.
        try:
            for splitter in self.findChildren(QSplitter):
                splitter.setHandleWidth(max(8, splitter.handleWidth()))
                splitter.setChildrenCollapsible(False)
                splitter.setOpaqueResize(False)

            for combo in self.findChildren(QComboBox):
                combo.setMinimumHeight(max(combo.minimumHeight(), 32))
                combo.setMinimumWidth(max(combo.minimumWidth(), 190))
                try:
                    view = combo.view()
                    if view is not None:
                        view.setMinimumWidth(max(view.minimumWidth(), 240))
                except Exception:
                    pass

            for le in self.findChildren(QLineEdit):
                le.setMinimumHeight(max(le.minimumHeight(), 32))

            for btn in self.findChildren(QPushButton):
                btn.setMinimumHeight(max(btn.minimumHeight(), 34))

            for lst in self.findChildren(QListWidget):
                lst.setMinimumHeight(max(lst.minimumHeight(), 132))

            for txt in self.findChildren(QTextEdit):
                txt.setMinimumHeight(max(txt.minimumHeight(), 92))

            for web in self.findChildren(QWebEngineView):
                web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                web.setMinimumHeight(max(web.minimumHeight(), 320))

            for tab in self.findChildren(QTableWidget):
                tab.setAlternatingRowColors(True)
                tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            # Geometry tuning must never block the app.
            pass

    def _open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self._ui_settings, parent=self)
        dlg.request_open_terminal.connect(self._open_terminal_dialog)
        dlg.request_open_manual.connect(self._open_manual)
        dlg.request_reboot.connect(self._reboot_application)
        if dlg.exec() != QDialog.Accepted:
            return
        self._ui_settings = self._sanitize_ui_settings(dlg.values())
        self._apply_ui_settings(show_status=True)
        self.save_session_state_now(force=True)

    def _ensure_runtime_log_file(self) -> Path:
        global RUNTIME_SAVE_LOG_FILE, RUNTIME_LOG_FILE_PATH
        log_path = str(self._ui_settings.get("log_file_path", "") or "").strip()
        if not log_path:
            log_path = str(Path(tempfile.gettempdir()) / "inaes_desktop_runtime.log")
            self._ui_settings["log_file_path"] = log_path
        p = Path(log_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write("")
        except Exception:
            # fallback in temp if configured path is invalid
            p = Path(tempfile.gettempdir()) / "inaes_desktop_runtime.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write("")
            self._ui_settings["log_file_path"] = str(p)
        self._ui_settings["save_log_file"] = True
        RUNTIME_SAVE_LOG_FILE = True
        RUNTIME_LOG_FILE_PATH = str(p)
        return p

    def _launch_external_terminal_tail(self, log_path: Path) -> bool:
        ptxt = str(log_path)
        try:
            if sys.platform == "darwin":
                cmd = f"clear; echo 'INAES runtime log: {ptxt}'; echo ''; tail -n 300 -F {shlex.quote(ptxt)}"
                esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
                osa = f'tell application "Terminal" to do script "{esc}"'
                subprocess.Popen(["osascript", "-e", osa], close_fds=True)
                return True
            if sys.platform.startswith("win"):
                ps_path = ptxt.replace("'", "''")
                ps_cmd = f"Get-Content -Path '{ps_path}' -Tail 300 -Wait"
                subprocess.Popen(
                    ["cmd", "/k", "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                    close_fds=True,
                )
                return True
            # linux fallback
            tail_cmd = f"tail -n 300 -F {shlex.quote(ptxt)}"
            candidates = [
                ["x-terminal-emulator", "-e", "bash", "-lc", tail_cmd],
                ["gnome-terminal", "--", "bash", "-lc", tail_cmd],
                ["konsole", "-e", "bash", "-lc", tail_cmd],
                ["xterm", "-e", "bash", "-lc", tail_cmd],
            ]
            for c in candidates:
                try:
                    subprocess.Popen(c, close_fds=True)
                    return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _open_terminal_dialog(self) -> None:
        log_path = self._ensure_runtime_log_file()
        self._append_runtime_log(
            f"Opening external terminal tail on log file: {log_path}",
            level="info",
        )
        if self._launch_external_terminal_tail(log_path):
            if self.statusBar() is not None:
                self.statusBar().showMessage(f"External terminal opened: {log_path}", 5000)
            return
        # Fallback only if external terminal launch fails.
        if self._terminal_dialog is None:
            self._terminal_dialog = LiveTerminalDialog(self)
        self._terminal_dialog.show()
        self._terminal_dialog.raise_()
        self._terminal_dialog.activateWindow()
        self._append_runtime_log("External terminal unavailable; opened embedded terminal window.", level="warning")

    def _open_manual(self, _unused: str = "") -> None:
        path = manual_pdf_path()
        if not path.exists():
            QMessageBox.warning(
                self,
                "Manual not found",
                f"The local manual PDF was not found:\n{path}",
            )
            self._append_runtime_log(f"Manual PDF not found: {path}", level="warning")
            return
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not ok:
            QMessageBox.warning(self, "Manual open failed", f"Could not open manual:\n{path}")
            self._append_runtime_log(f"Failed to open manual PDF: {path}", level="warning")
            return
        self._append_runtime_log(f"Opened local manual: {path}", level="info")

    def _reboot_application(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reboot session",
            "This will restart the application now.\nUnsaved work may be lost.\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._append_runtime_log("Reboot requested by user.", level="warning")
        self.save_session_state_now(force=True)
        try:
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as exc:
            QMessageBox.critical(self, "Reboot failed", str(exc))
            self._append_runtime_log(f"Reboot failed: {exc}", level="error")

    def _run_preflight_on_startup(self) -> None:
        policy = str(self._ui_settings.get("preflight_policy", "warn") or "warn").strip().lower()
        if policy == "skip":
            self._append_runtime_log("Preflight skipped by policy.", level="info")
            return
        failures: list[str] = []
        warnings: list[str] = []
        if bool(self._ui_settings.get("save_log_file", False)):
            log_path = str(self._ui_settings.get("log_file_path", "") or "").strip()
            if not log_path:
                warnings.append("Diagnostics log-to-file is enabled but no log file path is configured.")
            else:
                try:
                    p = Path(log_path).expanduser()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with p.open("a", encoding="utf-8") as fh:
                        fh.write("")
                except Exception as exc:
                    failures.append(f"Log file path is not writable: {exc}")
        exp_dir = str(self._ui_settings.get("default_export_dir", "") or "").strip()
        if exp_dir:
            try:
                Path(exp_dir).expanduser().mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                warnings.append(f"Default export folder not writable: {exc}")

        if not failures and not warnings:
            self._append_runtime_log("Preflight checks passed.", level="info")
            return

        lines = []
        if failures:
            lines.append("Failures:")
            lines.extend([f"- {x}" for x in failures])
        if warnings:
            lines.append("Warnings:")
            lines.extend([f"- {x}" for x in warnings])
        msg = "\n".join(lines)

        if policy == "strict" and failures:
            resp = QMessageBox.question(
                self,
                "Preflight failed (strict)",
                msg + "\n\nContinue anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                QTimer.singleShot(0, self.close)
                return
        else:
            QMessageBox.warning(self, "Preflight report", msg)
        self._append_runtime_log("Preflight report:\n" + msg, level="warning")

    def _session_payload(self) -> dict[str, Any]:
        geom = self.geometry()
        payload: dict[str, Any] = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "main_window": {
                "x": int(geom.x()),
                "y": int(geom.y()),
                "width": int(geom.width()),
                "height": int(geom.height()),
                "active_tab_index": int(self.tabs.currentIndex()) if isinstance(self.tabs, QTabWidget) else 0,
            },
            "data_upload": self.tab_data_upload.export_session_state() if self.tab_data_upload is not None else {},
            "ui_settings": dict(self._ui_settings),
        }
        return payload

    def save_session_state_now(self, *, force: bool = False) -> Path | None:
        if not force and not bool(self._ui_settings.get("session_autosave_on_close", True)):
            return None
        try:
            payload = self._session_payload()
            out = save_session_state(payload)
            return out
        except Exception as exc:
            if self.statusBar() is not None:
                self.statusBar().showMessage(f"Session save failed: {exc}", 4000)
            return None

    def restore_session_state_now(self) -> bool:
        try:
            payload = load_session_state()
        except Exception as exc:
            if self.statusBar() is not None:
                self.statusBar().showMessage(f"Session restore failed: {exc}", 4000)
            return False
        if not isinstance(payload, dict):
            return False

        self._ui_settings = self._sanitize_ui_settings(payload.get("ui_settings"))
        self._apply_ui_settings(show_status=False)

        if not bool(self._ui_settings.get("session_restore_on_startup", True)):
            if isinstance(self.tabs, QTabWidget):
                self.tabs.setCurrentIndex(0)
            if self.statusBar() is not None:
                self.statusBar().showMessage("Session restore disabled by settings.", 2500)
            return False

        mw = payload.get("main_window", {}) if isinstance(payload.get("main_window"), dict) else {}
        w = int(mw.get("width", self.width())) if str(mw.get("width", "")).strip() else self.width()
        h = int(mw.get("height", self.height())) if str(mw.get("height", "")).strip() else self.height()
        if w > 100 and h > 100:
            self.resize(w, h)
        x_txt = str(mw.get("x", "")).strip()
        y_txt = str(mw.get("y", "")).strip()
        if x_txt and y_txt:
            try:
                self.move(int(float(x_txt)), int(float(y_txt)))
            except Exception:
                pass

        if self.tab_data_upload is not None:
            data_payload = payload.get("data_upload", {})
            if isinstance(data_payload, dict):
                self.tab_data_upload.restore_session_state(data_payload)

        if isinstance(self.tabs, QTabWidget):
            # UX rule: app always opens on Data Upload tab.
            self.tabs.setCurrentIndex(0)

        if self.statusBar() is not None:
            self.statusBar().showMessage("Session restored.", 2500)
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_session_state_now()
        if isinstance(self.tabs, QTabWidget):
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                shutdown = getattr(tab, "_shutdown_background_threads", None)
                if callable(shutdown):
                    try:
                        shutdown()
                    except Exception:
                        pass
        super().closeEvent(event)
