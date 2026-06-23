from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.boxplots import BoxplotConfig, available_group_columns, prepare_boxplot_points
from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df
from inaes_core.io_universal import read_table_from_path
from inaes_core.metadata_nm import compute_metadata_with_nm


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: boxplots core module")
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

    groups = available_group_columns(nm_df)
    print("groups:", groups[:10], "...", len(groups))
    group_col = "Location" if "Location" in groups else (groups[0] if groups else "Sample")

    d, status, ycol, group_plot_col, x_levels = prepare_boxplot_points(
        nm_df,
        BoxplotConfig(
            y_metric="nM10",
            size_choice="b_5_m",
            group_col=group_col,
            use_numeric_ranges=False,
            scale="log10",
            show_points=True,
        ),
    )
    print("box_status:", status)
    print("ycol:", ycol, "| group_plot_col:", group_plot_col, "| levels:", len(x_levels))
    print("d_shape:", d.shape)
    print(d.head(5).to_string(index=False))


if __name__ == "__main__":
    main()

