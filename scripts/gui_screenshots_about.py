"""Capture the About dialog and its in-app licence view without a display server."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from micro_slide_stitcher.gui.about import AboutDialog


OUT = Path("/home/user/workspace/about_screenshots")
CASES = (("small_480x520", 480, 520), ("1280x800", 1280, 800))


def capture(dialog: AboutDialog, path: Path) -> None:
    """Process layout events before saving a single dialog image."""
    QApplication.processEvents()
    if not dialog.grab().save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    for name, width, height in CASES:
        dialog = AboutDialog()
        dialog.resize(width, height)
        dialog.show()
        QApplication.processEvents()
        capture(dialog, OUT / f"about_dialog_{name}.png")

        dialog.btn_show_licence.setChecked(True)
        dialog.scroll.ensureWidgetVisible(dialog.licence_text, 0, 8)
        capture(dialog, OUT / f"about_licence_{name}.png")
        dialog.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
