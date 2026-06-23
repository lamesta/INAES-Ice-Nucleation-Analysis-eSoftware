"""Data contracts used during migration.

These contracts mirror the current legacy app semantics and help prevent
logic drift while the UI is being rewritten.
"""

CURVES_CORE_COLUMNS = ["Sample", "Freezing.temperature", "nm"]
CURVES_STANDARD_COLUMNS = [
    "Sample",
    "Size",
    "Freezing.temperature",
    "nm",
    "Control",
    "Dilution.factor",
    "Location",
    "FF",
]

METADATA_CORE_COLUMNS = ["Sample"]

# Critical sections that must preserve algorithm parity first.
LOCKED_PARITY_BLOCKS = [
    "RAW analysis / nM normalization formulas",
    "Frozen Fraction filtering semantics",
    "Kneepoint detection and breakpoint ranking",
    "nM10/nM15 LOESS estimation",
]

