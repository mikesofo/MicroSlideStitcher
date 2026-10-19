#!/usr/bin/env python3
"""Measure what bundle adjustment actually does, against known ground truth.

Synthetic scans are generated with ``scripts/make_synthetic_video.py``, which writes one
lossless still per scan position *and* a ``*.truth.json`` holding the exact position of
every one of them.  Because the image-sequence input mode keeps ``Frame.index`` equal to
the ordered source index, estimated frame ``k`` is scan position ``k``: the comparison
needs no alignment step and no fitting.

For each case (short sweep, long sweep, serpentine) the pipeline is run in four
configurations - sweep mode, sweep mode with ``--bundle-adjust``, serpentine mode, and
serpentine mode with ``--no-bundle-adjust`` - and for each run we report

* RMS and maximum deviation of the estimated frame positions from ground truth, in px;
* mean/median/max reprojection error before and after the optimisation, as the pipeline
  itself measured it;
* how many non-adjacent pairs were shortlisted, matched and accepted;
* wall-clock runtime, and the share of it spent on discovery plus optimisation.

Frame 0 is the gauge in both the estimate and the truth (its transform is the identity),
so the deviations are directly comparable and nothing is fitted to flatter the result.

Usage:
    python scripts/evaluate_bundle_adjustment.py --workdir /tmp/ba_eval \
        --out /home/user/workspace/ba_evaluation.json
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts" / "make_synthetic_video.py"


def generate(case: dict, workdir: Path) -> dict:
    """Render one synthetic scan; returns its ground truth dictionary."""
    out = workdir / case["name"] / "scan.mp4"
    images = workdir / case["name"] / "images"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(GEN), "--out", str(out), "--image-dir", str(images),
           "--frame-size", str(case["frame_w"]), str(case["frame_h"]),
           "--steps", str(case["steps"]), "--step-x", str(case["step_x"]),
           "--step-y", str(case["step_y"]), "--cols", str(case["cols"]),
           "--hold", "2", "--seed", str(case.get("seed", 7))]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    truth = json.loads(out.with_suffix(".truth.json").read_text(encoding="utf-8"))
    truth["images"] = str(images)
    return truth


def run_pipeline(images: Path, out_dir: Path, extra: list[str]) -> tuple[dict, float, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "micro_slide_stitcher",
           "--input", str(images), "--output", str(out_dir / "pano.png"),
           "--diagnostics", str(out_dir / "diag"), "--input-mode", "images", *extra]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    elapsed = time.time() - t0
    summary_path = out_dir / "diag" / "summary.json"
    summary = (json.loads(summary_path.read_text(encoding="utf-8"))
               if summary_path.exists() else {"verdict": "FAILED", "stderr": proc.stderr[-2000:]})
    return summary, elapsed, proc.returncode


def deviations(diag: Path, truth: dict) -> dict:
    """Position deviation of the largest segment's frames from ground truth, in px.

    ``transforms.json`` always holds the *chained* solution (it is written straight after
    registration, so a default run stays byte-identical).  When bundle adjustment changed
    the geometry, the pipeline also writes ``transforms_bundle_adjusted.json`` - the
    transforms the panorama was really composed from - and that is what must be scored.
    """
    adjusted = diag / "transforms_bundle_adjusted.json"
    used = adjusted if adjusted.exists() else diag / "transforms.json"
    data = json.loads(used.read_text(encoding="utf-8"))
    segments = sorted(data["segments"], key=lambda s: -s["n_frames"])
    seg = segments[0]
    positions = truth["positions"]
    indices = seg["frame_indices"]
    origin = np.array(positions[indices[0]], float)
    est, true = [], []
    for k, mat in zip(indices, seg["cumulative_transforms"]):
        m = np.array(mat, float)
        est.append(m[:2, 2])
        true.append(np.array(positions[k], float) - origin)
    est_a, true_a = np.array(est), np.array(true)
    err = np.linalg.norm(est_a - true_a, axis=1)
    return {
        "scored_file": used.name,
        "frames_compared": len(indices),
        "segments_in_run": len(data["segments"]),
        "largest_segment_frames": seg["n_frames"],
        "rms_position_error_px": round(float(math.sqrt(float((err ** 2).mean()))), 3),
        "max_position_error_px": round(float(err.max()), 3),
        "mean_position_error_px": round(float(err.mean()), 3),
        "last_frame_error_px": round(float(err[-1]), 3),
    }


def evaluate(case: dict, workdir: Path) -> dict:
    truth = generate(case, workdir)
    images = Path(truth["images"])
    configs = {
        "sweep": ["--scan-mode", "sweep"],
        "sweep+ba": ["--scan-mode", "sweep", "--bundle-adjust"],
        "serpentine": ["--scan-mode", "serpentine"],
        "serpentine-no-ba": ["--scan-mode", "serpentine", "--no-bundle-adjust"],
    }
    # Optional extra pair of runs with a user-tightened translation gate: this is where a
    # row turn (reverse direction and step down in one move) can break a sweep-mode chain.
    tight = case.get("tight_translation_frac")
    if tight:
        configs["sweep-tight-gate"] = ["--scan-mode", "sweep",
                                       "--max-translation-frac", str(tight)]
        configs["serpentine-tight-gate"] = ["--scan-mode", "serpentine",
                                            "--max-translation-frac", str(tight)]
    results = {}
    for label, extra in configs.items():
        out_dir = workdir / case["name"] / label.replace("+", "_")
        summary, elapsed, code = run_pipeline(images, out_dir, extra)
        entry: dict = {"command": " ".join(extra), "exit_code": code,
                       "verdict": summary.get("verdict"),
                       "runtime_s": round(elapsed, 2),
                       "timing": summary.get("timing", {})}
        diag = out_dir / "diag"
        if (diag / "transforms.json").exists():
            entry.update(deviations(diag, truth))
        ba = summary.get("bundle_adjustment", {}) or {}
        entry["bundle_adjustment"] = {
            "enabled": ba.get("enabled"), "applied": ba.get("applied"),
            "optimiser": ba.get("optimiser"), "iterations": ba.get("iterations"),
            "reason": ba.get("reason"),
            "non_adjacent_candidates": ba.get("candidates"),
            "non_adjacent_matched": ba.get("attempted"),
            "non_adjacent_accepted": ba.get("accepted"),
            "correspondences": ba.get("correspondences"),
            "reproj_before": ba.get("reproj_before"),
            "reproj_after": ba.get("reproj_after"),
            "position_shift": ba.get("position_shift"),
            "seconds": ba.get("seconds"),
        }
        counts = summary.get("counts", {}) or {}
        entry["segments_found"] = counts.get("segments")
        entry["segments_written"] = counts.get("segments_written")
        entry["frames_written_total"] = counts.get("frames_written_total")
        entry["registration_reproj_median_px"] = (
            (summary.get("registration_stats", {}) or {})
            .get("mean_reproj_error_px", {}).get("median"))
        results[label] = entry
        print(f"  {case['name']:<12} {label:<18} "
              f"rms={entry.get('rms_position_error_px', 'n/a')} "
              f"max={entry.get('max_position_error_px', 'n/a')} "
              f"segs={entry.get('segments_written')} {entry['runtime_s']}s")
    return {"case": case, "truth_positions": truth["positions"],
            "expected_panorama": truth["expected_panorama"], "runs": results}


CASES = [
    # a short single sweep: one row, eight positions, 50 % overlap at 640x480
    dict(name="short_sweep", steps=7, cols=10_000, step_x=120, step_y=0,
         frame_w=640, frame_h=480),
    # a long single sweep: one row, sixty positions - the case where a chain can drift
    dict(name="long_sweep", steps=59, cols=10_000, step_x=120, step_y=0,
         frame_w=640, frame_h=480),
    # a serpentine scan: six columns by five rows, stepping down half a field per row,
    # so consecutive rows overlap and genuine non-adjacent matches exist.  The tight gate
    # runs show what a row turn does to a sweep-mode chain when the user has narrowed
    # --max-translation-frac (0.45 of the frame, a reasonable setting for a slow scan).
    dict(name="serpentine", steps=29, cols=6, step_x=120, step_y=240,
         frame_w=640, frame_h=480, tight_translation_frac=0.45),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=None, help="run only these case names")
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    cases = [c for c in CASES if args.only is None or c["name"] in args.only]
    report = {"cases": []}
    for case in cases:
        print(f"case {case['name']}")
        report["cases"].append(evaluate(case, args.workdir))
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
