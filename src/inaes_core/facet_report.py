"""Per-sample faceted reports (Frozen Fraction, Freezing Curves) and the colour
rules they share with the Frozen Fraction tab.

Colour modes:
- "dilution":      one distinct palette colour per dilution factor.
- "sample_shades": one palette hue per sample; dilutions are shades of that hue,
                   darkest = most concentrated (lowest dilution factor).
- "sample":        one solid palette colour per sample.
- "single_shades": the first palette colour for every sample, shades per dilution.
- "single":        the first palette colour for every point.
- "auto":          "dilution" for a single sample, "sample_shades" otherwise.
"""

from __future__ import annotations

import colorsys
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from inaes_core.kneepoint_report import kp_figure_bytes, sanitize_file_stem

COLOR_MODE_OPTIONS: list[tuple[str, str]] = [
    ("Auto (1 sample: per dilution; more: sample hue + shades)", "auto"),
    ("Distinct color per dilution", "dilution"),
    ("Sample hue, shades per dilution", "sample_shades"),
    ("Solid color per sample", "sample"),
    ("One color for all samples, shades per dilution", "single_shades"),
    ("One solid color for all samples", "single"),
]
_COLOR_MODES = {v for _, v in COLOR_MODE_OPTIONS}

REPORT_FORMATS: tuple[str, ...] = ("png", "svg", "pdf")

PANEL_WIDTH_PX = 360
PANEL_HEIGHT_PX = 280
_SHADE_KEY_BASE = "#6b7280"


def dilution_sort_key(x: Any) -> tuple[int, float, str]:
    s = str(x)
    try:
        return (0, float(s), s)
    except ValueError:
        return (1, 0.0, s)


def resolve_color_mode(mode: Any, n_samples: int) -> str:
    m = str(mode or "auto").strip().lower()
    if m not in _COLOR_MODES:
        m = "auto"
    if m == "auto":
        return "dilution" if int(n_samples) <= 1 else "sample_shades"
    return m


def _color_to_rgb(color: Any) -> tuple[int, int, int]:
    s = str(color or "").strip()
    m = re.match(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", s, flags=re.I)
    if m:
        return tuple(max(0, min(255, int(round(float(v))))) for v in m.groups())  # type: ignore[return-value]
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 91, 143, 249


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        *(max(0, min(255, int(round(v * 255.0)))) for v in (r, g, b))
    )


def dilution_shades(base_color: Any, n: int) -> list[str]:
    """n shades of one hue, from darkest (index 0) to lightest."""
    r, g, b = (v / 255.0 for v in _color_to_rgb(base_color))
    if int(n) <= 1:
        return [_rgb_to_hex(r, g, b)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l_dark = max(0.15, min(l, 0.5) - 0.18)
    l_light = min(0.88, max(l, 0.5) + 0.30)
    out: list[str] = []
    for i in range(int(n)):
        li = l_dark + (l_light - l_dark) * i / (int(n) - 1)
        out.append(_rgb_to_hex(*colorsys.hls_to_rgb(h, li, s)))
    return out


def point_color_map(
    points: pd.DataFrame,
    *,
    mode: Any,
    palette: Sequence[str],
    sample_col: str = "Sample",
    dil_col: str = "Dilution.plot",
) -> tuple[str, dict[tuple[str, str], str], list[str], list[str]]:
    """Colour per (sample, dilution) pair.

    Returns (effective_mode, color_map, samples, dilutions). Shades are indexed on
    the dilutions present in the whole selection, so one dilution has the same
    lightness in every sample.
    """
    pal = [str(c) for c in (palette or [])] or ["#5b8ff9"]
    samples = sorted(points[sample_col].astype(str).unique().tolist())
    dilutions = sorted(points[dil_col].astype(str).unique().tolist(), key=dilution_sort_key)
    eff = resolve_color_mode(mode, len(samples))
    cmap: dict[tuple[str, str], str] = {}
    single_shades = dilution_shades(pal[0], len(dilutions)) if eff == "single_shades" else []
    for i_s, smp in enumerate(samples):
        shades = dilution_shades(pal[i_s % len(pal)], len(dilutions)) if eff == "sample_shades" else []
        for i_d, dil in enumerate(dilutions):
            if eff == "dilution":
                cmap[(smp, dil)] = pal[i_d % len(pal)]
            elif eff == "sample":
                cmap[(smp, dil)] = pal[i_s % len(pal)]
            elif eff == "single_shades":
                cmap[(smp, dil)] = single_shades[i_d]
            elif eff == "single":
                cmap[(smp, dil)] = pal[0]
            else:
                cmap[(smp, dil)] = shades[i_d]
    return eff, cmap, samples, dilutions


def assign_report_panels(points: pd.DataFrame, *, sample_col: str = "Sample", size_col: str = "Size") -> pd.DataFrame:
    """One panel per sample; 'sample | size' when the selection holds several sizes."""
    d = points.copy()
    multi_size = size_col in d.columns and d[size_col].astype(str).nunique() > 1
    if multi_size:
        d["Panel"] = d[sample_col].astype(str) + " | " + d[size_col].astype(str)
    else:
        d["Panel"] = d[sample_col].astype(str)
    return d


def auto_report_columns(n_panels: int) -> int:
    return int(max(1, min(6, int(np.ceil(np.sqrt(max(int(n_panels), 1)))))))


def _padded_range(lo: float, hi: float, frac: float = 0.03) -> list[float]:
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return [0.0, 1.0]
    if hi <= lo:
        pad = max(abs(lo) * 0.05, 1e-6)
        return [lo - pad, hi + pad]
    pad = (hi - lo) * frac
    return [lo - pad, hi + pad]


def build_facet_grid_figure(
    points: pd.DataFrame,
    *,
    y_col: str,
    y_log: bool,
    y_title: str,
    color_mode: Any,
    palette: Sequence[str],
    ncols: int = 0,
    shared_axes: bool = True,
    point_size: float = 5.0,
    border_width: float = 0.3,
    sample_symbol: str = "circle",
    control_symbol: str = "square-open",
    show_grid: bool = True,
    title: str | None = None,
    x_col: str = "Freezing.temperature",
    x_title: str = "Temperature (°C)",
    y_fixed_range: Sequence[float] | None = None,
) -> go.Figure:
    """Grid of per-panel scatter plots (column 'Panel'), facet_wrap style.

    Expects columns Panel, Sample, Size, Dilution.plot, Control_norm, x_col, y_col.
    Uses SVG scatter traces so svg/pdf exports stay vector.
    """
    d = points.copy()
    d[x_col] = pd.to_numeric(d[x_col], errors="coerce")
    d[y_col] = pd.to_numeric(d[y_col], errors="coerce")
    d = d[np.isfinite(d[x_col]) & np.isfinite(d[y_col])].copy()
    if y_log:
        d = d[d[y_col] > 0].copy()
    if len(d) == 0:
        raise ValueError("No points to plot for the selected samples.")

    panels = sorted(d["Panel"].astype(str).unique().tolist())
    n = len(panels)
    nc = int(ncols) if int(ncols or 0) > 0 else auto_report_columns(n)
    nc = max(1, min(nc, n))
    nr = int(np.ceil(n / nc))

    title_txt = str(title or "").strip()
    title_px = 46 if title_txt else 6
    legend_px = 34
    top_px = title_px + legend_px + 30
    bottom_px = 70
    left_px = 86
    right_px = 24
    width = left_px + right_px + nc * PANEL_WIDTH_PX
    height = top_px + bottom_px + nr * PANEL_HEIGHT_PX
    grid_w = width - left_px - right_px
    grid_h = height - top_px - bottom_px
    h_spacing = min(0.2, 52.0 / grid_w) if nc > 1 else 0.0
    v_spacing = min(0.2, 64.0 / grid_h) if nr > 1 else 0.0

    fig = make_subplots(
        rows=nr,
        cols=nc,
        subplot_titles=panels,
        horizontal_spacing=h_spacing,
        vertical_spacing=v_spacing,
    )

    eff, cmap, _samples, dilutions = point_color_map(d, mode=color_mode, palette=palette)
    has_control = bool((d["Control_norm"].astype(str) == "Yes").any())
    legend_done: set[str] = set()

    for idx, panel in enumerate(panels):
        r = idx // nc + 1
        c = idx % nc + 1
        pdf = d[d["Panel"].astype(str) == panel]
        for (smp, dil, ctrl), g in pdf.groupby(
            [pdf["Sample"].astype(str), pdf["Dilution.plot"].astype(str), pdf["Control_norm"].astype(str)],
            sort=False,
        ):
            color = cmap.get((smp, dil), "#5b8ff9")
            group = f"dil:{dil}" if eff in {"dilution", "sample_shades", "single_shades"} else f"smp:{smp}"
            show_legend = eff in {"dilution", "single_shades"} and group not in legend_done
            if show_legend:
                legend_done.add(group)
            fig.add_trace(
                go.Scatter(
                    x=g[x_col],
                    y=g[y_col],
                    mode="markers",
                    name=str(dil),
                    legendgroup=group,
                    legendrank=1 + dilutions.index(dil) if dil in dilutions else 1000,
                    showlegend=show_legend,
                    marker=dict(
                        color=color,
                        symbol=control_symbol if ctrl == "Yes" else sample_symbol,
                        size=float(point_size),
                        opacity=0.9,
                        line=dict(color="#111827", width=float(border_width)),
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
                        "Control=%{customdata[3]}<br>y=%{y:.4g}<br>T=%{x:.2f}°C<extra></extra>"
                    ),
                ),
                row=r,
                col=c,
            )

    # Legend-only keys: grey shade ramp for sample_shades (clicking toggles that
    # dilution in every panel) and the control symbol.
    if eff == "sample_shades":
        for dil, shade in zip(dilutions, dilution_shades(_SHADE_KEY_BASE, len(dilutions))):
            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    name=str(dil),
                    legendgroup=f"dil:{dil}",
                    showlegend=True,
                    marker=dict(color=shade, symbol=sample_symbol, size=float(point_size) + 2,
                                line=dict(color="#111827", width=float(border_width))),
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )
    if has_control:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                name="control",
                showlegend=True,
                marker=dict(color="#9ca3af", symbol=control_symbol, size=float(point_size) + 2,
                            line=dict(color="#111827", width=max(float(border_width), 1.0))),
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    fig.update_xaxes(showgrid=bool(show_grid), ticks="outside", showline=True, mirror=True, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=bool(show_grid), ticks="outside", showline=True, mirror=True, tickfont=dict(size=10))
    if y_log:
        fig.update_yaxes(type="log", exponentformat="power", showexponent="all")
    if bool(shared_axes):
        fig.update_xaxes(range=_padded_range(float(d[x_col].min()), float(d[x_col].max())))
        if y_fixed_range is not None:
            fig.update_yaxes(range=[float(v) for v in y_fixed_range])
        elif y_log:
            lo = float(np.log10(d[y_col].min()))
            hi = float(np.log10(d[y_col].max()))
            fig.update_yaxes(range=_padded_range(lo, hi, frac=0.05))
        else:
            fig.update_yaxes(range=_padded_range(float(d[y_col].min()), float(d[y_col].max())))
    elif y_fixed_range is not None:
        fig.update_yaxes(range=[float(v) for v in y_fixed_range])

    legend_title = {
        "dilution": "Dilution.factor",
        "sample_shades": "Dilution.factor (shade)",
        "single_shades": "Dilution.factor",
        "sample": "",
        "single": "",
    }.get(eff, "")
    layout: dict[str, Any] = dict(
        width=width,
        height=height,
        margin=dict(l=left_px, r=right_px, t=top_px, b=bottom_px),
        showlegend=bool(legend_done) or eff == "sample_shades" or has_control,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, yref="container", xanchor="left", x=0.0,
                    title=dict(text=legend_title)),
    )
    if title_txt:
        layout["title"] = {"text": title_txt, "x": 0.01, "y": 0.995, "yanchor": "top", "yref": "container"}
    fig.update_layout(**layout)
    # Legend sits in the top margin, just under the title.
    fig.update_layout(legend=dict(y=1.0 - title_px / float(height), yref="container", yanchor="top"))
    fig.add_annotation(text=x_title, xref="paper", yref="paper", x=0.5, y=0.0, yshift=-46,
                       xanchor="center", yanchor="top", showarrow=False, font=dict(size=13))
    fig.add_annotation(text=y_title, xref="paper", yref="paper", x=0.0, y=0.5, xshift=-62,
                       xanchor="center", yanchor="middle", textangle=-90, showarrow=False, font=dict(size=13))
    return fig


def export_facet_report(
    fig: go.Figure,
    *,
    output_dir: Path,
    file_prefix: str,
    formats: Sequence[str],
    scale: float = 2.0,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Write the report figure once per requested format (png/svg/pdf)."""
    progress = progress_callback or (lambda _pct, _msg: None)
    is_cancelled = cancel_requested or (lambda: False)
    fmts = [str(f).strip().lower() for f in formats if str(f).strip().lower() in REPORT_FORMATS]
    if not fmts:
        raise ValueError("Select at least one format (png, svg, pdf).")
    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_file_stem(file_prefix or "report") or "report"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    width = int(fig.layout.width or 1600)
    height = int(fig.layout.height or 1200)
    saved: list[str] = []
    for i, fmt in enumerate(fmts):
        if bool(is_cancelled()):
            raise RuntimeError("Report export cancelled by user.")
        progress(int(5 + 90 * i / len(fmts)), f"Rendering {fmt.upper()}...")
        data = kp_figure_bytes(fig, fmt=fmt, width=width, height=height, scale=float(scale))
        path = out_dir / f"{stem}_{stamp}.{fmt}"
        path.write_bytes(data)
        saved.append(str(path))
    progress(100, "Done.")
    return {"saved": saved}
