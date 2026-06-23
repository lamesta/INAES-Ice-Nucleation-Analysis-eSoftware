from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import re
import numpy as np
import pandas as pd
from scipy import stats


def _parse_relaxed_numeric_token(v: Any) -> float:
    """Parse numeric tokens with relaxed support for locale/scientific quirks.

    Examples supported:
    - 1.59.E-03 -> 1.59E-03
    - 1,59E-03  -> 1.59E-03
    - 100x      -> 100
    """
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.number)):
        try:
            x = float(v)
            return x if np.isfinite(x) else np.nan
        except Exception:
            return np.nan

    s = str(v).strip()
    if not s:
        return np.nan
    s = s.replace(" ", "")
    s = s.replace(",", ".")
    s = str(s).replace("−", "-")
    s = str(s).replace("–", "-")
    s = str(s).replace("—", "-")
    s = str(s).replace("·", ".")
    s = str(s).replace("∙", ".")
    s = str(s).replace("×", "x")
    # Common dilution token style: "10x"/"100X"
    s = s[:-1] if str(s).lower().endswith("x") else s
    # Legacy typo: decimal dot before exponent marker (e.g., 1.59.E-03)
    s = str(s)
    s = re.sub(r"(?i)\.([e][+\-]?\d+)$", r"\1", s)
    # Strip trailing punctuation often found in copied table exports.
    s = re.sub(r"[;:]+$", "", s)
    s = re.sub(r"\.$", "", s)
    try:
        x = float(s)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _coerce_numeric_series_relaxed(values: Any) -> pd.Series:
    s = pd.Series(values)
    out = pd.to_numeric(s, errors="coerce")
    bad = ~np.isfinite(out)
    if bad.any():
        fixed = s[bad].map(_parse_relaxed_numeric_token)
        out.loc[bad] = fixed
    return pd.to_numeric(out, errors="coerce")


def _is_nm_like_metric_name(name: Any) -> bool:
    s = str(name or "").strip().lower()
    if not s:
        return False
    return s == "nm" or s.startswith("nm") or s.startswith("n_m") or "_nm" in s


def _corr_label(method: str, x: np.ndarray, y: np.ndarray) -> str:
    method = str(method)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    if len(x) < 3:
        return "n<3"

    if method == "Spearman":
        r, p = stats.spearmanr(x, y)
        return f"rho={r:.2f}, p={p:.3g}"
    if method == "Pearson":
        r, p = stats.pearsonr(x, y)
        return f"r={r:.2f}, p={p:.3g}"
    if method == "Quadratic Fit":
        import statsmodels.api as sm

        X = np.column_stack([x, x**2])
        X = sm.add_constant(X)
        fit = sm.OLS(y, X).fit()
        return f"R²={fit.rsquared:.2f}, p={fit.f_pvalue:.3g}"
    return ""


def fit_curve_with_ci(
    method: str,
    x: np.ndarray,
    y: np.ndarray,
    x_grid: np.ndarray,
    *,
    fit_log_y: bool = False,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    import statsmodels.api as sm

    method = str(method)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    if len(x) < 4:
        return None, None, None

    if method in ["Spearman", "Pearson"]:
        y_fit = y.copy()
        if fit_log_y:
            pos = y_fit > 0
            x = x[pos]
            y_fit = y_fit[pos]
            if len(x) < 4:
                return None, None, None
            y_fit = np.log10(y_fit)
        X = sm.add_constant(x)
        fit = sm.OLS(y_fit, X).fit()
        Xg = sm.add_constant(x_grid)
        pred = fit.get_prediction(Xg).summary_frame(alpha=0.05)
        yhat = pred["mean"].to_numpy(dtype=float)
        ylo = pred["mean_ci_lower"].to_numpy(dtype=float)
        yhi = pred["mean_ci_upper"].to_numpy(dtype=float)
        if fit_log_y:
            yhat = np.power(10.0, yhat)
            ylo = np.power(10.0, ylo)
            yhi = np.power(10.0, yhi)
        return yhat, ylo, yhi

    if method == "Quadratic Fit":
        X = np.column_stack([x, x**2])
        X = sm.add_constant(X)
        fit = sm.OLS(y, X).fit()
        Xg = np.column_stack([x_grid, x_grid**2])
        Xg = sm.add_constant(Xg)
        pred = fit.get_prediction(Xg).summary_frame(alpha=0.05)
        return (
            pred["mean"].to_numpy(dtype=float),
            pred["mean_ci_lower"].to_numpy(dtype=float),
            pred["mean_ci_upper"].to_numpy(dtype=float),
        )

    if method == "GAM":
        try:
            from statsmodels.gam.api import BSplines, GLMGam

            x1 = x.reshape(-1, 1)
            bs = BSplines(x1, df=[6], degree=[3])
            gam = GLMGam(y, smoother=bs, exog=sm.add_constant(np.ones_like(y)))
            res = gam.fit()
            xg1 = x_grid.reshape(-1, 1)
            bs_g = BSplines(xg1, df=[6], degree=[3])
            gam_g = GLMGam(y, smoother=bs_g, exog=sm.add_constant(np.ones_like(y)))
            yhat = np.asarray(gam_g.predict(res.params), dtype=float)
            try:
                pr = gam_g.get_prediction(res.params)
                sf = pr.summary_frame(alpha=0.05)
                ylo = np.asarray(sf.get("mean_ci_lower", np.nan), dtype=float)
                yhi = np.asarray(sf.get("mean_ci_upper", np.nan), dtype=float)
                if ylo.shape == yhat.shape and yhi.shape == yhat.shape:
                    return yhat, ylo, yhi
            except Exception:
                pass
            return yhat, None, None
        except Exception:
            return None, None, None

    return None, None, None


@dataclass
class CorrelationConfig:
    method: str = "Spearman"
    x_col: str = ""
    y_choice: str = "nM10"
    selected_locations: list[str] | None = None


def available_correlation_options(metadata_with_nm: pd.DataFrame) -> dict[str, list[str]]:
    df = metadata_with_nm.copy()
    df.columns = [str(c) for c in df.columns]
    numeric_cols: list[str] = []
    for c in df.columns:
        s = _coerce_numeric_series_relaxed(df[c])
        if int(np.isfinite(s.to_numpy(dtype=float)).sum()) >= 2:
            numeric_cols.append(c)
    y_opts: list[str] = []
    if ("nM10_b5" in df.columns) or ("nM10_b02" in df.columns):
        y_opts.append("nM10")
    if ("nM15_b5" in df.columns) or ("nM15_b02" in df.columns):
        y_opts.append("nM15")
    # Keep legacy ordering parity: size-aware selectors first, then all numeric columns.
    y_opts += list(numeric_cols)
    x_opts = numeric_cols
    locs = sorted(df["Location"].dropna().astype(str).unique().tolist()) if "Location" in df.columns else []
    return {"x": x_opts, "y": y_opts, "locations": locs}


def prepare_correlation_frame(metadata_with_nm: pd.DataFrame, cfg: CorrelationConfig) -> tuple[pd.DataFrame, str]:
    d = metadata_with_nm.copy()
    d.columns = [str(c) for c in d.columns]

    if "Location" not in d.columns:
        d["Location"] = "(no Location)"
    if "Sample" not in d.columns:
        d["Sample"] = "(n/a)"
    d["Location"] = d["Location"].astype(str).replace("", "(no Location)")
    d["Sample"] = d["Sample"].astype(str)

    x_col = str(cfg.x_col or "").strip()
    if not x_col or x_col not in d.columns:
        raise ValueError(f"X column not found: {x_col}")

    if cfg.selected_locations:
        locs = [str(x).strip() for x in cfg.selected_locations if str(x).strip()]
        if locs:
            d = d[d["Location"].isin(locs)].copy()
    if len(d) == 0:
        raise ValueError("No rows after location filtering.")

    status = (
        f"rows={len(d)} | method={cfg.method} | X={x_col} | Y={cfg.y_choice} | "
        f"locations={d['Location'].nunique()}"
    )
    return d, status
