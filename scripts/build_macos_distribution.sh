#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
RELEASE_BASE="${PROJECT_ROOT}/release_delivery"
RELEASE_ROOT="${RELEASE_BASE}/macOS_share"
PYTHON_BIN="${PYTHON_BIN:-}"

cd "${PROJECT_ROOT}"

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${PARENT_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${PARENT_ROOT}/.venv/bin/python"
  elif [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
  elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || command -v python)"
  fi
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

mkdir -p docs
if [[ ! -f "docs/INAES_Software_Manual.pdf" ]]; then
  echo "Missing manual: docs/INAES_Software_Manual.pdf" >&2
  exit 1
fi

"${PYTHON_BIN}" -m pip install -r requirements.txt
"${PYTHON_BIN}" -m pip install --upgrade pyinstaller kaleido pillow

rm -rf build dist
"${PYTHON_BIN}" -m PyInstaller packaging/INAES_macos.spec --noconfirm --clean

APP_PATH="${PROJECT_ROOT}/dist/INAES.app"
if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: ${APP_PATH} not found" >&2
  exit 1
fi

STAGE="${PROJECT_ROOT}/dist/dmg_stage"
DMG_PATH="${PROJECT_ROOT}/dist/INAES-macOS.dmg"
rm -rf "${STAGE}" "${DMG_PATH}"
mkdir -p "${STAGE}"
cp -R "${APP_PATH}" "${STAGE}/INAES.app"
ln -s /Applications "${STAGE}/Applications"

hdiutil create \
  -volname "INAES" \
  -srcfolder "${STAGE}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}" >/dev/null

rm -rf "${RELEASE_ROOT}"
mkdir -p "${RELEASE_ROOT}/docs"
cp "${DMG_PATH}" "${RELEASE_ROOT}/INAES-macOS.dmg"
cp "${PROJECT_ROOT}/docs/INAES_Software_Manual.pdf" "${RELEASE_ROOT}/docs/"
shasum -a 256 "${RELEASE_ROOT}/INAES-macOS.dmg" > "${RELEASE_ROOT}/INAES-macOS.dmg.sha256"

cat > "${RELEASE_ROOT}/README_INSTALL_MAC.txt" <<'TXT'
INAES macOS installer

Install:
1. Open INAES-macOS.dmg.
2. Drag INAES.app into Applications.
3. Launch INAES from Applications.
4. If macOS blocks the first launch, right-click INAES.app and choose Open.

The manual is included inside the application package and is also provided in docs/.
TXT

mkdir -p "${RELEASE_BASE}"
(cd "${RELEASE_BASE}" && zip -qry macOS_share.zip macOS_share)

echo "macOS package created:"
echo "- ${RELEASE_ROOT}/INAES-macOS.dmg"
echo "- ${RELEASE_BASE}/macOS_share.zip"
