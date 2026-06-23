from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df
from inaes_core.frozen_fraction import FrozenFractionFilter, available_ff_options, prepare_frozen_fraction_points
from inaes_core.io_universal import read_table_from_path


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: frozen fraction prep module")
    p.add_argument("file", type=Path, help="Path to curves-like file")
    p.add_argument("--map-sample", default=None)
    p.add_argument("--map-temp", default=None)
    p.add_argument("--map-nm", default=None)
    p.add_argument("--map-control", default=None)
    p.add_argument("--map-dilution", default=None)
    p.add_argument("--map-ff", default=None)
    p.add_argument("--hide-control", action="store_true")
    args = p.parse_args()

    raw = read_table_from_path(args.file)
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
    curves, warnings, resolved = standardize_curves_df(raw, cfg)

    print("resolved:", resolved)
    print("warnings:", len(warnings))
    opts = available_ff_options(curves)
    print("options:", opts)

    flt = FrozenFractionFilter(
        selected_samples=opts.get("samples", []),
        selected_sizes=opts.get("sizes", []),
        selected_dilutions=opts.get("dilutions", []),
        show_control=not args.hide_control,
    )
    points, status = prepare_frozen_fraction_points(curves, flt)
    print("status:", status)
    print("shape:", points.shape)
    print("control_counts:", points["Control_norm"].value_counts().to_dict())
    print(points[["Sample", "Dilution.plot", "Control_norm", "Freezing.temperature", "FF"]].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
