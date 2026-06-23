#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from inaes_core.io_universal import read_table_from_path
from inaes_core.kneepoint import available_kp_options, filter_kp_points_for_sample
from inaes_core.kneepoint_report import (
    build_kneepoint_report_preview,
    create_kneepoint_report_zip,
    export_kneepoint_report_zip_from_preview,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke test: kneepoint report export")
    p.add_argument("file", type=Path, help="Curves file path")
    return p


def main() -> int:
    args = _parser().parse_args()
    df = read_table_from_path(args.file)
    opts = available_kp_options(df)
    samples = opts.get("samples", [])
    sizes = opts.get("sizes", [])
    dils = opts.get("dilutions", [])
    if len(samples) < 1 or len(sizes) < 1 or len(dils) < 1:
        raise SystemExit("Not enough options to run report smoke test.")

    selected_size = None
    selected_dils: list[str] = []
    report_samples: list[str] = []
    for size in sizes:
        dils_try = dils[: min(3, len(dils))] if len(dils) > 0 else []
        if len(dils_try) == 0:
            continue
        valid_samples: list[str] = []
        for s in samples:
            pts = filter_kp_points_for_sample(
                df,
                sample=s,
                size=size,
                dilutions=dils_try,
                temp_min=None,
                temp_max=None,
            )
            if len(pts) >= 10:
                valid_samples.append(s)
        if len(valid_samples) >= 1:
            selected_size = size
            selected_dils = dils_try
            report_samples = valid_samples[: min(3, len(valid_samples))]
            break

    if selected_size is None or len(report_samples) == 0:
        raise SystemExit("Could not find a valid sample/size/dilution combination for report smoke test.")
    with tempfile.TemporaryDirectory(prefix="inaes_kp_report_smoke_") as td:
        out_dir = Path(td)
        try:
            preview = build_kneepoint_report_preview(
                df,
                report_samples=report_samples,
                size=selected_size,
                dilutions=selected_dils,
                temp_min=None,
                temp_max=None,
                spar=0.4,
                nbreaks=2,
                flat_q=0.35,
                rise_q=0.70,
                point_size=6,
                line_width=2.0,
                show_breakpoints=True,
                show_grid=True,
                nm_axis_label="nm (g^-1)",
            )
            res = export_kneepoint_report_zip_from_preview(
                preview,
                output_dir=out_dir,
                file_prefix="kp_report_smoke",
                show_grid=True,
                nm_axis_label="nm (g^-1)",
            )
        except Exception as exc:
            msg = str(exc)
            if "Kaleido" in msg or "kaleido" in msg:
                print(f"SKIP: kaleido unavailable for report export ({msg})")
                return 0
            raise

        # Backward-compatible one-call path should still work.
        try:
            _ = create_kneepoint_report_zip(
                df,
                report_samples=report_samples,
                size=selected_size,
                dilutions=selected_dils,
                temp_min=None,
                temp_max=None,
                spar=0.4,
                nbreaks=2,
                flat_q=0.35,
                rise_q=0.70,
                point_size=6,
                line_width=2.0,
                show_breakpoints=True,
                show_grid=True,
                nm_axis_label="nm (g^-1)",
                output_dir=out_dir,
                file_prefix="kp_report_smoke_wrapper",
            )
        except Exception as exc:
            msg = str(exc)
            if "Kaleido" in msg or "kaleido" in msg:
                print(f"SKIP: kaleido unavailable for wrapper export ({msg})")
                return 0
            raise

        zip_path = Path(str(res["output_path"]))
        if not zip_path.exists():
            raise SystemExit("Report zip was not created.")
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        required = {
            "kneepoint_summary.csv",
            "kneepoint_parameters.csv",
            "all_samples_grid.svg",
            "all_samples_grid.pdf",
            "kneepoint_report.svg",
            "kneepoint_report.pdf",
        }
        missing = [r for r in required if r not in names]
        if missing:
            raise SystemExit(f"Report zip missing required artifacts: {missing}")
        if not any(n.startswith("plots_by_sample/") and n.endswith(".svg") for n in names):
            raise SystemExit("Report zip missing per-sample SVG exports.")
        if not any(n.startswith("plots_by_sample/") and n.endswith(".pdf") for n in names):
            raise SystemExit("Report zip missing per-sample PDF exports.")

        print(f"OK report zip created: {zip_path}")
        print(f"status: {res.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
