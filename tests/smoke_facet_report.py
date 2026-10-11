#!/usr/bin/env python3
from __future__ import annotations

import argparse
import colorsys
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.facet_report import (
    _color_to_rgb,
    assign_report_panels,
    build_facet_grid_figure,
    dilution_shades,
    point_color_map,
    resolve_color_mode,
)
from inaes_core.freezing_curves import FreezingCurvesFilter, prepare_freezing_curves_points
from inaes_core.frozen_fraction import FrozenFractionFilter, available_ff_options, prepare_frozen_fraction_points
from inaes_core.io_universal import read_table_from_path

PALETTE = ["rgb(228,26,28)", "rgb(55,126,184)", "#4daf4a"]


def _lightness(c: str) -> float:
    r, g, b = (v / 255.0 for v in _color_to_rgb(c))
    return colorsys.rgb_to_hls(r, g, b)[1]


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke test: per-sample facet reports and FF colour modes")
    p.add_argument("curves", type=Path)
    args = p.parse_args()
    df = read_table_from_path(args.curves)

    if resolve_color_mode("auto", 1) != "dilution" or resolve_color_mode("auto", 3) != "sample_shades":
        raise SystemExit("auto colour mode resolution changed")
    shades = dilution_shades(PALETTE[0], 6)
    light = [_lightness(c) for c in shades]
    if len(set(shades)) != 6 or light != sorted(light):
        raise SystemExit(f"shades must be distinct and go dark -> light: {shades}")

    samples = available_ff_options(df)["samples"]
    ff, _ = prepare_frozen_fraction_points(
        df, FrozenFractionFilter(selected_samples=samples, selected_sizes=[], selected_dilutions=[], show_control=True)
    )
    eff, cmap, smp, dils = point_color_map(ff, mode="sample", palette=PALETTE)
    if eff != "sample" or any(cmap[(s, d)] != PALETTE[i % 3] for i, s in enumerate(smp) for d in dils):
        raise SystemExit("solid-per-sample colours wrong")
    eff, cmap, smp, dils = point_color_map(ff, mode="dilution", palette=PALETTE)
    if any(cmap[(s, d)] != PALETTE[j % 3] for s in smp for j, d in enumerate(dils)):
        raise SystemExit("per-dilution colours wrong")
    eff, cmap, smp, dils = point_color_map(ff, mode="single", palette=PALETTE)
    if set(cmap.values()) != {PALETTE[0]}:
        raise SystemExit("one-solid-colour mode must use only the first palette colour")
    eff, cmap, smp, dils = point_color_map(ff, mode="single_shades", palette=PALETTE)
    if any(cmap[(s, d)] != cmap[(smp[0], d)] for s in smp for d in dils) or len(set(cmap.values())) != len(dils):
        raise SystemExit("one-colour shades must be identical across samples and distinct across dilutions")
    eff, cmap, smp, dils = point_color_map(ff, mode="auto", palette=PALETTE)
    if eff != "sample_shades" or cmap[(smp[0], dils[0])] == cmap[(smp[1], dils[0])]:
        raise SystemExit("auto with several samples must give per-sample hues")

    panels = assign_report_panels(ff)
    n_expected = ff.groupby(["Sample", "Size"]).ngroups
    if panels["Panel"].nunique() != n_expected:
        raise SystemExit(f"expected {n_expected} sample|size panels, got {panels['Panel'].nunique()}")
    fig = build_facet_grid_figure(
        panels, y_col="FF", y_log=False, y_title="Frozen Fraction", color_mode="auto",
        palette=PALETTE, y_fixed_range=[0, 1], title="FF report",
    )
    n_points = sum(len(t.x) for t in fig.data if t.x is not None and t.x[0] is not None)
    if n_points != len(panels):
        raise SystemExit(f"report lost points: {n_points} plotted vs {len(panels)} rows")

    fc, _ = prepare_freezing_curves_points(
        df, FreezingCurvesFilter(selected_sizes=[], selected_dilutions=[], selected_locations=[], include_controls=False)
    )
    fig_fc = build_facet_grid_figure(
        assign_report_panels(fc), y_col="nm", y_log=True, y_title="nm", color_mode="dilution", palette=PALETTE,
    )
    if fig_fc.layout.yaxis.type != "log":
        raise SystemExit("Freezing Curves report must use a log y axis")
    print(f"OK facet report | FF panels={panels['Panel'].nunique()} points={n_points} | FC traces={len(fig_fc.data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
