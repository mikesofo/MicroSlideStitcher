"""Offscreen screenshots of the in-app user manual, for visual inspection.

Usage:  QT_QPA_PLATFORM=offscreen python scripts/gui_screenshots_manual.py OUTDIR
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "manual_screenshots")
    out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])
    from micro_slide_stitcher.gui.mainwindow import MainWindow

    shots: list[tuple[str, str]] = []

    def grab(widget, name: str, note: str) -> None:
        app.processEvents()
        path = out / f"{name}.png"
        widget.grab().save(str(path))
        shots.append((str(path), note))
        print(f"wrote {path}  ({widget.width()}x{widget.height()})  {note}")

    settings = QSettings(str(out / "screenshot_settings.ini"), QSettings.IniFormat)
    win = MainWindow(settings=settings)
    win.resize(1280, 820)
    win.show()
    app.processEvents()

    # ---- large size: 1280x800, the documented design target
    win.manual_action.trigger()
    manual = win.manual_window
    assert manual is not None
    manual.resize(1280, 800)
    manual.show()
    app.processEvents()
    grab(manual, "manual_01_toc_1280x800", "table of contents, top of the manual")

    manual.go_to_anchor("s2")
    grab(manual, "manual_02_section2_1280x800", "mid-manual: recording a good scan")

    manual.go_to_anchor("s5")
    grab(manual, "manual_03_section5_1280x800", "mid-manual: reading the results / verdict table")

    manual.go_to_anchor("s8")
    grab(manual, "manual_04_flagtable_1280x800", "generated complete flag reference table")

    manual.ed_find.setText("stabilisation")
    app.processEvents()
    grab(manual, "manual_05_find_1280x800", "find box in use, matches highlighted")

    manual.ed_find.setText("blur-threshold")
    app.processEvents()
    manual.find_next()
    grab(manual, "manual_06_find_flagtable_1280x800",
         "find box jumping to a match inside the flag table")

    # ---- small size: the enforced minimum, worst case for clipping
    manual.ed_find.setText("")
    app.processEvents()
    manual.resize(720, 460)
    app.processEvents()
    manual.go_to_anchor("top")
    grab(manual, "manual_07_toc_720x460", "small window: table of contents")
    manual.go_to_anchor("s8")
    grab(manual, "manual_08_flagtable_720x460", "small window: flag table")
    manual.ed_find.setText("overlap")
    app.processEvents()
    grab(manual, "manual_09_find_720x460", "small window: find box in use")

    # ---- the Help menu itself, and the status-bar button
    manual.close()
    app.processEvents()
    menu = win.menuBar().actions()[0].menu()
    menu.popup(win.mapToGlobal(win.rect().topLeft()))
    app.processEvents()
    grab(menu, "manual_10_help_menu", "Help menu showing 'User manual' and its F1 shortcut")
    menu.close()
    grab(win, "manual_11_mainwindow_1280x820", "main window with the 'User manual (F1)' button")

    (out / "INDEX.txt").write_text(
        "\n".join(f"{p}\t{n}" for p, n in shots) + "\n", encoding="utf-8")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
