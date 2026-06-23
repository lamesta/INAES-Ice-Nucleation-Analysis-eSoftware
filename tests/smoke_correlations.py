from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.correlations import CorrelationConfig, available_correlation_options, prepare_correlation_frame
from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df
from inaes_core.io_universal import read_table_from_path
from inaes_core.metadata_nm import compute_metadata_with_nm


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: correlations core module")
    p.add_argument("curves", type=Path, help="Path to curves-like file")
    p.add_argument("metadata", type=Path, help="Path to metadata-like file")
    p.add_argument("--map-sample", default=None)
    p.add_argument("--map-temp", default=None)
    p.add_argument("--map-nm", default=None)
    p.add_argument("--map-control", default=None)
    p.add_argument("--map-dilution", default=None)
    p.add_argument("--map-ff", default=None)
    args = p.parse_args()

    raw_curves = read_table_from_path(args.curves)
    raw_meta = read_table_from_path(args.metadata)
    cfg = CurvesMappingConfig(
        map_sample=args.map_sample,
        map_temp=args.map_temp,
        map_nm=args.map_nm,
        map_control=args.map_control,
        map_dilution=args.map_dilution,
        map_ff=args.map_ff,
        auto_control_from_sample=True,
        auto_dilution_from_sample=True,
        manual_size_value="b_5_m",
    )
    curves, warnings, resolved = standardize_curves_df(raw_curves, cfg)
    print("resolved:", resolved)
    print("warnings:", len(warnings))

    nm_df, nm_status = compute_metadata_with_nm(curves, raw_meta)
    print("metadata_with_nm:", nm_status)
    print("nm_shape:", nm_df.shape)

    opts = available_correlation_options(nm_df)
    x_col = "GenLatitude" if "GenLatitude" in opts.get("x", []) else (opts.get("x", [None])[0])
    y_col = "nM10" if "nM10" in opts.get("y", []) else (opts.get("y", [None])[0])
    locs = opts.get("locations", [])
    print("x_count:", len(opts.get("x", [])), "| y_count:", len(opts.get("y", [])), "| loc_count:", len(locs))

    d, status = prepare_correlation_frame(
        nm_df,
        CorrelationConfig(
            method="Spearman",
            x_col=str(x_col or ""),
            y_choice=str(y_col or ""),
            selected_locations=locs,
        ),
    )
    print("corr_status:", status)
    print("d_shape:", d.shape)
    print(d.head(5).to_string(index=False))


if __name__ == "__main__":
    main()

