from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd


def _coerce_control_text(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    c = s.lower()
    yes_tokens = {"yes", "y", "true", "1", "control", "ctrl", "blank", "neg", "negative"}
    no_tokens = {"no", "n", "false", "0", "sample"}
    if c in yes_tokens:
        return "Yes"
    if c in no_tokens:
        return "No"
    if any(k in c for k in ["yes", "true"]):
        return "Yes"
    if any(k in c for k in ["no", "false"]):
        return "No"
    if any(k in c for k in ["control", "ctrl", "blank", "neg", "negative"]):
        return "Yes"
    return s


def canonical_dilution_token(v: Any) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ""
    s = str(v).strip()
    if not s:
        return ""
    s_num = s.replace(",", ".")
    try:
        num = float(s_num)
    except Exception:
        return s
    if not np.isfinite(num):
        return s
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.12g}"


@dataclass
class CompareSamplesFilter:
    selected_samples: Sequence[str]
    selected_sizes: Sequence[str]
    selected_dilutions: Sequence[str]
    max_samples: int = 30


def available_cmp_options(curves_df: pd.DataFrame) -> dict[str, list[str]]:
    d = curves_df.copy()
    samples = (
        sorted(d["Sample"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist())
        if "Sample" in d.columns
        else []
    )
    sizes = (
        sorted(d["Size"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist())
        if "Size" in d.columns
        else []
    )
    dils: list[Any] = []
    if "Dilution.factor" in d.columns:
        dcol = d["Dilution.factor"].dropna()
        try:
            dils = sorted(dcol.unique().tolist())
        except Exception:
            dils = sorted(dcol.astype(str).unique().tolist())
    return {"samples": samples, "sizes": sizes, "dilutions": dils}


def _trim_5_95_within_curve(g: pd.DataFrame) -> pd.DataFrame:
    g2 = g.sort_values("Freezing.temperature", ascending=False)
    n = len(g2)
    lo = int(np.ceil(n * 0.05))
    hi = int(np.floor(n * 0.95))
    if hi <= lo:
        return g2
    return g2.iloc[lo:hi]


def prepare_compare_samples_points(
    curves_df: pd.DataFrame,
    flt: CompareSamplesFilter,
) -> tuple[pd.DataFrame, str, str]:
    need = ["Sample", "Size", "Freezing.temperature", "nm", "Control", "Dilution.factor"]
    missing = [c for c in need if c not in curves_df.columns]
    if missing:
        raise ValueError(f"Compare Samples FC: missing columns {missing}. Available: {list(curves_df.columns)}")

    d = curves_df.copy()
    d["Freezing.temperature"] = pd.to_numeric(d["Freezing.temperature"], errors="coerce")
    d["nm"] = pd.to_numeric(d["nm"], errors="coerce")
    d = d.dropna(subset=["Sample", "Size", "Freezing.temperature", "nm"]).copy()
    d = d[np.isfinite(d["Freezing.temperature"]) & np.isfinite(d["nm"]) & (d["nm"] > 0)].copy()

    d["Control_norm"] = d["Control"].map(_coerce_control_text).where(lambda s: s.isin(["Yes", "No"]), "No").astype(str)
    d = d[d["Control_norm"] != "Yes"].copy()

    if "Location" in d.columns:
        d["Location"] = d["Location"].astype(str)
    else:
        d["Location"] = "(no Location)"

    d["Dilution.plot"] = d["Dilution.factor"].map(canonical_dilution_token)
    d = d[d["Dilution.plot"] != ""].copy()

    selected_samples = [str(s).strip() for s in (flt.selected_samples or []) if str(s).strip()]
    if len(selected_samples) == 0:
        raise ValueError("Select at least 1 Sample.")
    if len(selected_samples) > int(max(flt.max_samples, 1)):
        selected_samples = selected_samples[: int(max(flt.max_samples, 1))]

    d = d[d["Sample"].astype(str).isin(selected_samples)].copy()

    selected_sizes = [str(s).strip() for s in (flt.selected_sizes or []) if str(s).strip()]
    if len(selected_sizes) == 0:
        raise ValueError("Select at least 1 Size.")
    d = d[d["Size"].astype(str).isin(selected_sizes)].copy()

    selected_dils = list(flt.selected_dilutions or [])
    if len(selected_dils) == 0:
        raise ValueError("Select at least 1 Dilution.factor.")
    d = d[d["Dilution.factor"].isin(selected_dils)].copy()

    if len(d) == 0:
        raise ValueError("No points after filtering.")

    d["Curve_ID"] = (
        d["Sample"].astype(str)
        + "|"
        + d["Size"].astype(str)
        + "|"
        + d["Dilution.plot"].astype(str)
        + "|"
        + d["Location"].astype(str)
    )
    d = d.groupby("Curve_ID", group_keys=False).apply(_trim_5_95_within_curve)

    if "Curve_ID" not in d.columns:
        if isinstance(d.index, pd.MultiIndex) and ("Curve_ID" in d.index.names):
            d = d.reset_index(level=d.index.names.index("Curve_ID")).rename(columns={"level_0": "Curve_ID"})
        elif d.index.name == "Curve_ID":
            d = d.reset_index().rename(columns={"index": "Curve_ID"})
        else:
            d["Curve_ID"] = (
                d["Sample"].astype(str)
                + "|"
                + d["Size"].astype(str)
                + "|"
                + d["Dilution.plot"].astype(str)
                + "|"
                + d["Location"].astype(str)
            )

    if len(d) == 0:
        raise ValueError("No points after 5–95% trimming.")

    num_samples = int(d["Sample"].astype(str).nunique())
    num_sizes = int(d["Size"].astype(str).nunique())
    color_by = "Size" if (num_samples == 1 and num_sizes > 1) else "Sample"
    n_curves = int(d["Curve_ID"].astype(str).nunique()) if "Curve_ID" in d.columns else 0

    status = (
        f"rows={len(d)} | curves={n_curves} | samples={selected_samples} | "
        f"sizes={selected_sizes} | dilutions={selected_dils} | color_by={color_by}"
    )
    return d, status, color_by
