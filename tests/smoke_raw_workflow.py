#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.io_universal import read_table_from_path
from inaes_core.raw_workflow import (
    RawAnalyzeConfig,
    compute_analyzed_curves_from_raw,
    merge_analyzed_curve_tables,
    suggest_raw_column_mapping,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: RAW analyze + merge workflow core module")
    p.add_argument("--raw", type=Path, required=True, help="RAW-like table path")
    p.add_argument("--previous", type=Path, required=False, help="Previous analyzed file path")
    return p


def main() -> int:
    args = _parser().parse_args()
    raw_df = read_table_from_path(args.raw)
    mapping = suggest_raw_column_mapping(raw_df)
    if "Sample" not in mapping or "Freezing.temperature" not in mapping or "FF" not in mapping:
        raise SystemExit(f"Missing required RAW mapping suggestions. got={mapping}")

    cfg = RawAnalyzeConfig(
        map_sample=mapping.get("Sample"),
        map_temp=mapping.get("Freezing.temperature"),
        map_ff=mapping.get("FF"),
        map_size=mapping.get("Size"),
        map_location=mapping.get("Location"),
        map_control=mapping.get("Control"),
        map_dilution=mapping.get("Dilution.factor"),
        method="mass_extraction_nm",
        wash_volume_ml=400,
        sample_mass_g=10,
        n0=384,
        droplet_volume_ul=30,
    )
    analyzed_df, analyzed_status = compute_analyzed_curves_from_raw(raw_df, cfg)
    if len(analyzed_df) == 0:
        raise SystemExit("RAW analysis produced 0 rows.")
    print(f"OK analyzed rows={len(analyzed_df)} | {analyzed_status}")

    if args.previous:
        prev_df = read_table_from_path(args.previous)
        merged_df, merge_status = merge_analyzed_curve_tables(prev_df, analyzed_df)
        if len(merged_df) == 0:
            raise SystemExit("Merge produced 0 rows.")
        print(f"OK merged rows={len(merged_df)} | {merge_status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
