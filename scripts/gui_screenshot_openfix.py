"""Save offscreen screenshots of the report/folder handler failure banner.

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/gui_screenshot_openfix.py OUTDIR
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from micro_slide_stitcher.gui import mainwindow
from micro_slide_stitcher.gui.mainwindow import MainWindow, PathRevealThread


def pump(seconds: float = 0.2) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def save(win: MainWindow, outdir: Path, name: str) -> None:
    pump()
    path = outdir / name
    assert win.grab().save(str(path)), path
    print(f"saved {path}", flush=True)


def main() -> int:
    outdir = Path(sys.argv[1]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    target = (outdir / "example output folder").resolve()
    target.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    settings = QSettings(str(outdir / "settings.ini"), QSettings.IniFormat)
    win = MainWindow(settings=settings)
    win.resize(1280, 800)
    win.show()

    original_open_url = mainwindow.QDesktopServices.openUrl
    original_commands = PathRevealThread._commands
    original_launch = mainwindow._launch_detached
    mainwindow.QDesktopServices.openUrl = staticmethod(lambda url: False)
    PathRevealThread._commands = lambda self: [("xdg-open", ["xdg-open", str(self.path)])]
    mainwindow._launch_detached = lambda command: False
    try:
        win._reveal_path(target, "directory")
        pump(0.4)
        save(win, outdir, "01_folder_handler_failure.png")
        win._reveal_path(target / "report.html", "report.html")
        pump(0.4)
        save(win, outdir, "02_report_handler_failure.png")
    finally:
        mainwindow.QDesktopServices.openUrl = original_open_url
        PathRevealThread._commands = original_commands
        mainwindow._launch_detached = original_launch
    win.close()
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
