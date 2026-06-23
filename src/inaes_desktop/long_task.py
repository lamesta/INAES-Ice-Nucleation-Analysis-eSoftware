from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot


class LongTaskWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        progress_kwarg: str | None = "progress_callback",
        cancel_kwarg: str | None = "cancel_requested",
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = tuple(args or ())
        self._kwargs = dict(kwargs or {})
        self._progress_kwarg = progress_kwarg
        self._cancel_kwarg = cancel_kwarg
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def cancel_requested(self) -> bool:
        return bool(self._cancel_event.is_set())

    def _emit_progress(self, pct: Any, msg: Any) -> None:
        try:
            p = int(float(pct))
        except Exception:
            p = 0
        p = max(0, min(100, p))
        try:
            self.progress.emit(p, str(msg or ""))
        except RuntimeError:
            # Worker object may be deleted during app shutdown.
            pass

    def _safe_emit(self, signal: Signal, *args: Any) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            # Worker object may be deleted while app is closing.
            pass

    @Slot()
    def run(self) -> None:
        if self.cancel_requested():
            self._safe_emit(self.cancelled, "Cancelled before start.")
            return
        call_kwargs = dict(self._kwargs)
        if self._progress_kwarg and self._progress_kwarg not in call_kwargs:
            call_kwargs[self._progress_kwarg] = self._emit_progress
        if self._cancel_kwarg and self._cancel_kwarg not in call_kwargs:
            call_kwargs[self._cancel_kwarg] = self.cancel_requested

        try:
            result = self._fn(*self._args, **call_kwargs)
        except Exception as exc:
            txt = str(exc)
            if self.cancel_requested() or ("cancel" in txt.lower()):
                self._safe_emit(self.cancelled, txt or "Cancelled.")
            else:
                self._safe_emit(self.failed, txt)
            return

        if self.cancel_requested():
            self._safe_emit(self.cancelled, "Cancelled.")
            return
        self._safe_emit(self.succeeded, result)
