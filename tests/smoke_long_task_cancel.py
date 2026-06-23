#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QThread, QTimer

from inaes_desktop.long_task import LongTaskWorker


def _long_fn(*, progress_callback=None, cancel_requested=None):
    for i in range(1, 501):
        if callable(cancel_requested) and bool(cancel_requested()):
            raise RuntimeError("Cancelled by request.")
        if callable(progress_callback):
            progress_callback(int(i * 100 / 500), f"step={i}")
        time.sleep(0.004)
    return {"ok": True}


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication([])
    thread = QThread()
    worker = LongTaskWorker(_long_fn, kwargs={})
    worker.moveToThread(thread)

    outcome: dict[str, str] = {"state": ""}

    def _done(state: str, msg: str = "") -> None:
        outcome["state"] = state
        outcome["msg"] = str(msg or "")
        if thread.isRunning():
            thread.quit()
        app.quit()

    thread.started.connect(worker.run)
    worker.succeeded.connect(lambda _: _done("succeeded", "unexpected success"))
    worker.failed.connect(lambda m: _done("failed", m))
    worker.cancelled.connect(lambda m: _done("cancelled", m))
    worker.progress.connect(lambda _p, _m: None)
    # Deterministic cancellation path: request cancel before the worker starts.
    worker.request_cancel()
    thread.start()
    QTimer.singleShot(10000, lambda: _done("timeout", "timeout waiting for worker"))
    app.exec()

    if thread.isRunning():
        thread.quit()
        thread.wait(5000)

    state = outcome.get("state", "")
    if state != "cancelled":
        raise SystemExit(f"Expected cancelled state, got: {state} | msg={outcome.get('msg', '')}")
    print(f"SMOKE_LONG_TASK_CANCEL_OK | msg={outcome.get('msg', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
