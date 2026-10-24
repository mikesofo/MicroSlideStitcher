"""0.7.1: motion-aware frame selection, the overlap check at every merge, shift-only BA."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from micro_slide_stitcher.cli import main
from micro_slide_stitcher.keyframes import FrameStat, SelectionLimits, _flag_blur
from micro_slide_stitcher.register import RegistrationLimits, overlap_check

ROOT = Path(__file__).resolve().parents[1]


def _texture(h: int = 360, w: int = 480, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 6, w // 6, 3), dtype=np.uint8)
    img = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.GaussianBlur(img, (3, 3), 0)


def _pair(dx: int, dy: int):
    """Frame A and frame B = the same scene seen shifted by (dx, dy)."""
    big = _texture(1200, 1600)
    a = big[300:660, 400:880].copy()
    b = big[300 + dy:660 + dy, 400 + dx:880 + dx].copy()
    # B-point = A-point - (dx, dy)
    M = np.array([[1, 0, -dx], [0, 1, -dy]], np.float64)
    ys, xs = np.mgrid[0:360:12, 0:480:12]
    src = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    keep = (src[:, 0] - dx >= 0) & (src[:, 0] - dx < 480) & (src[:, 1] - dy >= 0) & \
           (src[:, 1] - dy < 360)
    return a, b, M, src[keep]


def test_a_genuine_overlap_passes_every_check():
    a, b, M, src = _pair(150, 60)
    ok, why, vals = overlap_check(M, src, None, a, b, RegistrationLimits())
    assert ok, why
    assert vals["overlap_x"] == pytest.approx(330 / 480, abs=1e-3)
    assert vals["overlap_y"] == pytest.approx(300 / 360, abs=1e-3)
    assert vals["overlap_ncc"] > 0.9


@pytest.mark.parametrize("dx,dy", [(450, 0), (0, 340)])
def test_too_little_overlap_in_either_direction_is_rejected(dx, dy):
    a, b, M, src = _pair(dx, dy)
    ok, why, _ = overlap_check(M, src, None, a, b, RegistrationLimits())
    assert not ok and "BOTH directions" in why


def test_wide_but_very_thin_overlap_is_rejected_whatever_its_area():
    # 100 % horizontal overlap but only ~6 % vertical: a sliver
    a, b, M, src = _pair(0, 338)
    ok, why, _ = overlap_check(M, src, None, a, b, RegistrationLimits())
    assert not ok


def test_clumped_matched_points_are_rejected():
    a, b, M, src = _pair(100, 40)
    clump = src[(src[:, 0] < 140) & (src[:, 1] < 80)][:30]
    ok, why, _ = overlap_check(M, clump, None, a, b, RegistrationLimits())
    assert not ok and "clumped" in why


def test_a_wrong_alignment_fails_the_image_agreement_check():
    a, b, M, src = _pair(120, 50)
    M_wrong = M.copy()
    M_wrong[0, 2] += 40         # geometrically plausible, but 40 px off
    M_wrong[1, 2] += 25
    ok, why, vals = overlap_check(M_wrong, src, None, a, b, RegistrationLimits())
    assert not ok and "do not agree" in why
    assert vals["overlap_ncc"] < 0.6


def _stats(sharp, aniso):
    return [FrameStat(n=i, timestamp_s=i / 30, sharpness=s, sharp_x=s / 2, sharp_y=s / 2,
                      anisotropy=an) for i, (s, an) in enumerate(zip(sharp, aniso))]


def test_motion_blur_is_detected_relative_to_neighbouring_frames():
    sharp = [100.0] * 60
    aniso = [1.5] * 60          # naturally directional tissue texture: not blur
    sharp[30], aniso[30] = 30.0, 4.5    # smeared along one direction
    sharp[40] = 40.0                    # out of focus, not directional
    st = _stats(sharp, aniso)
    _flag_blur(st, 30.0, SelectionLimits())
    assert st[30].blurred and st[30].blur_reason.startswith("motion blur")
    assert st[40].blurred and st[40].blur_reason.startswith("blurred")
    assert sum(s.blurred for s in st) == 2


def test_a_steady_pan_is_not_mistaken_for_blur():
    # a slow continuous pan: every frame equally (mildly) directional and equally sharp
    st = _stats([100.0] * 90, [2.2] * 90)
    _flag_blur(st, 30.0, SelectionLimits())
    assert not any(s.blurred for s in st)


def test_motion_selection_never_keeps_a_blurred_frame(synthetic_video, tmp_path):
    diag = tmp_path / "diag"
    rc = main(["--input", str(synthetic_video["video"]), "--output", str(tmp_path / "p.png"),
               "--normalize", "none", "--blur-threshold", "5", "--diagnostics", str(diag)])
    assert rc == 0
    rows = list(csv.DictReader((diag / "frame_selection.csv").open()))
    assert len(rows) >= 100                      # every decoded frame is reported
    kept = [r for r in rows if r["kept"] == "True"]
    assert kept and not [r for r in kept if r["blurred"] == "True"]
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["segments"] == 1


def test_frames_that_share_no_content_are_never_merged(gap_video_disconnected, tmp_path):
    diag = tmp_path / "diag"
    rc = main(["--input", str(gap_video_disconnected["video"]),
               "--output", str(tmp_path / "p.png"), "--normalize", "none",
               "--blur-threshold", "5", "--diagnostics", str(diag)])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["segments_written"] == 2
    pairs = json.loads((diag / "transforms.json").read_text())["pairs"]
    for p in pairs:
        if p["ok"]:
            assert min(p["overlap_x"], p["overlap_y"]) >= 0.10
            assert p["overlap_ncc"] >= 0.60


def test_serpentine_bundle_adjustment_is_shift_only(serpentine_images, tmp_path):
    """0.7.0 could pick a scaled model that shrank frames to fake a better fit, and the
    OpenCV adjuster could not be stopped at the time limit.  0.7.1 refines shifts only."""
    diag = tmp_path / "diag"
    rc = main(["--input", str(serpentine_images["images"]),
               "--output", str(tmp_path / "p.png"), "--scan-mode", "serpentine",
               "--normalize", "none", "--blur-threshold", "5", "--diagnostics", str(diag)])
    assert rc == 0
    ba = json.loads((diag / "summary.json").read_text())["bundle_adjustment"]
    assert ba["enabled"]
    if ba["applied"]:
        assert ba["optimiser"] == "numpy_lm_huber_translation_only"


# ---------------------------------------------------------------------------- 0.8 reconnect
def test_pieces_that_show_the_same_tissue_are_reconnected(gap_video_overlapping, tmp_path):
    """Force a break (tight translation limit, no rejoin), then let content matching find
    the frames on both sides that overlap."""
    diag = tmp_path / "diag"
    rc = main(["--input", str(gap_video_overlapping["video"]),
               "--output", str(tmp_path / "p.png"), "--frame-selection", "fixed",
               "--sampling-fps", "2", "--normalize", "none", "--blur-threshold", "5",
               "--max-translation-frac", "0.5", "--no-rejoin", "--diagnostics", str(diag)])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text())
    rcn = summary["reconnect"]
    assert rcn["attempted"] and len(rcn["merges"]) == 1
    assert rcn["merges"][0]["links"] >= 2
    assert summary["counts"]["segments"] == 1
    used = [l for l in rcn["links"] if l["used"]]
    assert len({l["frame_a"] for l in used}) >= 2 and len({l["frame_b"] for l in used}) >= 2


def test_reconnection_can_be_switched_off(gap_video_overlapping, tmp_path):
    diag = tmp_path / "diag"
    rc = main(["--input", str(gap_video_overlapping["video"]),
               "--output", str(tmp_path / "p.png"), "--frame-selection", "fixed",
               "--sampling-fps", "2", "--normalize", "none", "--blur-threshold", "5",
               "--max-translation-frac", "0.5", "--no-rejoin", "--no-reconnect",
               "--diagnostics", str(diag)])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["segments"] == 2 and not summary["reconnect"]["merges"]


def test_one_link_is_never_enough_to_merge(gap_video_overlapping, tmp_path, monkeypatch):
    from micro_slide_stitcher import reconnect as rc_mod
    orig = rc_mod._spread
    monkeypatch.setattr(rc_mod, "_spread", lambda c, b: orig(c, b)[:1])
    diag = tmp_path / "diag"
    rc = main(["--input", str(gap_video_overlapping["video"]),
               "--output", str(tmp_path / "p.png"), "--frame-selection", "fixed",
               "--sampling-fps", "2", "--normalize", "none", "--blur-threshold", "5",
               "--max-translation-frac", "0.5", "--no-rejoin", "--diagnostics", str(diag)])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["counts"]["segments"] == 2 and not summary["reconnect"]["merges"]
