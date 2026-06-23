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
from inaes_core.kneepoint import available_kp_options, filter_kp_points_for_sample, kneepoint_analysis


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: kneepoint core module")
    p.add_argument("file", type=Path, help="Path to curves-like file")
    p.add_argument("--map-sample", default=None)
    p.add_argument("--map-temp", default=None)
    p.add_argument("--map-nm", default=None)
    p.add_argument("--map-control", default=None)
    p.add_argument("--map-dilution", default=None)
    p.add_argument("--map-ff", default=None)
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

    opts = available_kp_options(curves)
    print("options:", opts)
    if not opts.get("samples") or not opts.get("sizes") or not opts.get("dilutions"):
        print("No kneepoint options available after standardization.")
        return

    dils = opts["dilutions"][: min(3, len(opts["dilutions"]))]
    sample = None
    size = None
    points = None
    for s in opts["samples"]:
        for sz in opts["sizes"]:
            one = filter_kp_points_for_sample(
                curves,
                sample=s,
                size=sz,
                dilutions=dils,
            )
            if len(one) >= 10:
                sample = s
                size = sz
                points = one
                break
        if sample is not None:
            break
    if sample is None or size is None or points is None:
        print("No sample/size combination has enough points (need >=10).")
        return
    print("selection:", {"sample": sample, "size": size, "dils": dils})
    print("points_shape:", points.shape)

    res = kneepoint_analysis(
        curves,
        sample=sample,
        size=size,
        dilutions=dils,
        spar=0.4,
        n_breaks=2,
        flat_quantile=0.35,
        rise_quantile=0.70,
        boot_R=30,
        cv_k=5,
    )
    print("breakpoints:", [round(float(x), 4) for x in res.breakpoints])
    print("nm_at_breakpoints:", [round(float(x), 4) for x in res.nm_at_breakpoints])
    print("anova_like:", res.anova_like)
    print("bootstrap_ok:", res.bootstrap.get("n_boot_ok", 0))
    print("cv:", res.cv)


if __name__ == "__main__":
    main()
