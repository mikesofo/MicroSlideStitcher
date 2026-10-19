"""End-to-end CLI runs on the synthetic video, plus probe/help/guard checks."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from micro_slide_stitcher.cli import main
from micro_slide_stitcher.probe import ProbeError, probe_video
from micro_slide_stitcher.stitch import estimate_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_cli_help_runs():
    proc = subprocess.run([sys.executable, "-m", "micro_slide_stitcher", "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--sampling-fps" in proc.stdout
    assert "worked examples" in proc.stdout


def test_probe_rejects_non_video(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"definitely not a video")
    with pytest.raises(ProbeError):
        probe_video(bad)
    with pytest.raises(ProbeError):
        probe_video(tmp_path / "missing.mp4")


def test_probe_reads_synthetic(synthetic_video):
    meta = probe_video(synthetic_video["video"])
    assert (meta.width, meta.height) == tuple(synthetic_video["truth"]["frame_size"])
    assert meta.codec == "h264"
    assert meta.frame_count_estimate > 100
    assert meta.fps == pytest.approx(30.0, abs=0.1)


def test_end_to_end_full_resolution(synthetic_video, tmp_path):
    truth = synthetic_video["truth"]
    out = tmp_path / "pano.tif"
    diag = tmp_path / "diag"
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(out),
               "--sampling-fps", "2", "--detector", "orb", "--normalize", "clahe",
               "--blur-threshold", "10", "--diagnostics", str(diag)])
    assert rc == 0, "pipeline returned failure"
    assert out.exists() and out.stat().st_size > 0

    import tifffile
    pano = tifffile.imread(out)
    exp_w, exp_h = truth["expected_panorama"]
    # one scan position is deliberately motion-blurred and gets dropped, so the
    # panorama may be a little smaller than the full ground-truth layout.
    assert pano.shape[1] == pytest.approx(exp_w, rel=0.15)
    assert pano.shape[0] == pytest.approx(exp_h, rel=0.15)

    for name in ["video_metadata.json", "frame_metrics.csv", "frame_metrics.json",
                 "accepted_frames.txt", "rejected_frames.txt", "transforms.json",
                 "transforms.csv", "camera_path.png", "exposure_comparison.png",
                 "panorama_preview.png", "pipeline.log", "report.html", "summary.json"]:
        assert (diag / name).exists(), f"missing diagnostic: {name}"
    assert list((diag / "accepted_frames").glob("*.png"))
    assert list((diag / "matches").glob("match_*.png"))

    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["frames_stitched"] >= 8
    assert summary["registration_stats"]["pairs_registered"] >= 7
    assert summary["verdict"] in ("SUCCESSFUL", "PARTIALLY SUCCESSFUL")
    # not-a-directory cleanup: intermediates removed by default
    assert not (diag / "_work").exists()


def test_end_to_end_max_dimension_png(synthetic_video, tmp_path):
    out = tmp_path / "preview.png"
    diag = tmp_path / "diag_small"
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(out),
               "--max-dimension", "240", "--sampling-fps", "2", "--blur-threshold", "5",
               "--normalize", "none", "--diagnostics", str(diag), "--keep-intermediates"])
    assert rc == 0
    import cv2
    pano = cv2.imread(str(out))
    assert pano is not None
    assert max(pano.shape[:2]) < 1000
    assert (diag / "_work").exists()  # intermediates kept


def test_memory_guard_triggers(synthetic_video, tmp_path):
    out = tmp_path / "pano.png"
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(out),
               "--sampling-fps", "2", "--blur-threshold", "10",
               "--memory-limit-gb", "0.0001",
               "--diagnostics", str(tmp_path / "diag_mem")])
    assert rc == 3
    assert not out.exists()
    failure = (tmp_path / "diag_mem" / "failure.txt").read_text()
    assert "max-dimension" in failure


def test_estimate_bytes_monotonic():
    assert estimate_bytes(1000, 1000) < estimate_bytes(2000, 2000)


def test_blur_threshold_too_high_fails_cleanly(synthetic_video, tmp_path):
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(tmp_path / "p.png"),
               "--blur-threshold", "1e9", "--diagnostics", str(tmp_path / "d")])
    assert rc == 3
    assert "quality gating" in (tmp_path / "d" / "failure.txt").read_text()
