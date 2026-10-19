"""PyInstaller entry point for the combined MicroSlideStitcher desktop executable."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _looks_like_input(value: str) -> bool:
    """A bare existing file or directory is a GUI input, not a CLI command."""
    try:
        return Path(value).expanduser().exists()
    except OSError:
        return False


def _gui(argv: list[str]) -> int:
    from micro_slide_stitcher.gui import main as gui_main
    return gui_main(argv)


def _cli(argv: list[str]) -> int:
    from micro_slide_stitcher.cli import main as cli_main
    return cli_main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # These must never construct QApplication: they are used by terminals and
    # by desktop launchers testing whether the application is installed.
    if argv and argv[0] in ("--help", "-h", "--version"):
        return _cli(argv)

    if argv[:2] == ["-m", "micro_slide_stitcher"]:
        # Compatibility with older GUI command previews.  The frozen program
        # is already the CLI executable, unlike the source-tree interpreter.
        return _cli(argv[2:])

    if argv and argv[0] == "--_gui-selftest":
        from gui_selftest import main as selftest_main
        return selftest_main(argv[1:])

    if not argv:
        return _gui([])
    if argv[0] == "--gui":
        return _gui(argv[1:])
    if len(argv) == 1 and not argv[0].startswith("-") and _looks_like_input(argv[0]):
        return _gui(argv)
    return _cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
