from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy.interpolate import UnivariateSpline
from sklearn.model_selection import KFold

from .freezing_curves import canonical_dilution_token


def _require_columns(df: pd.DataFrame, required: list[str], context: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{context}: missing required columns {missing}. "
            f"Available: {list(df.columns)}"
        )


def available_kp_options(curves_df: pd.DataFrame) -> dict[str, list[str]]:
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


@dataclass
class KneeResult:
    breakpoints: list[float]
    nm_at_breakpoints: list[float]
    anova_like: dict[str, Any]
    bootstrap: dict[str, Any]
    cv: dict[str, Any]
    spline_grid: pd.DataFrame
    piecewise_params: dict[str, Any]


@dataclass
class KneeTransitionResult:
    breakpoints_all: pd.DataFrame
    breakpoints_kept: pd.DataFrame
    spline_grid: pd.DataFrame
    raw_points: pd.DataFrame
    thresholds: dict[str, float]
    anova_like: dict[str, float]


KNEE_INTERNAL_BREAKS_DEFAULT = 10


def _normalize_temp_bounds(temp_min: Any = None, temp_max: Any = None) -> tuple[float | None, float | None]:
    tmin = pd.to_numeric(pd.Series([temp_min]), errors="coerce").iloc[0]
    tmax = pd.to_numeric(pd.Series([temp_max]), errors="coerce").iloc[0]
    tmin_f = float(tmin) if np.isfinite(tmin) else None
    tmax_f = float(tmax) if np.isfinite(tmax) else None
    if (tmin_f is not None) and (tmax_f is not None) and (tmin_f > tmax_f):
        tmin_f, tmax_f = tmax_f, tmin_f
    return tmin_f, tmax_f


def _map_spar_to_s(x: np.ndarray, y: np.ndarray, spar: float) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    if len(y) == 0:
        return 0.0

    spar = float(np.clip(spar, 0.0, 1.0))
    if spar <= 0:
        return 0.0

    n = max(int(len(y)), 1)
    var_y = float(np.nanvar(y)) if np.isfinite(np.nanvar(y)) else 1.0
    try:
        spline_hi = UnivariateSpline(x, y, s=max(1.0, n * var_y * 1e6))
        s_hi = float(np.nansum((y - spline_hi(x)) ** 2))
    except Exception:
        s_hi = float(n * max(var_y, 1e-12))

    if not np.isfinite(s_hi) or s_hi <= 0:
        s_hi = float(n * max(var_y, 1e-12))
    return float(s_hi * spar)


def _kp_fit_piecewise_breaks(pw_model: Any, n_segments: int) -> np.ndarray:
    n_segments = int(max(n_segments, 2))
    try:
        x_data = np.asarray(getattr(pw_model, "x_data", []), dtype=float)
        x_data = x_data[np.isfinite(x_data)]
        if len(x_data) >= 2:
            x_min = float(np.nanmin(x_data))
            x_max = float(np.nanmax(x_data))
            n_internal = n_segments - 1
            if n_internal >= 1 and x_max > x_min:
                guess = np.linspace(x_min, x_max, n_internal + 2, dtype=float)[1:-1]
                breaks = pw_model.fit_guess(guess)
                return np.asarray(breaks, dtype=float)
    except Exception:
        pass

    try:
        breaks = pw_model.fitfast(n_segments, pop=3)
        return np.asarray(breaks, dtype=float)
    except Exception:
        breaks = pw_model.fit(n_segments)
        return np.asarray(breaks, dtype=float)


def _kp_candidate_internal_breaks(x: np.ndarray, *, min_k: int = 2, max_k: int = 14) -> list[int]:
    ux = np.unique(np.asarray(x, dtype=float))
    n_ux = int(len(ux))
    if n_ux < 8:
        return [max(1, min(int(KNEE_INTERNAL_BREAKS_DEFAULT), max(n_ux - 2, 1)))]
    # Conservative upper bound to avoid overfitting small series.
    cap_by_points = max(2, int(np.floor(n_ux / 6)))
    k_max = int(max(min_k, min(max_k, cap_by_points)))
    cands = list(range(int(min_k), int(k_max) + 1))
    if len(cands) == 0:
        cands = [max(1, min(int(KNEE_INTERNAL_BREAKS_DEFAULT), max(n_ux - 2, 1)))]
    return cands


def _kp_minmax_norm(values: list[float]) -> list[float]:
    arr = np.asarray(values, dtype=float)
    out = np.full_like(arr, np.nan, dtype=float)
    m = np.isfinite(arr)
    if not np.any(m):
        return out.tolist()
    v = arr[m]
    vmin = float(np.nanmin(v))
    vmax = float(np.nanmax(v))
    if (not np.isfinite(vmin)) or (not np.isfinite(vmax)) or np.isclose(vmax, vmin):
        out[m] = 0.0
        return out.tolist()
    out[m] = (v - vmin) / (vmax - vmin)
    return out.tolist()


def _kp_select_internal_breaks(
    x: np.ndarray,
    y: np.ndarray,
    x_grid: np.ndarray,
    y_grid: np.ndarray,
    *,
    mode: str,
    cv_k: int = 5,
    min_k: int = 2,
    max_k: int = 14,
) -> tuple[int, dict[str, Any]]:
    mode_txt = str(mode or "legacy").strip().lower()
    if mode_txt not in {"legacy", "bic", "cv", "cv+bic"}:
        mode_txt = "legacy"

    if mode_txt == "legacy":
        return int(KNEE_INTERNAL_BREAKS_DEFAULT), {
            "segment_selection_mode": "legacy",
            "selected_n_breaks_internal": int(KNEE_INTERNAL_BREAKS_DEFAULT),
            "candidate_n_breaks_internal": [int(KNEE_INTERNAL_BREAKS_DEFAULT)],
            "bic_scores": [],
            "cv_scores": [],
            "combined_scores": [],
            "selected_by": "legacy",
        }

    import pwlf

    candidates = _kp_candidate_internal_breaks(x, min_k=min_k, max_k=max_k)
    rows: list[dict[str, Any]] = []

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x_grid = np.asarray(x_grid, dtype=float)
    y_grid = np.asarray(y_grid, dtype=float)
    n = int(len(x_grid))
    n_splits = int(max(2, min(int(cv_k), max(2, len(x) // 4))))
    can_cv = len(x) >= (n_splits + 2)

    for k_internal in candidates:
        n_segments = int(k_internal) + 1
        bic_val = np.nan
        cv_val = np.nan

        try:
            pw = pwlf.PiecewiseLinFit(x_grid, y_grid)
            _ = _kp_fit_piecewise_breaks(pw, n_segments)
            y_pw = pw.predict(x_grid)
            rss = float(np.nansum((y_grid - y_pw) ** 2))
            rss = max(rss, 1e-18)
            # Effective parameter count for continuous piecewise linear:
            # intercept + base slope + k break positions + k slope deltas.
            p_eff = float(2 + (2 * int(k_internal)))
            bic_val = float(n * np.log(rss / max(n, 1)) + p_eff * np.log(max(n, 2)))
        except Exception:
            bic_val = np.nan

        if can_cv:
            mse_folds: list[float] = []
            try:
                kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
                for tr_idx, te_idx in kf.split(x):
                    xtr = x[tr_idx]
                    ytr = y[tr_idx]
                    xte = x[te_idx]
                    yte = y[te_idx]
                    if len(np.unique(xtr)) < (int(k_internal) + 2):
                        continue
                    try:
                        pw_tr = pwlf.PiecewiseLinFit(xtr, ytr)
                        _ = _kp_fit_piecewise_breaks(pw_tr, n_segments)
                        yhat = pw_tr.predict(xte)
                        mse = float(np.nanmean((yte - yhat) ** 2))
                        if np.isfinite(mse):
                            mse_folds.append(mse)
                    except Exception:
                        continue
            except Exception:
                mse_folds = []
            if len(mse_folds) > 0:
                cv_val = float(np.nanmean(mse_folds))

        rows.append(
            {
                "n_breaks_internal": int(k_internal),
                "bic": float(bic_val) if np.isfinite(bic_val) else np.nan,
                "cv_mse": float(cv_val) if np.isfinite(cv_val) else np.nan,
            }
        )

    score_df = pd.DataFrame(rows)
    if score_df.empty:
        return int(KNEE_INTERNAL_BREAKS_DEFAULT), {
            "segment_selection_mode": mode_txt,
            "selected_n_breaks_internal": int(KNEE_INTERNAL_BREAKS_DEFAULT),
            "candidate_n_breaks_internal": [],
            "bic_scores": [],
            "cv_scores": [],
            "combined_scores": [],
            "selected_by": "fallback_empty_scores",
        }

    bic_list = score_df["bic"].tolist() if "bic" in score_df.columns else [np.nan] * len(score_df)
    cv_list = score_df["cv_mse"].tolist() if "cv_mse" in score_df.columns else [np.nan] * len(score_df)
    bic_norm = _kp_minmax_norm(bic_list)
    cv_norm = _kp_minmax_norm(cv_list)
    combined: list[float] = []
    for b, c in zip(bic_norm, cv_norm):
        if np.isfinite(b) and np.isfinite(c):
            combined.append(float(0.5 * b + 0.5 * c))
        elif np.isfinite(b):
            combined.append(float(b))
        elif np.isfinite(c):
            combined.append(float(c))
        else:
            combined.append(np.nan)
    score_df["combined"] = combined

    def _argmin_col(col: str) -> int | None:
        if col not in score_df.columns:
            return None
        vals = pd.to_numeric(score_df[col], errors="coerce").to_numpy(dtype=float)
        if vals.size == 0 or not np.any(np.isfinite(vals)):
            return None
        return int(score_df.iloc[int(np.nanargmin(vals))]["n_breaks_internal"])

    k_bic = _argmin_col("bic")
    k_cv = _argmin_col("cv_mse")
    k_comb = _argmin_col("combined")

    selected = None
    selected_by = mode_txt
    if mode_txt == "bic":
        selected = k_bic
    elif mode_txt == "cv":
        selected = k_cv
    elif mode_txt == "cv+bic":
        selected = k_comb

    if selected is None:
        selected = k_bic if k_bic is not None else (k_cv if k_cv is not None else int(KNEE_INTERNAL_BREAKS_DEFAULT))
        selected_by = f"{mode_txt}_fallback"

    selected = int(max(1, selected))
    diag = {
        "segment_selection_mode": mode_txt,
        "selected_n_breaks_internal": int(selected),
        "candidate_n_breaks_internal": [int(v) for v in score_df["n_breaks_internal"].tolist()],
        "bic_scores": [float(v) if np.isfinite(v) else np.nan for v in bic_list],
        "cv_scores": [float(v) if np.isfinite(v) else np.nan for v in cv_list],
        "combined_scores": [float(v) if np.isfinite(v) else np.nan for v in combined],
        "selected_by": str(selected_by),
    }
    return selected, diag


def _kp_extract_plus_to_minus_breaks(
    breaks: Sequence[float],
    pw_model: Any,
    spline_model: Any,
    *,
    flat_quantile: float,
    rise_quantile: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    b = np.asarray(list(breaks), dtype=float)
    if len(b) < 3:
        cols = [
            "breakpoint_T_C",
            "breakpoint_nm",
            "state_before_cooling",
            "state_after_cooling",
            "transition",
            "keep_knee",
            "slope_before_cooling",
            "slope_after_cooling",
            "slope_before_dx",
            "slope_after_dx",
            "knee_strength",
        ]
        empty = pd.DataFrame(columns=cols)
        return (
            empty.copy(),
            empty.copy(),
            {"flat_thr_abs_cooling": 0.0, "rise_thr_abs_cooling": 0.0},
        )

    n_segments = len(b) - 1
    seg_rows: list[dict[str, Any]] = []
    for i in range(n_segments):
        x0 = float(b[i])
        x1 = float(b[i + 1])
        if np.isclose(x1, x0, atol=1e-12):
            slope_dx = np.nan
        else:
            yp = pw_model.predict(np.asarray([x0, x1], dtype=float))
            slope_dx = float((yp[1] - yp[0]) / (x1 - x0))
        slope_cooling = float(-slope_dx) if np.isfinite(slope_dx) else np.nan
        seg_rows.append(
            {
                "seg_idx_x_asc": int(i),
                "slope_dx": slope_dx,
                "slope_cooling": slope_cooling,
            }
        )
    seg_df = pd.DataFrame(seg_rows)

    slope_vals = seg_df["slope_cooling"].to_numpy(dtype=float)
    abs_slopes = np.abs(slope_vals)
    abs_slopes = abs_slopes[np.isfinite(abs_slopes)]
    fq = float(np.clip(flat_quantile, 0.0, 1.0))
    rq = float(np.clip(rise_quantile, 0.0, 1.0))
    if rq <= fq:
        rq = min(1.0, fq + 0.2)
    if len(abs_slopes) > 0:
        flat_thr = float(np.quantile(abs_slopes, fq))
        rise_thr = float(np.quantile(abs_slopes, rq))
        if rise_thr <= flat_thr:
            rise_thr = flat_thr + max(float(np.nanmax(abs_slopes)) * 0.05, 1e-9)
        med_slope = float(np.nanmedian(slope_vals[np.isfinite(slope_vals)]))
    else:
        flat_thr = 0.0
        rise_thr = 1e-9
        med_slope = 0.0

    orient = 1.0 if med_slope >= 0 else -1.0
    min_rel_drop = float(np.clip(rq - fq, 0.12, 0.70))

    rows: list[dict[str, Any]] = []
    for j in range(1, n_segments):
        bp = float(b[j])
        seg_before = seg_df.iloc[int(j)]
        seg_after = seg_df.iloc[int(j - 1)]

        sb = float(seg_before["slope_cooling"])
        sa = float(seg_after["slope_cooling"])
        sb_eff = orient * sb
        sa_eff = orient * sa
        delta_eff = sb_eff - sa_eff
        rel_drop = float(delta_eff / max(abs(sb_eff), 1e-12))

        keep = bool((sb_eff > 0) and (delta_eff > 0) and (rel_drop >= min_rel_drop))

        state_before = "+" if sb_eff > 0 else "-"
        state_after = "-" if keep else ("+" if sa_eff > 0 else "-")
        transition = f"{state_before}->{state_after}"
        strength = float(delta_eff)
        if spline_model is None:
            nm_bp = float("nan")
        else:
            log_nm = float(spline_model(bp))
            nm_bp = float(np.power(10.0, log_nm)) if np.isfinite(log_nm) else float("nan")
        rows.append(
            {
                "breakpoint_T_C": bp,
                "breakpoint_nm": nm_bp,
                "state_before_cooling": state_before,
                "state_after_cooling": state_after,
                "transition": transition,
                "keep_knee": keep,
                "slope_before_cooling": float(seg_before["slope_cooling"]),
                "slope_after_cooling": float(seg_after["slope_cooling"]),
                "slope_before_dx": float(seg_before["slope_dx"]),
                "slope_after_dx": float(seg_after["slope_dx"]),
                "knee_strength": strength,
                "knee_rel_drop": rel_drop,
            }
        )

    bp_all = pd.DataFrame(rows).sort_values("breakpoint_T_C", ascending=False).reset_index(drop=True)
    if bp_all.empty:
        bp_kept = bp_all.copy()
    else:
        bp_kept = bp_all[bp_all["keep_knee"]].copy().sort_values(
            by=["knee_strength", "breakpoint_T_C"], ascending=[False, False]
        ).reset_index(drop=True)
        bp_kept = bp_kept.sort_values("breakpoint_T_C", ascending=False).reset_index(drop=True)

    return (
        bp_all,
        bp_kept,
        {
            "flat_thr_abs_cooling": flat_thr,
            "rise_thr_abs_cooling": rise_thr,
            "orientation": orient,
            "min_rel_drop": min_rel_drop,
        },
    )


def kneepoint_transition_analysis(
    curves_df: pd.DataFrame,
    *,
    sample: str,
    size: str,
    dilutions: Sequence[Any],
    spar: float = 0.4,
    n_breaks: int = 2,
    flat_quantile: float = 0.35,
    rise_quantile: float = 0.70,
    temp_col: str = "Freezing.temperature",
    nm_col: str = "nm",
    sample_col: str = "Sample",
    size_col: str = "Size",
    control_col: str = "Control",
    dilution_col: str = "Dilution.factor",
) -> KneeTransitionResult:
    required = [temp_col, nm_col, sample_col, size_col, control_col, dilution_col]
    _require_columns(curves_df, required, "Curves file")

    df = curves_df.copy()
    df[temp_col] = pd.to_numeric(df[temp_col], errors="coerce")
    df[nm_col] = pd.to_numeric(df[nm_col], errors="coerce")

    sel_dil_keys = {canonical_dilution_token(v) for v in list(dilutions)}
    df["__dil_key"] = df[dilution_col].map(canonical_dilution_token)

    df = df.dropna(subset=[temp_col, nm_col, sample_col, size_col])
    df = df[(df[sample_col].astype(str) == str(sample)) & (df[size_col].astype(str) == str(size))]
    df = df[df[control_col].astype(str).str.lower() != "yes"]
    df = df[df["__dil_key"].isin(sel_dil_keys)]
    df = df[(df[nm_col] > 0) & np.isfinite(df[nm_col]) & np.isfinite(df[temp_col])]

    if len(df) < 10:
        raise ValueError(f"Not enough points after filtering (n={len(df)}). Need >= 10.")

    raw_points = df.sort_values(temp_col).copy()
    raw_points["log_nm"] = np.log10(raw_points[nm_col].to_numpy(dtype=float))

    tmp = (
        raw_points[[temp_col, "log_nm"]]
        .rename(columns={temp_col: "x", "log_nm": "y"})
        .dropna()
        .groupby("x", as_index=False)["y"]
        .mean()
        .sort_values("x")
    )
    x = tmp["x"].to_numpy(dtype=float)
    y = tmp["y"].to_numpy(dtype=float)

    if len(x) < 10 or len(np.unique(x)) < 4:
        raise ValueError("Not enough unique temperature points after collapsing duplicates for spline fit.")

    s_val = _map_spar_to_s(x, y, float(spar))
    spline = UnivariateSpline(x, y, s=s_val)

    x_grid = np.linspace(float(np.nanmin(x)), float(np.nanmax(x)), 2000)
    y_grid = spline(x_grid)
    spline_grid = pd.DataFrame({temp_col: x_grid, "log_nm_spline": y_grid})

    import pwlf

    requested_breaks = max(int(n_breaks), 1)
    n_breaks_internal = int(KNEE_INTERNAL_BREAKS_DEFAULT)
    n_segments = n_breaks_internal + 1
    pw = pwlf.PiecewiseLinFit(x_grid, y_grid)
    breaks = _kp_fit_piecewise_breaks(pw, n_segments)

    bp_all, bp_kept_candidates, thr = _kp_extract_plus_to_minus_breaks(
        breaks,
        pw,
        spline,
        flat_quantile=float(flat_quantile),
        rise_quantile=float(rise_quantile),
    )
    bp_kept = (
        bp_kept_candidates
        .sort_values(by=["knee_strength", "breakpoint_T_C"], ascending=[False, False])
        .head(requested_breaks)
        .sort_values("breakpoint_T_C", ascending=False)
        .reset_index(drop=True)
    )

    a = np.vstack([x_grid, np.ones_like(x_grid)]).T
    coef, _, _, _ = np.linalg.lstsq(a, y_grid, rcond=None)
    y_lin = a @ coef
    rss_lin = float(np.sum((y_grid - y_lin) ** 2))
    y_pw = pw.predict(x_grid)
    rss_pw = float(np.sum((y_grid - y_pw) ** 2))

    return KneeTransitionResult(
        breakpoints_all=bp_all,
        breakpoints_kept=bp_kept,
        spline_grid=spline_grid,
        raw_points=raw_points,
        thresholds={
            "flat_thr_abs_cooling": float(thr["flat_thr_abs_cooling"]),
            "rise_thr_abs_cooling": float(thr["rise_thr_abs_cooling"]),
            "spar_mapped_s": float(s_val),
            "n_breaks_requested": float(requested_breaks),
            "n_breaks_internal": float(n_breaks_internal),
            "n_breaks_all_internal": float(len(bp_all)),
            "n_knees_candidates_plus_to_minus": float(len(bp_kept_candidates)),
            "n_knees_selected": float(len(bp_kept)),
        },
        anova_like={
            "rss_linear": rss_lin,
            "rss_piecewise": rss_pw,
            "delta_rss": rss_lin - rss_pw,
        },
    )


def kneepoint_analysis(
    curves_df: pd.DataFrame,
    *,
    sample: str,
    size: str,
    dilutions: Sequence[Any],
    spar: float = 0.4,
    n_breaks: int = 2,
    flat_quantile: float = 0.35,
    rise_quantile: float = 0.70,
    segment_selection_mode: str = "legacy",
    segment_min_internal_breaks: int = 2,
    segment_max_internal_breaks: int = 14,
    boot_R: int = 500,
    cv_k: int = 5,
    temp_col: str = "Freezing.temperature",
    nm_col: str = "nm",
    sample_col: str = "Sample",
    size_col: str = "Size",
    control_col: str = "Control",
    dilution_col: str = "Dilution.factor",
    temp_min: float | None = None,
    temp_max: float | None = None,
) -> KneeResult:
    required = [temp_col, nm_col, sample_col, size_col, control_col, dilution_col]
    _require_columns(curves_df, required, "Curves file")

    df = curves_df.copy()
    df[temp_col] = pd.to_numeric(df[temp_col], errors="coerce")
    df[nm_col] = pd.to_numeric(df[nm_col], errors="coerce")
    df = df.dropna(subset=[temp_col, nm_col, sample_col, size_col]).copy()
    df = df[(df[sample_col].astype(str) == str(sample)) & (df[size_col].astype(str) == str(size))].copy()
    df = df[df[control_col].astype(str).str.lower() != "yes"].copy()
    df = df[df[dilution_col].isin(list(dilutions))].copy()
    df = df[(df[nm_col] > 0) & np.isfinite(df[nm_col]) & np.isfinite(df[temp_col])].copy()
    tmin, tmax = _normalize_temp_bounds(temp_min, temp_max)
    if tmin is not None:
        df = df[df[temp_col] >= float(tmin)].copy()
    if tmax is not None:
        df = df[df[temp_col] <= float(tmax)].copy()

    if len(df) < 10:
        raise ValueError(f"Not enough points after filtering (n={len(df)}). Need >= 10.")

    x_raw = df[temp_col].to_numpy(dtype=float)
    y_raw = np.log10(df[nm_col].to_numpy(dtype=float))

    tmp = pd.DataFrame({"x": x_raw, "y": y_raw}).dropna()
    tmp = tmp.groupby("x", as_index=False)["y"].mean().sort_values("x")
    x = tmp["x"].to_numpy(dtype=float)
    y = tmp["y"].to_numpy(dtype=float)

    if len(x) < 10 or len(np.unique(x)) < 4:
        raise ValueError("Not enough unique temperature points after collapsing duplicates for spline fit.")

    s_val = _map_spar_to_s(x, y, float(spar))
    spline = UnivariateSpline(x, y, s=s_val)

    x_grid = np.linspace(float(np.nanmin(x)), float(np.nanmax(x)), 2000)
    y_grid = spline(x_grid)
    spline_grid = pd.DataFrame({temp_col: x_grid, "log_nm_spline": y_grid})

    import pwlf

    pw = pwlf.PiecewiseLinFit(x_grid, y_grid)
    requested_breaks = max(int(n_breaks), 1)
    n_breaks_internal, seg_sel_diag = _kp_select_internal_breaks(
        x,
        y,
        x_grid,
        y_grid,
        mode=str(segment_selection_mode or "legacy"),
        cv_k=int(cv_k),
        min_k=int(segment_min_internal_breaks),
        max_k=int(segment_max_internal_breaks),
    )
    n_segments = n_breaks_internal + 1
    breaks = _kp_fit_piecewise_breaks(pw, n_segments)

    bp_all, bp_kept_candidates, thr = _kp_extract_plus_to_minus_breaks(
        breaks,
        pw,
        spline,
        flat_quantile=float(flat_quantile),
        rise_quantile=float(rise_quantile),
    )
    transition_rows = bp_all.to_dict("records")
    bp_kept = (
        bp_kept_candidates.sort_values(by=["knee_strength", "breakpoint_T_C"], ascending=[False, False])
        .head(requested_breaks)
        .sort_values("breakpoint_T_C", ascending=False)
        .reset_index(drop=True)
    )
    internal_bps_sorted = sorted(bp_kept["breakpoint_T_C"].astype(float).tolist(), reverse=True)
    nm_at_bps = []
    for bp in internal_bps_sorted:
        log_nm = float(spline(bp))
        nm_at_bps.append(float(np.power(10.0, log_nm)) if np.isfinite(log_nm) else float("nan"))

    a = np.vstack([x_grid, np.ones_like(x_grid)]).T
    coef, _, _, _ = np.linalg.lstsq(a, y_grid, rcond=None)
    y_lin = a @ coef
    rss_lin = float(np.sum((y_grid - y_lin) ** 2))

    y_pw = pw.predict(x_grid)
    rss_pw = float(np.sum((y_grid - y_pw) ** 2))

    anova_like = {
        "rss_linear": rss_lin,
        "rss_piecewise": rss_pw,
        "delta_rss": rss_lin - rss_pw,
    }

    rng = np.random.default_rng(42)
    boot_bps: list[list[float]] = []
    for _ in range(int(boot_R)):
        idx = rng.integers(0, len(x), size=len(x))
        xb = x[idx]
        yb = y[idx]
        try:
            spline_b = UnivariateSpline(xb, yb, s=_map_spar_to_s(xb, yb, float(spar)))
            yb_grid = spline_b(x_grid)
            pw_b = pwlf.PiecewiseLinFit(x_grid, yb_grid)
            breaks_b = _kp_fit_piecewise_breaks(pw_b, n_segments)
            _, bp_kept_b_candidates, _ = _kp_extract_plus_to_minus_breaks(
                breaks_b,
                pw_b,
                spline_b,
                flat_quantile=float(flat_quantile),
                rise_quantile=float(rise_quantile),
            )
            bp_kept_b = (
                bp_kept_b_candidates.sort_values(by=["knee_strength", "breakpoint_T_C"], ascending=[False, False])
                .head(requested_breaks)
                .sort_values("breakpoint_T_C", ascending=False)
                .reset_index(drop=True)
            )
            bps_b = sorted(bp_kept_b["breakpoint_T_C"].astype(float).tolist(), reverse=True)
            if len(bps_b) == len(internal_bps_sorted) and len(bps_b) > 0:
                boot_bps.append(bps_b)
        except Exception:
            continue

    if boot_bps:
        boot_arr = np.asarray(boot_bps, dtype=float)
        ci = []
        for j in range(boot_arr.shape[1]):
            lo, hi = np.nanpercentile(boot_arr[:, j], [2.5, 97.5])
            ci.append({"bp_index": j + 1, "ci_2.5": float(lo), "ci_97.5": float(hi)})
        bootstrap: dict[str, Any] = {"n_boot_ok": int(len(boot_bps)), "ci_percentile": ci}
    else:
        bootstrap = {"n_boot_ok": 0, "ci_percentile": []}

    cv_scores: list[float] = []
    kf = KFold(n_splits=int(cv_k), shuffle=True, random_state=42)
    for train_idx, test_idx in kf.split(x):
        xtr, ytr = x[train_idx], y[train_idx]
        xte, yte = x[test_idx], y[test_idx]
        try:
            spline_tr = UnivariateSpline(xtr, ytr, s=_map_spar_to_s(xtr, ytr, float(spar)))
            yhat = spline_tr(xte)
            mse = float(np.mean((yte - yhat) ** 2))
            cv_scores.append(mse)
        except Exception:
            continue

    cv = {
        "k": int(cv_k),
        "mse_mean": float(np.mean(cv_scores)) if cv_scores else np.nan,
        "mse_sd": float(np.std(cv_scores)) if cv_scores else np.nan,
        "n_folds_ok": int(len(cv_scores)),
    }

    piecewise_params = {
        "spline_spar": float(spar),
        "spline_s_mapped": float(s_val),
        "n_breaks_requested": int(requested_breaks),
        "n_breaks_internal": int(n_breaks_internal),
        "n_segments": int(n_segments),
        "segment_selection_mode": str(seg_sel_diag.get("segment_selection_mode", str(segment_selection_mode or "legacy"))),
        "segment_selection_selected_by": str(seg_sel_diag.get("selected_by", "legacy")),
        "segment_selection_candidates": [int(v) for v in seg_sel_diag.get("candidate_n_breaks_internal", [])],
        "segment_selection_bic_scores": [float(v) if np.isfinite(v) else np.nan for v in seg_sel_diag.get("bic_scores", [])],
        "segment_selection_cv_scores": [float(v) if np.isfinite(v) else np.nan for v in seg_sel_diag.get("cv_scores", [])],
        "segment_selection_combined_scores": [float(v) if np.isfinite(v) else np.nan for v in seg_sel_diag.get("combined_scores", [])],
        "breaks_all": list(map(float, breaks)),
        "piecewise_x_grid": x_grid.astype(float).tolist(),
        "piecewise_log_nm": np.asarray(y_pw, dtype=float).tolist(),
        "breaks_all_transitions": bp_all["breakpoint_T_C"].astype(float).tolist(),
        "breaks_candidates_plus_to_minus": bp_kept_candidates["breakpoint_T_C"].astype(float).tolist(),
        "breaks_kept_plus_to_minus": internal_bps_sorted,
        "n_knees_candidates_plus_to_minus": int(len(bp_kept_candidates)),
        "n_knees_selected": int(len(bp_kept)),
        "knee_filter_flat_quantile": float(np.clip(flat_quantile, 0.0, 1.0)),
        "knee_filter_rise_quantile": float(np.clip(rise_quantile, 0.0, 1.0)),
        "knee_filter_flat_thr_abs_cooling": float(thr["flat_thr_abs_cooling"]),
        "knee_filter_rise_thr_abs_cooling": float(thr["rise_thr_abs_cooling"]),
        "knee_filter_rows": transition_rows,
    }

    return KneeResult(
        breakpoints=internal_bps_sorted,
        nm_at_breakpoints=nm_at_bps,
        anova_like=anova_like,
        bootstrap=bootstrap,
        cv=cv,
        spline_grid=spline_grid,
        piecewise_params=piecewise_params,
    )


def filter_kp_points_for_sample(
    curves_df: pd.DataFrame,
    *,
    sample: str,
    size: str,
    dilutions: Sequence[Any],
    temp_min: float | None = None,
    temp_max: float | None = None,
    temp_col: str = "Freezing.temperature",
    nm_col: str = "nm",
    sample_col: str = "Sample",
    size_col: str = "Size",
    control_col: str = "Control",
    dilution_col: str = "Dilution.factor",
) -> pd.DataFrame:
    d = curves_df.copy()
    d.columns = [str(c) for c in d.columns]
    d[temp_col] = pd.to_numeric(d[temp_col], errors="coerce")
    d[nm_col] = pd.to_numeric(d[nm_col], errors="coerce")
    d = d.dropna(subset=[temp_col, nm_col, sample_col, size_col])
    d = d[(d[sample_col].astype(str) == str(sample)) & (d[size_col].astype(str) == str(size))]
    d = d[d[control_col].astype(str).str.lower() != "yes"]

    selected_dil = {canonical_dilution_token(x) for x in list(dilutions)}
    if len(selected_dil) > 0:
        d["__dil_plot"] = d[dilution_col].map(canonical_dilution_token)
        d = d[d["__dil_plot"].isin(selected_dil)]

    d = d[(d[nm_col] > 0) & np.isfinite(d[nm_col]) & np.isfinite(d[temp_col])]
    tmin, tmax = _normalize_temp_bounds(temp_min, temp_max)
    if tmin is not None:
        d = d[d[temp_col] >= float(tmin)]
    if tmax is not None:
        d = d[d[temp_col] <= float(tmax)]
    d = d.sort_values(temp_col)
    d["log_nm"] = np.log10(d[nm_col])
    return d
