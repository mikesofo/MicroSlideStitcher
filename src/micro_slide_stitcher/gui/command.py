"""Turn the GUI's settings into the exact `python -m micro_slide_stitcher` command line.

This is the only place that knows how GUI state becomes CLI arguments, and it is used
both for the read-only command preview shown to the user and for the subprocess that
is actually launched - so what the user sees is literally what runs.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from .cli_spec import BOOL, OPT_INT, SPECS

MODULE = "micro_slide_stitcher"


def _format_value(spec, value: Any) -> str:
    if spec.kind == "float":
        text = f"{float(value):g}"
        return text
    if spec.kind in ("int", OPT_INT):
        return str(int(value))
    return str(value)


def flag_args(settings: dict[str, Any], include_defaults: bool = False) -> list[str]:
    """The flag part of the command line, in the canonical order of ``cli_spec.SPECS``.

    A flag is emitted when its value differs from the CLI's own default, or always when
    ``include_defaults`` is true (boolean flags still only appear when switched on, and
    ``--max-dimension`` only when it is set - those have no "off" spelling).
    """
    args: list[str] = []
    for spec in SPECS:
        value = settings.get(spec.flag, spec.default)
        if spec.kind == BOOL:
            if value:
                args.append(spec.flag)
            continue
        if value is None:
            continue
        if spec.kind == OPT_INT and value in (None, 0):
            continue
        if not include_defaults and _same(value, spec.default):
            continue
        args += [spec.flag, _format_value(spec, value)]
    return args


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) < 1e-12
    except (TypeError, ValueError):
        return str(a) == str(b)


def build_argv(input_path: str | Path, output_path: str | Path,
               diagnostics: str | Path | None, settings: dict[str, Any],
               include_defaults: bool = False,
               python: str | None = None) -> list[str]:
    """Full argv for launching the CLI as a subprocess."""
    # In a PyInstaller build sys.executable is the application itself, not a
    # Python interpreter.  Its packaging entrypoint dispatches ordinary CLI
    # arguments directly, so passing ``-m micro_slide_stitcher`` would be
    # meaningless (and used to break GUI runs from the frozen executable).
    if getattr(sys, "frozen", False) and python is None:
        argv = [sys.executable, "--input", str(input_path), "--output", str(output_path)]
    else:
        argv = [python or sys.executable, "-m", MODULE,
                "--input", str(input_path), "--output", str(output_path)]
    if diagnostics:
        argv += ["--diagnostics", str(diagnostics)]
    argv += flag_args(settings, include_defaults)
    return argv


def command_string(input_path: str | Path, output_path: str | Path,
                   diagnostics: str | Path | None, settings: dict[str, Any],
                   include_defaults: bool = False,
                   python: str | None = None) -> str:
    """The same command as a copy-pasteable, shell-quoted one-liner."""
    argv = build_argv(input_path, output_path, diagnostics, settings,
                      include_defaults, python)
    return " ".join(shlex.quote(part) for part in argv)


def wrapped_command_string(*args, width: int = 78, **kwargs) -> str:
    """`command_string` broken over several backslash-continued lines for display."""
    text = command_string(*args, **kwargs)
    parts = shlex.split(text)
    lines: list[list[str]] = [[]]
    # keep "python -m micro_slide_stitcher" together on the first line
    head = parts[:3]
    lines[0] = head
    for part in parts[3:]:
        if part.startswith("--"):
            lines.append([part])
        else:
            lines[-1].append(part)
    # merge short lines back together up to `width`
    merged: list[str] = []
    current = " ".join(shlex.quote(p) for p in lines[0])
    for group in lines[1:]:
        chunk = " ".join(shlex.quote(p) for p in group)
        if len(current) + len(chunk) + 1 <= width:
            current = f"{current} {chunk}"
        else:
            merged.append(current)
            current = chunk
    merged.append(current)
    return " \\\n    ".join(merged)
