"""Diagnostics writers: CSV/JSON metrics, transform dumps, camera path plot."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .logging_utils import get_logger
from .metrics import FrameMetric
from .register import PairResult, RejoinAttempt, Segment

FRAME_FIELDS = ["index", "filename", "timestamp_s", "focus_score", "duplicate_score",
                "mean_intensity", "accepted", "reason"]


def write_frame_metrics(metrics: list[FrameMetric], diag: Path) -> dict:
    diag.mkdir(parents=True, exist_ok=True)
    csv_path = diag / "frame_metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=FRAME_FIELDS)
        wr.writeheader()
        for m in metrics:
            wr.writerow(m.to_dict())
    json_path = diag / "frame_metrics.json"
    json_path.write_text(json.dumps([m.to_dict() for m in metrics], indent=2), encoding="utf-8")

    acc = diag / "accepted_frames.txt"
    rej = diag / "rejected_frames.txt"
    acc.write_text("".join(f"{m.index}\t{m.filename}\t{m.timestamp_s:.3f}\tfocus={m.focus_score}\n"
                           for m in metrics if m.accepted), encoding="utf-8")
    rej.write_text("".join(f"{m.index}\t{m.filename}\t{m.timestamp_s:.3f}\t{m.reason}\n"
                           for m in metrics if not m.accepted), encoding="utf-8")
    return {"frame_metrics_csv": str(csv_path), "frame_metrics_json": str(json_path),
            "accepted_frames": str(acc), "rejected_frames": str(rej)}


def write_transforms(pairs: list[PairResult], segments: list[Segment], diag: Path) -> dict:
    diag.mkdir(parents=True, exist_ok=True)
    payload = {
        "pairs": [p.to_dict() for p in pairs],
        "segments": [
            {
                "rank": i,
                "n_frames": s.length,
                "frame_indices": [f.index for f in s.frames],
                "rejoin_links": s.rejoin_links,
                "cumulative_transforms": [np.asarray(T).tolist() for T in s.transforms],
            }
            for i, s in enumerate(segments)
        ],
    }
    jp = diag / "transforms.json"
    jp.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    cp = diag / "transforms.csv"
    with cp.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["src_index", "dst_index", "detector", "model", "ok", "matches", "inliers",
                     "inlier_ratio", "mean_reproj_error", "tx", "ty", "scale", "rotation_deg",
                     "bridged", "rejoin", "reason"])
        for p in pairs:
            wr.writerow([p.src_index, p.dst_index, p.detector, p.model, p.ok, p.matches,
                         p.inliers, p.inlier_ratio, p.mean_reproj_error, p.tx, p.ty, p.scale,
                         p.rotation_deg, p.bridged, p.rejoin, p.reason])
    return {"transforms_json": str(jp), "transforms_csv": str(cp)}


def write_bundle_adjusted_transforms(segments: list[Segment], diag: Path) -> dict:
    """The transforms the panoramas were actually composed from, after bundle adjustment.

    ``transforms.json`` is written straight after registration and always holds the
    *chained* solution, so that a default run stays byte-for-byte reproducible.  When
    bundle adjustment changes the geometry this second file records what was really used,
    in the same layout, so the two can be compared directly.  It is only written when at
    least one segment was refined.
    """
    diag.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "cumulative transforms after global bundle adjustment; compare with "
                "transforms.json, which holds the pairwise chained solution",
        "segments": [
            {
                "rank": i,
                "n_frames": s.length,
                "frame_indices": [f.index for f in s.frames],
                "rejoin_links": s.rejoin_links,
                "cumulative_transforms": [np.asarray(T).tolist() for T in s.transforms],
            }
            for i, s in enumerate(segments)
        ],
    }
    jp = diag / "transforms_bundle_adjusted.json"
    jp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"transforms_bundle_adjusted_json": str(jp)}


def write_rejoin_attempts(attempts: list[RejoinAttempt], diag: Path) -> dict:
    """Dump every wide-baseline segment-rejoin attempt (accepted and rejected)."""
    diag.mkdir(parents=True, exist_ok=True)
    jp = diag / "rejoin_attempts.json"
    payload = {
        "n_attempts": len(attempts),
        "n_accepted": sum(1 for a in attempts if a.accepted),
        "attempts": [a.to_dict() for a in attempts],
    }
    jp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"rejoin_attempts_json": str(jp)}


def segment_pairs(segment: Segment, pairs: list[PairResult]) -> list[PairResult]:
    """The registered pairs that actually chain the frames of one segment together."""
    lookup: dict[tuple[int, int], PairResult] = {}
    for p in pairs:
        if p.ok:
            lookup[(p.src_index, p.dst_index)] = p
    out: list[PairResult] = []
    for a, b in zip(segment.frames, segment.frames[1:]):
        p = lookup.get((a.index, b.index))
        if p is not None:
            out.append(p)
    return out


def plot_camera_path(trajectory: list[tuple[float, float]], out_path: Path,
                     frame_wh: tuple[int, int] | None = None,
                     label: str = "stitched segment") -> Path:
    """Camera path plot. Uses matplotlib (a documented dependency)."""
    log = get_logger()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        log.warning("matplotlib unavailable (%s); camera path not plotted", exc)
        return out_path
    xs = [p[0] for p in trajectory]
    ys = [p[1] for p in trajectory]
    fig, ax = plt.subplots(figsize=(9, 7), dpi=110)
    ax.plot(xs, ys, "-", color="#3b7dd8", lw=1.4, zorder=2)
    sc = ax.scatter(xs, ys, c=range(len(xs)), cmap="viridis", s=26, zorder=3)
    if xs:
        ax.annotate("start", (xs[0], ys[0]), textcoords="offset points", xytext=(6, 6),
                    color="green")
        ax.annotate("end", (xs[-1], ys[-1]), textcoords="offset points", xytext=(6, 6),
                    color="red")
    if frame_wh:
        ax.set_title(f"Camera path of {label} ({len(xs)} frames, "
                     f"frame {frame_wh[0]}x{frame_wh[1]} px)")
    else:
        ax.set_title(f"Camera path of {label} ({len(xs)} frames)")
    ax.set_xlabel("panorama x (px)")
    ax.set_ylabel("panorama y (px)")
    ax.invert_yaxis()
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label="chain position")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def registration_stats(pairs: list[PairResult]) -> dict:
    ok = [p for p in pairs if p.ok]
    bad = [p for p in pairs if not p.ok]
    def stat(vals):
        if not vals:
            return {"n": 0}
        arr = np.asarray(vals, dtype=float)
        arr = arr[~np.isnan(arr)]
        if arr.size == 0:
            return {"n": 0}
        return {"n": int(arr.size), "min": round(float(arr.min()), 4),
                "median": round(float(np.median(arr)), 4),
                "mean": round(float(arr.mean()), 4), "max": round(float(arr.max()), 4)}
    return {
        "pairs_attempted": len(pairs),
        "pairs_registered": len(ok),
        "pairs_failed": len(bad),
        "pairs_bridged": sum(1 for p in ok if p.bridged),
        "rejoins_used": sum(1 for p in ok if p.rejoin),
        "sift_fallbacks": sum(1 for p in ok if p.detector == "sift"),
        "homography_fallbacks": sum(1 for p in ok if p.model == "homography"),
        "inliers": stat([p.inliers for p in ok]),
        "inlier_ratio": stat([p.inlier_ratio for p in ok]),
        "mean_reproj_error_px": stat([p.mean_reproj_error for p in ok]),
        "abs_translation_px": stat([abs(p.tx) + abs(p.ty) for p in ok]),
        "failure_reasons": sorted({p.reason for p in bad}),
    }
