"""Drive the GUI through an image-folder run and save offscreen screenshots."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from micro_slide_stitcher.gui.mainwindow import MainWindow


def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(condition, timeout: float, label: str) -> None:
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        app.processEvents()
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {label}")


def save(win: MainWindow, outdir: Path, name: str) -> None:
    pump(0.25)
    path = outdir / name
    assert win.grab().save(str(path)), path
    print(f"saved {path}", flush=True)


def main() -> int:
    images, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / "run"
    work.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    win = MainWindow(settings=QSettings(str(work / "settings.ini"), QSettings.IniFormat))
    win.resize(1280, 800)
    win.show()
    pump(0.5)
    win.load_input(images)
    wait_for(lambda: bool(win.metadata), 60, "image metadata")
    win.ed_output.setText(str(work / "image_panorama.tif"))
    win._derive_diagnostics()
    save(win, outdir, "01_image_folder_loaded.png")

    done: list[int] = []
    win.runFinished.connect(done.append)
    win.start_preview_run()
    wait_for(lambda: "Frame gating" in win.log_pane.toPlainText(), 300, "gating")
    save(win, outdir, "02_image_run_log.png")
    wait_for(lambda: bool(done), 600, "run completion")

    win.tabs.setCurrentIndex(3)
    save(win, outdir, "03_image_summary.png")
    win.tabs.setCurrentIndex(1)
    save(win, outdir, "04_image_timeline.png")
    win.tabs.setCurrentIndex(2)
    wait_for(lambda: win.panorama.view.has_image, 180, "panorama")
    save(win, outdir, "05_image_panorama.png")
    win.close()
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
