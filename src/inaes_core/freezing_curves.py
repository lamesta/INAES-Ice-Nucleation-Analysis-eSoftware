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
class FreezingCurvesFilter:
    selected_sizes: Sequence[str]
    selected_dilutions: Sequence[str]
    selected_locations: Sequence[str]
    include_controls: bool = False


@dataclass
class MeanCIConfig:
    group_col: str = "Location"
    temp_step: float = 0.25
    smooth: float = 0.35
    min_curves_per_group: int = 2
    ci_method: str = "legacy"


def available_fc_options(curves_df: pd.DataFrame) -> dict[str, list[str]]:
    d = curves_df.copy()
    sizes = (
        sorted(d["Size"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist())
        if "Size" in d.columns
        else []
    )
    locations = (
        sorted(d["Location"].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique().tolist())
        if "Location" in d.columns
        else []
    )
    dils: list[Any] = []
    if "Dilution.factor" in d.columns:
        dcol = d["Dilution.factor"].dropna()
        try:
            dils = sorted(dcol.unique().tolist())
        except Exception:
            dils = sorted(dcol.astype(str).unique().tolist())
    return {"sizes": sizes, "locations": locations, "dilutions": dils}


def prepare_freezing_curves_points(
    curves_df: pd.DataFrame,
    flt: FreezingCurvesFilter,
) -> tuple[pd.DataFrame, str]:
    need = ["Sample", "Size", "Control", "Dilution.factor", "Freezing.temperature", "nm"]
    missing = [c for c in need if c not in curves_df.columns]
    if missing:
        raise ValueError(f"Freezing Curves: missing columns {missing}. Available: {list(curves_df.columns)}")

    d = curves_df.copy()
    d["Freezing.temperature"] = pd.to_numeric(d["Freezing.temperature"], errors="coerce")
    d["nm"] = pd.to_numeric(d["nm"], errors="coerce")
    d = d.dropna(subset=["Sample", "Size", "Freezing.temperature", "nm"]).copy()
    if "Location" in d.columns:
        d["Location"] = d["Location"].astype(str)
    else:
        d["Location"] = "(no Location)"
    d = d[np.isfinite(d["Freezing.temperature"]) & np.isfinite(d["nm"]) & (d["nm"] > 0)].copy()

    d["Control_norm"] = np.where(
        d["Control"].astype(str).str.lower() == "yes",
        "Yes",
        "No",
    ).astype(str)
    d = d[d["Dilution.factor"].notna()].copy()
    d["Dilution.plot"] = d["Dilution.factor"].map(canonical_dilution_token)

    if "Sample_ID" in d.columns:
        d["Curve_ID"] = d["Sample_ID"].astype(str)
    else:
        d["Curve_ID"] = (
            d["Sample"].astype(str)
            + "|"
            + d["Size"].astype(str)
            + "|"
            + d["Location"].astype(str)
            + "|"
            + d["Dilution.factor"].astype(str)
        )

    selected_sizes = [str(s).strip() for s in (flt.selected_sizes or []) if str(s).strip()]
    if selected_sizes:
        d = d[d["Size"].astype(str).isin(selected_sizes)].copy()

    selected_locs = [str(s).strip() for s in (flt.selected_locations or []) if str(s).strip()]
    if selected_locs:
        d = d[d["Location"].astype(str).isin(selected_locs)].copy()

    selected_dils = list(flt.selected_dilutions or [])
    if len(selected_dils) > 0:
        d = d[d["Dilution.factor"].isin(selected_dils)].copy()

    if not flt.include_controls:
        d = d[d["Control"].astype(str).str.lower() != "yes"].copy()

    # Legacy Dash parity: trim 5-95% within semantic curve key.
    d["_curve_key"] = (
        d["Sample"].astype(str)
        + "|"
        + d["Size"].astype(str)
        + "|"
        + d["Location"].astype(str)
        + "|"
        + d["Dilution.factor"].astype(str)
    )

    def _trim_group_shiny(g: pd.DataFrame) -> pd.DataFrame:
        g2 = g.sort_values("Freezing.temperature", ascending=False)
        n = len(g2)
        start = int(np.ceil(n * 0.05))
        end = int(np.floor(n * 0.95))
        if end <= start:
            return g2
        return g2.iloc[start:end]

    d = d.groupby("_curve_key", group_keys=False).apply(_trim_group_shiny)

    status = (
        f"rows={len(d)} | curves={d['Curve_ID'].nunique() if len(d) else 0} | "
        f"sizes={selected_sizes or 'all'} | dilutions={selected_dils or 'all'} | "
        f"locations={selected_locs or 'all'} | include_controls={flt.include_controls}"
    )
    return d, status


def _smooth_log_series(values: np.ndarray, smooth: float) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return v
    s = float(np.clip(smooth, 0.0, 1.0))
    if s <= 0:
        return v
    win = int(round(3 + s * 17))
    win = max(3, min(win, len(v)))
    if win % 2 == 0:
        win += 1
    if win > len(v):
        win = len(v) if len(v) % 2 == 1 else max(1, len(v) - 1)
    if win <= 1:
        return v
    return pd.Series(v).rolling(window=win, center=True, min_periods=1).mean().to_numpy(dtype=float)


def _estimate_n0_from_ff(ff_values: np.ndarray) -> float:
    ff = np.asarray(ff_values, dtype=float)
    ff = ff[np.isfinite(ff)]
    ff = ff[(ff >= 0.0) & (ff <= 1.0)]
    if len(ff) < 2:
        return float("nan")
    ff = np.unique(np.round(ff, 10))
    if len(ff) < 2:
        return float("nan")
    diffs = np.diff(np.concatenate([[0.0], ff]))
    diffs = diffs[np.isfinite(diffs) & (diffs > 1e-6)]
    if len(diffs) == 0:
        return float("nan")
    k = max(1, min(8, len(diffs)))
    step = float(np.median(np.sort(diffs)[:k]))
    if not np.isfinite(step) or step <= 0.0:
        return float("nan")
    guess = int(np.clip(round(1.0 / step), 2, 50000))
    candidates = list(range(max(2, guess - 5), min(50000, guess + 5) + 1))
    if not candidates:
        return float(guess)

    def _score(n0: int) -> float:
        v = ff * float(n0)
        return float(np.nanmedian(np.abs(v - np.rint(v))))

    best = min(candidates, key=_score)
    return float(best)


def _estimate_norm_factor_a(ff_values: np.ndarray, nm_values: np.ndarray) -> float:
    ff = np.asarray(ff_values, dtype=float)
    nm = np.asarray(nm_values, dtype=float)
    m = np.isfinite(ff) & np.isfinite(nm) & (ff > 0.0) & (ff < 1.0) & (nm > 0.0)
    if not np.any(m):
        return float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        a = -np.log1p(-ff[m]) / nm[m]
    a = a[np.isfinite(a) & (a > 0.0)]
    if len(a) == 0:
        return float("nan")
    return float(np.median(a))


def _curve_event_counts(dc: pd.DataFrame, n0: int) -> tuple[np.ndarray, np.ndarray]:
    s = dc[["Freezing.temperature", "FF"]].copy()
    s["Freezing.temperature"] = pd.to_numeric(s["Freezing.temperature"], errors="coerce")
    s["FF"] = pd.to_numeric(s["FF"], errors="coerce")
    s = s.dropna(subset=["Freezing.temperature", "FF"]).copy()
    s = s[np.isfinite(s["Freezing.temperature"]) & np.isfinite(s["FF"])].copy()
    # Keep FF==1 rows: they carry the final freezing events and are required by KM.
    s = s[(s["FF"] >= 0.0) & (s["FF"] <= 1.0)].copy()
    if len(s) < 2:
        return np.array([], dtype=float), np.array([], dtype=int)
    s = s.groupby("Freezing.temperature", as_index=False, sort=True)["FF"].mean()
    s = s.sort_values("Freezing.temperature", ascending=False).copy()
    ff = s["FF"].to_numpy(dtype=float)
    ff = np.maximum.accumulate(np.clip(ff, 0.0, 1.0 - 1e-12))
    cum = np.rint(ff * float(n0)).astype(int)
    cum = np.maximum.accumulate(np.clip(cum, 0, int(n0)))
    d = np.diff(np.concatenate([[0], cum]))
    t = s["Freezing.temperature"].to_numpy(dtype=float)
    keep = d > 0
    if not np.any(keep):
        return np.array([], dtype=float), np.array([], dtype=int)
    return t[keep], d[keep].astype(int)


def _km_survival_loglog_ci(survival: np.ndarray, n_total: int, z_score: float) -> tuple[np.ndarray, np.ndarray]:
    s = np.asarray(survival, dtype=float)
    lo = np.full_like(s, np.nan, dtype=float)
    up = np.full_like(s, np.nan, dtype=float)
    if int(n_total) <= 0:
        return lo, up

    # Paper-faithful log-log CI (Eq. 5): S * exp(± z * sqrt((1-S)/(N*S*ln(S)^2))).
    valid = np.isfinite(s) & (s > 0.0) & (s < 1.0)
    if not np.any(valid):
        return lo, up

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ln_s = np.log(s[valid])
        denom = float(n_total) * s[valid] * np.square(ln_s)
        se = np.sqrt((1.0 - s[valid]) / denom)
        expo = np.clip(float(z_score) * se, -700.0, 700.0)
        lo_v = s[valid] * np.exp(+expo)
        up_v = s[valid] * np.exp(-expo)

    lo_v = np.clip(lo_v, 0.0, 1.0)
    up_v = np.clip(up_v, 0.0, 1.0)
    swap = lo_v > up_v
    if np.any(swap):
        tmp = lo_v[swap].copy()
        lo_v[swap] = up_v[swap]
        up_v[swap] = tmp

    lo[valid] = lo_v
    up[valid] = up_v
    return lo, up


def _compute_mean_ci_curves_legacy(
    filtered_points: pd.DataFrame,
    cfg: MeanCIConfig,
) -> tuple[pd.DataFrame, str]:
    d = filtered_points.copy()
    if len(d) == 0:
        return pd.DataFrame(), "No points after filtering."

    group_col = cfg.group_col if cfg.group_col in d.columns else "Location"
    if group_col not in d.columns:
        raise ValueError(f"Group column '{cfg.group_col}' not found. Available: {list(d.columns)}")

    if "Curve_ID" not in d.columns:
        d["Curve_ID"] = (
            d["Sample"].astype(str)
            + "|"
            + d["Size"].astype(str)
            + "|"
            + d["Location"].astype(str)
            + "|"
            + d["Dilution.plot"].astype(str)
        )

    rows: list[pd.DataFrame] = []
    group_done = 0
    groups_skipped = 0

    for gval, dg in d.groupby(group_col, dropna=False):
        curves = []
        for _, dc in dg.groupby("Curve_ID", dropna=False):
            s = dc[["Freezing.temperature", "nm"]].copy()
            s["Freezing.temperature"] = pd.to_numeric(s["Freezing.temperature"], errors="coerce")
            s["nm"] = pd.to_numeric(s["nm"], errors="coerce")
            s = s.dropna(subset=["Freezing.temperature", "nm"]).copy()
            s = s[np.isfinite(s["Freezing.temperature"]) & np.isfinite(s["nm"]) & (s["nm"] > 0)].copy()
            if len(s) < 3:
                continue
            s = s.groupby("Freezing.temperature", as_index=False, sort=True)["nm"].mean()
            x = s["Freezing.temperature"].to_numpy(dtype=float)
            y = s["nm"].to_numpy(dtype=float)
            order = np.argsort(x)
            x = x[order]
            y = y[order]
            if len(x) < 3 or x[-1] <= x[0]:
                continue
            curves.append((x, y))

        n_curves = len(curves)
        if n_curves < max(1, int(cfg.min_curves_per_group)):
            groups_skipped += 1
            continue

        xmin = min(x[0] for x, _ in curves)
        xmax = max(x[-1] for x, _ in curves)
        step = float(cfg.temp_step)
        if not np.isfinite(step) or step <= 0:
            step = 0.25
        if xmax - xmin < step:
            groups_skipped += 1
            continue

        grid = np.arange(xmin, xmax + step * 0.5, step, dtype=float)
        if len(grid) < 5:
            groups_skipped += 1
            continue

        mat = np.full((n_curves, len(grid)), np.nan, dtype=float)
        for i, (x, y) in enumerate(curves):
            lx = np.log10(y)
            mask = (grid >= x[0]) & (grid <= x[-1])
            if mask.any():
                mat[i, mask] = np.interp(grid[mask], x, lx)

        n_eff = np.sum(np.isfinite(mat), axis=0)
        mean_log = np.full(mat.shape[1], np.nan, dtype=float)
        for j in range(mat.shape[1]):
            col = mat[:, j]
            col = col[np.isfinite(col)]
            if len(col) > 0:
                mean_log[j] = float(np.mean(col))
        std_log = np.full(mat.shape[1], np.nan, dtype=float)
        for j in range(mat.shape[1]):
            col = mat[:, j]
            col = col[np.isfinite(col)]
            if len(col) > 1:
                std_log[j] = float(np.std(col, ddof=1))
        se_log = np.where(n_eff > 1, std_log / np.sqrt(n_eff), np.nan)
        ci_log = 1.96 * se_log

        ok = np.isfinite(mean_log) & (n_eff >= 2)
        if not ok.any():
            groups_skipped += 1
            continue

        mean_log_s = mean_log.copy()
        ci_log_s = ci_log.copy()
        mean_log_s[ok] = _smooth_log_series(mean_log[ok], cfg.smooth)
        ci_log_s[ok] = _smooth_log_series(np.nan_to_num(ci_log[ok], nan=0.0), cfg.smooth * 0.6)

        mean_nm = np.full_like(mean_log_s, np.nan, dtype=float)
        low_nm = np.full_like(mean_log_s, np.nan, dtype=float)
        up_nm = np.full_like(mean_log_s, np.nan, dtype=float)

        exp_mean = np.clip(mean_log_s[ok], -250.0, 250.0)
        exp_low = np.clip((mean_log_s[ok] - ci_log_s[ok]), -250.0, 250.0)
        exp_up = np.clip((mean_log_s[ok] + ci_log_s[ok]), -250.0, 250.0)
        mean_nm[ok] = np.power(10.0, exp_mean)
        low_nm[ok] = np.power(10.0, exp_low)
        up_nm[ok] = np.power(10.0, exp_up)

        one = pd.DataFrame(
            {
                "group": [str(gval)] * len(grid),
                "Freezing.temperature": grid,
                "mean_nm": mean_nm,
                "low_nm": low_nm,
                "up_nm": up_nm,
                "n_curves_at_temp": n_eff,
                "n_curves_group": [n_curves] * len(grid),
                "ci_method": ["legacy"] * len(grid),
            }
        )
        one = one[np.isfinite(one["mean_nm"]) & np.isfinite(one["low_nm"]) & np.isfinite(one["up_nm"])].copy()
        one = one[(one["mean_nm"] > 0) & (one["low_nm"] > 0) & (one["up_nm"] > 0)].copy()
        if len(one) == 0:
            groups_skipped += 1
            continue
        rows.append(one)
        group_done += 1

    if not rows:
        return pd.DataFrame(), (
            f"No valid group summaries. groups_skipped={groups_skipped}. "
            "Possible causes: too few curves per group or insufficient overlapping temperature range."
        )

    out = pd.concat(rows, ignore_index=True)
    status = (
        f"method=legacy | groups={group_done} | rows={len(out)} | group_col={group_col} | "
        f"temp_step={cfg.temp_step} | smooth={cfg.smooth:.2f}"
    )
    return out, status


def _compute_mean_ci_curves_km(
    filtered_points: pd.DataFrame,
    cfg: MeanCIConfig,
) -> tuple[pd.DataFrame, str]:
    d = filtered_points.copy()
    if len(d) == 0:
        return pd.DataFrame(), "No points after filtering."
    if "FF" not in d.columns:
        return pd.DataFrame(), (
            "Kaplan–Meier CI requires column 'FF' in filtered points. "
            "Switch to Legacy CI or include/mappa FF in input data."
        )

    group_col = cfg.group_col if cfg.group_col in d.columns else "Location"
    if group_col not in d.columns:
        raise ValueError(f"Group column '{cfg.group_col}' not found. Available: {list(d.columns)}")

    if "Curve_ID" not in d.columns:
        d["Curve_ID"] = (
            d["Sample"].astype(str)
            + "|"
            + d["Size"].astype(str)
            + "|"
            + d["Location"].astype(str)
            + "|"
            + d["Dilution.plot"].astype(str)
        )

    d["FF"] = pd.to_numeric(d["FF"], errors="coerce")
    d["nm"] = pd.to_numeric(d["nm"], errors="coerce")
    d["Freezing.temperature"] = pd.to_numeric(d["Freezing.temperature"], errors="coerce")
    # Events for KM are temperature+FF driven (include FF==1), while nm is only required
    # for estimating the group normalization factor A.
    d = d[
        np.isfinite(d["Freezing.temperature"])
        & np.isfinite(d["FF"])
        & (d["FF"] >= 0.0)
        & (d["FF"] <= 1.0)
    ].copy()
    if len(d) == 0:
        return pd.DataFrame(), "Kaplan–Meier CI: no valid rows with finite T and FF in [0,1]."

    rows: list[pd.DataFrame] = []
    groups_done = 0
    groups_skipped = 0
    eps = 1e-12
    z_alpha_2 = 1.959963984540054

    for gval, dg in d.groupby(group_col, dropna=False):
        event_temps_all: list[np.ndarray] = []
        a_values: list[float] = []
        curves_used = 0

        for _, dc in dg.groupby("Curve_ID", dropna=False):
            n0_est = _estimate_n0_from_ff(dc["FF"].to_numpy(dtype=float))
            if not np.isfinite(n0_est) or n0_est < 2:
                continue
            n0_i = int(round(float(n0_est)))

            # Estimate A from non-saturated points only.
            dc_a = dc.copy()
            dc_a = dc_a[np.isfinite(dc_a["nm"]) & (dc_a["nm"] > 0.0) & (dc_a["FF"] > 0.0) & (dc_a["FF"] < 1.0)].copy()
            if len(dc_a) < 2:
                continue
            a_i = _estimate_norm_factor_a(
                dc_a["FF"].to_numpy(dtype=float),
                dc_a["nm"].to_numpy(dtype=float),
            )
            if not np.isfinite(a_i) or a_i <= 0.0:
                continue

            temps, d_counts = _curve_event_counts(dc, n0_i)
            if len(temps) == 0:
                continue
            event_temps_all.append(np.repeat(temps.astype(float), d_counts.astype(int)))
            a_values.append(float(a_i))
            curves_used += 1

        if curves_used < max(1, int(cfg.min_curves_per_group)):
            groups_skipped += 1
            continue
        if len(event_temps_all) == 0:
            groups_skipped += 1
            continue

        a_group = float(np.median(np.asarray(a_values, dtype=float)))
        if not np.isfinite(a_group) or a_group <= 0.0:
            groups_skipped += 1
            continue

        all_events = np.concatenate(event_temps_all) if len(event_temps_all) else np.array([], dtype=float)
        all_events = all_events[np.isfinite(all_events)]
        if len(all_events) == 0:
            groups_skipped += 1
            continue

        km = pd.Series(all_events).value_counts().sort_index(ascending=False).reset_index()
        km.columns = ["Freezing.temperature", "events"]
        km["events"] = pd.to_numeric(km["events"], errors="coerce").fillna(0.0).astype(float)

        n_total = int(len(all_events))
        if n_total <= 0:
            groups_skipped += 1
            continue

        km["cumulative_events_before"] = km["events"].cumsum().shift(1, fill_value=0.0)
        km["at_risk"] = float(n_total) - km["cumulative_events_before"]
        km["at_risk"] = np.maximum(km["at_risk"].to_numpy(dtype=float), 0.0)

        with np.errstate(divide="ignore", invalid="ignore"):
            term = np.where(km["at_risk"] > 0.0, 1.0 - (km["events"] / km["at_risk"]), 1.0)
        term = np.clip(term, 0.0, 1.0)
        s_hat = pd.Series(term).cumprod().to_numpy(dtype=float)

        cumulative_events = km["events"].cumsum().to_numpy(dtype=float)
        f_hat = np.clip(cumulative_events / float(n_total), 0.0, 1.0)

        s_ci_low, s_ci_up = _km_survival_loglog_ci(s_hat, n_total=n_total, z_score=z_alpha_2)
        f_low = 1.0 - s_ci_up
        f_up = 1.0 - s_ci_low
        f_low = np.clip(f_low, 0.0, 1.0)
        f_up = np.clip(f_up, 0.0, 1.0)

        # Same edge handling used by the reference notebook for the final S(T)=0 step.
        if len(s_hat) >= 2 and np.isfinite(s_hat[-1]) and abs(float(s_hat[-1])) <= eps:
            if np.isfinite(f_low[-2]):
                f_low[-1] = f_low[-2]
            f_up[-1] = 1.0

        with np.errstate(divide="ignore", invalid="ignore"):
            mean_nm = -np.log(np.clip(s_hat, eps, 1.0)) / a_group
            low_nm = -np.log1p(-np.clip(f_low, 0.0, 1.0 - eps)) / a_group
            up_nm = -np.log1p(-np.clip(f_up, 0.0, 1.0 - eps)) / a_group

        out = pd.DataFrame(
            {
                "group": [str(gval)] * len(km),
                "Freezing.temperature": km["Freezing.temperature"].to_numpy(dtype=float),
                "mean_nm": mean_nm,
                "low_nm": low_nm,
                "up_nm": up_nm,
                "n_curves_at_temp": km["at_risk"].to_numpy(dtype=float),
                "n_curves_group": [curves_used] * len(km),
                "ci_method": ["kaplan_meier"] * len(km),
            }
        )
        out = out[
            np.isfinite(out["mean_nm"])
            & np.isfinite(out["low_nm"])
            & np.isfinite(out["up_nm"])
            & (out["mean_nm"] > 0)
            & (out["low_nm"] > 0)
            & (out["up_nm"] > 0)
        ].copy()
        if len(out) == 0:
            groups_skipped += 1
            continue

        rows.append(out)
        groups_done += 1

    if not rows:
        return pd.DataFrame(), (
            "Kaplan–Meier CI: no valid group summaries. "
            f"groups_skipped={groups_skipped}. Check FF availability/quality and selected filters."
        )

    out = pd.concat(rows, ignore_index=True)
    out = out.sort_values(["group", "Freezing.temperature"], ascending=[True, False]).reset_index(drop=True)
    status = (
        f"method=kaplan_meier | groups={groups_done} | rows={len(out)} | group_col={group_col} | "
        f"n0_est=auto-from-FF | CI=log-log KM (Whale et al., 2026)"
    )
    return out, status


def compute_mean_ci_curves(
    filtered_points: pd.DataFrame,
    cfg: MeanCIConfig | None = None,
) -> tuple[pd.DataFrame, str]:
    cfg = cfg or MeanCIConfig()
    ci_method = str(getattr(cfg, "ci_method", "legacy") or "legacy").strip().lower()
    if ci_method in {"kaplan_meier", "kaplan-meier", "km", "article"}:
        return _compute_mean_ci_curves_km(filtered_points, cfg)
    return _compute_mean_ci_curves_legacy(filtered_points, cfg)
