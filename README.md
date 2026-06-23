# INAES

INAES is scientific desktop software for ice nucleation/freezing assay analysis.

This repository contains the native desktop application built with Python and PySide6. The legacy Dash/web application is not part of the desktop distribution and is not used for packaging or releases.

## Platforms

- macOS
- Windows

## Download and Install

Release builds are published from GitHub Releases.

### macOS

1. Download `INAES-macOS.dmg` from the latest release.
2. Open the DMG.
3. Drag `INAES.app` into `Applications`.
4. Launch INAES from `Applications`.

Unsigned macOS builds may show a Gatekeeper warning on first launch. Right-clicking the app and choosing Open can be required unless a future release is notarized with an Apple Developer certificate.

### Windows

1. Download `INAES-Setup-Windows.exe` from the latest release.
2. Run the installer.
3. Launch INAES from the Start Menu or desktop shortcut.

If needed, `INAES-Portable-Windows.zip` is also provided as a portable package. Unsigned Windows builds may show a Microsoft SmartScreen warning until code signing is added.

## Developer Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run_desktop.py
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run_desktop.py
```

## Project Layout

- `run_desktop.py`: desktop application entrypoint
- `src/inaes_desktop/`: PySide6 desktop UI
- `src/inaes_core/`: scientific analysis core used by the desktop app
- `assets/`: application icons and splash screen
- `docs/INAES_Software_Manual.pdf`: user manual included in packaged builds
- `packaging/`: PyInstaller specs and Windows Inno Setup installer script
- `scripts/`: macOS and Windows distribution build scripts

## Build Notes

PyInstaller builds are platform-specific.

- Build macOS packages on macOS.
- Build Windows packages on Windows or with the `windows-latest` GitHub Actions runner.
- The GitHub Actions release workflow builds `INAES-macOS.dmg`, `INAES-Setup-Windows.exe`, and `INAES-Portable-Windows.zip`.
