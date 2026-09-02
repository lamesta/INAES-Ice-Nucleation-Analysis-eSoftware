from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence

import numpy as np
import pandas as pd


@dataclass
class CurvesMappingConfig:
    map_sample: Any = None
    map_size: Any = None
    map_location: Any = None
    map_temp: Any = None
    map_nm: Any = None
    map_control: Any = None
    map_dilution: Any = None
    map_ff: Any = None

    use_size_grouping: bool = True
    size_single_label: str = "NoSizeGroup"
    manual_size_value: str = ""

    use_location_grouping: bool = True
    location_single_label: str = "SingleLocation"
    manual_location_value: str = ""

    auto_dilution_from_sample: bool = True
    auto_control_from_sample: bool = True
    control_detection_keywords: str = "MilliQ,milli-q,blank,control,ctrl,mq"


def _canon_col(c: str) -> str:
    c = str(c).strip().lower()
    c = re.sub(r"[\s\-\/]+", "_", c)
    c = re.sub(r"[^a-z0-9_\.]+", "", c)
    return c


def _clean_mapping_value(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    bad = {"none", "null", "nan", "skip", "__skip__", "<none>"}
    if s.lower() in bad:
        return None
    return s


def _parse_relaxed_numeric_token(v: Any) -> float:
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
    s = re.sub(r"(?i)x$", "", s)
    s = s.replace(",", ".")
    s = re.sub(r"(?i)\.([e][+\-]?\d+)$", r"\1", s)
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


def _expected_aliases() -> dict[str, list[str]]:
    return {
        "Sample": ["sample", "sample_id", "sample.name", "samplename", "sample_name"],
        "Size": ["size", "particle_size", "particlesize", "fraction", "batch"],
        "Location": ["location", "site", "sampling_site", "sampling site"],
        "Freezing.temperature": [
            "freezing.temperature",
            "freezing_temperature",
            "freezing temperature",
            "temperature",
            "t",
            "temp",
        ],
        "nm": ["nm", "n_m", "n_m_num", "n_m_num!", "nm_corrected", "nm.corrected", "nv", "n_v"],
        "Control": ["control", "is_control", "negative_control", "blank", "type", "sample_type", "kind"],
        "Dilution.factor": [
            "dilution.factor",
            "dilution_factor",
            "dilution",
            "dilutionfactor",
            "dilution factor",
        ],
        "FF": [
            "ff",
            "frozen_fraction",
            "frozenfraction",
            "frozen.fraction",
            "frozen fraction",
            "frozen_fraction_value",
        ],
    }


def suggest_curves_column_mapping(df: pd.DataFrame) -> dict[str, str]:
    aliases = _expected_aliases()
    cols = [str(c) for c in df.columns]
    canon_to_orig = {_canon_col(c): c for c in cols}
    suggestions: dict[str, str] = {}
    used_sources: set[str] = set()

    for expected, cands in aliases.items():
        if expected in cols:
            suggestions[expected] = expected
            used_sources.add(expected)
            continue
        for cand in cands:
            src = canon_to_orig.get(_canon_col(cand))
            if src and src not in used_sources:
                suggestions[expected] = src
                used_sources.add(src)
                break
    return suggestions


def _split_sample_name_components(sample_value: Any) -> tuple[str, str | None, float | None]:
    """Return (sample_base, size_from_name, trailing_dilution_from_name).

    The trailing dilution is recognized only from the last token, and only
    when it is a power of ten (10, 100, 1000, ... with no upper bound),
    with an optional trailing 'x' (e.g. '100x').
    """
    s = str(sample_value or "").strip()
    if not s:
        return "", None, None

    tokens = [t for t in re.split(r"[_\-\s]+", s) if str(t).strip()]
    if not tokens:
        return s, None, None

    dilution: float | None = None
    m_d = re.fullmatch(r"(?i)(10+)x?", str(tokens[-1]).strip())
    if m_d:
        try:
            dilution = float(m_d.group(1))
        except Exception:
            dilution = None
        tokens = tokens[:-1]

    size_val: str | None = None
    size_idx: int | None = None
    allowed_sizes = {"b_02_m", "b_5_m", "b_63_m"}
    for i in range(len(tokens) - 1, -1, -1):
        cand = _normalize_size_label(tokens[i])
        if cand in allowed_sizes:
            size_val = cand
            size_idx = i
            break

    if size_idx is not None:
        tokens = [t for j, t in enumerate(tokens) if j != size_idx]

    base = "_".join([str(t).strip() for t in tokens if str(t).strip()]).strip("_")
    if not base:
        base = s
    return base, size_val, dilution


def _parse_dilution_from_sample(sample_value: Any) -> float | None:
    _, _, d = _split_sample_name_components(sample_value)
    if d is None or not np.isfinite(d) or d <= 0:
        return None
    return float(d)


def _normalize_size_label(size_value: Any) -> str | None:
    s = str(size_value or "").strip()
    if not s or s.lower() in {"nan", "none"}:
        return None

    s_compact = re.sub(r"\s+", "", s).lower().replace("-", "_")
    if re.fullmatch(r"b_?0?2(?:_\d+(?:\.\d+)?)?_?m", s_compact) or re.fullmatch(r"b0?2(?:_\d+(?:\.\d+)?)?m", s_compact):
        return "b_02_m"
    if re.fullmatch(r"b_?0?5(?:_\d+(?:\.\d+)?)?_?m", s_compact) or re.fullmatch(r"b0?5(?:_\d+(?:\.\d+)?)?m", s_compact):
        return "b_5_m"

    c = _canon_col(s)
    alias_map = {
        "b5": "b_5_m",
        "b_5": "b_5_m",
        "b_5_m": "b_5_m",
        "b05": "b_5_m",
        "b_05": "b_5_m",
        "b02": "b_02_m",
        "b_02": "b_02_m",
        "b0_2": "b_02_m",
        "b_0_2": "b_02_m",
        "b0.2": "b_02_m",
        "b_0.2": "b_02_m",
        "b_02_m": "b_02_m",
        "b63": "b_63_m",
        "b_63": "b_63_m",
        "b_63_m": "b_63_m",
    }
    if c in alias_map:
        return alias_map[c]

    m = re.search(r"(?i)\bb[\s_\-]*([0-9]+(?:[._][0-9]+)?)\b", s)
    if not m:
        return s
    token = m.group(1).replace(".", "_")
    if token in {"5", "05"}:
        return "b_5_m"
    if token in {"02", "0_2"}:
        return "b_02_m"
    if token in {"63"}:
        return "b_63_m"
    token = re.sub(r"[^0-9_]+", "", token).strip("_")
    if not token:
        return s
    return f"b_{token}_m"


def _parse_size_from_sample(sample_value: Any) -> str | None:
    _, size_val, _ = _split_sample_name_components(sample_value)
    if size_val:
        return size_val

    s = str(sample_value or "").strip()
    if not s:
        return None
    m = re.search(
        r"(?i)(?:^|[^a-z0-9])b[\s_\-]*([0-9]+(?:[._][0-9]+)?)(?:\s*(?:um|µm|m))?(?:$|[^a-z0-9])",
        s,
    )
    if not m:
        return None
    return _normalize_size_label(f"b{m.group(1)}")


def _strip_leading_control_keyword_from_sample(sample_value: Any, keywords: Sequence[str]) -> str:
    original = str(sample_value or "").strip()
    if not original or not keywords:
        return original

    uniq_keys: list[str] = []
    seen: set[str] = set()
    for k in keywords:
        ks = str(k or "").strip()
        if not ks:
            continue
        ck = _canon_col(ks)
        if ck in seen:
            continue
        seen.add(ck)
        uniq_keys.append(ks)

    uniq_keys = sorted(
        uniq_keys,
        key=lambda x: len(re.sub(r"[\s_\-]+", "", str(x or ""))),
        reverse=True,
    )

    s = original
    changed = True
    while changed and s:
        changed = False
        for kw in uniq_keys:
            parts = [p for p in re.split(r"[\s_\-]+", str(kw)) if p]
            if not parts:
                continue
            key_pat = r"[\s_\-]*".join(re.escape(p) for p in parts)
            pat = rf"(?i)^\s*{key_pat}(?:(?=[\s_\-])[\s_\-]+|$)"
            m = re.match(pat, s)
            if not m:
                continue
            tail = s[m.end():].strip(" _-\t")
            if not tail:
                changed = False
                break
            s = tail
            changed = True
            break

    return s if s else original


def _parse_control_keywords(raw_keywords: Any) -> list[str]:
    if raw_keywords is None:
        return []
    txt = str(raw_keywords).strip()
    if not txt:
        return []
    if re.search(r"[,\n;|]", txt):
        parts = re.split(r"[,\n;|]+", txt)
    else:
        parts = re.split(r"\s+", txt)
    out: list[str] = []
    for p in parts:
        k = p.strip().lower()
        if k and k not in out:
            out.append(k)
    return out


def _detect_control_from_sample(sample_value: Any, keywords: Sequence[str]) -> bool:
    s = str(sample_value or "").strip().lower()
    if not s or not keywords:
        return False
    compact = re.sub(r"[\s_\-]+", "", s)
    for k in keywords:
        kk = str(k or "").strip().lower()
        if not kk:
            continue
        kk_compact = re.sub(r"[\s_\-]+", "", kk)
        if kk in s or (kk_compact and kk_compact in compact):
            return True
    return False


def _coerce_control_text(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    c = _canon_col(s)
    yes_tokens = {"yes", "y", "true", "1", "control", "ctrl", "blank", "neg", "negative"}
    no_tokens = {"no", "n", "false", "0", "sample"}
    if c in yes_tokens:
        return "Yes"
    if c in no_tokens:
        return "No"
    s_l = str(s).strip().lower()
    if re.search(r"\b(yes|true)\b", s_l):
        return "Yes"
    if re.search(r"\b(no|false)\b", s_l):
        return "No"
    if re.search(r"\b(control|ctrl|blank|neg|negative)\b", s_l):
        return "Yes"
    return s


def _normalize_control_plot_label(control_value: Any, sample_value: Any | None = None) -> str:
    base = _coerce_control_text(control_value)
    if base in {"Yes", "No"}:
        return str(base)

    joined = " ".join([str(control_value or ""), str(sample_value or "")]).strip().lower()
    compact = re.sub(r"[\s_\-]+", "", joined)
    positive_keywords = ["milliq", "milli-q", "blank", "control", "ctrl"]
    for kw in positive_keywords:
        kwc = re.sub(r"[\s_\-]+", "", kw.lower())
        if kw.lower() in joined or kwc in compact:
            return "Yes"

    if base is None:
        return "No"
    return str(base)


def _repair_control_labels_with_sample_keywords(
    control_values: Sequence[Any],
    sample_values: Sequence[Any],
    *,
    keywords: Any = None,
    allow_global_inversion_fix: bool = True,
) -> tuple[pd.Series, list[str]]:
    control_in = pd.Series(control_values, dtype=object)
    sample_in = pd.Series(sample_values, dtype=object)
    n = min(len(control_in), len(sample_in))
    if n == 0:
        return pd.Series(dtype=object), []

    control_in = control_in.iloc[:n]
    idx = control_in.index
    sample_series = pd.Series(sample_in.iloc[:n].tolist(), index=idx, dtype=object)
    ctrl_series = pd.Series(
        [_normalize_control_plot_label(c, s) for c, s in zip(control_in.tolist(), sample_series.tolist())],
        index=idx,
        dtype=object,
    )
    notes: list[str] = []
    if len(ctrl_series) == 0:
        return ctrl_series.astype(str), notes

    keys = _parse_control_keywords(
        keywords if keywords is not None else "MilliQ,milli-q,blank,control,ctrl,mq"
    )
    if not keys:
        return ctrl_series.fillna("No").astype(str), notes

    sample_detect = sample_series.map(lambda s: _detect_control_from_sample(s, keys))
    comparable = ctrl_series.isin(["Yes", "No"])
    det_mask = comparable & sample_detect
    non_det_mask = comparable & (~sample_detect)

    if allow_global_inversion_fix and det_mask.any() and non_det_mask.any():
        yes_det = int((ctrl_series[det_mask] == "Yes").sum())
        no_det = int((ctrl_series[det_mask] == "No").sum())
        yes_non = int((ctrl_series[non_det_mask] == "Yes").sum())
        no_non = int((ctrl_series[non_det_mask] == "No").sum())
        if yes_det == 0 and no_det > 0 and yes_non > 0 and yes_non >= no_non:
            ctrl_series = ctrl_series.map(lambda v: "No" if v == "Yes" else ("Yes" if v == "No" else v))
            notes.append("Detected inverted Control Yes/No semantics vs Sample-name control keywords; auto-corrected labels.")

    force_yes_mask = sample_detect & (ctrl_series.astype(str) != "Yes")
    if force_yes_mask.any():
        ctrl_series.loc[force_yes_mask] = "Yes"
        notes.append(
            f"Forced Control='Yes' for {int(force_yes_mask.sum())} rows based on Sample-name control keywords (e.g. MilliQ/blank)."
        )

    return ctrl_series.fillna("No").astype(str), notes


def _build_sample_id_for_curves(df: pd.DataFrame) -> pd.Series:
    return (
        df["Sample"].astype(str)
        + "|" + df["Size"].astype(str)
        + "|" + df["Location"].astype(str)
        + "|" + df["Dilution.factor"].astype(str)
    )


def standardize_curves_df(
    df_raw: pd.DataFrame,
    config: CurvesMappingConfig | None = None,
) -> tuple[pd.DataFrame, list[str], dict[str, str]]:
    warnings: list[str] = []
    df = df_raw.copy()
    cfg = config or CurvesMappingConfig()
    sugg = suggest_curves_column_mapping(df)

    raw_map = {
        "Sample": cfg.map_sample,
        "Size": cfg.map_size,
        "Location": cfg.map_location,
        "Freezing.temperature": cfg.map_temp,
        "nm": cfg.map_nm,
        "Control": cfg.map_control,
        "Dilution.factor": cfg.map_dilution,
        "FF": cfg.map_ff,
    }

    resolved: dict[str, str] = {}
    for expected in raw_map.keys():
        src = _clean_mapping_value(raw_map.get(expected))
        if not src:
            src = sugg.get(expected)
        if not src and expected in df.columns:
            src = expected
        if src and src in df.columns:
            resolved[expected] = src

    for field in ["Sample", "Freezing.temperature", "nm"]:
        src = resolved.get(field)
        if not src:
            raise ValueError(
                f"Select or map source column for '{field}'. Available columns: {list(df.columns)}"
            )

    inv: dict[str, list[str]] = {}
    for expected, src in resolved.items():
        inv.setdefault(src, []).append(expected)
    dup_sources = {src: tgts for src, tgts in inv.items() if len(tgts) > 1}
    if dup_sources:
        dup_txt = "; ".join([f"{src} -> {tgts}" for src, tgts in dup_sources.items()])
        raise ValueError(
            f"Curves mapping duplicates detected (same source used for multiple fields): {dup_txt}"
        )

    for expected, src in resolved.items():
        df[expected] = df[src]

    df["Sample"] = df["Sample"].astype(str).str.strip()
    df["Freezing.temperature"] = _coerce_numeric_series_relaxed(df["Freezing.temperature"])
    df["nm"] = _coerce_numeric_series_relaxed(df["nm"])

    before = len(df)
    # Keep rows with missing nm so FF panel can still show controls/FF-only rows.
    df = df.dropna(subset=["Sample", "Freezing.temperature"]).copy()
    df = df[df["Sample"].astype(str).str.len() > 0].copy()
    dropped = before - len(df)
    if dropped > 0:
        warnings.append(
            f"Dropped {dropped} rows with missing required values (Sample, Freezing.temperature)."
        )
    if len(df) == 0:
        raise ValueError("No valid rows remain after cleaning core fields.")

    finite_temp_mask = np.isfinite(df["Freezing.temperature"])
    if (~finite_temp_mask).any():
        bad_n = int((~finite_temp_mask).sum())
        df = df.loc[finite_temp_mask].copy()
        warnings.append(f"Dropped {bad_n} rows with non-finite temperature values.")
    if len(df) == 0:
        raise ValueError("No valid finite rows remain after numeric cleaning.")

    nm_nonfinite_mask = df["nm"].notna() & (~np.isfinite(df["nm"]))
    if nm_nonfinite_mask.any():
        bad_nm = int(nm_nonfinite_mask.sum())
        df.loc[nm_nonfinite_mask, "nm"] = np.nan
        warnings.append(f"Set {bad_nm} non-finite nm values to NA (rows kept for FF/control handling).")

    sample_series = df["Sample"].astype(str).copy()
    sample_series_for_detection = sample_series.copy()
    parsed_size = sample_series.map(_parse_size_from_sample)
    parsed_dil = sample_series.map(_parse_dilution_from_sample)
    use_size_grouping_b = bool(cfg.use_size_grouping)
    use_location_grouping_b = bool(cfg.use_location_grouping)

    manual_size_raw = str(cfg.manual_size_value or "").strip()
    manual_size_norm = _normalize_size_label(manual_size_raw) if manual_size_raw else None
    if manual_size_raw and not manual_size_norm:
        manual_size_norm = manual_size_raw
    size_single_label_txt = str(cfg.size_single_label or "").strip() or "SingleGroup"
    size_mapped = "Size" in resolved

    if not use_size_grouping_b:
        df["Size"] = "b_5_m"
        df["Size.group.label"] = size_single_label_txt
        warnings.append(
            f"Size grouping disabled; all rows mapped to pseudo-size 'b_5_m' (label: '{size_single_label_txt}')."
        )
    else:
        if not size_mapped:
            if manual_size_norm:
                df["Size"] = manual_size_norm
                warnings.append(
                    f"Size column not mapped; using manual size '{manual_size_norm}' for all rows."
                )
            else:
                df["Size"] = parsed_size
                n_parsed = int(parsed_size.notna().sum())
                if n_parsed > 0:
                    warnings.append(
                        f"Size column not mapped; inferred Size from Sample in {n_parsed} rows."
                    )
                else:
                    df["Size"] = "b_5_m"
                    warnings.append(
                        "Size column not mapped and no size pattern found in Sample; using pseudo-size 'b_5_m'."
                    )
        else:
            df["Size"] = df["Size"].map(_normalize_size_label)

        missing_size = (
            df["Size"].isna()
            | (df["Size"].astype(str).str.strip() == "")
            | (df["Size"].astype(str).str.lower() == "nan")
        )
        if missing_size.any():
            if manual_size_norm:
                df.loc[missing_size, "Size"] = manual_size_norm
                warnings.append(
                    f"Filled {int(missing_size.sum())} missing Size values with '{manual_size_norm}'."
                )
            else:
                fill_mask = missing_size & parsed_size.notna()
                if fill_mask.any():
                    df.loc[fill_mask, "Size"] = parsed_size.loc[fill_mask]
                    warnings.append(
                        f"Filled {int(fill_mask.sum())} missing Size values from Sample parsing."
                    )
                still_missing = (
                    df["Size"].isna()
                    | (df["Size"].astype(str).str.strip() == "")
                    | (df["Size"].astype(str).str.lower() == "nan")
                )
                if still_missing.any():
                    df.loc[still_missing, "Size"] = "b_5_m"
                    warnings.append(
                        f"Filled {int(still_missing.sum())} remaining missing Size values with pseudo-size 'b_5_m'."
                    )

    manual_loc = str(cfg.manual_location_value or "").strip()
    loc_single_label = str(cfg.location_single_label or "").strip() or "(single_location)"
    loc_mapped = "Location" in resolved

    if not use_location_grouping_b:
        df["Location"] = loc_single_label
        warnings.append(f"Location grouping disabled; all rows assigned to '{loc_single_label}'.")
    else:
        if not loc_mapped:
            if manual_loc:
                df["Location"] = manual_loc
                warnings.append(
                    f"Location column not mapped; using manual location '{manual_loc}' for all rows."
                )
            else:
                df["Location"] = "(unknown)"
                warnings.append("Location column not mapped; defaulted to '(unknown)'.")
        else:
            df["Location"] = df["Location"].astype(str).replace({"nan": "(unknown)"}).fillna("(unknown)")
            loc_missing = (df["Location"].astype(str).str.strip() == "") | (df["Location"].astype(str) == "(unknown)")
            if manual_loc and loc_missing.any():
                df.loc[loc_missing, "Location"] = manual_loc
                warnings.append(
                    f"Filled {int(loc_missing.sum())} missing Location values with '{manual_loc}'."
                )

    control_keywords = _parse_control_keywords(cfg.control_detection_keywords)
    if control_keywords:
        sample_group_series = sample_series.map(
            lambda s: _strip_leading_control_keyword_from_sample(s, control_keywords)
        )
        sample_group_series = sample_group_series.where(
            sample_group_series.astype(str).str.strip() != "",
            sample_series,
        ).astype(str)
        sample_norm_mask = sample_group_series.astype(str) != sample_series.astype(str)
        if sample_norm_mask.any():
            changed_n = int(sample_norm_mask.sum())
            examples = (
                pd.DataFrame(
                    {
                        "orig": sample_series.loc[sample_norm_mask].astype(str).to_numpy(),
                        "norm": sample_group_series.loc[sample_norm_mask].astype(str).to_numpy(),
                    }
                )
                .drop_duplicates()
                .head(4)
                .apply(lambda r: f"{r['orig']} -> {r['norm']}", axis=1)
                .tolist()
            )
            warnings.append(
                "Normalized Sample by removing leading control keyword prefix "
                f"in {changed_n} rows."
                + (f" Examples: {examples}" if examples else "")
            )
        df["Sample"] = sample_group_series

    control_detected = sample_series_for_detection.map(
        lambda s: _detect_control_from_sample(s, control_keywords)
    )
    control_detected_in_control_col = (
        df["Control"].astype(str).map(lambda s: _detect_control_from_sample(s, control_keywords))
        if "Control" in resolved
        else pd.Series(False, index=df.index)
    )
    control_detected_any = control_detected | control_detected_in_control_col

    if "Control" not in resolved:
        if bool(cfg.auto_control_from_sample):
            df["Control"] = np.where(control_detected_any, "Yes", "No")
            warnings.append(
                f"Control column not mapped; inferred Control from keywords ({int(control_detected_any.sum())} rows as control)."
            )
        else:
            df["Control"] = "No"
            warnings.append("Control column not mapped; defaulted to 'No'.")
    else:
        ctrl = df["Control"].map(_coerce_control_text)
        if bool(cfg.auto_control_from_sample):
            fill_mask = ctrl.isna() & control_detected_any
            if fill_mask.any():
                ctrl = ctrl.where(~fill_mask, "Yes")
                warnings.append(
                    f"Filled {int(fill_mask.sum())} missing Control values from keyword detection."
                )
            ctrl_repaired, ctrl_notes = _repair_control_labels_with_sample_keywords(
                ctrl.fillna("No").astype(str),
                sample_series_for_detection,
                keywords=control_keywords,
            )
            ctrl_repaired = ctrl_repaired.astype(str)
            force_yes_from_control_col = control_detected_in_control_col & (ctrl_repaired != "Yes")
            if force_yes_from_control_col.any():
                ctrl_repaired.loc[force_yes_from_control_col] = "Yes"
                warnings.append(
                    f"Forced Control='Yes' for {int(force_yes_from_control_col.sum())} rows based on control-column keywords."
                )
            df["Control"] = ctrl_repaired
            warnings.extend(ctrl_notes)
        else:
            df["Control"] = ctrl.fillna("No").astype(str)

    if "Dilution.factor" in resolved:
        dil_num = _coerce_numeric_series_relaxed(df["Dilution.factor"])
    else:
        dil_num = pd.Series(np.nan, index=df.index, dtype=float)
    dil_num = pd.to_numeric(dil_num, errors="coerce")
    if bool(cfg.auto_dilution_from_sample):
        parsed_dil_num = pd.to_numeric(parsed_dil, errors="coerce")
        dil_num = dil_num.where(dil_num.notna(), parsed_dil_num)
    dil_num = pd.to_numeric(dil_num, errors="coerce").fillna(1.0)
    bad_dil = (~np.isfinite(dil_num.to_numpy(dtype=float))) | (dil_num <= 0)
    if bad_dil.any():
        warnings.append(f"{int(bad_dil.sum())} invalid dilution values reset to 1.")
        dil_num = dil_num.where(~bad_dil, 1.0)
    df["Dilution.factor"] = pd.to_numeric(dil_num, errors="coerce").fillna(1.0).astype(float)

    if "FF" in resolved:
        df["FF"] = _coerce_numeric_series_relaxed(df["FF"])

    out = pd.DataFrame(index=df.index)
    out["Sample"] = df["Sample"].astype(str)
    out["Size"] = df["Size"].astype(str)
    out["Freezing.temperature"] = _coerce_numeric_series_relaxed(df["Freezing.temperature"])
    out["nm"] = _coerce_numeric_series_relaxed(df["nm"])
    out["Control"] = df["Control"].astype(str)
    out["Dilution.factor"] = _coerce_numeric_series_relaxed(df["Dilution.factor"]).fillna(1.0)
    out["Location"] = df["Location"].astype(str)
    if "FF" in df.columns:
        out["FF"] = _coerce_numeric_series_relaxed(df["FF"])
    out["Sample_ID"] = _build_sample_id_for_curves(out)
    out["Sample.name"] = sample_series_for_detection.astype(str)
    out["Dilution"] = out["Dilution.factor"]

    return out, warnings, resolved

