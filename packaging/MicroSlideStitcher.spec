# -*- mode: python ; coding: utf-8 -*-
"""Onefile Linux x86_64 build specification for MicroSlideStitcher."""
import os
from pathlib import Path

_spec_root = Path(SPECPATH).resolve()
ROOT = _spec_root if (_spec_root / "src").is_dir() else _spec_root.parent
ENTRY = ROOT / "packaging" / "entrypoint.py"
ICON = ROOT / "packaging" / "MicroSlideStitcher.png"

# Each exclusion is either a build/test-only package or an unused Qt feature.
EXCLUDES = [
    "tkinter", "pytest", "pytestqt", "pytest_qt", "setuptools", "pip", "PyInstaller",
    "IPython", "pandas", "scipy",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
]

analysis = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src"), str(ROOT / "packaging")],
    binaries=[],
    datas=[(str(ICON), ".")],
    hiddenimports=["micro_slide_stitcher.gui", "micro_slide_stitcher.gui.about",
                   "micro_slide_stitcher.gui.mainwindow", "micro_slide_stitcher.gui.runner",
                   "gui_selftest"],
    hookspath=[], hooksconfig={"PySide6": {"excluded_qt_plugins": ["wayland*", "xcbglintegrations",
                                                         "egldeviceintegrations", "tls", "networkinformation"]}},
    excludes=EXCLUDES,
    noarchive=False,
)

# Build in deliberate, separately tested rounds.  The "compat" round avoids
# copying two host-built libraries that require GLIBC_2.43; Qt/OpenCV then use
# the target system's liblcms2 and glib2 copies.  The "final" round additionally
# removes Qt components this program never loads.  Set MSS_PACKAGING_STAGE to
# "baseline", "compat", or "final" when measuring a round.
STAGE = os.environ.get("MSS_PACKAGING_STAGE", "final").lower()
if STAGE not in {"baseline", "compat", "final"}:
    raise SystemExit("MSS_PACKAGING_STAGE must be baseline, compat, or final")

COMPAT_EXCLUDES = ("liblcms2.so", "libglib-2.0.so")
FINAL_PATH_EXCLUDES = (
    "/plugins/virtualkeyboard/",
    "/plugins/qmltooling/",
    "/plugins/egldeviceintegrations/",
    "/plugins/sqldrivers/",
    "/plugins/platformthemes/libqgtk3.so",
    "/translations/",
    # Python never imports these optional Qt feature sets.  Their shared
    # libraries are collected before plugin filtering, so remove them here as
    # well rather than retaining a dead dependency closure.
    "PySide6/Qt/lib/libQt6Quick",
    "PySide6/Qt/lib/libQt6Qml",
    "PySide6/Qt/lib/libQt6Pdf",
    "PySide6/Qt/lib/libQt6VirtualKeyboard",
    "PySide6/Qt/lib/libQt6WaylandClient",
    "PySide6/Qt/lib/libQt6WlShellIntegration",
    "PySide6/Qt/lib/libQt6EglFS",
    "PySide6/QtDBus.abi3.so",
    # libqgtk3's closure is otherwise retained even after libqgtk3 itself is
    # removed.  Qt Widgets does not depend on GTK; offscreen and normal GUI
    # tests exercise the retained Qt platform paths.
    "libgtk-3.so",
    "libgdk-3.so",
    "libglycin-2.so",
    "libatk-",
    "libatspi.so",
    "libpango",
    "libcairo",
    "libepoxy.so",
    "libgdk_pixbuf",
)


def _filter_toc(entries):
    """Strip only staged files which are proven unused by frozen tests."""
    denied = COMPAT_EXCLUDES if STAGE == "compat" else (
        COMPAT_EXCLUDES + FINAL_PATH_EXCLUDES if STAGE == "final" else ()
    )
    kept, removed = [], []
    for entry in entries:
        destination = entry[0].replace("\\", "/")
        if any(token in destination for token in denied):
            removed.append(entry)
        else:
            kept.append(entry)
    if removed:
        removed_bytes = sum(Path(entry[1]).stat().st_size for entry in removed
                            if len(entry) > 1 and Path(entry[1]).is_file())
        print(f"MicroSlideStitcher packaging stage={STAGE}: removed "
              f"{len(removed)} payload entries ({removed_bytes} source bytes)")
        for entry in removed:
            print(f"  excluded: {entry[0]}")
    return kept


analysis.binaries = _filter_toc(analysis.binaries)
analysis.datas = _filter_toc(analysis.datas)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="MicroSlideStitcher",
    debug=False, bootloader_ignore_signals=False,
    strip=True, upx=False, console=True,
    icon=str(ICON),
)
