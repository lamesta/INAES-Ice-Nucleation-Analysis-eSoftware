from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd


ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "cp1252",
    "latin-1",
    "iso-8859-1",
    "mac_roman",
)


def _sniff_delimiter(sample_text: str) -> str | None:
    txt = (sample_text or "").replace("\x00", "")
    if not txt.strip():
        return None

    try:
        dialect = csv.Sniffer().sniff(txt[:65536], delimiters=[",", ";", "\t", "|"])
        return str(dialect.delimiter)
    except Exception:
        pass

    lines = [ln for ln in txt.splitlines() if ln.strip()][:20]
    if not lines:
        return None

    candidates = [",", ";", "\t", "|"]
    counts = {d: sum(ln.count(d) for ln in lines) for d in candidates}
    best = max(counts, key=counts.get)
    return best if counts.get(best, 0) > 0 else None


def _read_csv_with_fallback(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    sample_bytes = raw_bytes[:65536]

    guessed_sep_by_enc: dict[str, str] = {}
    for enc in ENCODING_CANDIDATES:
        try:
            guessed = _sniff_delimiter(sample_bytes.decode(enc))
            if guessed:
                guessed_sep_by_enc[enc] = guessed
        except Exception:
            continue

    best_df: pd.DataFrame | None = None
    best_score = -1.0
    errors: list[str] = []

    for enc in ENCODING_CANDIDATES:
        if name.endswith(".tsv"):
            sep_trials: list[str | None] = ["\t", None]
        else:
            sep_trials = []
            guessed = guessed_sep_by_enc.get(enc)
            if guessed:
                sep_trials.append(guessed)
            sep_trials.extend([",", ";", "\t", "|", None])
            seen: set[str | None] = set()
            sep_trials = [s for s in sep_trials if not (s in seen or seen.add(s))]

        for sep in sep_trials:
            try:
                bio = io.BytesIO(raw_bytes)
                if sep is None:
                    df = pd.read_csv(bio, sep=None, engine="python", encoding=enc)
                else:
                    df = pd.read_csv(bio, sep=sep, encoding=enc)

                n_rows, n_cols = int(df.shape[0]), int(df.shape[1])
                score = float(n_cols * 10) + min(float(n_rows), 1000.0) / 1000.0
                if guessed_sep_by_enc.get(enc) == sep and sep is not None:
                    score += 2.0

                if score > best_score:
                    best_score = score
                    best_df = df

                if n_cols >= 2 and (n_rows > 0 or any(str(c).strip() for c in df.columns)):
                    return df
            except Exception as exc:
                errors.append(f"encoding={enc}, sep={repr(sep)} -> {exc}")

    if best_df is not None:
        return best_df

    try:
        return pd.read_csv(io.BytesIO(raw_bytes), sep=None, engine="python")
    except Exception as exc:
        details = "; ".join(errors[:8])
        if len(errors) > 8:
            details += f"; ... (+{len(errors) - 8} more)"
        raise ValueError(
            "Could not decode/read table. Tried multiple encodings and separators. "
            f"First attempts: {details}"
        ) from exc


def read_table_from_path(path_like: str | Path) -> pd.DataFrame:
    path = Path(path_like)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        try:
            return pd.read_excel(path)
        except Exception as exc:
            raise ValueError(f"Could not read Excel file: {path.name}. {exc}") from exc

    data = path.read_bytes()
    return _read_csv_with_fallback(data, path.name)

