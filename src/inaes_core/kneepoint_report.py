from __future__ import annotations

from datetime import datetime
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from .kneepoint import KneeResult, filter_kp_points_for_sample, kneepoint_analysis

DEFAULT_NM_AXIS_LABEL = "nm (g^-1)"


def _coerce_nm_axis_label(axis_label: Any) -> str:
    s = str(axis_label or "").strip()
    return s if s else DEFAULT_NM_AXIS_LABEL


def _format_math_exponents(text: Any) -> str:
    s = str(text or "")
    if not s:
        return s
    s = re.sub(r"([A-Za-z0-9_,]+)\^\{([+-]?\d+)\}", r"\1<sup>\2</sup>", s)
    s = re.sub(r"([A-Za-z0-9_,]+)\^([+-]?\d+)", r"\1<sup>\2</sup>", s)
    return s


def sanitize_file_stem(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", str(name or "file"))
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return stem or "file"


def normalize_temp_bounds(temp_min: Any = None, temp_max: Any = None) -> tuple[float | None, float | None]:
    tmin = pd.to_numeric(pd.Series([temp_min]), errors="coerce").iloc[0]
    tmax = pd.to_numeric(pd.Series([temp_max]), errors="coerce").iloc[0]
    tmin = float(tmin) if np.isfinite(tmin) else None
    tmax = float(tmax) if np.isfinite(tmax) else None
    if (tmin is not None) and (tmax is not None) and (tmin > tmax):
        tmin, tmax = tmax, tmin
    return tmin, tmax


def _breakpoint_pairs(res: KneeResult) -> list[tuple[float, float]]:
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


def _piecewise_overlay_data(res: KneeResult) -> tuple[np.ndarray, np.ndarray, list[float]]:
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
    if len(breaks_all) >= 2:
        breaks_internal = list(np.array(sorted(breaks_all), dtype=float)[1:-1])
    else:
        breaks_internal = []
    return x_pw, y_pw, breaks_internal


def kp_figure_bytes(fig: go.Figure, *, fmt: str, width: int = 1800, height: int | None = None, scale: float = 2.0) -> bytes:
    h = int(height) if (height is not None and np.isfinite(height)) else int(fig.layout.height or 1200)
    s = float(scale if fmt.lower() != "pdf" else 1.0)
    return pio.to_image(fig, format=fmt, width=int(width), height=int(h), scale=s)


def kp_kaleido_available() -> tuple[bool, str]:
    try:
        test_fig = go.Figure(data=[go.Scatter(x=[0, 1], y=[0, 1])])
        _ = pio.to_image(test_fig, format="svg", width=300, height=180, scale=1)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def kp_summary_row(sample: str, res: KneeResult, requested_breaks: int) -> dict[str, Any]:
    row: dict[str, Any] = {"Sample": str(sample)}
    n_req = max(int(requested_breaks), 1)
    pairs = _breakpoint_pairs(res)
    for i in range(1, n_req + 1):
        t_col = f"Kneepoint{i}_T"
        nm_col = f"Kneepoint{i}_nm"
        if i <= len(pairs):
            row[t_col] = float(pairs[i - 1][0])
            row[nm_col] = float(pairs[i - 1][1]) if np.isfinite(pairs[i - 1][1]) else np.nan
        else:
            row[t_col] = np.nan
            row[nm_col] = np.nan
    return row


def kp_parameters_row(sample: str, res: KneeResult, params: dict[str, Any] | None = None) -> dict[str, Any]:
    p = params if isinstance(params, dict) else {}
    pw = getattr(res, "piecewise_params", {}) if isinstance(getattr(res, "piecewise_params", {}), dict) else {}

    def _num(v: Any) -> float:
        x = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
        return float(x) if np.isfinite(x) else np.nan

    return {
        "Sample": str(sample),
        "Spline_spar": _num(p.get("spar", pw.get("spline_spar"))),
        "Spline_s_mapped": _num(pw.get("spline_s_mapped")),
        "Flat_quantile": _num(p.get("flat_quantile")),
        "Rise_quantile": _num(p.get("rise_quantile")),
        "Temp_range_enabled": bool(p.get("temp_range_enabled", False)),
        "Temp_min_C": _num(p.get("temp_min")),
        "Temp_max_C": _num(p.get("temp_max")),
    }


def kp_parameters_df_from_sample_items(sample_items: Sequence[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for it in sample_items:
        if not isinstance(it, dict):
            continue
        sample = str(it.get("sample", "")).strip()
        res = it.get("result")
        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        if not sample or not isinstance(res, KneeResult):
            continue
        rows.append(kp_parameters_row(sample, res, params))
    cols = [
        "Sample",
        "Spline_spar",
        "Spline_s_mapped",
        "Flat_quantile",
        "Rise_quantile",
        "Temp_range_enabled",
        "Temp_min_C",
        "Temp_max_C",
    ]
    out = pd.DataFrame(rows)
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    return out[cols].reset_index(drop=True)


def kp_build_single_sample_figure(
    sample: str,
    pts: pd.DataFrame,
    res: KneeResult,
    *,
    point_size: int = 6,
    line_width: float = 2.0,
    show_breakpoints: bool = True,
    show_grid: bool = True,
    nm_axis_label: str = DEFAULT_NM_AXIS_LABEL,
) -> tuple[go.Figure, list[dict[str, Any]]]:
    temp_col = "Freezing.temperature"
    fig = go.Figure()
    fig.update_layout(height=520, margin=dict(l=45, r=20, t=35, b=40))

    loc_arr = (
        pts["Location"].astype(str).to_numpy()
        if "Location" in pts.columns
        else np.repeat("(no Location)", len(pts))
    )
    fig.add_trace(
        go.Scatter(
            x=pts[temp_col],
            y=pts["nm"],
            mode="markers",
            name="Raw points",
            marker=dict(size=int(point_size), color="#93c5fd", line=dict(color="black", width=0.4), opacity=0.85),
            customdata=np.stack([np.repeat(str(sample), len(pts)), loc_arr], axis=1),
            hovertemplate="Sample=%{customdata[0]}<br>Location=%{customdata[1]}<br>T=%{x:.2f}°C<br>nm=%{y:.3e}<extra></extra>",
            showlegend=False,
        )
    )

    g = res.spline_grid
    spline_nm = np.power(10.0, pd.to_numeric(g["log_nm_spline"], errors="coerce").to_numpy(dtype=float))
    fig.add_trace(
        go.Scatter(
            x=g[temp_col],
            y=spline_nm,
            mode="lines",
            name="Smoothing spline",
            line=dict(color="#60a5fa", width=float(line_width)),
            hovertemplate=f"Sample={sample}<br>T=%{{x:.2f}}°C<br>nm=%{{y:.3e}}<extra></extra>",
            showlegend=False,
        )
    )

    bp_rows: list[dict[str, Any]] = []
    for i, (bp, nm_bp) in enumerate(_breakpoint_pairs(res), start=1):
        y_bp = float(nm_bp) if nm_bp > 0 else np.nan
        y_bp_log = float(np.log10(nm_bp)) if nm_bp > 0 else np.nan
        fig.add_vline(x=float(bp), line_width=float(line_width), line_dash="dash", line_color="#f59e0b")
        fig.add_trace(
            go.Scatter(
                x=[float(bp)],
                y=[y_bp],
                mode="markers+text",
                name=f"Knee {i}",
                text=[f"K{i}: {bp:.2f}°C\n{nm_bp:.2e}"],
                textposition="top center",
                marker=dict(size=int(point_size) + 2, color="#f59e0b", line=dict(color="black", width=0.5)),
                hovertemplate=f"Sample={sample}<br>Knee {i}<br>T={bp:.2f}°C<br>nm={nm_bp:.3e}<extra></extra>",
                showlegend=False,
            )
        )
        bp_rows.append({"knee": i, "T_C": float(bp), "nm": float(nm_bp), "log10_nm": y_bp_log})

    if bool(show_breakpoints):
        x_pw, y_pw, _bps_internal = _piecewise_overlay_data(res)
        if x_pw.size > 0 and y_pw.size == x_pw.size:
            fig.add_trace(
                go.Scatter(
                    x=x_pw,
                    y=y_pw,
                    mode="lines",
                    name="Piecewise segments",
                    line=dict(color="#7c3aed", width=max(1.2, float(line_width) - 0.2), dash="dot"),
                    hovertemplate=f"Sample={sample}<br>T=%{{x:.2f}}°C<br>piecewise nm=%{{y:.3e}}<extra></extra>",
                    showlegend=False,
                )
            )

    fig.update_layout(
        title={"text": f"Kneepoint - {sample}", "x": 0.01},
        xaxis_title=temp_col,
        yaxis_title=_format_math_exponents(_coerce_nm_axis_label(nm_axis_label)),
        template="plotly_white",
    )
    fig.update_xaxes(showgrid=bool(show_grid))
    y_all = np.concatenate(
        [
            pd.to_numeric(pts["nm"], errors="coerce").to_numpy(dtype=float),
            spline_nm,
        ]
    )
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
            ticktext=[_format_math_exponents(f"10^{e}") for e in exps],
            showgrid=bool(show_grid),
        )
    else:
        fig.update_yaxes(showgrid=bool(show_grid))
    return fig, bp_rows


def kp_build_grid_figure(
    sample_items: list[dict[str, Any]],
    *,
    ncols: int = 2,
    show_breakpoints: bool = True,
    show_grid: bool = True,
    nm_axis_label: str = DEFAULT_NM_AXIS_LABEL,
) -> go.Figure:
    n = len(sample_items)
    ncols = max(1, int(min(ncols, max(n, 1))))
    nrows = int(np.ceil(n / ncols))
    titles = [str(it["sample"]) for it in sample_items]
    specs = [[{"type": "xy"} for _ in range(ncols)] for _ in range(nrows)]
    fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles, specs=specs)

    y_global: list[np.ndarray] = []

    for idx, it in enumerate(sample_items):
        r = (idx // ncols) + 1
        c = (idx % ncols) + 1
        pts = it["points"]
        res = it["result"]
        spline_nm = np.power(10.0, pd.to_numeric(res.spline_grid["log_nm_spline"], errors="coerce").to_numpy(dtype=float))
        y_global.append(pd.to_numeric(pts["nm"], errors="coerce").to_numpy(dtype=float))
        y_global.append(spline_nm)
        fig.add_trace(
            go.Scatter(
                x=pts["Freezing.temperature"],
                y=pts["nm"],
                mode="markers",
                marker=dict(size=4, color="#93c5fd", line=dict(color="black", width=0.3), opacity=0.8),
                showlegend=False,
                hovertemplate=f"Sample={it['sample']}<br>T=%{{x:.2f}}°C<br>nm=%{{y:.3e}}<extra></extra>",
            ),
            row=r,
            col=c,
        )
        fig.add_trace(
            go.Scatter(
                x=res.spline_grid["Freezing.temperature"],
                y=spline_nm,
                mode="lines",
                line=dict(color="#60a5fa", width=1.8),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=r,
            col=c,
        )
        for bp, nm_bp in _breakpoint_pairs(res):
            y_bp = float(nm_bp) if nm_bp > 0 else np.nan
            fig.add_trace(
                go.Scatter(
                    x=[float(bp)],
                    y=[y_bp],
                    mode="markers",
                    marker=dict(size=7, color="#f59e0b", line=dict(color="black", width=0.4)),
                    showlegend=False,
                    hovertemplate=f"Sample={it['sample']}<br>T={bp:.2f}°C<br>nm={nm_bp:.3e}<extra></extra>",
                ),
                row=r,
                col=c,
            )
            fig.add_vline(x=float(bp), line_width=1.0, line_dash="dash", line_color="#f59e0b", row=r, col=c)
        if bool(show_breakpoints):
            x_pw, y_pw, _bps_internal = _piecewise_overlay_data(res)
            if x_pw.size > 0 and y_pw.size == x_pw.size:
                fig.add_trace(
                    go.Scatter(
                        x=x_pw,
                        y=y_pw,
                        mode="lines",
                        line=dict(color="#7c3aed", width=1.4, dash="dot"),
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    row=r,
                    col=c,
                )
        fig.update_xaxes(title_text="Freezing.temperature", showgrid=bool(show_grid), row=r, col=c)

    y_concat = np.concatenate([v for v in y_global if v is not None and len(v) > 0]) if y_global else np.asarray([], dtype=float)
    y_concat = y_concat[np.isfinite(y_concat) & (y_concat > 0)]
    if y_concat.size > 0:
        exp_min = int(np.floor(np.log10(float(np.nanmin(y_concat)))))
        exp_max = int(np.ceil(np.log10(float(np.nanmax(y_concat)))))
        exps = [e for e in range(exp_min, exp_max + 1) if -300 <= int(e) <= 300]
        if len(exps) == 0:
            exps = [int(np.clip(exp_min, -300, 300)), int(np.clip(exp_max, -300, 300))]
            exps = sorted(set(exps))
        tickvals = [float(np.power(10.0, float(e))) for e in exps]
        ticktext = [_format_math_exponents(f"10^{e}") for e in exps]
    else:
        tickvals, ticktext = None, None

    for idx in range(n):
        r = (idx // ncols) + 1
        c = (idx % ncols) + 1
        fig.update_yaxes(
            title_text=_format_math_exponents(_coerce_nm_axis_label(nm_axis_label)),
            type="log",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=bool(show_grid),
            row=r,
            col=c,
        )

    fig.update_layout(
        # More vertical space per panel to avoid flattened/squashed report plots.
        height=max(520 * nrows, 620),
        margin=dict(l=50, r=30, t=70, b=40),
        title={"text": "Kneepoint - All Selected Samples", "x": 0.01},
        template="plotly_white",
    )
    return fig


def kp_build_full_report_figure(
    summary_df: pd.DataFrame,
    sample_items: list[dict[str, Any]],
    *,
    ncols: int = 2,
    show_breakpoints: bool = True,
    show_grid: bool = True,
    nm_axis_label: str = DEFAULT_NM_AXIS_LABEL,
) -> go.Figure:
    n = len(sample_items)
    ncols = max(1, int(min(ncols, max(n, 1))))
    nrows_plot = int(np.ceil(max(n, 1) / ncols))

    table_frac = float(np.clip(0.14 + (0.018 * len(summary_df)), 0.18, 0.46))
    plot_frac = max(1.0 - table_frac, 0.25)
    row_heights = [table_frac] + [plot_frac / nrows_plot for _ in range(nrows_plot)]

    specs: list[list[Any]] = []
    specs.append([{"type": "xy", "colspan": ncols}] + [None for _ in range(ncols - 1)])
    for _ in range(nrows_plot):
        specs.append([{"type": "xy"} for _ in range(ncols)])

    rows_total = 1 + nrows_plot
    vertical_spacing = min(0.02, 0.5 / max(rows_total - 1, 1))
    fig = make_subplots(
        rows=1 + nrows_plot,
        cols=ncols,
        specs=specs,
        row_heights=row_heights,
        vertical_spacing=float(vertical_spacing),
        subplot_titles=[""] + [str(it["sample"]) for it in sample_items],
    )

    table_df = summary_df.copy()
    table_df.columns = [str(c) for c in table_df.columns]
    kp_idx: list[int] = []
    for c in table_df.columns:
        m = re.match(r"^Kneepoint(\d+)_(T|nm)$", c, flags=re.IGNORECASE)
        if m:
            kp_idx.append(int(m.group(1)))
    kp_idx = sorted(set(kp_idx))

    value_cols: list[str] = ["Sample"] if "Sample" in table_df.columns else [table_df.columns[0]]
    for k in kp_idx:
        t_col = f"Kneepoint{k}_T"
        nm_col = f"Kneepoint{k}_nm"
        value_cols.extend([t_col, nm_col])
    used = set(value_cols)
    extra_cols = [c for c in table_df.columns if c not in used]
    if extra_cols:
        value_cols.extend(extra_cols)
    for c in value_cols:
        if c not in table_df.columns:
            table_df[c] = np.nan
    table_df = table_df[value_cols]
    for c in table_df.columns:
        if c == "Sample":
            table_df[c] = table_df[c].astype(str)
            continue
        table_df[c] = table_df[c].map(lambda v: "" if pd.isna(v) else f"{float(v):.6g}")

    col_width = [1.8] + [1.0 for _ in range(max(len(value_cols) - 1, 0))]
    x_edges = np.cumsum([0.0] + col_width).tolist()
    total_w = float(x_edges[-1]) if x_edges else 1.0
    header_top_h = 1.0
    header_sub_h = 0.92
    data_h = 0.82
    total_h = float(header_top_h + header_sub_h + (data_h * max(len(table_df), 1)))

    fig.add_trace(
        go.Scatter(
            x=[0.0, total_w],
            y=[0.0, total_h],
            mode="markers",
            marker=dict(opacity=0),
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )
    fig.update_xaxes(visible=False, range=[0.0, total_w], fixedrange=True, row=1, col=1)
    fig.update_yaxes(visible=False, range=[0.0, total_h], fixedrange=True, row=1, col=1)

    # Report table style: white background (requested), dark text, soft gray borders.
    header_fill = "#ffffff"
    cell_fill = "#ffffff"
    line_color = "#cbd5e1"
    table_shapes: list[dict[str, Any]] = []
    table_annotations: list[dict[str, Any]] = []

    def _add_table_cell(
        x0: float,
        x1: float,
        y0: float,
        y1: float,
        text: str,
        *,
        fill: str,
        font_color: str,
        font_size: int,
        bold: bool = False,
        align: str = "center",
    ) -> None:
        table_shapes.append(
            dict(
                type="rect",
                xref="x",
                yref="y",
                x0=float(x0),
                x1=float(x1),
                y0=float(y0),
                y1=float(y1),
                line=dict(color=line_color, width=1.0),
                fillcolor=fill,
                layer="above",
            )
        )
        ann: dict[str, Any] = dict(
            xref="x",
            yref="y",
            y=float((y0 + y1) / 2.0),
            text=f"<b>{text}</b>" if bold and text else text,
            showarrow=False,
            font=dict(color=font_color, size=font_size),
            yanchor="middle",
        )
        if align == "left":
            ann["x"] = float(x0 + (0.06 * (x1 - x0)))
            ann["xanchor"] = "left"
            ann["align"] = "left"
        else:
            ann["x"] = float((x0 + x1) / 2.0)
            ann["xanchor"] = "center"
            ann["align"] = "center"
        table_annotations.append(ann)

    y_top0 = total_h - header_top_h
    y_top1 = total_h
    _add_table_cell(
        x_edges[0], x_edges[1], y_top0, y_top1,
        "Sample", fill=header_fill, font_color="#0f172a", font_size=12, bold=True, align="left"
    )

    col_pos = 1
    while col_pos < len(value_cols):
        name = value_cols[col_pos]
        m = re.match(r"^Kneepoint(\d+)_(T|nm)$", name, flags=re.IGNORECASE)
        if m and (col_pos + 1 < len(value_cols)):
            k = int(m.group(1))
            x0 = x_edges[col_pos]
            x1 = x_edges[min(col_pos + 2, len(x_edges) - 1)]
            _add_table_cell(
                x0, x1, y_top0, y_top1, f"Kneepoint {k}",
                fill=header_fill, font_color="#0f172a", font_size=12, bold=True
            )
            col_pos += 2
        else:
            x0 = x_edges[col_pos]
            x1 = x_edges[col_pos + 1]
            _add_table_cell(
                x0, x1, y_top0, y_top1, name,
                fill=header_fill, font_color="#0f172a", font_size=12, bold=True
            )
            col_pos += 1

    y_sub0 = total_h - header_top_h - header_sub_h
    y_sub1 = total_h - header_top_h
    _add_table_cell(
        x_edges[0], x_edges[1], y_sub0, y_sub1,
        "", fill=header_fill, font_color="#0f172a", font_size=11, bold=True
    )
    for j in range(1, len(value_cols)):
        name = value_cols[j]
        lbl = "T" if name.endswith("_T") else ("nm" if name.endswith("_nm") else name)
        _add_table_cell(
            x_edges[j], x_edges[j + 1], y_sub0, y_sub1, lbl,
            fill=header_fill, font_color="#0f172a", font_size=11, bold=True
        )

    y_cursor = y_sub0
    for ridx in range(len(table_df)):
        y1 = y_cursor
        y0 = y1 - data_h
        for cidx, cname in enumerate(value_cols):
            txt = str(table_df.iloc[ridx][cname])
            _add_table_cell(
                x_edges[cidx], x_edges[cidx + 1], y0, y1, txt,
                fill=cell_fill, font_color="#0f172a", font_size=11, align="left" if cidx == 0 else "center"
            )
        y_cursor = y0

    fig.update_layout(shapes=table_shapes, annotations=list(fig.layout.annotations) + table_annotations)

    y_global: list[np.ndarray] = []
    for idx, it in enumerate(sample_items):
        rr = 2 + (idx // ncols)
        cc = 1 + (idx % ncols)
        pts = it["points"]
        res = it["result"]
        spline_nm = np.power(10.0, pd.to_numeric(res.spline_grid["log_nm_spline"], errors="coerce").to_numpy(dtype=float))
        y_global.append(pd.to_numeric(pts["nm"], errors="coerce").to_numpy(dtype=float))
        y_global.append(spline_nm)
        fig.add_trace(
            go.Scatter(
                x=pts["Freezing.temperature"],
                y=pts["nm"],
                mode="markers",
                marker=dict(size=5, color="#93c5fd", line=dict(color="black", width=0.35), opacity=0.85),
                showlegend=False,
                hovertemplate=f"Sample={it['sample']}<br>T=%{{x:.2f}}°C<br>nm=%{{y:.3e}}<extra></extra>",
            ),
            row=rr,
            col=cc,
        )
        fig.add_trace(
            go.Scatter(
                x=res.spline_grid["Freezing.temperature"],
                y=spline_nm,
                mode="lines",
                line=dict(color="#60a5fa", width=2.1),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=rr,
            col=cc,
        )
        for bp, nm_bp in _breakpoint_pairs(res):
            y_bp = float(nm_bp) if nm_bp > 0 else np.nan
            fig.add_trace(
                go.Scatter(
                    x=[float(bp)],
                    y=[y_bp],
                    mode="markers",
                    marker=dict(size=7, color="#f59e0b", line=dict(color="black", width=0.4)),
                    showlegend=False,
                    hovertemplate=f"Sample={it['sample']}<br>T={bp:.2f}°C<br>nm={nm_bp:.3e}<extra></extra>",
                ),
                row=rr,
                col=cc,
            )
            fig.add_vline(x=float(bp), line_width=1.0, line_dash="dash", line_color="#f59e0b", row=rr, col=cc)
        if bool(show_breakpoints):
            x_pw, y_pw, _bps_internal = _piecewise_overlay_data(res)
            if x_pw.size > 0 and y_pw.size == x_pw.size:
                fig.add_trace(
                    go.Scatter(
                        x=x_pw,
                        y=y_pw,
                        mode="lines",
                        line=dict(color="#7c3aed", width=1.4, dash="dot"),
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    row=rr,
                    col=cc,
                )
        fig.update_xaxes(title_text="Freezing.temperature", showgrid=bool(show_grid), row=rr, col=cc)

    y_concat = np.concatenate([v for v in y_global if v is not None and len(v) > 0]) if y_global else np.asarray([], dtype=float)
    y_concat = y_concat[np.isfinite(y_concat) & (y_concat > 0)]
    if y_concat.size > 0:
        exp_min = int(np.floor(np.log10(float(np.nanmin(y_concat)))))
        exp_max = int(np.ceil(np.log10(float(np.nanmax(y_concat)))))
        exps = [e for e in range(exp_min, exp_max + 1) if -300 <= int(e) <= 300]
        if len(exps) == 0:
            exps = [int(np.clip(exp_min, -300, 300)), int(np.clip(exp_max, -300, 300))]
            exps = sorted(set(exps))
        tickvals = [float(np.power(10.0, float(e))) for e in exps]
        ticktext = [_format_math_exponents(f"10^{e}") for e in exps]
    else:
        tickvals, ticktext = None, None

    for idx in range(n):
        rr = 2 + (idx // ncols)
        cc = 1 + (idx % ncols)
        fig.update_yaxes(
            title_text=_format_math_exponents(_coerce_nm_axis_label(nm_axis_label)),
            type="log",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=bool(show_grid),
            row=rr,
            col=cc,
        )

    fig.update_layout(
        # Taller layout and slightly denser x packing -> closer to square panels in exported report.
        height=max(int(760 + (620 * nrows_plot) + (24 * len(summary_df))), 1600),
        margin=dict(l=40, r=30, t=70, b=40),
        title={"text": "Kneepoint Report", "x": 0.01},
        template="plotly_white",
    )
    return fig


def build_kneepoint_report_preview(
    curves_df: pd.DataFrame,
    *,
    report_samples: Sequence[Any],
    size: Any,
    dilutions: Sequence[Any],
    temp_min: Any,
    temp_max: Any,
    spar: Any,
    nbreaks: Any,
    flat_q: Any,
    rise_q: Any,
    point_size: Any,
    line_width: Any,
    show_breakpoints: bool,
    show_grid: bool,
    nm_axis_label: Any,
    segment_selection_mode: str = "legacy",
    segment_min_internal_breaks: int = 2,
    segment_max_internal_breaks: int = 14,
    sample_overrides: dict[str, dict[str, Any]] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def _report_progress(pct: int, msg: str) -> None:
        if progress_callback is None:
            return
        try:
            p = int(max(0, min(100, int(pct))))
            progress_callback(p, str(msg))
        except Exception:
            # Never fail report generation because of progress plumbing.
            pass

    def _is_cancelled() -> bool:
        if cancel_requested is None:
            return False
        try:
            return bool(cancel_requested())
        except Exception:
            return False

    def _raise_if_cancelled() -> None:
        if _is_cancelled():
            raise RuntimeError("Report generation cancelled by user.")

    _report_progress(1, "Validating report input...")
    _raise_if_cancelled()
    raw_samples = list(report_samples) if isinstance(report_samples, (list, tuple, set)) else [report_samples]
    selected_samples: list[str] = []
    for s in raw_samples:
        ss = str(s or "").strip()
        if ss and (ss not in selected_samples):
            selected_samples.append(ss)
    if len(selected_samples) == 0:
        raise ValueError("Select at least one report sample.")
    size_txt = str(size or "").strip()
    if not size_txt:
        raise ValueError("Select Size in Kneepoint settings before generating report.")
    selected_dils = list(dilutions) if isinstance(dilutions, (list, tuple, set)) else [dilutions]
    if len([d for d in selected_dils if str(d).strip()]) == 0:
        raise ValueError("Select at least one Dilution.factor in Kneepoint settings before generating report.")

    _report_progress(5, "Starting sample analysis...")
    _raise_if_cancelled()

    req_breaks = max(int(nbreaks or 1), 1)
    spar_val = float(spar if spar is not None else 0.40)
    flat_q_val = float(flat_q if flat_q is not None else 0.35)
    rise_q_val = float(rise_q if rise_q is not None else 0.70)
    point_size_val = int(point_size or 6)
    line_width_val = float(line_width or 2.0)
    show_breakpoints_val = bool(show_breakpoints)
    nm_axis_label_txt = _coerce_nm_axis_label(nm_axis_label)
    segment_mode_val = str(segment_selection_mode or "legacy").strip().lower()
    if segment_mode_val not in {"legacy", "bic", "cv", "cv+bic"}:
        segment_mode_val = "legacy"
    seg_min_val = max(int(segment_min_internal_breaks or 2), 1)
    seg_max_val = max(int(segment_max_internal_breaks or 14), seg_min_val)
    overrides_map = sample_overrides if isinstance(sample_overrides, dict) else {}

    sample_items: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    tmin, tmax = normalize_temp_bounds(temp_min, temp_max)
    n_total = max(len(selected_samples), 1)

    for idx, sample in enumerate(selected_samples, start=1):
        _raise_if_cancelled()
        _report_progress(8 + int((idx - 1) * 70 / n_total), f"Analyzing sample {idx}/{n_total}: {sample}")
        try:
            ov = overrides_map.get(str(sample), {}) if isinstance(overrides_map.get(str(sample), {}), dict) else {}
            ov_dils = ov.get("dilutions", selected_dils)
            ov_dils = list(ov_dils) if isinstance(ov_dils, (list, tuple, set)) else list(selected_dils)
            ov_spar = float(ov.get("spar", spar_val))
            ov_nbreaks = max(int(ov.get("n_breaks", req_breaks)), 1)
            ov_flat_q = float(ov.get("flat_quantile", flat_q_val))
            ov_rise_q = float(ov.get("rise_quantile", rise_q_val))
            ov_temp_min = ov.get("temp_min", tmin)
            ov_temp_max = ov.get("temp_max", tmax)
            ov_range_enabled = bool(ov.get("temp_range_enabled", (ov_temp_min is not None) or (ov_temp_max is not None)))
            if not ov_range_enabled:
                ov_temp_min, ov_temp_max = None, None
            ov_point_size = int(ov.get("point_size", point_size_val))
            ov_line_width = float(ov.get("line_width", line_width_val))
            ov_show_breakpoints = bool(ov.get("show_breakpoints", show_breakpoints_val))
            ov_show_grid = bool(ov.get("show_grid", bool(show_grid)))
            ov_segment_mode = str(ov.get("segment_selection_mode", segment_mode_val) or "legacy").strip().lower()
            if ov_segment_mode not in {"legacy", "bic", "cv", "cv+bic"}:
                ov_segment_mode = segment_mode_val
            ov_seg_min = max(int(ov.get("segment_min_internal_breaks", seg_min_val)), 1)
            ov_seg_max = max(int(ov.get("segment_max_internal_breaks", seg_max_val)), ov_seg_min)

            pts = filter_kp_points_for_sample(
                curves_df,
                sample=sample,
                size=size_txt,
                dilutions=ov_dils,
                temp_min=ov_temp_min,
                temp_max=ov_temp_max,
            )
            if len(pts) < 6:
                skipped.append(f"{sample}: insufficient points after filters (n={len(pts)})")
                continue

            res = kneepoint_analysis(
                curves_df,
                sample=sample,
                size=size_txt,
                dilutions=ov_dils,
                spar=ov_spar,
                n_breaks=ov_nbreaks,
                flat_quantile=ov_flat_q,
                rise_quantile=ov_rise_q,
                segment_selection_mode=ov_segment_mode,
                segment_min_internal_breaks=ov_seg_min,
                segment_max_internal_breaks=ov_seg_max,
                temp_min=ov_temp_min,
                temp_max=ov_temp_max,
                boot_R=200,
                cv_k=5,
            )

            fig_single, _ = kp_build_single_sample_figure(
                sample,
                pts,
                res,
                point_size=ov_point_size,
                line_width=ov_line_width,
                show_breakpoints=ov_show_breakpoints,
                show_grid=ov_show_grid,
                nm_axis_label=nm_axis_label_txt,
            )
            params_used = {
                "dilutions": list(ov_dils),
                "spar": float(ov_spar),
                "n_breaks": int(ov_nbreaks),
                "flat_quantile": float(ov_flat_q),
                "rise_quantile": float(ov_rise_q),
                "temp_min": ov_temp_min,
                "temp_max": ov_temp_max,
                "temp_range_enabled": bool(ov_range_enabled),
                "point_size": int(ov_point_size),
                "line_width": float(ov_line_width),
                "show_breakpoints": bool(ov_show_breakpoints),
                "show_grid": bool(ov_show_grid),
                "segment_selection_mode": str(ov_segment_mode),
                "segment_min_internal_breaks": int(ov_seg_min),
                "segment_max_internal_breaks": int(ov_seg_max),
            }
            sample_items.append({"sample": sample, "points": pts, "result": res, "figure": fig_single, "params": params_used})
            summary_rows.append(kp_summary_row(sample, res, req_breaks))
        except Exception as exc:
            skipped.append(f"{sample}: {exc}")
        _raise_if_cancelled()
        _report_progress(8 + int(idx * 70 / n_total), f"Analyzed sample {idx}/{n_total}: {sample}")

    if len(sample_items) == 0:
        skip_txt = "; ".join(skipped[:4])
        if len(skipped) > 4:
            skip_txt += f"; +{len(skipped) - 4} more"
        raise RuntimeError(f"Report not created. No valid samples. {skip_txt}")

    summary_df = pd.DataFrame(summary_rows)
    ordered_cols = ["Sample"]
    for i in range(1, req_breaks + 1):
        ordered_cols.extend([f"Kneepoint{i}_T", f"Kneepoint{i}_nm"])
    for c in ordered_cols:
        if c not in summary_df.columns:
            summary_df[c] = np.nan
    summary_df = summary_df[ordered_cols].reset_index(drop=True)
    parameters_df = kp_parameters_df_from_sample_items(sample_items)

    status = f"Preview ready: processed={len(sample_items)} | skipped={len(skipped)}"
    if (tmin is not None) or (tmax is not None):
        status += f" | T_range=[{tmin if tmin is not None else '-inf'}, {tmax if tmax is not None else '+inf'}]"
    _report_progress(100, "Preview created.")
    return {
        "summary_df": summary_df,
        "parameters_df": parameters_df,
        "sample_items": sample_items,
        "processed_samples": [it["sample"] for it in sample_items],
        "skipped": skipped,
        "status": status,
        "requested_breaks": req_breaks,
        "size": size_txt,
        "dilutions": list(selected_dils),
        "temp_min": tmin,
        "temp_max": tmax,
        "spar": spar_val,
        "flat_q": flat_q_val,
        "rise_q": rise_q_val,
        "point_size": point_size_val,
        "line_width": line_width_val,
        "show_breakpoints": show_breakpoints_val,
        "show_grid": bool(show_grid),
        "nm_axis_label": nm_axis_label_txt,
        "segment_selection_mode": segment_mode_val,
        "segment_min_internal_breaks": int(seg_min_val),
        "segment_max_internal_breaks": int(seg_max_val),
        "sample_overrides": {str(k): dict(v) for k, v in overrides_map.items() if isinstance(v, dict)},
    }


def export_kneepoint_report_zip_from_preview(
    preview: dict[str, Any],
    *,
    output_dir: Path,
    file_prefix: str = "kneepoint_report",
    show_breakpoints: bool | None = None,
    show_grid: bool | None = None,
    nm_axis_label: Any = None,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def _report_progress(pct: int, msg: str) -> None:
        if progress_callback is None:
            return
        try:
            p = int(max(0, min(100, int(pct))))
            progress_callback(p, str(msg))
        except Exception:
            pass

    def _is_cancelled() -> bool:
        if cancel_requested is None:
            return False
        try:
            return bool(cancel_requested())
        except Exception:
            return False

    def _raise_if_cancelled() -> None:
        if _is_cancelled():
            raise RuntimeError("Report generation cancelled by user.")

    if not isinstance(preview, dict):
        raise ValueError("Invalid preview payload.")
    summary_df = preview.get("summary_df")
    if not isinstance(summary_df, pd.DataFrame):
        raise ValueError("Preview is missing summary_df.")
    sample_items_raw = preview.get("sample_items")
    if not isinstance(sample_items_raw, list):
        raise ValueError("Preview is missing sample_items.")
    parameters_df = preview.get("parameters_df")
    if not isinstance(parameters_df, pd.DataFrame):
        parameters_df = kp_parameters_df_from_sample_items(sample_items_raw)
    sample_items: list[dict[str, Any]] = [it for it in sample_items_raw if isinstance(it, dict)]
    if len(sample_items) == 0:
        raise ValueError("Preview has no valid sample items to export.")
    skipped_raw = preview.get("skipped", [])
    skipped: list[str] = [str(x) for x in skipped_raw] if isinstance(skipped_raw, (list, tuple, set)) else []

    effective_show_grid = bool(preview.get("show_grid", True) if show_grid is None else show_grid)
    effective_show_breakpoints = bool(preview.get("show_breakpoints", True) if show_breakpoints is None else show_breakpoints)
    nm_axis_label_txt = _coerce_nm_axis_label(nm_axis_label or preview.get("nm_axis_label"))

    _report_progress(5, "Checking export engine (Kaleido)...")
    _raise_if_cancelled()
    kaleido_ok, kaleido_msg = kp_kaleido_available()
    if not kaleido_ok:
        raise RuntimeError(
            "Report export to SVG/PDF requires Kaleido. Install with: python -m pip install --upgrade kaleido. "
            f"Detail: {kaleido_msg}"
        )

    _report_progress(18, "Building report figures...")
    _raise_if_cancelled()
    grid_fig = kp_build_grid_figure(
        sample_items,
        ncols=2,
        show_breakpoints=effective_show_breakpoints,
        show_grid=effective_show_grid,
        nm_axis_label=nm_axis_label_txt,
    )
    report_fig = kp_build_full_report_figure(
        summary_df,
        sample_items,
        ncols=2,
        show_breakpoints=effective_show_breakpoints,
        show_grid=effective_show_grid,
        nm_axis_label=nm_axis_label_txt,
    )
    _report_progress(40, "Writing ZIP package...")
    _raise_if_cancelled()

    export_errors: list[str] = []

    def _write_fig_pair(zf: zipfile.ZipFile, stem_path: str, fig: go.Figure, width: int, height: int) -> None:
        for fmt in ("svg", "pdf"):
            try:
                img = kp_figure_bytes(fig, fmt=fmt, width=width, height=height)
                zf.writestr(f"{stem_path}.{fmt}", img)
            except Exception as exc:
                export_errors.append(f"{stem_path}.{fmt}: {exc}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = sanitize_file_stem(file_prefix or "kneepoint_report")
    out_name = f"{prefix}_{stamp}.zip"
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / out_name

    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _raise_if_cancelled()
        zf.writestr("kneepoint_summary.csv", summary_df.to_csv(index=False))
        zf.writestr("kneepoint_parameters.csv", parameters_df.to_csv(index=False))
        n_zip_steps = max(len(sample_items), 1)
        for i, it in enumerate(sample_items, start=1):
            _raise_if_cancelled()
            sample_stem = sanitize_file_stem(it["sample"])
            fig_single = it.get("figure")
            if not isinstance(fig_single, go.Figure):
                fig_single, _ = kp_build_single_sample_figure(
                    str(it.get("sample", "")),
                    it["points"],
                    it["result"],
                    point_size=int(preview.get("point_size", 6)),
                    line_width=float(preview.get("line_width", 2.0)),
                    show_breakpoints=effective_show_breakpoints,
                    show_grid=effective_show_grid,
                    nm_axis_label=nm_axis_label_txt,
                )
            fig_h = int(fig_single.layout.height or 520)
            _write_fig_pair(zf, f"plots_by_sample/{sample_stem}", fig_single, width=1800, height=fig_h)
            _report_progress(42 + int(i * 33 / n_zip_steps), f"Packaging sample plot {i}/{n_zip_steps}: {it['sample']}")
        _raise_if_cancelled()
        grid_h = int(grid_fig.layout.height or 1400)
        _write_fig_pair(zf, "all_samples_grid", grid_fig, width=1850, height=grid_h)
        _report_progress(82, "Packaging all-samples grid...")
        _raise_if_cancelled()
        report_h = int(report_fig.layout.height or 1800)
        _write_fig_pair(zf, "kneepoint_report", report_fig, width=1950, height=report_h)
        _report_progress(94, "Packaging full report figure...")

    status = f"Report ready: processed={len(sample_items)} | skipped={len(skipped)}"
    tmin = preview.get("temp_min")
    tmax = preview.get("temp_max")
    if (tmin is not None) or (tmax is not None):
        status += f" | T_range=[{tmin if tmin is not None else '-inf'}, {tmax if tmax is not None else '+inf'}]"
    if len(export_errors) > 0:
        status += f" | export_warnings={len(export_errors)}"
    status += f" | saved={out_path}"
    _report_progress(100, "Report created.")

    return {
        "output_path": str(out_path),
        "status": status,
        "summary_df": summary_df,
        "processed_samples": [it["sample"] for it in sample_items],
        "skipped": skipped,
        "export_errors": export_errors,
    }


def create_kneepoint_report_zip(
    curves_df: pd.DataFrame,
    *,
    report_samples: Sequence[Any],
    size: Any,
    dilutions: Sequence[Any],
    temp_min: Any,
    temp_max: Any,
    spar: Any,
    nbreaks: Any,
    flat_q: Any,
    rise_q: Any,
    point_size: Any,
    line_width: Any,
    show_breakpoints: bool,
    show_grid: bool,
    nm_axis_label: Any,
    segment_selection_mode: str = "legacy",
    segment_min_internal_breaks: int = 2,
    segment_max_internal_breaks: int = 14,
    sample_overrides: dict[str, dict[str, Any]] | None = None,
    output_dir: Path,
    file_prefix: str = "kneepoint_report",
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def _report_progress(pct: int, msg: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(int(max(0, min(100, int(pct)))), str(msg))
        except Exception:
            pass

    def _map_progress(lo: int, hi: int) -> Callable[[int, str], None]:
        span = max(int(hi) - int(lo), 1)

        def _inner(pct: int, msg: str) -> None:
            p = int(max(0, min(100, int(pct))))
            mapped = int(lo + (span * p / 100.0))
            _report_progress(mapped, msg)

        return _inner

    preview = build_kneepoint_report_preview(
        curves_df=curves_df,
        report_samples=report_samples,
        size=size,
        dilutions=dilutions,
        temp_min=temp_min,
        temp_max=temp_max,
        spar=spar,
        nbreaks=nbreaks,
        flat_q=flat_q,
        rise_q=rise_q,
        point_size=point_size,
        line_width=line_width,
        show_breakpoints=show_breakpoints,
        show_grid=show_grid,
        nm_axis_label=nm_axis_label,
        segment_selection_mode=segment_selection_mode,
        segment_min_internal_breaks=segment_min_internal_breaks,
        segment_max_internal_breaks=segment_max_internal_breaks,
        sample_overrides=sample_overrides,
        progress_callback=_map_progress(1, 68),
        cancel_requested=cancel_requested,
    )
    out = export_kneepoint_report_zip_from_preview(
        preview,
        output_dir=output_dir,
        file_prefix=file_prefix,
        show_breakpoints=show_breakpoints,
        show_grid=show_grid,
        nm_axis_label=nm_axis_label,
        progress_callback=_map_progress(68, 100),
        cancel_requested=cancel_requested,
    )
    _report_progress(100, "Report created.")
    return out
