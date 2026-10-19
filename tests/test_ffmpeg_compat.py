"""ffmpeg CLI-compatibility guards.

Regression for the 0.2.1 fix: the extractor hard-coded ``-vsync 0``, which ffmpeg 8
(shipped by Arch/Manjaro) removed -- extraction died with
"Unrecognized option 'vsync'" on every run. These tests pin the negotiation.
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

from micro_slide_stitcher import extract
from micro_slide_stitcher.probe import _require


def test_frame_rate_mode_is_negotiated_not_hardcoded():
    """The extraction command must take its frame-rate flag from the negotiator.

    ``-vsync`` may still appear inside the runtime fallback, but never as the flag
    the primary command is built with.
    """
    src = inspect.getsource(extract.extract_frames)
    assert "frame_rate_mode_args(ffmpeg)" in src
    assert "*mode" in src
    cmd_lines = [ln for ln in src.splitlines() if '"-vf", vf' in ln]
    assert cmd_lines, "could not find the ffmpeg command construction"
    assert all("-vsync" not in ln and "-fps_mode" not in ln for ln in cmd_lines)


def test_frame_rate_mode_args_matches_local_ffmpeg():
    ffmpeg = _require("ffmpeg")
    mode = extract.frame_rate_mode_args(ffmpeg)
    assert mode in ((), ("-fps_mode", "passthrough"), ("-vsync", "0"))

    help_text = subprocess.run([ffmpeg, "-hide_banner", "-h", "full"],
                               capture_output=True, text=True, timeout=60)
    text = (help_text.stdout or "") + (help_text.stderr or "")
    if "-fps_mode" in text:
        assert mode == ("-fps_mode", "passthrough"), (
            "this ffmpeg advertises -fps_mode, which is the option we should use")
    elif "-vsync" in text:
        assert mode == ("-vsync", "0")


def test_chosen_flag_is_actually_accepted_by_local_ffmpeg(tmp_path):
    """The negotiated flag must survive a real ffmpeg invocation on this machine."""
    ffmpeg = _require("ffmpeg")
    mode = extract.frame_rate_mode_args(ffmpeg)
    out = tmp_path / "probe_%03d.png"
    cmd = [ffmpeg, "-v", "error", "-nostdin",
           "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
           "-vf", "fps=2", *mode, "-an", "-sn", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"ffmpeg rejected {mode}: {proc.stderr.strip()}"
    assert "nrecognized option" not in proc.stderr
    assert sorted(tmp_path.glob("probe_*.png")), "no frames written"


def test_extract_falls_back_when_help_output_lies(tmp_path, monkeypatch):
    """If the negotiated flag is rejected at runtime, extraction retries and succeeds."""
    ffmpeg = _require("ffmpeg")
    src = tmp_path / "clip.mp4"
    make = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi",
                           "-i", "testsrc=size=64x48:rate=10:duration=2",
                           "-pix_fmt", "yuv420p", str(src)],
                          capture_output=True, text=True, timeout=120)
    if make.returncode != 0:
        pytest.skip("could not synthesise a test clip")

    monkeypatch.setattr(extract, "frame_rate_mode_args",
                        lambda _ffmpeg: ("-definitely_not_an_option", "1"))
    frames = extract.extract_frames(src, tmp_path / "work", sampling_fps=2.0,
                                    normalize="none")
    assert len(frames) >= 2
