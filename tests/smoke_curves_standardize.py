from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.curves_mapping import CurvesMappingConfig, standardize_curves_df
from inaes_core.io_universal import read_table_from_path


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: curves standardization parity module")
    p.add_argument("file", type=Path, help="Path to curves-like file")
    p.add_argument("--map-sample", default=None)
    p.add_argument("--map-temp", default=None)
    p.add_argument("--map-nm", default=None)
    p.add_argument("--map-control", default=None)
    p.add_argument("--map-dilution", default=None)
    p.add_argument("--map-ff", default=None)
    p.add_argument("--manual-size", default="b_5_m")
    args = p.parse_args()

    df = read_table_from_path(args.file)
    cfg = CurvesMappingConfig(
        map_sample=args.map_sample,
        map_temp=args.map_temp,
        map_nm=args.map_nm,
        map_control=args.map_control,
        map_dilution=args.map_dilution,
        map_ff=args.map_ff,
        manual_size_value=args.manual_size,
        auto_control_from_sample=True,
        auto_dilution_from_sample=True,
    )
    out, warnings, resolved = standardize_curves_df(df, cfg)
    print("resolved:", resolved)
    print("shape   :", out.shape)
    print("columns :", list(out.columns))
    print("warnings:", len(warnings))
    for w in warnings[:8]:
        print(" -", w)
    print(out.head(3).to_string(index=False))


if __name__ == "__main__":
    main()

