from __future__ import annotations

from typing import Any
import threading

import numpy as np
import pandas as pd

_LOESS_CALL_LOCK = threading.Lock()
_METADATA_WITH_NM_LOCK = threading.Lock()


def _loess_predict(x: np.ndarray, y: np.ndarray, x_pred: np.ndarray, span: float = 0.1) -> np.ndarray:
    # LOESS backends can crash when called concurrently from multiple QThreads.
    # We serialize calls to keep metadata_with_nm computation stable during
    # rapid source/metadata switches.
    with _LOESS_CALL_LOCK:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        x_pred = np.asarray(x_pred, dtype=float)
        idx = np.argsort(x)
        x_s, y_s = x[idx], y[idx]

        try:
            from skmisc.loess import loess  # type: ignore

            model = loess(x_s, y_s, span=float(span), degree=2)
            model.fit()
            pred = model.predict(x_pred)
            return np.asarray(pred.values, dtype=float)
        except Exception:
            pass

        from statsmodels.nonparametric.smoothers_lowess import lowess

        sm = lowess(y_s, x_s, frac=float(span), it=0, return_sorted=True)
        x_sm, y_sm = sm[:, 0], sm[:, 1]
        return np.interp(x_pred, x_sm, y_sm, left=np.nan, right=np.nan)


def _unique_numeric_temps(values: Any) -> list[float]:
    if values is None:
        return []
    if isinstance(values, (list, tuple, np.ndarray, pd.Series, set)):
        raw = list(values)
    else:
        raw = [values]

    out: list[float] = []
    for v in raw:
        try:
            t = float(v)
        except Exception:
            continue
        if not np.isfinite(t):
            continue
        if any(np.isclose(t, u, atol=1e-9) for u in out):
            continue
        out.append(t)
    return out


def _nm_metric_name_for_temp(temp_c: float) -> str:
    t = float(temp_c)
    if np.isclose(t, -10.0, atol=1e-9):
        return "nM10"
    if np.isclose(t, -15.0, atol=1e-9):
        return "nM15"
    a = abs(t)
    if np.isclose(a, round(a), atol=1e-9):
        mag = str(int(round(a)))
    else:
        mag = f"{a:.3f}".rstrip("0").rstrip(".").replace(".", "_")
    if t > 0 and not np.isclose(t, 0.0, atol=1e-9):
        return f"nMpos{mag}"
    return f"nM{mag}"


def _canon_size(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return ""
    s = s.replace("-", "_")
    if s in {"b_5_m", "b5", "b_5", "b05", "b_05", "b05_m", "b_05_m"}:
        return "b_5_m"
    if s in {"b_02_m", "b02", "b_02", "b2", "b_2", "b_2_m"}:
        return "b_02_m"
    return str(v)


def _compute_metadata_with_nm_impl(
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    *,
    span: float = 0.1,
    min_points: int = 10,
    trim_q_low: float = 0.05,
    trim_q_high: float = 0.95,
    sample_col: str = "Sample",
    size_col: str = "Size",
    temp_col: str = "Freezing.temperature",
    nm_col: str = "nm",
    custom_temps: Any | None = None,
) -> tuple[pd.DataFrame, str]:
    for c in [sample_col, size_col, temp_col, nm_col]:
        if c not in curves_df.columns:
            raise ValueError(f"Curves file missing required column: {c}")
    if sample_col not in metadata_df.columns:
        raise ValueError(f"Metadata file missing required column: {sample_col}")

    df = curves_df.copy()
    df[nm_col] = pd.to_numeric(df[nm_col], errors="coerce")
    df[temp_col] = pd.to_numeric(df[temp_col], errors="coerce")
    df[sample_col] = df[sample_col].astype(str)
    df[size_col] = df[size_col].map(_canon_size)
    df = df.dropna(subset=[nm_col, temp_col, sample_col, size_col]).copy()
    df = df[np.isfinite(df[nm_col]) & np.isfinite(df[temp_col])].copy()

    if len(df) == 0:
        out = metadata_df.copy()
        for c in ["nM10_b5", "nM15_b5", "nM10_b02", "nM15_b02"]:
            if c not in out.columns:
                out[c] = np.nan
        return out, "No valid curves rows after numeric cleaning."

    q = df.groupby(sample_col)[nm_col].quantile([trim_q_low, trim_q_high]).unstack()
    q.columns = ["q_low", "q_high"]
    df = df.merge(q, left_on=sample_col, right_index=True, how="left")
    df = df[(df[nm_col] >= df["q_low"]) & (df[nm_col] <= df["q_high"])].drop(columns=["q_low", "q_high"])

    base_temps = [-10.0, -15.0]
    target_temps = _unique_numeric_temps(base_temps + _unique_numeric_temps(custom_temps))
    target_metrics = [_nm_metric_name_for_temp(t) for t in target_temps]

    rows: list[dict[str, Any]] = []
    for (sample, size), g in df.groupby([sample_col, size_col], sort=False):
        g2 = g.dropna(subset=[nm_col, temp_col]).copy()
        if len(g2) < int(min_points):
            continue
        x = g2[temp_col].to_numpy(dtype=float)
        y = g2[nm_col].to_numpy(dtype=float)
        pred = _loess_predict(x, y, np.array(target_temps, dtype=float), span=float(span))
        one: dict[str, Any] = {sample_col: sample, size_col: size, "n_points": int(len(g2))}
        for m, val in zip(target_metrics, pred):
            v = float(val)
            if np.isfinite(v) and v < 0:
                v = np.nan
            one[m] = v
        rows.append(one)

    nm_df = pd.DataFrame(rows)
    out = metadata_df.copy()

    size_suffix = [("b_5_m", "b5"), ("b_02_m", "b02")]
    target_cols = [f"{m}_{sx}" for m in target_metrics for _, sx in size_suffix]
    if nm_df.empty:
        for c in target_cols:
            if c not in out.columns:
                out[c] = np.nan
        return out, "No sample-size groups with enough points for LOESS prediction."

    metric_cols = [c for c in target_metrics if c in nm_df.columns]
    for size_val, suffix in size_suffix:
        sub = nm_df[nm_df[size_col] == size_val][[sample_col] + metric_cols].copy()
        renamed = {m: f"{m}_{suffix}" for m in metric_cols}
        sub = sub.rename(columns=renamed)
        out = out.merge(sub, on=sample_col, how="left", suffixes=("_x", "_y"))
        for base in renamed.values():
            col_x = f"{base}_x" if f"{base}_x" in out.columns else None
            col_y = f"{base}_y" if f"{base}_y" in out.columns else None
            if base not in out.columns:
                if col_y and col_x:
                    out[base] = out[col_y].combine_first(out[col_x])
                elif col_y:
                    out[base] = out[col_y]
                elif col_x:
                    out[base] = out[col_x]
            else:
                if col_y:
                    out[base] = out[base].combine_first(out[col_y])
                if col_x:
                    out[base] = out[base].combine_first(out[col_x])
            drops = [c for c in [col_x, col_y] if c]
            if drops:
                out = out.drop(columns=drops)

    for c in target_cols:
        if c not in out.columns:
            out[c] = np.nan

    status = (
        f"metadata_with_nm rows={len(out)} | curves_in={len(curves_df)} | "
        f"curves_used={len(df)} | groups={len(nm_df)} | span={span}"
    )
    return out, status


def compute_metadata_with_nm(
    curves_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    *,
    span: float = 0.1,
    min_points: int = 10,
    trim_q_low: float = 0.05,
    trim_q_high: float = 0.95,
    sample_col: str = "Sample",
    size_col: str = "Size",
    temp_col: str = "Freezing.temperature",
    nm_col: str = "nm",
    custom_temps: Any | None = None,
) -> tuple[pd.DataFrame, str]:
    # Serialize full metadata_with_nm computation to avoid hard crashes during
    # concurrent refreshes (e.g., rapid metadata/source changes across tabs).
    with _METADATA_WITH_NM_LOCK:
        return _compute_metadata_with_nm_impl(
            curves_df=curves_df,
            metadata_df=metadata_df,
            span=span,
            min_points=min_points,
            trim_q_low=trim_q_low,
            trim_q_high=trim_q_high,
            sample_col=sample_col,
            size_col=size_col,
            temp_col=temp_col,
            nm_col=nm_col,
            custom_temps=custom_temps,
        )
