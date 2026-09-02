param(
  [string]$PythonExe = "",
  [string]$AppVersion = "1.0.0",
  [string]$Publisher = "INAES",
  [switch]$SkipInstaller = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RepoRoot = $ProjectRoot
Set-Location $ProjectRoot

function Test-CommandExists {
  param([string]$Name)
  try {
    $null = Get-Command $Name -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}

function Resolve-Python {
  param([string]$Requested)
  if ($Requested -and (Test-Path $Requested)) { return $Requested }
  if ($Requested -and (Test-CommandExists $Requested)) { return $Requested }
  if (Test-CommandExists "py") {
    try {
      & py -3.12 -c "import sys; print(sys.version)" | Out-Null
      return "py -3.12"
    } catch {}
    try {
      & py -3.11 -c "import sys; print(sys.version)" | Out-Null
      return "py -3.11"
    } catch {}
  }
  if (Test-CommandExists "python") { return "python" }
  if (Test-CommandExists "winget") {
    Write-Host "Python not found. Installing Python 3.12 via winget ..."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if (Test-CommandExists "py") { return "py -3.12" }
    if (Test-CommandExists "python") { return "python" }
  }
  throw "Python 3.11+ was not found. Install Python 3.12 x64 and retry."
}

function Invoke-Python {
  param([string]$PythonCmd, [string[]]$Args)
  if ($PythonCmd.StartsWith("py ")) {
    $parts = $PythonCmd.Split(" ", 2)
    & $parts[0] $parts[1] @Args
  } else {
    & $PythonCmd @Args
  }
}

function Find-ISCC {
  $candidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Inno Setup 6\ISCC.exe"
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
  }
  if (Test-CommandExists "ISCC.exe") { return "ISCC.exe" }
  return $null
}

function Ensure-InnoSetup {
  $iscc = Find-ISCC
  if ($iscc) { return $iscc }
  if (Test-CommandExists "winget") {
    Write-Host "Inno Setup not found. Installing via winget ..."
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    $iscc = Find-ISCC
    if ($iscc) { return $iscc }
  }
  return $null
}

$PythonCmd = Resolve-Python -Requested $PythonExe
Write-Host "Using Python: $PythonCmd"

$VenvDir = Join-Path $ProjectRoot ".venv-windows-build"
if (-not (Test-Path $VenvDir)) {
  Invoke-Python $PythonCmd @("-m", "venv", $VenvDir)
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  Write-Warning "Virtualenv creation via '$PythonCmd' did not produce $VenvPython. Retrying with 'python' on PATH."
  if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
  & python -m venv $VenvDir
}
if (-not (Test-Path $VenvPython)) {
  throw "Failed to create build virtualenv at $VenvDir (no python.exe found after venv creation)."
}

& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -r requirements.txt
& $VenvPython -m pip install --upgrade pyinstaller kaleido pillow

if (-not (Test-Path "docs\INAES_Software_Manual.pdf")) {
  throw "Missing manual: docs\INAES_Software_Manual.pdf"
}

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

& $VenvPython -m PyInstaller "packaging\INAES_windows.spec" --noconfirm --clean

$AppDir = Join-Path $ProjectRoot "dist\INAES"
$ExePath = Join-Path $AppDir "INAES.exe"
if (-not (Test-Path $ExePath)) {
  throw "Build failed: $ExePath not found."
}

$ReadmeDist = Join-Path $ProjectRoot "dist\README_WINDOWS_PORTABLE.txt"
@"
INAES Windows portable build

If installer compilation is unavailable, distribute INAES-Portable-Windows.zip.

Usage:
1. Extract the ZIP.
2. Run INAES.exe from the extracted INAES folder.

Do not run INAES.exe from inside the compressed ZIP preview.
"@ | Set-Content -Path $ReadmeDist -Encoding UTF8

$PortableZip = Join-Path $ProjectRoot "dist\INAES-Portable-Windows.zip"
if (Test-Path $PortableZip) { Remove-Item -Force $PortableZip }
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $PortableZip -Force

if (-not $SkipInstaller) {
  $ISCC = Ensure-InnoSetup
  if ($ISCC) {
    & $ISCC `
      "/DMyAppVersion=$AppVersion" `
      "/DMyAppPublisher=$Publisher" `
      "packaging\inaes_windows_installer.iss"
  } else {
    Write-Warning "Inno Setup not available. Installer skipped; portable ZIP was created."
  }
}

$ReleaseRoot = Join-Path $RepoRoot "release_delivery\windows_desktop_installer"
if (Test-Path $ReleaseRoot) { Remove-Item -Recurse -Force $ReleaseRoot }
New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "docs") -Force | Out-Null
Copy-Item $PortableZip $ReleaseRoot -Force
if (Test-Path (Join-Path $ProjectRoot "dist\INAES-Setup-Windows.exe")) {
  Copy-Item (Join-Path $ProjectRoot "dist\INAES-Setup-Windows.exe") $ReleaseRoot -Force
}
Copy-Item "docs\INAES_Software_Manual.pdf" (Join-Path $ReleaseRoot "docs") -Force

@"
INAES Windows distribution

Preferred installer:
- INAES-Setup-Windows.exe

Fallback portable package:
- INAES-Portable-Windows.zip

Build this package on Windows x64:
1. Right-click RUN_WINDOWS_ONE_CLICK_BUILD.bat
2. Choose Run as administrator only if Python/Inno Setup installation needs it.
3. Otherwise normal double click is enough.

The app is the PySide6 desktop version. It is not the legacy web/Dash app.
"@ | Set-Content -Path (Join-Path $ReleaseRoot "README_INSTALL_WINDOWS.txt") -Encoding UTF8

Set-Location (Join-Path $RepoRoot "release_delivery")
if (Test-Path "windows_desktop_installer.zip") { Remove-Item -Force "windows_desktop_installer.zip" }
Compress-Archive -Path "windows_desktop_installer" -DestinationPath "windows_desktop_installer.zip" -Force

Write-Host "Windows distribution outputs:"
Write-Host "- $ReleaseRoot"
Write-Host "- $(Join-Path $RepoRoot 'release_delivery\windows_desktop_installer.zip')"
