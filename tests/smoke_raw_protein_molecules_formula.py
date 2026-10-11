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

from inaes_core.raw_workflow import AVOGADRO_PER_MOL, RawAnalyzeConfig, compute_analyzed_curves_from_raw


def _analyze(method: str, **params: object) -> pd.DataFrame:
    raw = pd.DataFrame(
        {
            "Content": ["protein_1x", "protein_100x"],
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
        method=method,
        droplet_volume_ul=30.0,
        n0=384,
        **params,
    )
    out, status = compute_analyzed_curves_from_raw(raw, cfg)
    if len(out) != 2:
        raise SystemExit(f"Expected 2 rows, got {len(out)} | {status}")
    return out


def main() -> int:
    lam = -np.log1p(-0.5)
    # 1 mg/mL of a 100 kDa protein in 30 µL: (1e-3 g/mL / 1e5 g/mol) * 0.03 mL * N_A molecules.
    n_drop = (1e-3 / 1e5) * 0.03 * AVOGADRO_PER_MOL
    expected = np.array([lam * 1.0 / n_drop, lam * 100.0 / n_drop])

    by_mass = _analyze(
        "protein_molecules_nn",
        protein_input_mode="mass",
        protein_mass_conc_mg_per_ml=1.0,
        protein_molar_mass_kda=100.0,
    )
    got = by_mass["nm"].to_numpy(dtype=float)
    if not np.allclose(got, expected, rtol=1e-12, atol=0.0):
        raise SystemExit(f"Mass-input formula mismatch: got={got}, expected={expected}")
    if str(by_mass["normalization_units"].iloc[0]) != "molecule^-1":
        raise SystemExit(f"Unexpected units: {by_mass['normalization_units'].iloc[0]}")

    # The same solution expressed as molar concentration: 1e-3 g/mL / 1e5 g/mol = 10 µM.
    by_molar = _analyze("protein_molecules_nn", protein_input_mode="molar", protein_molar_conc_um=10.0)
    got_molar = by_molar["nm"].to_numpy(dtype=float)
    if not np.allclose(got_molar, expected, rtol=1e-12, atol=0.0):
        raise SystemExit(f"Molar-input formula mismatch: got={got_molar}, expected={expected}")

    # Per-molecule = per-mass (g^-1) * molar mass / Avogadro.
    by_mass_nm = _analyze("mass_concentration_nm", mass_conc_g_per_ml=1e-3)
    via_nm = by_mass_nm["nm"].to_numpy(dtype=float) * 1e5 / AVOGADRO_PER_MOL
    if not np.allclose(got, via_nm, rtol=1e-12, atol=0.0):
        raise SystemExit(f"Per-molecule vs per-mass mismatch: got={got}, via_nm={via_nm}")

    print("OK protein_molecules_nn matches hand formula, molar input and nM * M / N_A.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
