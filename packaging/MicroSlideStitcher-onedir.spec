# -*- mode: python ; coding: utf-8 -*-
"""Onedir payload used only inside the AppImage AppDir."""
from pathlib import Path

# Reuse exactly the same analysis/exclusions as the onefile build.
onefile_spec = Path(SPECPATH) / "MicroSlideStitcher.spec"
exec(compile(onefile_spec.read_text(encoding="utf-8"), str(onefile_spec), "exec"))

exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name="MicroSlideStitcher",
    debug=False, bootloader_ignore_signals=False,
    strip=True, upx=False, console=True,
)
coll = COLLECT(
    exe, analysis.binaries, analysis.datas,
    strip=True, upx=False,
    name="MicroSlideStitcher",
)
