from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QObject, Signal


@dataclass
class LoadedTable:
    path: Path
    df: pd.DataFrame


class AppState(QObject):
    curves_raw_changed = Signal()
    curves_standardized_changed = Signal()
    metadata_changed = Signal()
    nm_axis_label_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.curves_raw: LoadedTable | None = None
        self.curves_standardized: LoadedTable | None = None
        self.metadata: LoadedTable | None = None
        self.nm_axis_label: str = "nm (g^-1)"

    def set_curves_raw(self, table: LoadedTable | None) -> None:
        # Any new raw/analyzed source invalidates previously standardized cache.
        self.curves_raw = table
        if self.curves_standardized is not None:
            self.curves_standardized = None
            self.curves_standardized_changed.emit()
        elif table is None:
            # Keep signal semantics stable even when already None.
            self.curves_standardized_changed.emit()
        self.curves_raw_changed.emit()

    def set_curves_standardized(self, table: LoadedTable | None) -> None:
        self.curves_standardized = table
        self.curves_standardized_changed.emit()

    def set_metadata(self, table: LoadedTable | None) -> None:
        self.metadata = table
        self.metadata_changed.emit()

    def set_nm_axis_label(self, label: str | None) -> None:
        txt = str(label or "").strip()
        if not txt:
            txt = "nm (g^-1)"
        if txt == self.nm_axis_label:
            return
        self.nm_axis_label = txt
        self.nm_axis_label_changed.emit()
