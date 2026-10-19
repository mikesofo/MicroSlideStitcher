from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# The GUI tests must never need a display; harmless for the CLI tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory) -> dict:
    """Build a small synthetic scan video with known translations (once per session)."""
    out_dir = tmp_path_factory.mktemp("synth")
    video = out_dir / "synthetic.mp4"
    cmd = [sys.executable, str(ROOT / "scripts" / "make_synthetic_video.py"),
           "--out", str(video), "--frame-size", "480", "360", "--steps", "9",
           "--step-x", "90", "--step-y", "110", "--cols", "5", "--hold", "15",
           "--blur-positions", "4", "--seed", "3"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    truth = json.loads(video.with_suffix(".truth.json").read_text())
    return {"video": video, "truth": truth, "dir": out_dir}


@pytest.fixture(scope="session")
def synthetic_images(tmp_path_factory) -> dict:
    """A synthetic video and its matching lossless still-image sequence."""
    out_dir = tmp_path_factory.mktemp("synth_images")
    video = out_dir / "synthetic.mp4"
    images = out_dir / "images"
    cmd = [sys.executable, str(ROOT / "scripts" / "make_synthetic_video.py"),
           "--out", str(video), "--image-dir", str(images), "--frame-size", "480", "360",
           "--steps", "9", "--step-x", "90", "--step-y", "110", "--cols", "5", "--hold", "15",
           "--blur-positions", "4", "--seed", "3"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    truth = json.loads(video.with_suffix(".truth.json").read_text())
    return {"video": video, "images": images, "truth": truth, "dir": out_dir}


@pytest.fixture(scope="session")
def serpentine_images(tmp_path_factory) -> dict:
    """A serpentine (multi-row) still-image scan: 4 columns x 3 rows, 12 positions.

    Rows run alternately left-to-right and right-to-left, and each row is stepped down by
    180 px against a 360 px frame height - half a field - so consecutive rows genuinely
    overlap.  That is what makes non-adjacent (loop-closure) matches exist at all, and it
    also creates the row turn: at the end of a row the pan reverses direction and steps
    down in one move.
    """
    out_dir = tmp_path_factory.mktemp("synth_serpentine")
    video = out_dir / "serpentine.mp4"
    images = out_dir / "images"
    cmd = [sys.executable, str(ROOT / "scripts" / "make_synthetic_video.py"),
           "--out", str(video), "--image-dir", str(images), "--frame-size", "480", "360",
           "--steps", "11", "--step-x", "120", "--step-y", "180", "--cols", "4",
           "--hold", "2", "--seed", "9"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    truth = json.loads(video.with_suffix(".truth.json").read_text())
    return {"video": video, "images": images, "truth": truth, "dir": out_dir}


def _gap_video(out_dir: Path, name: str, gap_x: int) -> dict:
    """A straight horizontal scan of 10 positions with one oversized jump after position 4."""
    video = out_dir / name
    cmd = [sys.executable, str(ROOT / "scripts" / "make_synthetic_video.py"),
           "--out", str(video), "--frame-size", "480", "360", "--steps", "9",
           "--step-x", "90", "--step-y", "0", "--cols", "99", "--hold", "15",
           "--gap-after", "4", "--gap-x", str(gap_x), "--seed", "5"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    truth = json.loads(video.with_suffix(".truth.json").read_text())
    return {"video": video, "truth": truth, "dir": out_dir, "gap_x": gap_x}


@pytest.fixture(scope="session")
def gap_video_overlapping(tmp_path_factory) -> dict:
    """Gap of 200 px on top of the normal 90 px step = one 290 px (0.60 frame width) jump.

    Run with --max-translation-frac 0.5 the adjacent pair across the gap is rejected as
    implausible and the chain breaks, but the two frames DO still overlap by ~40 %, so the
    wide-baseline rejoin pass (which allows a much larger translation) can recover it.
    """
    return _gap_video(tmp_path_factory.mktemp("synth_gap"), "gap_overlap.mp4", 200)


@pytest.fixture(scope="session")
def gap_video_disconnected(tmp_path_factory) -> dict:
    """Gap of 1500 px (> 3 frame widths): the two halves share no image content at all."""
    return _gap_video(tmp_path_factory.mktemp("synth_gap2"), "gap_none.mp4", 1500)
