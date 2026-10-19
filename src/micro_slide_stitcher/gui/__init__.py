"""Optional PySide6 desktop front end for the MicroSlideStitcher CLI.

The pipeline itself never imports this package, so the command line interface keeps
working with PySide6 absent. Install the GUI extra to use it:

    pip install -e '.[gui]'
"""
from __future__ import annotations

import sys

MISSING_PYSIDE_MESSAGE = """\
MicroSlideStitcher GUI: PySide6 is not installed.

The GUI is an OPTIONAL extra - the command line interface works without it:

    python -m micro_slide_stitcher --input scan.mp4 --output pano.tif

To install the GUI into the same environment (on Manjaro/Arch PySide6 comes from pip
and needs no pacman Qt packages):

    .venv/bin/pip install -e '.[gui]'        # or: .venv/bin/pip install PySide6

Then launch it with:

    .venv/bin/python -m micro_slide_stitcher.gui      # or micro-slide-stitcher-gui
"""


def pyside6_available() -> bool:
    """True when PySide6 can be imported."""
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


USAGE = """\
usage: micro-slide-stitcher-gui [INPUT]

Desktop front end for the MicroSlideStitcher CLI. It builds a command line, runs
`python -m micro_slide_stitcher` for you, and reads the diagnostics directory that run
produced; every setting is a real CLI flag and the window shows the exact command.

positional arguments:
  INPUT          video file or image folder to open on start-up (optional)

options:
  -h, --help     show this message and exit
  --version      show the version and exit

All pipeline options live in the window itself; run the CLI with --help for those.
"""


def main(argv: list[str] | None = None) -> int:
    """Entry point of `python -m micro_slide_stitcher.gui` / `micro-slide-stitcher-gui`.

    Exit codes: 0 = window closed normally (or --help/--version), 4 = PySide6 missing.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return 0
    if "--version" in argv:
        from .. import __version__
        print(f"MicroSlideStitcher GUI {__version__}")
        return 0
    if not pyside6_available():
        print(MISSING_PYSIDE_MESSAGE, file=sys.stderr)
        return 4

    from PySide6.QtWidgets import QApplication

    from .mainwindow import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("MicroSlideStitcher")
    app.setOrganizationName("MicroSlideStitcher")
    window = MainWindow()
    window.show()

    # A file or image folder positional argument opens that input straight away.
    for arg in argv:
        if not arg.startswith("-"):
            window.load_input(arg)
            break
    return app.exec()


__all__ = ["main", "pyside6_available", "MISSING_PYSIDE_MESSAGE", "USAGE"]
