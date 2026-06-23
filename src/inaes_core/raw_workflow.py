from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from inaes_core.curves_mapping import (
    _build_sample_id_for_curves,
    _clean_mapping_value,
    _coerce_control_text,
    _coerce_numeric_series_relaxed,
    _detect_control_from_sample,
    _normalize_size_label,
    _parse_control_keywords,
    _parse_dilution_from_sample,
    _parse_size_from_sample,
    _repair_control_labels_with_sample_keywords,
    _split_sample_name_components,
    _strip_leading_control_keyword_from_sample,
)

RAW_VALI_METHOD_OPTIONS: list[tuple[str, str]] = [
    ("Mass-normalized nM (Micro-PINGUIN form: c_m + dilution)", "mass_concentration_nm"),
    ("Per liquid volume K(T)", "liquid_volume"),
    ("Standard RAW nM (Soil_mass / Water_volume)", "mass_extraction_nm"),
    ("Surface-site density n_s(T) (direct area/drop)", "surface_area_direct"),
    ("Surface-site density n_s,BET(T) (from nM + BET area)", "surface_area_bet_from_mass"),
    ("Per-cell cumulative metric", "cell_concentration"),
    ("Air-volume INP (filter wash-off)", "air_washoff"),
    ("Air-volume INP (filter drop-on)", "air_drop_on"),
    ("Custom dose-per-droplet X (generic nX = Λ/X)", "custom_dose"),
]

RAW_VALI_METHOD_HELP: dict[str, str] = {
    "mass_concentration_nm": "nM = -(1/V) ln(1-FF) * (d / c_m).",
    "liquid_volume": "K(T) = -(1/V) ln(1-FF).",
    "mass_extraction_nm": "nM = -ln(1-FF)/(30*10^-3) * (Water_volume/Soil_mass) * Dilution.factor.",
    "surface_area_direct": "n_s(T) = -ln(1-FF) / A_per_droplet.",
    "surface_area_bet_from_mass": "n_s,BET = nM / theta (BET area).",
    "cell_concentration": "n_cell(T) = -(1/V) ln(1-FF) * (d / c_cells).",
    "air_washoff": "N_INP = -ln(1-FF) * (Vwash / (Vdrop * x * Vs)).",
    "air_drop_on": "N_INP = -ln(1-FF) * (A_filter / (alpha * Vs)).",
    "custom_dose": "nX(T) = -ln(1-FF) / X.",
}

RAW_VALI_METHOD_AXIS_META: dict[str, dict[str, str]] = {
    "mass_concentration_nm": {"label": "nM", "units": "g^-1"},
    "liquid_volume": {"label": "K", "units": "mL^-1"},
    "mass_extraction_nm": {"label": "nM", "units": "g^-1"},
    "surface_area_direct": {"label": "n_s", "units": "m^-2"},
    "surface_area_bet_from_mass": {"label": "n_s,BET", "units": "m^-2"},
    "cell_concentration": {"label": "n_cell", "units": "cell^-1"},
    "air_washoff": {"label": "N_INP_air", "units": "L^-1"},
    "air_drop_on": {"label": "N_INP_air", "units": "L^-1"},
    "custom_dose": {"label": "nX", "units": "X^-1"},
}

MERGE_MAPPING_SKIP = "__skip__"
RAW_ANALYZED_MERGE_FIELDS = [
    "Sample.name",
    "Sample_ID",
    "nm",
    "FF",
    "Freezing.temperature",
    "Control",
    "Dilution.factor",
    "Location",
    "Size",
    "Sample",
]


def _clean_merge_mapping_value(v: Any) -> str | None:
    """Keep explicit merge sentinels (for example __skip__) intact."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _safe_float(x: Any, *, name: str = "value", positive: bool = False) -> float:
    try:
        v = float(x)
    except Exception as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not np.isfinite(v):
        raise ValueError(f"{name} must be finite.")
    if positive and v <= 0:
        raise ValueError(f"{name} must be > 0.")
    return v


def _safe_int(x: Any, *, name: str = "value", positive: bool = False) -> int:
    try:
        v = int(float(x))
    except Exception as exc:
        raise ValueError(f"{name} must be integer-like.") from exc
    if positive and v <= 0:
        raise ValueError(f"{name} must be > 0.")
    return v


@dataclass
class RawAnalyzeConfig:
    map_sample: Any = None
    map_temp: Any = None
    map_ff: Any = None
    map_size: Any = None
    map_location: Any = None
    map_control: Any = None
    map_dilution: Any = None
    auto_dilution_from_sample: bool = True
    use_size_grouping: bool = True
    size_single_label: Any = None
    manual_size_value: Any = None
    use_location_grouping: bool = True
    location_single_label: Any = None
    manual_location_value: Any = None
    auto_control_from_sample: bool = True
    control_detection_keywords: Any = None
    method: Any = "mass_extraction_nm"
    n0: Any = 384
    droplet_volume_ul: Any = 30
    mass_conc_g_per_ml: Any = None
    wash_volume_ml: Any = 400
    sample_mass_g: Any = 10
    extra_dilution_factor: Any = None
    cell_conc_per_ml: Any = None
    area_per_drop_m2: Any = None
    bet_area_m2_per_g: Any = None
    air_filter_fraction_x: Any = None
    air_sampled_volume_L: Any = None
    filter_exposed_area: Any = None
    droplet_footprint_area: Any = None
    custom_dose_per_drop: Any = None


def _raw_aliases() -> dict[str, list[str]]:
    return {
        "Sample": ["sample", "sample_id", "sample_name", "samplename", "sample.name", "content"],
        "Freezing.temperature": [
            "freezing.temperature",
            "freezing_temperature",
            "freezing temperature",
            "freeze_temp",
            "freeze temp",
            "frozen_temperature",
            "frozen temperature",
            "temperature",
            "temp",
            "t",
        ],
        "FF": ["ff", "frozen_fraction", "frozen fraction", "frozen.fraction", "frozenfraction", "fraction_frozen"],
        "Size": ["size", "particle_size", "particlesize", "fraction", "batch"],
        "Location": ["location", "site", "sampling_site", "sampling site"],
        "Control": ["control", "is_control", "negative_control", "blank", "type", "sample_type", "kind"],
        "Dilution.factor": ["dilution", "dilution_factor", "dilution.factor", "dilution factor"],
    }


def _canon_col(c: str) -> str:
    import re

    c = str(c).strip().lower()
    c = re.sub(r"[\s\-\/]+", "_", c)
    c = re.sub(r"[^a-z0-9_\.]+", "", c)
    return c


def suggest_raw_column_mapping(df: pd.DataFrame) -> dict[str, str]:
    cols = [str(c) for c in df.columns]
    canon_to_orig = {_canon_col(c): c for c in cols}
    out: dict[str, str] = {}
    used: set[str] = set()
    for expected, aliases in _raw_aliases().items():
        if expected in cols:
            out[expected] = expected
            used.add(expected)
            continue
        for alias in aliases:
            src = canon_to_orig.get(_canon_col(alias))
            if src and src not in used:
                out[expected] = src
                used.add(src)
                break
    return out


def _vali_lambda_from_ff(ff_values: pd.Series, n0: int) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, list[str]]:
    warnings: list[str] = []
    ff_num = pd.to_numeric(ff_values, errors="coerce")
    n0_i = max(int(n0), 1)
    upper = min(1.0 - 1e-12, (float(n0_i) - 0.5) / float(n0_i)) if n0_i > 1 else (1.0 - 1e-12)

    bad_non_numeric = ~np.isfinite(ff_num)
    if bad_non_numeric.any():
        warnings.append(f"{int(bad_non_numeric.sum())} FF values were non-numeric and became NaN.")

    below0 = (ff_num < 0) & np.isfinite(ff_num)
    above1 = (ff_num > 1) & np.isfinite(ff_num)
    if below0.any():
        warnings.append(f"{int(below0.sum())} FF values < 0 were clipped to 0.")
    if above1.any():
        warnings.append(f"{int(above1.sum())} FF values > 1 were clipped to {upper:.6f}.")

    ff_clipped = ff_num.clip(lower=0.0, upper=upper)
    ff_noff1 = ff_num.where(ff_num < 1.0)
    ff_ge1 = (ff_num >= 1.0) & np.isfinite(ff_num)
    if ff_ge1.any():
        warnings.append(
            f"{int(ff_ge1.sum())} rows have FF >= 1; output FF, nm and Freezing.temperature are set to NA for those rows."
        )

    lam = -np.log1p(-ff_clipped)
    lam_noff1 = -np.log1p(-ff_noff1)
    return ff_clipped, ff_noff1, lam, lam_noff1, warnings


def _extract_and_standardize_raw_df(df_raw: pd.DataFrame, cfg: RawAnalyzeConfig) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    df = df_raw.copy()
    mapping = {
        "Sample": _clean_mapping_value(cfg.map_sample),
        "Freezing.temperature": _clean_mapping_value(cfg.map_temp),
        "FF": _clean_mapping_value(cfg.map_ff),
        "Size": _clean_mapping_value(cfg.map_size),
        "Location": _clean_mapping_value(cfg.map_location),
        "Control": _clean_mapping_value(cfg.map_control),
        "Dilution.factor": _clean_mapping_value(cfg.map_dilution),
    }

    for field in ["Sample", "Freezing.temperature", "FF"]:
        src = mapping.get(field)
        if not src:
            raise ValueError(f"Select the source column for '{field}'.")
        if src not in df.columns:
            raise ValueError(f"Mapped source column not found for '{field}': {src}")

    inv: dict[str, list[str]] = {}
    for k, src in mapping.items():
        if src:
            inv.setdefault(src, []).append(k)
    dup_map = {src: tgts for src, tgts in inv.items() if len(tgts) > 1}
    if dup_map:
        raise ValueError(f"Raw mapping duplicates detected: {dup_map}")

    for expected, src in mapping.items():
        if src:
            df[expected] = df[src]

    df["Sample"] = df["Sample"].astype(str).str.strip()
    sample_name_series = df["Sample"].astype(str).copy()
    control_keywords = _parse_control_keywords(
        cfg.control_detection_keywords if cfg.control_detection_keywords is not None else "MilliQ,milli-q,blank,control,ctrl,mq"
    )
    control_detected = sample_name_series.map(lambda s: _detect_control_from_sample(s, control_keywords))
    control_detected_in_control_col = (
        df["Control"].astype(str).map(lambda s: _detect_control_from_sample(s, control_keywords))
        if "Control" in df.columns
        else pd.Series(False, index=df.index)
    )
    control_detected_any = control_detected | control_detected_in_control_col
    sample_components = sample_name_series.map(_split_sample_name_components)
    sample_group_series = sample_components.map(lambda x: x[0] if isinstance(x, tuple) else None)
    parsed_size_from_name = sample_components.map(lambda x: x[1] if isinstance(x, tuple) else None)
    parsed_dil_from_name = pd.to_numeric(
        sample_components.map(lambda x: x[2] if isinstance(x, tuple) else np.nan),
        errors="coerce",
    )

    sample_group_series = sample_group_series.where(
        sample_group_series.notna() & (sample_group_series.astype(str).str.strip() != ""),
        sample_name_series,
    ).astype(str)

    if control_keywords:
        sample_group_no_ctrl_prefix = sample_group_series.map(
            lambda s: _strip_leading_control_keyword_from_sample(s, control_keywords)
        )
        sample_group_series = sample_group_no_ctrl_prefix.astype(str)

    df["Sample.name"] = sample_name_series.astype(str)
    df["Sample"] = sample_group_series.astype(str)
    df["Freezing.temperature"] = _coerce_numeric_series_relaxed(df["Freezing.temperature"])
    df["FF"] = _coerce_numeric_series_relaxed(df["FF"])

    use_size_grouping_b = bool(cfg.use_size_grouping)
    use_location_grouping_b = bool(cfg.use_location_grouping)
    size_mapped = ("Size" in df.columns) and use_size_grouping_b
    parsed_size = parsed_size_from_name.copy()
    parsed_size = parsed_size.where(parsed_size.notna(), sample_name_series.map(_parse_size_from_sample))
    manual_size_raw = str(cfg.manual_size_value or "").strip()
    manual_size_norm = _normalize_size_label(manual_size_raw) if manual_size_raw else None
    if manual_size_raw and not manual_size_norm:
        manual_size_norm = manual_size_raw

    if not use_size_grouping_b:
        df["Size"] = "b_5_m"
    elif not size_mapped:
        if manual_size_norm:
            df["Size"] = manual_size_norm
        else:
            df["Size"] = parsed_size
    else:
        df["Size"] = df["Size"].map(_normalize_size_label)

    missing_size = (
        df["Size"].isna()
        | (df["Size"].astype(str).str.strip() == "")
        | (df["Size"].astype(str).str.lower() == "nan")
    )
    if use_size_grouping_b and missing_size.any():
        if manual_size_norm:
            df.loc[missing_size, "Size"] = manual_size_norm
        fill_with_parsed = parsed_size.where(parsed_size.notna())
        mask = missing_size & fill_with_parsed.notna()
        if mask.any():
            df.loc[mask, "Size"] = fill_with_parsed[mask]
    still_missing_size = (
        df["Size"].isna()
        | (df["Size"].astype(str).str.strip() == "")
        | (df["Size"].astype(str).str.lower() == "nan")
    )
    if use_size_grouping_b and still_missing_size.any():
        df.loc[still_missing_size, "Size"] = "b_5_m"

    manual_loc = str(cfg.manual_location_value or "").strip()
    if not use_location_grouping_b:
        loc_label = str(cfg.location_single_label or "").strip() or "(single_location)"
        df["Location"] = loc_label
    elif "Location" not in df.columns:
        if manual_loc:
            df["Location"] = manual_loc
        else:
            df["Location"] = "(unknown)"
    else:
        df["Location"] = df["Location"].astype(str).replace({"nan": "(unknown)"}).fillna("(unknown)")
        if manual_loc:
            loc_missing = (df["Location"].astype(str).str.strip() == "") | (df["Location"].astype(str) == "(unknown)")
            if loc_missing.any():
                df.loc[loc_missing, "Location"] = manual_loc

    if "Control" not in df.columns:
        if bool(cfg.auto_control_from_sample):
            df["Control"] = np.where(control_detected_any, "Yes", "No")
        else:
            df["Control"] = "No"
    else:
        ctrl = df["Control"].map(_coerce_control_text)
        if bool(cfg.auto_control_from_sample):
            fill_mask = ctrl.isna() & control_detected_any
            if fill_mask.any():
                ctrl = ctrl.where(~fill_mask, "Yes")
            ctrl_repaired, ctrl_notes = _repair_control_labels_with_sample_keywords(
                ctrl.fillna("No").astype(str),
                df["Sample.name"] if "Sample.name" in df.columns else df["Sample"],
                keywords=control_keywords,
            )
            ctrl_repaired = ctrl_repaired.astype(str)
            force_yes_from_control_col = control_detected_in_control_col & (ctrl_repaired != "Yes")
            if force_yes_from_control_col.any():
                ctrl_repaired.loc[force_yes_from_control_col] = "Yes"
            df["Control"] = ctrl_repaired
            warnings.extend(ctrl_notes)
        else:
            df["Control"] = ctrl.fillna("No").astype(str)

    if "Dilution.factor" in df.columns:
        dil_num = _coerce_numeric_series_relaxed(df["Dilution.factor"])
    else:
        dil_num = pd.Series(np.nan, index=df.index, dtype=float)

    parsed_dil = parsed_dil_from_name.astype(float)
    parsed_dil = parsed_dil.where(np.isfinite(parsed_dil), sample_name_series.map(_parse_dilution_from_sample).astype(float))
    if bool(cfg.auto_dilution_from_sample):
        dil_num = dil_num.where(np.isfinite(dil_num), parsed_dil)
    dil_num = dil_num.fillna(1.0)
    bad_dil = (~np.isfinite(dil_num)) | (dil_num <= 0)
    if bad_dil.any():
        warnings.append(f"{int(bad_dil.sum())} invalid dilution values reset to 1.")
        dil_num = dil_num.where(~bad_dil, 1.0)
    df["Dilution.factor"] = dil_num.astype(float)
    df["Dilution"] = df["Dilution.factor"]

    before = len(df)
    df = df.dropna(subset=["Sample", "Freezing.temperature", "FF"]).copy()
    df = df[df["Sample"].astype(str).str.len() > 0].copy()
    dropped = before - len(df)
    if dropped > 0:
        warnings.append(f"Dropped {dropped} rows with missing required values (Sample, Temp, FF).")
    if len(df) == 0:
        raise ValueError("No valid raw rows remain after cleaning.")

    keep_preferred = [
        "Sample.name",
        "Sample_ID",
        "Sample",
        "Size",
        "Location",
        "Control",
        "Dilution.factor",
        "Dilution",
        "Freezing.temperature",
        "FF",
    ]
    keep_cols = [c for c in keep_preferred if c in df.columns]
    if keep_cols:
        df = df[keep_cols].copy()
    return df, warnings


def _compute_raw_vali_normalized_df(df_std: pd.DataFrame, cfg: RawAnalyzeConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    m = str(cfg.method or "mass_extraction_nm")
    n0_i = _safe_int(cfg.n0, name="N0", positive=True)
    v_drop_ul = _safe_float(cfg.droplet_volume_ul, name="Droplet volume (µL)", positive=True)
    v_drop_ml = v_drop_ul / 1000.0

    df = df_std.copy()
    _, _, lam, _, ff_warnings = _vali_lambda_from_ff(df["FF"], n0_i)
    dil = pd.to_numeric(df["Dilution.factor"], errors="coerce").fillna(1.0)
    dil = dil.where(dil > 0, 1.0)
    df["Dilution.factor"] = dil
    df["Dilution"] = dil

    def _finalize(metric: pd.Series, *, label: str, units: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        n = len(df)
        out = pd.DataFrame(index=df.index)
        out["Sample.name"] = (
            df["Sample.name"].astype(str)
            if "Sample.name" in df.columns
            else df["Sample"].astype(str)
        )
        out["nm"] = pd.to_numeric(metric, errors="coerce")
        out["FF"] = pd.to_numeric(df["FF"], errors="coerce")
        out["Freezing.temperature"] = pd.to_numeric(df["Freezing.temperature"], errors="coerce")
        out["Control"] = df["Control"].astype(str) if "Control" in df.columns else pd.Series(["No"] * n, index=df.index, dtype=object)
        out["Dilution.factor"] = pd.to_numeric(df["Dilution.factor"], errors="coerce").fillna(1.0)
        out["Location"] = (
            df["Location"].astype(str)
            if "Location" in df.columns
            else pd.Series(["(unknown)"] * n, index=df.index, dtype=object)
        )
        out["Size"] = (
            df["Size"].astype(str)
            if "Size" in df.columns
            else pd.Series([np.nan] * n, index=df.index, dtype=object)
        )
        out["Sample"] = (
            df["Sample"].astype(str)
            if "Sample" in df.columns
            else out["Sample.name"].astype(str)
        )
        out["Sample_ID"] = _build_sample_id_for_curves(out)

        ff_eq1_mask = pd.to_numeric(out["FF"], errors="coerce") >= 1.0
        if bool(np.any(ff_eq1_mask)):
            out.loc[ff_eq1_mask, "FF"] = np.nan
            out.loc[ff_eq1_mask, "nm"] = np.nan
            out.loc[ff_eq1_mask, "Freezing.temperature"] = np.nan

        out = out[
            [
                "Sample.name",
                "Sample_ID",
                "nm",
                "FF",
                "Freezing.temperature",
                "Control",
                "Dilution.factor",
                "Location",
                "Size",
                "Sample",
            ]
        ].copy()
        out["normalization_method"] = m
        out["normalization_label"] = label
        out["normalization_units"] = units
        out["N0"] = n0_i
        out["Droplet.volume.uL"] = v_drop_ul

        meta = {
            "method": m,
            "label": label,
            "units": units,
            "N0": n0_i,
            "droplet_volume_ul": v_drop_ul,
            "warnings": ff_warnings,
            "method_note": RAW_VALI_METHOD_HELP.get(m, ""),
        }
        return out, meta

    if m == "mass_concentration_nm":
        c_m = _safe_float(cfg.mass_conc_g_per_ml, name="Mass concentration c_m (g/mL)", positive=True)
        x_dose = (c_m / dil) * v_drop_ml
        return _finalize(lam / x_dose, label="nM", units="g^-1")
    if m == "liquid_volume":
        x_dose = pd.Series(v_drop_ml, index=df.index, dtype=float)
        return _finalize(lam / x_dose, label="K", units="mL^-1")
    if m == "mass_extraction_nm":
        water_volume = _safe_float(cfg.wash_volume_ml, name="Water_volume (mL)", positive=True)
        soil_mass = _safe_float(cfg.sample_mass_g, name="Soil_mass (g)", positive=True)
        nm_scale = (water_volume / soil_mass) * dil / (30.0 * (10.0 ** -3.0))
        return _finalize(lam * nm_scale, label="nM", units="g^-1")
    if m == "surface_area_direct":
        a_drop = _safe_float(cfg.area_per_drop_m2, name="Area per droplet (m²/drop)", positive=True)
        return _finalize(lam / pd.Series(a_drop, index=df.index, dtype=float), label="n_s", units="m^-2")
    if m == "surface_area_bet_from_mass":
        c_m = _safe_float(cfg.mass_conc_g_per_ml, name="Mass concentration c_m (g/mL)", positive=True)
        theta = _safe_float(cfg.bet_area_m2_per_g, name="BET area theta (m²/g)", positive=True)
        x_mass = (c_m / dil) * v_drop_ml
        nm_mass = lam / x_mass
        return _finalize(nm_mass / theta, label="n_s,BET", units="m^-2")
    if m == "cell_concentration":
        c_cell = _safe_float(cfg.cell_conc_per_ml, name="Cell concentration (cells/mL)", positive=True)
        x_cells = (c_cell / dil) * v_drop_ml
        return _finalize(lam / x_cells, label="n_cell", units="cell^-1")
    if m == "air_washoff":
        v_wash_ml = _safe_float(cfg.wash_volume_ml, name="Wash volume (mL)", positive=True)
        x_frac = _safe_float(cfg.air_filter_fraction_x, name="Filter fraction x", positive=True)
        if x_frac > 1:
            raise ValueError("Filter fraction x must be <= 1.")
        v_air_l = _safe_float(cfg.air_sampled_volume_L, name="Sampled air volume (L)", positive=True)
        scale = v_wash_ml / (v_drop_ml * x_frac * v_air_l)
        return _finalize(lam * scale, label="N_INP_air", units="L^-1")
    if m == "air_drop_on":
        a_filter = _safe_float(cfg.filter_exposed_area, name="Exposed filter area", positive=True)
        a_alpha = _safe_float(cfg.droplet_footprint_area, name="Droplet footprint area", positive=True)
        v_air_l = _safe_float(cfg.air_sampled_volume_L, name="Sampled air volume (L)", positive=True)
        scale = (a_filter / a_alpha) / v_air_l
        return _finalize(lam * scale, label="N_INP_air", units="L^-1")
    if m == "custom_dose":
        x_custom = _safe_float(cfg.custom_dose_per_drop, name="Custom dose per droplet X", positive=True)
        return _finalize(lam / pd.Series(x_custom, index=df.index, dtype=float), label="nX", units="X^-1")
    raise ValueError(f"Unsupported normalization method: {m}")


def compute_analyzed_curves_from_raw(raw_df: pd.DataFrame, cfg: RawAnalyzeConfig) -> tuple[pd.DataFrame, str]:
    df_std, warnings = _extract_and_standardize_raw_df(raw_df, cfg)
    df_out, meta = _compute_raw_vali_normalized_df(df_std, cfg)
    all_warnings = warnings + list(meta.get("warnings") or [])
    warn_txt = ""
    if all_warnings:
        warn_txt = " | Warnings: " + " ; ".join(all_warnings[:6])
        if len(all_warnings) > 6:
            warn_txt += f" ; (+{len(all_warnings) - 6} more)"
    status = (
        f"Analyzed raw data with Vali | rows={len(df_out)} | method={meta.get('method')} -> {meta.get('label')} [{meta.get('units')}]"
        f" | N0={meta.get('N0')} | V={meta.get('droplet_volume_ul')} µL{warn_txt}"
    )
    return df_out, status


def _apply_raw_merge_column_mapping(
    prev_df: pd.DataFrame,
    new_df: pd.DataFrame,
    raw_to_prev_map: dict[str, Any] | None,
) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    prev_cols = [str(c) for c in prev_df.columns]
    out = new_df.copy()
    mapping = raw_to_prev_map or {}
    required_core_fields = {"Sample", "Freezing.temperature", "nm"}

    target_to_src: dict[str, str] = {}
    for src in RAW_ANALYZED_MERGE_FIELDS:
        if src not in out.columns:
            continue
        tgt = _clean_merge_mapping_value(mapping.get(src))
        if not tgt or tgt == MERGE_MAPPING_SKIP:
            continue
        if tgt not in prev_cols:
            raise ValueError(f"Merge mapping target '{tgt}' is not a column in previous analyzed file.")
        if src in required_core_fields and tgt != src:
            raise ValueError(f"Required column '{src}' cannot be mapped to '{tgt}'.")
        prev_src = target_to_src.get(tgt)
        if prev_src is not None and prev_src != src:
            raise ValueError(f"Merge mapping conflict: both '{prev_src}' and '{src}' map to '{tgt}'.")
        target_to_src[tgt] = src

    mapped_notes: list[str] = []
    dropped_unmapped_cols: list[str] = []
    explicitly_skipped_cols: list[str] = []

    for src in RAW_ANALYZED_MERGE_FIELDS:
        if src not in out.columns:
            continue
        tgt = _clean_merge_mapping_value(mapping.get(src))
        if tgt == MERGE_MAPPING_SKIP:
            if src in required_core_fields:
                raise ValueError(f"Required column '{src}' cannot be skipped.")
            out = out.drop(columns=[src])
            explicitly_skipped_cols.append(src)
            continue
        if not tgt:
            if src in prev_cols:
                continue
            out = out.drop(columns=[src])
            dropped_unmapped_cols.append(src)
            continue
        if tgt == src:
            continue
        if tgt in out.columns:
            out[tgt] = out[src].combine_first(out[tgt])
        else:
            out[tgt] = out[src]
        out = out.drop(columns=[src])
        mapped_notes.append(f"{src}->{tgt}")

    out = out.reindex(columns=prev_cols).copy()
    return out, mapped_notes, sorted(set(dropped_unmapped_cols)), sorted(set(explicitly_skipped_cols))


def merge_analyzed_curve_tables(
    prev_df: pd.DataFrame,
    new_df: pd.DataFrame,
    *,
    raw_to_prev_map: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, str]:
    prev = prev_df.copy()
    new = new_df.copy()
    prev.columns = [str(c) for c in prev.columns]
    new.columns = [str(c) for c in new.columns]

    for col in ["Sample", "Freezing.temperature", "nm"]:
        if col not in prev.columns:
            raise ValueError(f"Previous analyzed file missing required column: {col}")
        if col not in new.columns:
            raise ValueError(f"New analyzed raw file missing required column: {col}")

    new, mapped_notes, dropped_unmapped_cols, explicitly_skipped_cols = _apply_raw_merge_column_mapping(
        prev,
        new,
        raw_to_prev_map,
    )

    union_cols = list(dict.fromkeys(list(prev.columns) + list(new.columns)))
    merged = pd.concat([prev.reindex(columns=union_cols), new.reindex(columns=union_cols)], ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates()
    dedup = before - len(merged)
    msg = f"Merged analyzed tables | rows={len(merged)} | schema=previous schema only"
    if dedup > 0:
        msg += f" | exact duplicates removed={dedup}"
    if mapped_notes:
        msg += f" | mapped={mapped_notes[:8]}"
    if dropped_unmapped_cols:
        msg += f" | dropped_unmapped={dropped_unmapped_cols[:8]}"
    if explicitly_skipped_cols:
        msg += f" | skipped={explicitly_skipped_cols[:8]}"
    return merged, msg
