#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.io_universal import read_table_from_path
from inaes_core.raw_workflow import (
    RAW_VALI_METHOD_OPTIONS,
    RawAnalyzeConfig,
    compute_analyzed_curves_from_raw,
    suggest_raw_column_mapping,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: RAW normalization methods matrix")
    p.add_argument("--raw", type=Path, required=True, help="RAW-like table path")
    return p


def main() -> int:
    args = _parser().parse_args()
    raw_df = read_table_from_path(args.raw)
    mapping = suggest_raw_column_mapping(raw_df)
    for req in ("Sample", "Freezing.temperature", "FF"):
        if req not in mapping:
            raise SystemExit(f"Missing required RAW mapping suggestion: {req}. got={mapping}")

    base = dict(
        map_sample=mapping.get("Sample"),
        map_temp=mapping.get("Freezing.temperature"),
        map_ff=mapping.get("FF"),
        map_size=mapping.get("Size"),
        map_location=mapping.get("Location"),
        map_control=mapping.get("Control"),
        map_dilution=mapping.get("Dilution.factor"),
        auto_dilution_from_sample=True,
        use_size_grouping=True,
        use_location_grouping=True,
        auto_control_from_sample=True,
        control_detection_keywords="MilliQ,milli-q,blank,control,ctrl,mq",
        n0=384,
        droplet_volume_ul=30.0,
    )
    per_method = {
        "mass_concentration_nm": dict(mass_conc_g_per_ml=0.01),
        "liquid_volume": dict(),
        "mass_extraction_nm": dict(wash_volume_ml=400.0, sample_mass_g=10.0),
        "surface_area_direct": dict(area_per_drop_m2=1.0e-6),
        "surface_area_bet_from_mass": dict(mass_conc_g_per_ml=0.01, bet_area_m2_per_g=1.5),
        "cell_concentration": dict(cell_conc_per_ml=1.0e6),
        "air_washoff": dict(wash_volume_ml=20.0, air_filter_fraction_x=0.1, air_sampled_volume_L=200.0),
        "air_drop_on": dict(air_sampled_volume_L=200.0, filter_exposed_area=1.0, droplet_footprint_area=0.01),
        "custom_dose": dict(custom_dose_per_drop=0.5),
    }

    for _label, method in RAW_VALI_METHOD_OPTIONS:
        method = str(method)
        cfg_kwargs = dict(base)
        cfg_kwargs.update(per_method.get(method, {}))
        cfg_kwargs["method"] = method
        cfg = RawAnalyzeConfig(**cfg_kwargs)
        out, status = compute_analyzed_curves_from_raw(raw_df, cfg)
        if len(out) == 0:
            raise SystemExit(f"Method {method}: produced 0 rows.")
        nm = out.get("nm")
        finite_nm = int(np.isfinite(nm).sum()) if nm is not None else 0
        if finite_nm == 0:
            raise SystemExit(f"Method {method}: produced no finite nm values.")
        print(f"OK method={method} rows={len(out)} finite_nm={finite_nm} | {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
