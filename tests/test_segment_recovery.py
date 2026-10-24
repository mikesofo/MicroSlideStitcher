"""Wide-baseline segment recovery (feature A) and multi-segment output (feature B)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from micro_slide_stitcher.cli import main, segment_output_path, segment_suffix
from micro_slide_stitcher.extract import Frame
from micro_slide_stitcher.register import Segment, merge_segments

# The gap videos step 90 px per position with one 290 px jump; a tight
# --max-translation-frac makes that jump unregistrable for the adjacent-pair path.
TIGHT = ["--max-translation-frac", "0.5"]
COMMON = ["--frame-selection", "fixed", "--sampling-fps", "2", "--blur-threshold", "5", "--normalize", "none",
          "--min-inliers", "20"]


def _run(video: Path, out: Path, diag: Path, extra: list[str]) -> dict:
    rc = main(["--input", str(video), "--output", str(out), "--diagnostics", str(diag)]
              + COMMON + extra)
    assert rc == 0, "pipeline returned failure"
    return json.loads((diag / "summary.json").read_text())


def test_segment_output_naming_helpers(tmp_path):
    base = tmp_path / "pano.tif"
    assert segment_output_path(base, 1) == base
    assert segment_output_path(base, 2).name == "pano_seg02.tif"
    assert segment_output_path(base, 11).name == "pano_seg11.tif"
    assert segment_suffix(1) == ""
    assert segment_suffix(3) == "_seg03"


def test_merge_segments_composes_transform():
    """Merging must place segment b's frames in segment a's coordinate system."""
    fr = [Frame(i, i * 0.5, Path(f"f{i}.png")) for i in range(4)]
    def shift(dx, dy):
        T = np.eye(3)
        T[0, 2], T[1, 2] = dx, dy
        return T
    a = Segment(fr[:2], [np.eye(3), shift(100, 0)])
    b = Segment(fr[2:], [np.eye(3), shift(50, 0)])
    # recovered transform maps a[1] -> b[0] with the content shifted by -300 px
    M = shift(-300, 0)
    merged = merge_segments(a, b, 1, 0, M)
    assert merged.length == 4
    # b[0] sits 100 + 300 px right of a[0]; b[1] a further 50 px right
    assert merged.transforms[2][0, 2] == pytest.approx(400.0)
    assert merged.transforms[3][0, 2] == pytest.approx(450.0)


def test_rejoin_recovers_break_when_overlap_remains(gap_video_overlapping, tmp_path):
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag_rejoin"
    summary = _run(gap_video_overlapping["video"], out, diag, TIGHT)

    rj = json.loads((diag / "rejoin_attempts.json").read_text())
    accepted = [a for a in rj["attempts"] if a["accepted"]]
    assert len(accepted) == 1, rj
    a = accepted[0]
    assert a["detector"] == "sift"
    assert a["dst_index"] == a["src_index"] + 1        # the directly adjacent pair, re-tried
    assert a["inliers"] >= 20 and a["inlier_ratio"] >= 0.15
    assert abs(a["tx"]) == pytest.approx(290, abs=6)   # the fabricated gap, recovered

    assert summary["counts"]["segments"] == 1
    assert summary["counts"]["segments_written"] == 1
    assert summary["counts"]["rejoins_accepted"] == 1
    assert summary["registration_stats"]["rejoins_used"] == 1
    assert len(summary["segments"]) == 1
    assert summary["segments"][0]["rejoin_links"]
    # one panorama only, with the plain requested name
    assert out.exists()
    assert not list(tmp_path.glob("pano_seg*.png"))
    assert "rejoined" in " ".join(summary["warnings"]).lower()


def test_no_rejoin_restores_old_behaviour(gap_video_overlapping, tmp_path):
    """--no-rejoin must leave the chain broken (and write nothing extra with a high floor)."""
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag_norejoin"
    summary = _run(gap_video_overlapping["video"], out, diag,
                   TIGHT + ["--no-rejoin", "--no-reconnect", "--min-segment-frames", "1000"])
    assert summary["counts"]["segments"] == 2
    assert summary["counts"]["segments_written"] == 1
    assert summary["counts"]["rejoin_attempts"] == 0
    assert not (diag / "rejoin_attempts.json").exists()
    assert out.exists()
    assert not list(tmp_path.glob("pano_seg*.png"))
    assert summary["counts"]["frames_stitched"] < summary["counts"]["accepted"]


def test_no_rejoin_writes_every_segment(gap_video_overlapping, tmp_path):
    """Same broken chain, but multi-segment output is on: both pieces must be written."""
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag_norejoin_multi"
    summary = _run(gap_video_overlapping["video"], out, diag, TIGHT + ["--no-rejoin", "--no-reconnect"])
    assert summary["counts"]["segments"] == 2
    assert summary["counts"]["segments_written"] == 2
    assert out.exists() and (tmp_path / "pano_seg02.png").exists()
    assert summary["verdict"] == "PARTIALLY SUCCESSFUL"


def test_unrejoinable_gap_yields_two_panoramas(gap_video_disconnected, tmp_path):
    out = tmp_path / "pano.tif"
    diag = tmp_path / "diag_split"
    summary = _run(gap_video_disconnected["video"], out, diag, [])

    # the rejoin pass must have been attempted and honestly reported as failed
    rj = json.loads((diag / "rejoin_attempts.json").read_text())
    assert rj["n_attempts"] >= 1
    assert rj["n_accepted"] == 0
    assert all(not a["accepted"] for a in rj["attempts"])

    assert summary["counts"]["segments"] == 2
    assert summary["counts"]["segments_written"] == 2
    assert out.exists() and (tmp_path / "pano_seg02.tif").exists()
    assert not (tmp_path / "pano_seg03.tif").exists()
    for name in ["panorama_preview.png", "panorama_preview_seg02.png",
                 "camera_path.png", "camera_path_seg02.png"]:
        assert (diag / name).exists(), f"missing per-segment diagnostic: {name}"

    segs = summary["segments"]
    assert [e["rank"] for e in segs] == [1, 2]
    assert segs[0]["n_frames"] >= segs[1]["n_frames"]
    for e in segs:
        assert e["width"] > 0 and e["height"] > 0
        assert e["time_end_s"] > e["time_start_s"]
        assert 0 < e["coverage_percent"] <= 100
        assert e["registration_stats"]["pairs_registered"] >= 1
        assert Path(e["output"]).exists()
    # every accepted frame is accounted for, and the verdict says so
    assert summary["counts"]["frames_written_total"] == summary["counts"]["accepted"]
    assert summary["verdict"] == "PARTIALLY SUCCESSFUL"
    joined = " ".join(summary["warnings"])
    assert "separate pieces" in joined
    assert "accounted for" in joined
    report = (diag / "report.html").read_text()
    assert "pano_seg02.tif" in report
    assert "Wide-baseline segment recovery" in report


def test_small_segment_is_reported_as_skipped(gap_video_disconnected, tmp_path):
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag_skip"
    summary = _run(gap_video_disconnected["video"], out, diag,
                   ["--min-segment-frames", "1000"])
    assert summary["counts"]["segments_written"] == 1
    assert summary["counts"]["segments_skipped"] == 1
    skipped = summary["skipped_segments"][0]
    assert skipped["n_frames"] >= 1
    assert skipped["frame_indices"]
    assert skipped["time_end_s"] >= skipped["time_start_s"]
    assert not list(tmp_path.glob("pano_seg*.png"))
    assert "SKIPPED" in " ".join(summary["warnings"])


def test_single_segment_run_is_unchanged(synthetic_video, tmp_path):
    """A scan that chains cleanly must behave exactly as before: one file, no seg suffixes."""
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag_single"
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(out),
               "--frame-selection", "fixed",
               "--sampling-fps", "2", "--blur-threshold", "10", "--normalize", "clahe",
               "--diagnostics", str(diag)])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["segments"] == 1
    assert summary["counts"]["segments_written"] == 1
    assert summary["counts"]["segments_skipped"] == 0
    assert summary["counts"]["rejoin_attempts"] == 0
    assert out.exists()
    assert not list(tmp_path.glob("pano_seg*.png"))
    assert not (diag / "rejoin_attempts.json").exists()
    assert not list(diag.glob("*_seg0*.png"))
    assert set(summary["outputs"]) >= {"panorama", "preview", "camera_path", "report",
                                       "panorama_pixels"}
    assert (diag / "camera_path.png").exists()
    assert (diag / "panorama_preview.png").exists()
