#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.raw_workflow import RawAnalyzeConfig, compute_analyzed_curves_from_raw


def _analyze(*, droplet_volume_ul: float) -> pd.DataFrame:
    raw = pd.DataFrame(
        {
            "Content": ["sample_1x", "sample_100x"],
            "Freeze Temp": [-10.0, -10.0],
            "Frozen Fraction": [0.5, 0.5],
            "Dilution.factor": [1.0, 100.0],
        }
    )
    cfg = RawAnalyzeConfig(
        map_sample="Content",
        map_temp="Freeze Temp",
        map_ff="Frozen Fraction",
        map_dilution="Dilution.factor",
        method="cell_concentration",
        cell_conc_per_ml=1.0e6,
        droplet_volume_ul=droplet_volume_ul,
        n0=384,
    )
    out, status = compute_analyzed_curves_from_raw(raw, cfg)
    if len(out) != 2:
        raise SystemExit(f"Expected 2 rows, got {len(out)} | {status}")
    return out


def main() -> int:
    out_30 = _analyze(droplet_volume_ul=30.0)
    lam = -np.log1p(-0.5)
    expected_d1 = lam * 1.0 / (1.0e6 * 0.03)
    expected_d100 = lam * 100.0 / (1.0e6 * 0.03)
    got = out_30["nm"].to_numpy(dtype=float)
    if not np.allclose(got, [expected_d1, expected_d100], rtol=1e-12, atol=0.0):
        raise SystemExit(f"30 uL formula mismatch: got={got}, expected={[expected_d1, expected_d100]}")
    if not np.isclose(got[1] / got[0], 100.0, rtol=1e-12):
        raise SystemExit(f"Dilution scaling mismatch: ratio={got[1] / got[0]}")

    out_50 = _analyze(droplet_volume_ul=50.0)
    expected_50_d100 = lam * 100.0 / (1.0e6 * 0.05)
    got_50_d100 = float(out_50["nm"].iloc[1])
    if not np.isclose(got_50_d100, expected_50_d100, rtol=1e-12, atol=0.0):
        raise SystemExit(f"50 uL formula mismatch: got={got_50_d100}, expected={expected_50_d100}")

    print("OK cell_concentration formula uses dilution and selected droplet volume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
