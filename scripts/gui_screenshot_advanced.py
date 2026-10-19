import sys, time
from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from micro_slide_stitcher.gui.mainwindow import MainWindow
app = QApplication([])
out = Path("/home/user/workspace/gui_screenshots")
win = MainWindow(settings=QSettings(str(out / "run/adv.ini"), QSettings.IniFormat))
win.resize(1280, 800); win.show()
win.load_input(Path(sys.argv[1]))
t=time.monotonic()
while not win.metadata and time.monotonic()-t < 60:
    app.processEvents(); time.sleep(0.05)
win.load_diagnostics(out / "run/vid2_preview_diagnostics")
win.settings_panel.set_advanced_expanded(True)
win.settings_panel.set_values({"--blend": "multiband", "--no-rejoin": True,
                               "--min-inliers": 30, "--rejoin-window": 5})
win.chk_all_flags.setChecked(True)
for _ in range(10): app.processEvents(); time.sleep(0.05)
from micro_slide_stitcher.gui.mainwindow import SidePanelScroll
sa = win.findChild(SidePanelScroll)
sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
for _ in range(10): app.processEvents(); time.sleep(0.05)
p = out / "10_advanced_settings_all_flags.png"
win.grab().save(str(p)); print("saved", p)
