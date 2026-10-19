"""Screenshots of the 0.7.0 scan-type and bundle-adjustment controls (offscreen).

Run from the project root with QT_QPA_PLATFORM=offscreen and one argument: a folder of
overlapping stills to load, so the window shows a real input and a real command preview.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from micro_slide_stitcher.gui.mainwindow import MainWindow, SidePanelScroll

OUT = Path("/home/user/workspace/ba_screenshots")


def settle(app, n=12):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.05)


def main() -> int:
    images = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    win = MainWindow(settings=QSettings(str(OUT / "run/ba.ini"), QSettings.IniFormat))
    win.resize(1400, 900)
    win.show()
    win.load_input(images)
    t = time.monotonic()
    while not win.metadata and time.monotonic() - t < 60:
        app.processEvents()
        time.sleep(0.05)
    settle(app)

    # 1. Scan type on its default, with the command preview visible.
    win.grab().save(str(OUT / "01_scan_type_default_sweep.png"))

    # 2. Both human-readable options. The popup is its own top-level window, which an
    #    offscreen win.grab() cannot see, so the popup view itself is grabbed.
    panel = win.settings_panel
    combo = panel.widget_for("--scan-mode")
    combo.showPopup()
    settle(app)
    view = combo.view().window()
    view.grab().save(str(OUT / "02_scan_type_options.png"))
    combo.hidePopup()
    settle(app)

    # 3. Serpentine chosen: the command preview must now carry --scan-mode serpentine.
    panel.set_values({"--scan-mode": "serpentine"})
    settle(app)
    win.grab().save(str(OUT / "03_scan_type_serpentine_selected.png"))
    print("command with serpentine:", win.command_text())

    # 4. Advanced settings open on the bundle-adjustment controls.
    panel.set_advanced_expanded(True)
    panel.set_values({"--bundle-adjust": True, "--ba-max-iterations": 300})
    win.chk_all_flags.setChecked(True)
    settle(app)
    sa = win.findChild(SidePanelScroll)
    sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
    settle(app)
    win.grab().save(str(OUT / "04_advanced_bundle_adjustment.png"))
    print("command with everything:", win.command_text())

    # 5. The results summary of a real serpentine run with bundle adjustment applied.
    if len(sys.argv) > 2:
        win.chk_all_flags.setChecked(False)
        panel.set_advanced_expanded(False)
        win.load_diagnostics(Path(sys.argv[2]))
        settle(app, 20)
        win.tabs.setCurrentIndex(3)
        settle(app)
        win.grab().save(str(OUT / "05_results_summary_bundle_adjusted.png"))

    # 6. Section 3 of the manual, the plain-language scan-type guidance.
    win.show_manual()
    manual = win.manual_window
    manual.resize(1000, 760)
    settle(app)
    manual.go_to_anchor("s2b")
    settle(app)
    manual.grab().save(str(OUT / "06_manual_scan_type_section.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
