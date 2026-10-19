"""Environment handling shared by frozen-build child processes."""
from __future__ import annotations

import os


def child_environment() -> dict[str, str]:
    """Return an environment safe for children of a PyInstaller onefile build.

    PyInstaller changes loader variables while its extracted application is running.
    They must not be inherited by a system browser, file manager, ffprobe or ffmpeg.
    In an ordinary Python installation this is simply a copy of the current environment.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("_PYI")}
    original_library_path = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original_library_path is not None:
        env["LD_LIBRARY_PATH"] = original_library_path
    return env
