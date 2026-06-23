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
class FrozenFractionFilter:
    selected_samples: Sequence[str]
    selected_sizes: Sequence[str]
    selected_dilutions: Sequence[str]
    show_control: bool = True


def available_ff_options(curves_df: pd.DataFrame) -> dict[str, list[str]]:
    d = curves_df.copy()
    samples = sorted(d["Sample"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist()) if "Sample" in d.columns else []
    sizes = sorted(d["Size"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist()) if "Size" in d.columns else []
    dils: list[str] = []
    if "Dilution.factor" in d.columns:
        dils = sorted(
            [str(x) for x in pd.unique(d["Dilution.factor"].map(canonical_dilution_token)) if str(x)],
            key=lambda x: (0, float(x), x) if x.replace(".", "", 1).isdigit() else (1, x),
        )
    return {"samples": samples, "sizes": sizes, "dilutions": dils}


def prepare_frozen_fraction_points(curves_df: pd.DataFrame, flt: FrozenFractionFilter) -> tuple[pd.DataFrame, str]:
    need = ["Sample", "Size", "Freezing.temperature", "FF", "Control", "Dilution.factor"]
    missing = [c for c in need if c not in curves_df.columns]
    if missing:
        raise ValueError(f"Frozen Fraction: missing columns {missing}. Available: {list(curves_df.columns)}")

    d = curves_df.copy()
    d["Freezing.temperature"] = pd.to_numeric(d["Freezing.temperature"], errors="coerce")
    d["FF"] = pd.to_numeric(d["FF"], errors="coerce")
    d = d.dropna(subset=["Sample", "Size", "Freezing.temperature", "FF"]).copy()
    d = d[np.isfinite(d["Freezing.temperature"]) & np.isfinite(d["FF"])].copy()
    d["Control_norm"] = d["Control"].map(_coerce_control_text).where(lambda s: s.isin(["Yes", "No"]), "No").astype(str)
    d["Dilution.plot"] = d["Dilution.factor"].map(canonical_dilution_token)
    d = d[d["Dilution.plot"] != ""].copy()

    selected_samples = [str(s).strip() for s in (flt.selected_samples or []) if str(s).strip()]
    if not selected_samples:
        raise ValueError("Select at least one sample.")
    d = d[d["Sample"].astype(str).str.strip().isin(selected_samples)].copy()

    selected_sizes = [str(s).strip() for s in (flt.selected_sizes or []) if str(s).strip()]
    if selected_sizes:
        d = d[d["Size"].astype(str).isin(selected_sizes)].copy()

    selected_dils = [canonical_dilution_token(x) for x in (flt.selected_dilutions or [])]
    selected_dils = [x for x in selected_dils if x]
    if selected_dils:
        d = d[d["Dilution.plot"].isin(selected_dils)].copy()

    if not flt.show_control:
        d = d[d["Control_norm"] != "Yes"].copy()

    status = (
        f"rows={len(d)} | samples={selected_samples} | sizes={selected_sizes or 'all'} | "
        f"dilutions={selected_dils or 'all'} | show_control={flt.show_control} | "
        f"control_yes={int((d['Control_norm']=='Yes').sum())}"
    )
    return d, status

