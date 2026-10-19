"""Drive the GUI offscreen against a real video and save screenshots.

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/gui_screenshots.py VIDEO OUTDIR
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication

from micro_slide_stitcher.gui.mainwindow import MainWindow

SIZE = (1400, 900)


def shot(win: MainWindow, outdir: Path, name: str) -> Path:
    app = QApplication.instance()
    for _ in range(6):
        app.processEvents()
    path = outdir / name
    pix = win.grab()
    assert pix.save(str(path)), f"could not save {path}"
    print(f"  saved {path} ({pix.width()}x{pix.height()})", flush=True)
    return path


def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(cond, timeout: float, label: str) -> None:
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        app.processEvents()
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {label}")


def main() -> int:
    video = Path(sys.argv[1]).resolve()
    outdir = Path(sys.argv[2]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / "run"
    work.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    settings = QSettings(str(work / "gui_settings.ini"), QSettings.IniFormat)
    win = MainWindow(settings=settings)
    win.resize(*SIZE)
    win.show()
    pump(0.5)

    print("loading video…", flush=True)
    win.load_input(video)
    wait_for(lambda: bool(win.metadata), 60, "ffprobe metadata")
    win.ed_output.setText(str(work / "vid2.tif"))
    win._derive_diagnostics()
    win.settings_panel.set_advanced_expanded(False)
    pump(0.3)
    shot(win, outdir, "01_idle_video_loaded.png")

    print("starting quick preview run…", flush=True)
    finished: list[int] = []
    win.runFinished.connect(lambda code: finished.append(code))
    win.start_preview_run()

    wait_for(lambda: "Frame gating" in win.log_pane.toPlainText(), 300, "gating log line")
    pump(0.4)
    shot(win, outdir, "02_mid_run_log.png")

    wait_for(lambda: finished, 900, "run to finish")
    print(f"  run finished with exit code {finished[0]}", flush=True)
    pump(0.5)

    win.tabs.setCurrentIndex(3)
    pump(0.3)
    shot(win, outdir, "03_finished_summary.png")

    win.tabs.setCurrentIndex(1)
    pump(0.3)
    shot(win, outdir, "04_frame_quality_timeline.png")

    win.tabs.setCurrentIndex(2)
    wait_for(lambda: win.panorama.view.has_image, 180, "panorama preview to load")
    pump(0.4)
    shot(win, outdir, "05_panorama_segment1.png")

    n = win.cmb_segment.count()
    print(f"  segments: {n}", flush=True)
    if n > 1:
        win.cmb_segment.setCurrentIndex(1)
        wait_for(lambda: win.panorama.view.has_image, 180, "segment 2 preview")
        pump(0.4)
        shot(win, outdir, "06_panorama_segment2.png")

    win.tabs.setCurrentIndex(0)
    pump(0.2)
    shot(win, outdir, "07_run_log_full.png")

    # the stated minimum working size
    win.resize(1280, 800)
    pump(0.6)
    win.tabs.setCurrentIndex(3)
    pump(0.4)
    shot(win, outdir, "08_summary_at_1280x800.png")
    win.tabs.setCurrentIndex(2)
    pump(0.6)
    shot(win, outdir, "09_panorama_at_1280x800.png")
    win.close()
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
