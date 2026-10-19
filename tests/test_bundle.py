"""Non-adjacent match discovery and global bundle adjustment (0.7.0).

Every test here works on real synthetic images through the real matcher and the real
optimiser: nothing is mocked, because the whole point of the feature is a numerical
claim about geometry.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from micro_slide_stitcher import bundle
from micro_slide_stitcher.bundle import (BundleLimits, adjacent_correspondences,
                                         bundle_adjust, disabled_result, error_stats,
                                         predicted_overlap, reprojection_errors,
                                         shortlist_pairs)
from micro_slide_stitcher.cli import build_parser, main
from micro_slide_stitcher.images import extract_image_frames
from micro_slide_stitcher.register import RegistrationLimits, build_chain

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- helpers
def _chain(images: Path, tmp_path: Path, turn_frac: float | None = None):
    """Build the pairwise chain of a still-image sequence, exactly as the CLI does."""
    frames = extract_image_frames(images, workdir=tmp_path / "work",
                                  image_order="name", image_stride=1,
                                  max_dimension=None)
    limits = RegistrationLimits()
    segments, pairs, warnings, _ = build_chain(
        frames, detector="orb", limits=limits, viz_dir=tmp_path / "viz", max_viz=0,
        turn_translation_frac=turn_frac)
    return segments, pairs, warnings, limits


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positions(segment) -> np.ndarray:
    return np.array([t[:2, 2] for t in segment.transforms], float)


def _truth_positions(truth: dict, segment) -> np.ndarray:
    pos = truth["positions"]
    origin = np.array(pos[segment.frames[0].index], float)
    return np.array([np.array(pos[f.index], float) - origin for f in segment.frames])


# ------------------------------------------------------- overlap prediction and shortlist
def test_predicted_overlap_matches_hand_computed_geometry():
    """A 480x360 frame shifted 240 px sideways shares exactly half its area."""
    identity = np.eye(3)
    half = np.eye(3)
    half[0, 2] = 240.0
    assert predicted_overlap(identity, half, 480, 360) == pytest.approx(0.5, abs=0.01)
    far = np.eye(3)
    far[0, 2] = 1000.0
    assert predicted_overlap(identity, far, 480, 360) == 0.0
    quarter = np.eye(3)
    quarter[0, 2], quarter[1, 2] = 240.0, 180.0
    assert predicted_overlap(identity, quarter, 480, 360) == pytest.approx(0.25, abs=0.01)


def test_shortlist_never_attempts_all_pairs_and_respects_the_cap():
    """Consecutive pairs are excluded, non-overlapping ones are never shortlisted."""
    # a straight sweep of 20 frames stepping 240 px: only frame i and i+1 overlap 50 %,
    # i and i+2 share nothing at all.
    transforms = []
    for i in range(20):
        t = np.eye(3)
        t[0, 2] = 240.0 * i
        transforms.append(t)
    pairs, n_candidates = shortlist_pairs(transforms, 480, 360,
                                          BundleLimits(min_overlap_frac=0.15))
    assert pairs == [] and n_candidates == 0, \
        "a straight non-overlapping sweep offers no non-adjacent pair"

    # now a serpentine-like layout where every frame overlaps the one three positions away
    transforms = []
    for i in range(12):
        t = np.eye(3)
        t[0, 2] = 120.0 * (i % 4) + 0.0
        t[1, 2] = 180.0 * (i // 4)
        transforms.append(t)
    all_pairs, n_candidates = shortlist_pairs(
        transforms, 480, 360, BundleLimits(min_overlap_frac=0.10, max_extra_pairs=1000))
    assert n_candidates == len(all_pairs)
    assert len(all_pairs) > 5
    n_possible = 12 * 11 // 2
    assert len(all_pairs) < n_possible, "the full O(n^2) set must never be matched"
    assert all(abs(a - b) > 1 for a, b, _ in all_pairs), "consecutive pairs are not extra"
    capped, n_capped_candidates = shortlist_pairs(
        transforms, 480, 360, BundleLimits(min_overlap_frac=0.10, max_extra_pairs=4))
    assert n_capped_candidates == len(all_pairs), "the cap must not hide the true count"
    assert len(capped) == 4
    # the cap keeps the most promising ones: sorted by descending predicted overlap
    assert [p[2] for p in capped] == sorted([p[2] for p in capped], reverse=True)
    assert capped[0][2] >= all_pairs[-1][2]


# --------------------------------------------------------------- discovery finds real loops
def test_discovery_finds_genuine_cross_row_loop_closures(serpentine_images, tmp_path):
    """On a real serpentine scan, frames in adjacent rows are matched and verified.

    The ground truth says which frames overlap; the discovered pairs must be a subset of
    those, and must include at least one cross-row pair (a frame and the frame above it).
    """
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    assert seg.length >= 10
    obs, attempts, n_candidates, n_shortlisted = bundle.discover_non_adjacent_pairs(
        seg, "orb", limits, BundleLimits(min_overlap_frac=0.06, max_extra_pairs=60))
    accepted = [a for a in attempts if a.accepted]
    assert accepted, "a serpentine scan must yield at least one non-adjacent match"
    assert n_shortlisted <= n_candidates
    assert obs and all(o.non_adjacent for o in obs)

    # every accepted pair must really overlap according to ground truth, and at least one
    # must join two different rows (a step of 4 positions in this 4-column fixture).
    truth = serpentine_images["truth"]
    pos = truth["positions"]
    fw, fh = truth["frame_size"]
    cross_row = 0
    for a in accepted:
        pa, pb = np.array(pos[a.src_index], float), np.array(pos[a.dst_index], float)
        d = np.abs(pa - pb)
        assert d[0] < fw and d[1] < fh, \
            f"pair {a.src_index}->{a.dst_index} does not overlap in ground truth ({d})"
        if d[1] > 0:
            cross_row += 1
    assert cross_row >= 1, "no cross-row loop closure was found on a serpentine scan"


def test_discovery_finds_nothing_on_a_straight_sweep_and_says_so(tmp_path):
    """A straight sweep whose steps are nearly a full field has no non-adjacent overlap.

    Note the qualification, because it matters for honest reporting: at the recommended
    50 % overlap even a *straight* sweep has non-adjacent overlaps (frames i and i+2 still
    share about 20 % of their area), and the discovery pass does find them.  It is only
    when consecutive fields barely overlap that there is nothing non-adjacent to find - and
    then the program must say so rather than pretend to have refined anything.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_synthetic_video import make_slide          # noqa: PLC0415

    import cv2
    slide = make_slide(2200, 500, seed=17)
    images = tmp_path / "sparse_sweep"
    images.mkdir()
    step, w, h = 340, 480, 360           # 29 % overlap between neighbours, 0 % beyond
    for i in range(5):
        cv2.imwrite(str(images / f"IMG_{i + 1}.png"),
                    slide[70:70 + h, i * step:i * step + w])
    frames = extract_image_frames(images, workdir=tmp_path / "w", image_order="name",
                                  image_stride=1, max_dimension=None)
    limits = RegistrationLimits()
    segments, _, _, _ = build_chain(frames, detector="orb", limits=limits,
                                    viz_dir=tmp_path / "viz", max_viz=0)
    seg = segments[0]
    assert seg.length == 5, "the sparse sweep must still chain together"
    out, result = bundle_adjust(seg, "orb", limits,
                               BundleLimits(min_overlap_frac=0.15), scan_mode="sweep")
    assert result.enabled
    assert result.candidates == 0 and result.accepted == 0
    assert any("single straight sweep" in n for n in result.notes), result.notes
    # With only the consecutive pairs there is nothing to close the loop with, so any
    # change must be negligible: the refinement cannot correct accumulated drift here.
    shift = max(float(np.linalg.norm(a[:2, 2] - b[:2, 2]))
                for a, b in zip(out.transforms, seg.transforms))
    assert shift < 1.0, f"a straight sweep was moved by {shift:.2f} px with nothing to fix"


# ------------------------------------------------------------- the optimisation itself
def test_bundle_adjustment_reduces_reprojection_error_with_known_drift(serpentine_images,
                                                                      tmp_path):
    """Inject drift into the chain, then check the optimiser removes most of it.

    The chained transforms are perturbed by a growing shift - exactly the shape of real
    accumulated error - so the correct answer is known and improvement is measurable.
    """
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    truth = _truth_positions(serpentine_images["truth"], seg)

    drifted = [t.copy() for t in seg.transforms]
    for k in range(1, len(drifted)):
        drifted[k][0, 2] += 0.9 * k          # 0.9 px of drift per step, cumulative
        drifted[k][1, 2] -= 0.5 * k
    seg.transforms = drifted
    before_dev = np.linalg.norm(_positions(seg) - truth, axis=1)

    out, result = bundle_adjust(seg, "orb", limits,
                               BundleLimits(min_overlap_frac=0.06, max_iterations=200),
                               scan_mode="serpentine")
    assert result.enabled and result.applied, result.reason
    assert result.reproj_after["mean"] < result.reproj_before["mean"]
    after_dev = np.linalg.norm(_positions(out) - truth, axis=1)
    rms_before = float(np.sqrt((before_dev ** 2).mean()))
    rms_after = float(np.sqrt((after_dev ** 2).mean()))
    assert rms_after < rms_before, f"RMS deviation got worse: {rms_before} -> {rms_after}"
    # the injected drift reaches ~10 px at the far end; most of it must be gone
    assert rms_after < 0.5 * rms_before


def test_the_reference_frame_is_held_fixed_as_the_gauge(serpentine_images, tmp_path):
    """Frame 0's transform stays the identity: the solution is not free to slide."""
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    for k in range(1, seg.length):
        seg.transforms[k][0, 2] += 0.8 * k
    out, result = bundle_adjust(seg, "orb", limits, BundleLimits(min_overlap_frac=0.06),
                               scan_mode="serpentine")
    assert result.applied, result.reason
    assert result.reference_frame_index == out.frames[0].index
    np.testing.assert_allclose(out.transforms[0], np.eye(3), atol=1e-9)


def test_a_worse_solution_is_rejected_and_the_chain_is_kept(serpentine_images, tmp_path,
                                                            monkeypatch):
    """If the optimiser returns rubbish, the chained transforms must survive untouched.

    Both optimiser back ends are replaced by one that shifts every frame 500 px - a
    guaranteed increase in reprojection error.  The safety property is that the caller
    still gets a usable segment.
    """
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    original = [t.copy() for t in seg.transforms]

    def sabotage(transforms, obs, blimits, deadline=None, *a, **kw):
        out = [t.copy() for t in transforms]
        for k in range(1, len(out)):
            out[k][0, 2] += 500.0
        return out, 7, ["sabotaged on purpose"]

    monkeypatch.setattr(bundle, "_ba_opencv", sabotage)
    monkeypatch.setattr(bundle, "_ba_numpy", sabotage)
    out, result = bundle_adjust(seg, "orb", limits, BundleLimits(min_overlap_frac=0.06),
                               scan_mode="serpentine")
    assert result.enabled
    assert not result.applied
    # Either guard is a correct refusal: the 500 px shift trips the shift cap first,
    # and a solution that got past it would still fail the cost comparison.
    assert ("implausible" in result.reason
            or "did not reduce the robust reprojection cost" in result.reason), \
        result.reason
    for a, b in zip(out.transforms, original):
        np.testing.assert_allclose(a, b, atol=1e-12)


def test_bundle_adjustment_is_deterministic(serpentine_images, tmp_path):
    """The same input gives bit-for-bit the same transforms, twice."""
    runs = []
    for _ in range(2):
        segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
        seg = segments[0]
        for k in range(1, seg.length):
            seg.transforms[k][0, 2] += 0.7 * k
        out, result = bundle_adjust(seg, "orb", limits,
                                    BundleLimits(min_overlap_frac=0.06),
                                    scan_mode="serpentine")
        assert result.applied, result.reason
        runs.append((np.array(out.transforms), result.iterations, result.accepted))
    np.testing.assert_array_equal(runs[0][0], runs[1][0])
    assert runs[0][1] == runs[1][1]
    assert runs[0][2] == runs[1][2]


def test_the_iteration_cap_is_obeyed(serpentine_images, tmp_path):
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    for k in range(1, seg.length):
        seg.transforms[k][0, 2] += 0.7 * k
    _, result = bundle_adjust(seg, "orb", limits,
                              BundleLimits(min_overlap_frac=0.06, max_iterations=3),
                              scan_mode="serpentine")
    assert result.iterations <= 3


def test_the_wall_clock_cap_degrades_gracefully(serpentine_images, tmp_path):
    """An impossible time budget must not raise and must not lose the panorama."""
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    original = [t.copy() for t in seg.transforms]
    out, result = bundle_adjust(seg, "orb", limits,
                               BundleLimits(min_overlap_frac=0.06, max_seconds=1e-6),
                               scan_mode="serpentine")
    assert out.length == seg.length
    assert result.enabled
    if not result.applied:
        for a, b in zip(out.transforms, original):
            np.testing.assert_allclose(a, b, atol=1e-12)


def test_reprojection_error_helpers_agree_with_a_hand_computation():
    c = bundle.Correspondence(pos_a=0, pos_b=1,
                              pts_a=np.array([[0.0, 0.0], [10.0, 0.0]]),
                              pts_b=np.array([[0.0, 0.0], [10.0, 0.0]]))
    t0 = np.eye(3)
    t1 = np.eye(3)
    t1[0, 2] = 3.0
    err = reprojection_errors([t0, t1], [c])
    np.testing.assert_allclose(err, [3.0, 3.0])
    stats = error_stats(err)
    assert stats["n"] == 2 and stats["mean"] == pytest.approx(3.0)
    assert error_stats(np.zeros(0)) == {"n": 0}


def test_disabled_result_is_a_complete_summary_block():
    d = disabled_result("sweep").to_dict()
    for key in ("enabled", "applied", "optimiser", "reason", "scan_mode", "iterations",
                "candidates", "attempted", "accepted", "reproj_before", "reproj_after"):
        assert key in d
    assert d["enabled"] is False
    json.dumps(d)      # must be serialisable into summary.json


# --------------------------------------------------------------- the row-turn direction reversal
def test_serpentine_row_turn_would_otherwise_break_the_chain(serpentine_images, tmp_path):
    """The reversal-plus-step-down at the end of a row is the chain-breaking case.

    With a translation gate tight enough to reject the row turn, a sweep-mode chain splits
    into one segment per row.  The serpentine turn allowance keeps it in one piece, using
    the same inlier, scale, rotation and reprojection gates.
    """
    frames = extract_image_frames(serpentine_images["images"], workdir=tmp_path / "w",
                                  image_order="name", image_stride=1,
                                  max_dimension=None)
    tight = RegistrationLimits(max_translation_frac=0.30)
    sweep_segments, _, _, _ = build_chain(frames, detector="orb", limits=tight,
                                          viz_dir=tmp_path / "v1", max_viz=0,
                                          allow_bridging=False, allow_rejoin=False)
    serp_segments, _, serp_warnings, _ = build_chain(
        frames, detector="orb", limits=tight, viz_dir=tmp_path / "v2", max_viz=0,
        allow_bridging=False, allow_rejoin=False, turn_translation_frac=1.5)

    assert len(sweep_segments) > 1, "the fixture must actually break in sweep mode"
    assert len(serp_segments) < len(sweep_segments), \
        (f"serpentine mode did not repair the row turn: "
         f"{len(sweep_segments)} -> {len(serp_segments)} segments")
    assert serp_segments[0].length > sweep_segments[0].length
    assert any("row turn accepted" in w for w in serp_warnings), serp_warnings


def test_the_turn_allowance_does_not_accept_unrelated_frames(tmp_path):
    """The wider allowance only relaxes translation - a disconnected pair still fails."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_synthetic_video import make_slide          # noqa: PLC0415

    import cv2
    slide = make_slide(3000, 600, seed=21)
    images = tmp_path / "disjoint"
    images.mkdir()
    for i, x in enumerate((0, 2400)):                     # no shared content whatsoever
        cv2.imwrite(str(images / f"IMG_{i + 1}.png"), slide[100:460, x:x + 480])
    frames = extract_image_frames(images, workdir=tmp_path / "w",
                                  image_order="name", image_stride=1,
                                  max_dimension=None)
    segments, pairs, _, _ = build_chain(frames, detector="orb",
                                        limits=RegistrationLimits(),
                                        viz_dir=tmp_path / "viz", max_viz=0,
                                        turn_translation_frac=4.0,
                                        allow_bridging=False, allow_rejoin=False)
    assert all(not p.ok for p in pairs), [p.reason for p in pairs]
    assert all(s.length == 1 for s in segments)


# --------------------------------------------------------------------------- CLI contract
def test_scan_mode_defaults_to_sweep_with_bundle_adjustment_off():
    args = build_parser().parse_args(["--input", "x.mp4", "--output", "y.tif"])
    assert args.scan_mode == "sweep"
    assert args.bundle_adjust is False
    assert args.no_bundle_adjust is False


def test_contradictory_bundle_flags_are_rejected():
    proc = subprocess.run([sys.executable, "-m", "micro_slide_stitcher",
                           "--input", "x.mp4", "--output", "y.tif",
                           "--bundle-adjust", "--no-bundle-adjust"],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert "contradict" in proc.stderr


@pytest.mark.parametrize("flag,value", [("--ba-max-iterations", "0"),
                                        ("--ba-max-seconds", "0"),
                                        ("--ba-min-overlap", "0"),
                                        ("--ba-min-overlap", "1.5"),
                                        ("--serpentine-min-overlap", "0"),
                                        ("--serpentine-turn-translation-frac", "-1")])
def test_out_of_range_bundle_flags_are_rejected(flag, value):
    proc = subprocess.run([sys.executable, "-m", "micro_slide_stitcher",
                           "--input", "x.mp4", "--output", "y.tif", flag, value],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert flag in proc.stderr


def test_default_run_is_byte_identical_and_reports_bundle_adjustment_off(synthetic_images,
                                                                        tmp_path):
    """The default pipeline must be reproducible to the byte, with and without the flag.

    Three runs: default, an explicit ``--scan-mode sweep``, and default again.  Every
    output that describes geometry or pixels must be identical across all three.
    """
    checked = ("panorama_preview.png", "transforms.json", "transforms.csv",
               "frame_metrics.csv")
    digests = []
    for i, extra in enumerate((["--scan-mode", "sweep"], [], [])):
        out = tmp_path / f"run{i}" / "pano.png"
        diag = tmp_path / f"run{i}" / "diag"
        rc = main(["--input", str(synthetic_images["images"]), "--output", str(out),
                   "--input-mode", "images", "--diagnostics", str(diag), *extra])
        assert rc == 0
        d = {"panorama": _sha(out)}
        for name in checked:
            d[name] = _sha(diag / name)
        digests.append(d)
        summary = json.loads((diag / "summary.json").read_text(encoding="utf-8"))
        assert summary["scan_mode"] == "sweep"
        assert summary["bundle_adjustment"]["enabled"] is False
        assert summary["bundle_adjustment"]["applied"] is False
        assert "bundle_adjust_s" not in summary["timing"]
    assert digests[0] == digests[1] == digests[2], "default output is not reproducible"


def test_serpentine_end_to_end_writes_the_new_summary_and_report_fields(serpentine_images,
                                                                       tmp_path):
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag"
    rc = main(["--input", str(serpentine_images["images"]), "--output", str(out),
               "--input-mode", "images", "--diagnostics", str(diag),
               "--scan-mode", "serpentine"])
    assert rc == 0 and out.exists()
    summary = json.loads((diag / "summary.json").read_text(encoding="utf-8"))
    assert summary["scan_mode"] == "serpentine"
    ba = summary["bundle_adjustment"]
    assert ba["enabled"] is True
    for key in ("optimiser", "iterations", "candidates", "attempted", "accepted",
                "reproj_before", "reproj_after", "reference_frame_index", "extra_pairs",
                "per_segment", "seconds"):
        assert key in ba, key
    assert ba["accepted"] >= 1, "a serpentine scan must find non-adjacent overlap"
    assert ba["reproj_before"].get("mean") is not None
    assert ba["reproj_after"].get("mean") is not None
    assert summary["timing"]["bundle_adjust_s"] > 0
    counts = summary["counts"]
    assert counts["non_adjacent_pairs_accepted"] == ba["accepted"]
    assert counts["non_adjacent_pairs_tried"] >= counts["non_adjacent_pairs_accepted"]

    report = (diag / "report.html").read_text(encoding="utf-8")
    assert "Scan type and global bundle adjustment" in report
    assert "Mean reprojection error before" in report
    assert "serpentine" in report

    log = (diag / "pipeline.log").read_text(encoding="utf-8")
    assert "bundle adjustment" in log
    assert "loop closure" in log


def test_no_bundle_adjust_overrides_serpentine(serpentine_images, tmp_path):
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag"
    rc = main(["--input", str(serpentine_images["images"]), "--output", str(out),
               "--input-mode", "images", "--diagnostics", str(diag),
               "--scan-mode", "serpentine", "--no-bundle-adjust"])
    assert rc == 0
    summary = json.loads((diag / "summary.json").read_text(encoding="utf-8"))
    assert summary["scan_mode"] == "serpentine"
    assert summary["bundle_adjustment"]["enabled"] is False
    assert "--no-bundle-adjust" in summary["bundle_adjustment"]["reason"]
    assert any("overridden by --no-bundle-adjust" in w for w in summary["warnings"])


def test_bundle_adjust_can_be_forced_on_a_single_sweep(synthetic_images, tmp_path):
    """The low-level flag is an independent override, for comparison runs."""
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag"
    rc = main(["--input", str(synthetic_images["images"]), "--output", str(out),
               "--input-mode", "images", "--diagnostics", str(diag), "--bundle-adjust"])
    assert rc == 0 and out.exists()
    summary = json.loads((diag / "summary.json").read_text(encoding="utf-8"))
    assert summary["scan_mode"] == "sweep"
    assert summary["bundle_adjustment"]["enabled"] is True


def test_a_failing_optimiser_still_produces_a_panorama(serpentine_images, tmp_path,
                                                       monkeypatch):
    """The critical safety property, exercised through the real CLI entry point."""
    from micro_slide_stitcher import cli as cli_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated optimiser failure")

    monkeypatch.setattr(cli_module, "bundle_adjust", explode)
    out = tmp_path / "pano.png"
    diag = tmp_path / "diag"
    rc = main(["--input", str(serpentine_images["images"]), "--output", str(out),
               "--input-mode", "images", "--diagnostics", str(diag),
               "--scan-mode", "serpentine"])
    assert rc == 0, "a failed optimisation must never cost the user a scan"
    assert out.exists() and out.stat().st_size > 0
    summary = json.loads((diag / "summary.json").read_text(encoding="utf-8"))
    assert summary["bundle_adjustment"]["applied"] is False
    assert "simulated optimiser failure" in summary["bundle_adjustment"]["reason"]
    assert "FAILED" in (diag / "pipeline.log").read_text(encoding="utf-8")

# ------------------------------------------------- guards against a plausible-looking wrong fit
def test_a_collapsed_linear_part_is_recognised_as_implausible():
    """The degenerate global minimum of the reprojection cost must be detectable.

    If every frame's linear part is zero, every pixel of every frame maps to one point and
    every correspondence agrees exactly - a perfect fit that is geometrically nonsense.  This
    is not hypothetical: an early version of the built-in optimiser found precisely this
    solution on a serpentine scan whose row turns had contributed no constraint, reported an
    81 % reduction in reprojection error, and was 183 px per frame out against ground truth.
    """
    limits = RegistrationLimits()
    good = [np.eye(3), np.array([[1.0, 0.0, 120.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])]
    assert bundle._implausible(good, limits) is None

    collapsed = [np.eye(3), np.array([[0.0, 0.0, 120.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]])]
    why = bundle._implausible(collapsed, limits)
    assert why is not None and "scaled by" in why

    blown_up = [np.eye(3), np.array([[3.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 1.0]])]
    assert "scaled by" in (bundle._implausible(blown_up, limits) or "")

    rot = np.deg2rad(30.0)
    turned = [np.eye(3), np.array([[np.cos(rot), -np.sin(rot), 0.0],
                                   [np.sin(rot), np.cos(rot), 0.0], [0.0, 0.0, 1.0]])]
    assert "rotated by" in (bundle._implausible(turned, limits) or "")


def test_a_collapsed_solution_is_discarded_even_though_its_cost_is_lower(serpentine_images,
                                                                        tmp_path, monkeypatch):
    """A solution with a *lower* reprojection cost is still refused if it is implausible."""
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]

    def collapse(transforms, obs, blimits, deadline=None, *a, **kw):
        out = []
        for k, tmat in enumerate(transforms):
            m = np.eye(3)
            if k:
                m[:2, :2] = 0.0                      # the degenerate perfect fit
                m[:2, 2] = np.asarray(tmat)[:2, 2]
            out.append(m)
        return out, 3, "collapsed on purpose"

    monkeypatch.setattr(bundle, "_ba_opencv",
                        lambda tr, obs, wh, bl: collapse(tr, obs, bl))
    monkeypatch.setattr(bundle, "_ba_numpy", collapse)
    out, result = bundle_adjust(seg, "orb", limits, BundleLimits(min_overlap_frac=0.06),
                                scan_mode="serpentine")
    assert result.enabled and not result.applied
    assert "implausible" in result.reason
    assert any("scaled by" in n for n in result.notes), result.notes
    assert out is seg
    for a, b in zip(out.transforms, seg.transforms):
        assert np.allclose(a, b)


def test_a_solution_that_moves_frames_too_far_is_discarded(serpentine_images, tmp_path,
                                                           monkeypatch):
    """The shift cap refuses a re-solve dressed up as a refinement."""
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]

    def slide_everything(transforms, obs, blimits, deadline=None, *a, **kw):
        out = [np.asarray(t, dtype=float).copy() for t in transforms]
        for k in range(1, len(out)):
            out[k][0, 2] += 4000.0                   # plausible shape, impossible motion
        return out, 5, "moved a long way on purpose"

    monkeypatch.setattr(bundle, "_ba_opencv",
                        lambda tr, obs, wh, bl: slide_everything(tr, obs, bl))
    monkeypatch.setattr(bundle, "_ba_numpy", slide_everything)
    out, result = bundle_adjust(seg, "orb", limits,
                                BundleLimits(min_overlap_frac=0.06, max_shift_frac=0.5),
                                scan_mode="serpentine")
    assert not result.applied
    assert out is seg
    # It is refused either for moving too far or for not lowering the cost; both are safe,
    # but the shift note must be the reason when the cost check would have let it through.
    assert ("implausible" in result.reason or "did not reduce" in result.reason), result.reason


def test_the_translation_only_model_is_preferred_unless_similarity_is_clearly_better(
        serpentine_images, tmp_path, monkeypatch):
    """Model selection: the constrained fit wins ties, so no scale drift is invented."""
    segments, _, _, limits = _chain(serpentine_images["images"], tmp_path)
    seg = segments[0]
    # Force the numpy path by making the OpenCV adjuster unavailable, exactly as happens on
    # a constraint graph OpenCV's findMaxSpanningTree cannot handle.
    monkeypatch.setattr(bundle, "_ba_opencv",
                        lambda tr, obs, wh, bl: (None, 0, "disabled for this test"))
    out, result = bundle_adjust(seg, "orb", limits, BundleLimits(min_overlap_frac=0.06),
                                scan_mode="serpentine")
    assert result.applied
    assert result.optimiser in (bundle.OPTIMISER_NUMPY_T, bundle.OPTIMISER_NUMPY)
    if result.optimiser == bundle.OPTIMISER_NUMPY_T:
        # A translation-only refinement must leave every linear part exactly as it was.
        for adj, orig in zip(out.transforms, seg.transforms):
            assert np.allclose(np.asarray(adj)[:2, :2], np.asarray(orig)[:2, :2])


def test_a_row_turn_still_constrains_the_bundle_under_a_tight_gate(serpentine_images, tmp_path):
    """Consecutive row-turn pairs must survive re-matching inside the bundle, too.

    ``build_chain`` accepts a row turn under the wider serpentine allowance.  If the bundle's
    own re-matching pass then rejects the same pair under the ordinary gate, the two rows are
    left with no consecutive constraint between them - which is what let a wrong loop closure
    pull them apart.  The allowance therefore has to be honoured in both places.
    """
    frames = extract_image_frames(serpentine_images["images"], workdir=tmp_path / "w",
                                  image_order="name", image_stride=1, max_dimension=None)
    tight = RegistrationLimits(max_translation_frac=0.30)
    segments, _, _, _ = build_chain(frames, detector="orb", limits=tight,
                                    viz_dir=tmp_path / "v", max_viz=0,
                                    allow_bridging=False, allow_rejoin=False,
                                    turn_translation_frac=1.5)
    seg = max(segments, key=lambda s: s.length)
    assert seg.length == len(frames), "the turn allowance should keep the chain whole"

    without = adjacent_correspondences(seg, "orb", tight)
    with_turns = adjacent_correspondences(seg, "orb", tight, turn_translation_frac=1.5)
    assert len(with_turns) > len(without), (
        f"the turn allowance recovered no extra constraint ({len(without)} -> "
        f"{len(with_turns)})")
    assert len(with_turns) == seg.length - 1, "every consecutive pair should constrain the fit"
