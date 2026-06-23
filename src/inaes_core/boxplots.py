from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _fmt_bin_edge(v: float) -> str:
    if not np.isfinite(v):
        return "nan"
    return f"{float(v):.4g}"


def _range_label_sort_key(label: str) -> tuple[float, float, str]:
    s = str(label or "").strip().lower()
    if "to" in s:
        parts = s.split("to")
        try:
            a = float(parts[0].strip())
            b = float(parts[1].strip())
            return (a, b, s)
        except Exception:
            pass
    try:
        v = float(s)
        return (v, v, s)
    except Exception:
        return (np.inf, np.inf, s)


def _build_numeric_group_ranges(
    series: pd.Series,
    *,
    mode: str,
    bin_count: int | None,
    bin_width: float | None,
) -> tuple[pd.Series, str]:
    out = pd.Series(np.nan, index=series.index, dtype="object")
    sv = pd.to_numeric(series, errors="coerce")
    mask = np.isfinite(sv.to_numpy(dtype=float))
    if int(mask.sum()) < 2:
        return out, "numeric ranges requested, but group values are not numeric."

    lo = float(np.nanmin(sv[mask]))
    hi = float(np.nanmax(sv[mask]))
    if not np.isfinite(lo) or not np.isfinite(hi):
        return out, "numeric ranges requested, but finite bounds are missing."

    mode_s = str(mode or "count")
    if mode_s == "width":
        w = float(bin_width) if bin_width is not None else 1.0
        if (not np.isfinite(w)) or (w <= 0):
            w = 1.0
        start = np.floor(lo / w) * w
        end = np.ceil(hi / w) * w
        if np.isclose(start, end, atol=1e-12):
            end = start + w
        edges = np.arange(start, end + (0.5 * w), w, dtype=float)
        note = f"numeric ranges by width={_fmt_bin_edge(float(w))}"
    else:
        n_bins = int(bin_count or 6)
        n_bins = max(2, min(n_bins, 200))
        edges = np.linspace(lo, hi, n_bins + 1, dtype=float)
        note = f"numeric ranges by count={n_bins}"

    edges = np.unique(edges)
    if len(edges) < 2:
        out.loc[mask] = f"[{_fmt_bin_edge(lo)}]"
        return out, "numeric ranges fallback: single interval."

    cats = pd.cut(sv, bins=edges, include_lowest=True, right=True, duplicates="drop")
    if getattr(cats, "cat", None) is None or len(cats.cat.categories) == 0:
        out.loc[mask] = f"[{_fmt_bin_edge(lo)}]"
        return out, "numeric ranges fallback: single interval."

    intervals = list(cats.cat.categories)
    labels = [f"{_fmt_bin_edge(float(iv.left))} to {_fmt_bin_edge(float(iv.right))}" for iv in intervals]
    cats_labeled = cats.cat.rename_categories(labels)
    out.loc[mask] = cats_labeled.astype(str).to_numpy()
    return out, note


@dataclass
class BoxplotConfig:
    y_metric: str = "nM10"
    size_choice: str = "b_5_m"
    group_col: str = "Location"
    use_numeric_ranges: bool = False
    bin_mode: str = "count"
    bin_count: int = 6
    bin_width: float = 1.0
    scale: str = "log10"
    show_points: bool = True


def available_group_columns(metadata_with_nm: pd.DataFrame) -> list[str]:
    df = metadata_with_nm.copy()
    df.columns = [str(c) for c in df.columns]
    preferred = ["Location", "Batch", "Site", "Region", "Sample"]
    nm_like = {"nM10_b5", "nM15_b5", "nM10_b02", "nM15_b02"}
    cols = [c for c in preferred if c in df.columns]
    others = [c for c in df.columns if c not in nm_like]
    out: list[str] = []
    for c in cols + others:
        if c not in out:
            out.append(c)
    return out


def prepare_boxplot_points(metadata_with_nm: pd.DataFrame, cfg: BoxplotConfig) -> tuple[pd.DataFrame, str, str, str, list[str]]:
    d = metadata_with_nm.copy()
    d.columns = [str(c) for c in d.columns]

    y_choice = str(cfg.y_metric or "nM10")
    if y_choice not in ["nM10", "nM15"]:
        raise ValueError("Invalid y metric. Use nM10 or nM15.")
    size_choice = str(cfg.size_choice or "b_5_m")
    if size_choice == "b_5_m":
        ycol = "nM10_b5" if y_choice == "nM10" else "nM15_b5"
    elif size_choice == "b_02_m":
        ycol = "nM10_b02" if y_choice == "nM10" else "nM15_b02"
    else:
        raise ValueError("Invalid size choice. Use b_5_m or b_02_m.")
    if ycol not in d.columns:
        raise ValueError(f"Column not found for selected response: {ycol}")

    group_col = str(cfg.group_col or "")
    if not group_col or group_col not in d.columns:
        raise ValueError(f"Grouping column not found: {group_col}")

    d[ycol] = pd.to_numeric(d[ycol], errors="coerce")
    d = d.dropna(subset=[ycol, group_col]).copy()

    group_plot_col = group_col
    group_note = "group mode: categorical"
    if bool(cfg.use_numeric_ranges):
        grp_num = pd.to_numeric(d[group_col], errors="coerce")
        finite_grp = int(np.isfinite(grp_num.to_numpy(dtype=float)).sum())
        if finite_grp >= 2:
            labels, note = _build_numeric_group_ranges(
                d[group_col],
                mode=str(cfg.bin_mode or "count"),
                bin_count=int(cfg.bin_count or 6),
                bin_width=float(cfg.bin_width or 1.0),
            )
            d["_box_group_range"] = labels
            d = d.dropna(subset=["_box_group_range"]).copy()
            group_plot_col = "_box_group_range"
            group_note = note
        else:
            group_note = "numeric ranges requested but Group by values are not numeric; fallback to categorical."

    if str(cfg.scale or "log10") == "log10":
        d = d[d[ycol] > 0].copy()
    if len(d) == 0:
        raise ValueError("No data after filtering (missing nM values?).")

    if group_plot_col == "_box_group_range":
        x_levels = sorted(d[group_plot_col].astype(str).dropna().unique().tolist(), key=_range_label_sort_key)
    else:
        meds = d.groupby(group_plot_col)[ycol].median().sort_values(ascending=False)
        x_levels = meds.index.astype(str).tolist()

    status = (
        f"rows={len(d)} | y={ycol} | group={group_col} | "
        f"scale={cfg.scale} | {group_note}"
    )
    return d, status, ycol, group_plot_col, x_levels

