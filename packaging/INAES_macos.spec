# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path(SPECPATH).resolve().parent
datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "docs" / "INAES_Software_Manual.pdf"), "docs"),
]
binaries = []
hiddenimports = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "scipy.stats",
    "scipy.interpolate",
    "sklearn.model_selection",
    "statsmodels.api",
    "statsmodels.gam.api",
    "statsmodels.nonparametric.smoothers_lowess",
    "pwlf",
    "kaleido",
]

# Keep the package lean: PyInstaller's standard hooks handle numpy/pandas/scipy/
# sklearn/statsmodels. These explicit additions cover Plotly static export and
# lazy imports used by the desktop workflows without collecting test suites.
datas += collect_data_files("plotly", include_py_files=False)
datas += collect_data_files("kaleido", include_py_files=False)
binaries += collect_dynamic_libs("kaleido")
hiddenimports += collect_submodules("pwlf")


a = Analysis(
    [str(ROOT / "run_desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "tkinter",
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "matplotlib.tests",
        "numpy.tests",
        "pandas.tests",
        "scipy.tests",
        "sklearn.tests",
        "statsmodels.tests",
        "plotly.tests",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="INAES",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="INAES",
)
app = BUNDLE(
    coll,
    name="INAES.app",
    icon=str(ROOT / "assets" / "app_icon.icns"),
    bundle_identifier="org.inaes.INAES",
    info_plist={
        "CFBundleDisplayName": "INAES",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
    },
)
