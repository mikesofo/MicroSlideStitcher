"""End-to-end and input-validation coverage for still-image sequence mode."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from micro_slide_stitcher.cli import main
from micro_slide_stitcher.images import probe_image_sequence, resolve_image_paths
from micro_slide_stitcher.probe import ProbeError


def _run_images(source: Path | str, out: Path, diag: Path, extra: list[str] | None = None) -> dict:
    rc = main(["--input", str(source), "--output", str(out), "--diagnostics", str(diag),
               "--normalize", "none", "--blur-threshold", "5", "--min-inliers", "20"]
              + (extra or []))
    assert rc == 0
    return json.loads((diag / "summary.json").read_text())


def test_image_sequence_end_to_end_writes_panorama(synthetic_images, tmp_path):
    summary = _run_images(synthetic_images["images"], tmp_path / "pano.tif", tmp_path / "diag")
    assert summary["input_mode"] == "images"
    assert summary["input_metadata"]["image_count"] == 10
    assert summary["input_metadata"]["uniformly_sized"] is True
    assert summary["counts"]["accepted"] >= 8
    assert summary["counts"]["frames_stitched"] >= 8
    assert summary["counts"]["segments_written"] == 1
    assert summary["verdict"] in ("SUCCESSFUL", "PARTIALLY SUCCESSFUL")
    assert (tmp_path / "pano.tif").exists()
    assert (tmp_path / "diag" / "image_metadata.json").exists()
    assert "image indices" in (tmp_path / "diag" / "report.html").read_text(encoding="utf-8")


def test_natural_filename_order_and_mtime_order(tmp_path):
    image = 255 * np.ones((48, 64, 3), dtype="uint8")
    for name in ("IMG_10.png", "IMG_2.png", "IMG_1.png"):
        assert cv2.imwrite(str(tmp_path / name), image)
    ordered = resolve_image_paths(tmp_path, "name")
    assert [p.name for p in ordered] == ["IMG_1.png", "IMG_2.png", "IMG_10.png"]
    for i, path in enumerate(reversed(ordered)):
        os.utime(path, ns=(1_000_000_000 + i, 1_000_000_000 + i))
    assert [p.name for p in resolve_image_paths(tmp_path, "mtime")] == [
        "IMG_10.png", "IMG_2.png", "IMG_1.png"]


def test_image_stride_uses_every_nth_image(synthetic_images, tmp_path):
    summary = _run_images(synthetic_images["images"], tmp_path / "stride.png", tmp_path / "diag",
                          ["--image-stride", "2"])
    metrics = json.loads((tmp_path / "diag" / "frame_metrics.json").read_text())
    assert summary["input_metadata"]["candidate_count"] == 5
    assert [m["index"] for m in metrics] == [0, 2, 4, 6, 8]


def test_mixed_dimensions_are_refused_unless_downscaled_uniform(tmp_path):
    assert cv2.imwrite(str(tmp_path / "IMG_1.png"), np.zeros((100, 200, 3), dtype="uint8"))
    assert cv2.imwrite(str(tmp_path / "IMG_2.png"), np.zeros((200, 100, 3), dtype="uint8"))
    with pytest.raises(ProbeError, match="mixed dimensions"):
        probe_image_sequence(tmp_path, "name", 1, None)
    with pytest.raises(SystemExit) as exc:
        main(["--input", str(tmp_path), "--output", str(tmp_path / "p.png")])
    assert exc.value.code == 2

    # A proportional 2x size difference becomes one final size at this cap.
    assert cv2.imwrite(str(tmp_path / "IMG_2.png"), np.zeros((200, 400, 3), dtype="uint8"))
    meta = probe_image_sequence(tmp_path, "name", 1, 100)
    assert (meta.width, meta.height) == (100, 50)
    assert meta.uniformly_sized is False


def test_glob_input_is_detected_as_images(synthetic_images, tmp_path):
    summary = _run_images(str(synthetic_images["images"] / "IMG_*.png"),
                          tmp_path / "glob.png", tmp_path / "diag")
    assert summary["input_mode"] == "images"
    assert summary["input_metadata"]["image_count"] == 10


def test_image_mode_does_not_need_ffmpeg_on_path(synthetic_images, tmp_path):
    project = Path(__file__).resolve().parents[1]
    venv_bin = Path(sys.executable).parent
    env = {**os.environ, "PATH": str(venv_bin), "QT_QPA_PLATFORM": "offscreen"}
    out, diag = tmp_path / "no_ffmpeg.png", tmp_path / "diag"
    proc = subprocess.run(
        [sys.executable, "-m", "micro_slide_stitcher", "--input", str(synthetic_images["images"]),
         "--output", str(out), "--diagnostics", str(diag), "--normalize", "none",
         "--blur-threshold", "5", "--min-inliers", "20"],
        cwd=project, capture_output=True, text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert out.exists()
    log = (diag / "pipeline.log").read_text(encoding="utf-8")
    assert "ffprobe" not in log
    assert "Extracting frames" not in log


def test_temporal_normalization_is_usage_error_for_images(synthetic_images, tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--input", str(synthetic_images["images"]), "--output", str(tmp_path / "p.png"),
              "--normalize", "temporal"])
    assert exc.value.code == 2


def test_image_registration_matches_equivalent_video(synthetic_images, tmp_path):
    common = ["--frame-selection", "fixed", "--sampling-fps", "2", "--normalize", "none", "--blur-threshold", "5",
              "--min-inliers", "20"]
    rc = main(["--input", str(synthetic_images["video"]), "--output", str(tmp_path / "video.png"),
               "--diagnostics", str(tmp_path / "video_diag")] + common)
    assert rc == 0
    image = _run_images(synthetic_images["images"], tmp_path / "images.png",
                        tmp_path / "images_diag")
    video = json.loads((tmp_path / "video_diag" / "summary.json").read_text())
    assert image["counts"]["frames_stitched"] == video["counts"]["frames_stitched"]
    assert image["registration_stats"]["pairs_registered"] == video["registration_stats"]["pairs_registered"]
