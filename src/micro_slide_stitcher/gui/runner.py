"""Background execution of the CLI in a subprocess, with log streaming and cancel.

The pipeline itself is never imported or re-implemented here: the GUI launches
``python -m micro_slide_stitcher`` exactly as a terminal would, reads the log lines it
prints (the same lines the run writes into ``<diagnostics>/pipeline.log``) and derives a
coarse progress value from them.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..environment import child_environment

#: Ordered pipeline stages with the progress percentage reached when the stage *starts*.
STAGES: list[tuple[str, int]] = [
    ("Probing the video", 2),
    ("Extracting frames with ffmpeg", 6),
    ("Quality gating (focus / duplicates)", 26),
    ("Registering frame pairs", 36),
    ("Wide-baseline rejoin", 62),
    ("Blending the panorama", 68),
    ("Writing outputs", 92),
    ("Finished", 100),
]

_RE_BLENDED = re.compile(r"blended (\d+)/(\d+) frames")
_RE_GATING = re.compile(r"Frame gating: (\d+)/(\d+) accepted")
_RE_EXTRACTED = re.compile(r"Extracted (\d+) candidate frames")
_RE_LEVEL = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (\w+)\s")


def line_level(line: str) -> str:
    """WARNING / ERROR / INFO / DEBUG of one formatted log line ('' if unknown)."""
    m = _RE_LEVEL.match(line)
    return m.group(1) if m else ""


@dataclass
class ProgressState:
    """Mutable state for `progress_from_line` (a run is a single left-to-right pass)."""
    percent: int = 0
    stage: str = "Starting"
    n_candidates: int = 0
    n_accepted: int = 0


def progress_from_line(line: str, state: ProgressState) -> bool:
    """Update `state` from one log line. Returns True when something changed."""
    def set_stage(index: int, extra: str = "") -> bool:
        name, pct = STAGES[index]
        if pct < state.percent and not extra:
            return False
        state.stage = f"{name}{extra}"
        state.percent = max(state.percent, pct)
        return True

    if "Probed " in line:
        return set_stage(0)
    if "Extracting frames at" in line:
        return set_stage(1)
    m = _RE_EXTRACTED.search(line)
    if m:
        state.n_candidates = int(m.group(1))
        return set_stage(2, f" - {state.n_candidates} candidate frames")
    m = _RE_GATING.search(line)
    if m:
        state.n_accepted, state.n_candidates = int(m.group(1)), int(m.group(2))
        return set_stage(3, f" - {state.n_accepted}/{state.n_candidates} frames accepted")
    if "raw segment(s)" in line:
        return set_stage(4)
    if "Wide-baseline rejoin pass" in line:
        return set_stage(4)
    if "Canvas bounds" in line:
        return set_stage(5)
    m = _RE_BLENDED.search(line)
    if m:
        done, total = int(m.group(1)), int(m.group(2))
        lo, hi = STAGES[5][1], STAGES[6][1]
        state.percent = max(state.percent, lo + int((hi - lo) * done / max(total, 1)))
        state.stage = f"Blending the panorama - {done}/{total} frames"
        return True
    if "Wrote panorama" in line:
        return set_stage(6)
    if "VERDICT:" in line:
        state.stage = f"Finished - {line.split('VERDICT:')[-1].strip()}"
        state.percent = 100
        return True
    if "PIPELINE FAILED" in line:
        state.stage = "Failed"
        state.percent = 100
        return True
    return False


class PipelineRunner(QThread):
    """Runs one CLI invocation. Signals are delivered on the GUI thread."""

    line = Signal(str, str)        # (text, level)
    progress = Signal(int, str)    # (percent, stage text)
    done = Signal(int, str)        # (exit code, human message); code -1 == cancelled

    def __init__(self, argv: list[str], diagnostics: Path, cwd: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.argv = list(argv)
        self.diagnostics = Path(diagnostics)
        self.cwd = Path(cwd) if cwd else None
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self.exit_code: int | None = None

    # ------------------------------------------------------------------ thread body
    def run(self) -> None:  # noqa: D102 - QThread entry point
        env = child_environment()
        env["PYTHONUNBUFFERED"] = "1"
        state = ProgressState()
        self.progress.emit(0, "Starting the pipeline")
        try:
            kwargs = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True   # own process group, so ffmpeg dies too
            self._proc = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
                cwd=str(self.cwd) if self.cwd else None, **kwargs)
        except OSError as exc:
            self.line.emit(f"could not start the pipeline: {exc}", "ERROR")
            self.exit_code = 2
            self.done.emit(2, f"Could not start the pipeline: {exc}")
            return

        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            text = raw.rstrip("\n")
            if not text:
                continue
            self.line.emit(text, line_level(text))
            if progress_from_line(text, state):
                self.progress.emit(state.percent, state.stage)
        code = self._proc.wait()
        self.exit_code = code

        if self._cancelled:
            self._note_cancelled()
            self.done.emit(-1, "Run cancelled. Diagnostics written so far are preserved.")
            return
        if code == 0:
            self.progress.emit(100, state.stage if state.percent >= 100 else "Finished")
            self.done.emit(0, "Run finished successfully.")
        elif code == 3:
            self.done.emit(3, "The pipeline could not produce a panorama (exit code 3). "
                              "The diagnostics directory is preserved.")
        elif code == 2:
            self.done.emit(2, "The CLI rejected the arguments (exit code 2).")
        else:
            self.done.emit(code, f"The pipeline exited with code {code}.")

    # ---------------------------------------------------------------------- cancel
    def cancel(self) -> None:
        """Terminate the child (and its ffmpeg children) and leave the workspace usable."""
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self.line.emit("cancel requested: terminating the pipeline process", "WARNING")
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:  # pragma: no cover - not exercised on Linux
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                return
        deadline = time.time() + 5.0
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:  # pragma: no cover
                    proc.kill()
            except OSError:
                pass

    def _note_cancelled(self) -> None:
        """Leave a breadcrumb so a half-written diagnostics dir is not mistaken for a run."""
        try:
            self.diagnostics.mkdir(parents=True, exist_ok=True)
            (self.diagnostics / "cancelled.txt").write_text(
                "This run was cancelled from the GUI before it finished.\n"
                "Whatever the pipeline had already written is kept unchanged; there is no\n"
                "summary.json for a cancelled run, and <diagnostics>/_work may still hold\n"
                "extracted candidate frames (safe to delete).\n"
                f"command: {' '.join(self.argv)}\n",
                encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            pass


def python_executable() -> str:
    """Interpreter used to launch the CLI (the one running the GUI)."""
    return sys.executable
