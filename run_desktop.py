#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _run_choreographer_wrapper_if_requested() -> bool:
    """Let a frozen INAES executable act as Choreographer's Chrome pipe wrapper.

    Kaleido/Choreographer launches Chromium on Unix with:
    ``sys.executable _unix_pipe_chromium_wrapper.py <chrome> ...``.
    In a PyInstaller app, ``sys.executable`` is INAES itself, so without this
    guard export attempts relaunch the full UI instead of starting Chrome.
    """
    if len(sys.argv) < 3:
        return False
    if Path(str(sys.argv[1])).name != "_unix_pipe_chromium_wrapper.py":
        return False

    import os

    os.dup2(0, 3)
    os.dup2(1, 4)
    os.set_inheritable(3, True)
    os.set_inheritable(4, True)

    import signal
    import subprocess
    from functools import partial

    cli = sys.argv[2:]
    print(f"wrapper CLI: {cli}", file=sys.stderr)
    process = subprocess.Popen(cli, pass_fds=(3, 4))

    def _terminate(proc: subprocess.Popen, _sig_num: int, _frame: object | None) -> None:
        proc.terminate()
        try:
            proc.wait(5)
        finally:
            proc.kill()

    handler = partial(_terminate, process)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    process.wait()
    print("{bye}")
    raise SystemExit(process.returncode or 0)


def _bootstrap_src_path() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> None:
    if _run_choreographer_wrapper_if_requested():
        return
    _bootstrap_src_path()
    from inaes_desktop.app import run

    run()


if __name__ == "__main__":
    main()
