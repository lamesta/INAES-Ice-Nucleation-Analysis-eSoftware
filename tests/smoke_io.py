from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inaes_core.io_universal import read_table_from_path


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke test: universal table reader")
    p.add_argument("file", type=Path, help="Path to CSV/TSV/TXT/XLS/XLSX")
    args = p.parse_args()

    df = read_table_from_path(args.file)
    print(f"Loaded: {args.file}")
    print(f"Shape : {df.shape}")
    print(f"Cols  : {list(df.columns)}")
    print(df.head(3).to_string(index=False))


if __name__ == "__main__":
    main()

