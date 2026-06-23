# INAES Desktop Distribution

This repository packages the INAES PySide6 desktop application only. It does not package or run the retired Dash/web application.

## Local macOS Build

Run from the repository root on macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
bash scripts/build_macos_distribution.sh
```

Outputs:

- `release_delivery/macOS_share/INAES-macOS.dmg`
- `release_delivery/macOS_share/INAES-macOS.dmg.sha256`
- `release_delivery/macOS_share.zip`

## Local Windows Build

Run from the repository root on Windows x64 PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_distribution.ps1
```

Outputs:

- `release_delivery/windows_desktop_installer/INAES-Setup-Windows.exe`
- `release_delivery/windows_desktop_installer/INAES-Portable-Windows.zip`
- `release_delivery/windows_desktop_installer.zip`

Windows builds must be created on Windows. PyInstaller is platform-specific, so do not try to build the Windows installer on macOS.

## GitHub Actions Release

Pushing a tag matching `v*.*.*` starts the release workflow:

```bash
git tag v0.1.0-test
git push origin v0.1.0-test
```

The workflow builds macOS and Windows artifacts, then attaches them to the GitHub Release for the tag.

## Signing Notes

- macOS builds are not notarized unless Apple Developer signing credentials are added later.
- Windows builds are not code-signed unless a certificate is added later.
- Unsigned public builds can show Gatekeeper or SmartScreen warnings.
