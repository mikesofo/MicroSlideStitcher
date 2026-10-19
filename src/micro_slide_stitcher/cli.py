"""Command line interface and pipeline orchestration."""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from .diagnostics import (plot_camera_path, registration_stats, segment_pairs,
                          write_bundle_adjusted_transforms, write_frame_metrics,
                          write_rejoin_attempts, write_transforms)
from .bundle import BundleLimits, BundleResult, bundle_adjust, disabled_result
from .extract import extract_frames, gate_frames
from .images import IMAGE_SUFFIXES, extract_image_frames, is_image_path, probe_image_sequence
from .logging_utils import setup_logger
from .probe import MultimediaToolMissingError, ProbeError, probe_video, write_metadata
from .register import RegistrationLimits, Segment, build_chain
from .report import write_report
from .stitch import MemoryGuardError, auto_crop, compose, save_panorama

EPILOG = """\
worked examples
---------------
# 1. simplest possible run (all defaults: 2 fps, ORB, CLAHE, feather blending)
python -m micro_slide_stitcher --input scan_10x.mp4 --output pano.tif

# 2. fast low-resolution preview first (recommended before a full-res run)
python -m micro_slide_stitcher --input scan_10x.mp4 --output preview.png \\
    --max-dimension 960 --sampling-fps 2 --diagnostics diag_preview

# 3. the full documented invocation
python -m micro_slide_stitcher \\
    --input /data/scan_20x.mp4 \\
    --output /data/panorama.tif \\
    --sampling-fps 2 \\
    --detector orb \\
    --normalize clahe \\
    --blur-threshold 10 \\
    --diagnostics /data/diagnostics

# 4. difficult, low-texture or slightly blurry footage
python -m micro_slide_stitcher --input scan.mov --output pano.tif \\
    --detector sift --sampling-fps 3 --blur-threshold 4 --dedup-threshold 0.999 \\
    --blend multiband --keep-intermediates

# 5. a scan with a fast pan in the middle: search wider when trying to rejoin the
#    two halves, and write every piece of >= 5 frames that still cannot be joined
python -m micro_slide_stitcher --input scan.mp4 --output pano.tif \\
    --rejoin-window 4 --rejoin-max-translation-frac 2.5 --min-segment-frames 5

# 6. reproduce the old behaviour (no wide-baseline recovery, longest piece only)
python -m micro_slide_stitcher --input scan.mp4 --output pano.tif \\
    --no-rejoin --min-segment-frames 100000

# 7. a serpentine scan (rows back and forth, each row stepped down by about half a
#    field): the rows overlap, so cross-row matches are searched for and all frame
#    positions are refined together, which removes the drift of a single long chain
python -m micro_slide_stitcher --input serpentine.mp4 --output pano.tif \\
    --scan-mode serpentine

# 8. the same global refinement forced onto a single sweep, for comparison
python -m micro_slide_stitcher --input sweep.mp4 --output pano.tif \\
    --bundle-adjust --ba-max-iterations 300

exit codes: 0 = panorama written, 2 = usage/input error, 3 = pipeline could not
produce a panorama (diagnostics are still preserved).
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m micro_slide_stitcher",
        description="Stitch a smartphone-through-microscope-eyepiece video or overlapping "
                    "still images into one high-resolution panorama of a histology slide.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", required=True,
                   help="input video, image directory, one image, or quoted image glob; "
                        "never modified")
    p.add_argument("--input-mode", choices=["auto", "video", "images"], default="auto",
                   help="interpret --input as auto-detected video or images (default: "
                        "%(default)s); use images to force a directory/glob")
    p.add_argument("--output", required=True, type=Path,
                   help="output panorama path; extension decides format (.tif/.tiff = "
                        "compressed TIFF, .png, .jpg)")
    p.add_argument("--sampling-fps", type=float, default=2.0,
                   help="frames sampled per second of video (default: %(default)s). "
                        "Raise for fast panning, lower for slow scans.")
    p.add_argument("--image-order", choices=["name", "mtime"], default="name",
                   help="image-sequence order: name = natural filename order (IMG_2 before "
                        "IMG_10), mtime = modification time (default: %(default)s)")
    p.add_argument("--image-stride", type=int, default=1, metavar="N",
                   help="image-sequence sampling: take every Nth ordered image (default: "
                        "%(default)s)")
    p.add_argument("--scan-mode", choices=["sweep", "serpentine"], default="sweep",
                   help="how the slide was scanned (default: %(default)s). sweep = one "
                        "continuous pan, registered as a pairwise chain - unchanged "
                        "behaviour. serpentine = back and forth in rows, each row stepped "
                        "down so it overlaps the row above: this turns on non-adjacent "
                        "(loop-closure) match discovery and global bundle adjustment, "
                        "searches harder for cross-row overlap, and tolerates the larger "
                        "jump at a row turn. Slower, but geometrically better.")
    p.add_argument("--bundle-adjust", action="store_true",
                   help="refine all frame positions together by minimising the total "
                        "reprojection error instead of trusting the pairwise chain. "
                        "Implied by --scan-mode serpentine; give it explicitly to bundle "
                        "adjust a single sweep as well (useful for comparison). If the "
                        "optimisation fails or makes the error worse the chained solution "
                        "is kept and a panorama is still written.")
    p.add_argument("--no-bundle-adjust", action="store_true",
                   help="never bundle adjust, even in --scan-mode serpentine (which would "
                        "otherwise imply it); non-adjacent match discovery is skipped too. "
                        "Cannot be combined with --bundle-adjust.")
    p.add_argument("--ba-max-iterations", type=int, default=200, metavar="N",
                   help="iteration cap for the bundle adjustment (default: %(default)s)")
    p.add_argument("--ba-max-seconds", type=float, default=120.0, metavar="S",
                   help="wall-clock cap in seconds for non-adjacent discovery plus bundle "
                        "adjustment (default: %(default)s); on expiry the chained solution "
                        "or the best refinement so far is kept")
    p.add_argument("--ba-max-pairs", type=int, default=60, metavar="N",
                   help="cap on how many non-adjacent frame pairs are really feature-matched "
                        "(default: %(default)s). Candidates are shortlisted by predicted "
                        "overlap first, so the full O(n^2) set is never matched.")
    p.add_argument("--ba-min-overlap", type=float, default=0.15, metavar="F",
                   help="a non-adjacent pair is only worth matching if the current placement "
                        "predicts it shares at least this fraction of a frame (default: "
                        "%(default)s); used in --scan-mode sweep")
    p.add_argument("--ba-max-shift-frac", type=float, default=0.5, metavar="F",
                   help="how far the refinement may move a frame, as a fraction of the "
                        "frame diagonal (default: %(default)s). A refinement nudges a "
                        "solution; anything larger is a re-solve and is discarded in favour "
                        "of the chained solution, because a lower reprojection error is not "
                        "by itself proof of a more accurate result.")
    p.add_argument("--serpentine-min-overlap", type=float, default=0.06, metavar="F",
                   help="the same threshold used in --scan-mode serpentine (default: "
                        "%(default)s). Deliberately lower than --ba-min-overlap because in "
                        "a serpentine scan the row above is expected to overlap and is worth "
                        "looking for even when the initial placement predicts only a sliver.")
    p.add_argument("--serpentine-turn-translation-frac", type=float, default=1.5, metavar="F",
                   help="in --scan-mode serpentine only: translation allowance for a pair "
                        "that the ordinary --max-translation-frac gate rejected (default: "
                        "%(default)s of the frame size). This is the row turn, where the pan "
                        "reverses direction and steps down, and without the wider allowance "
                        "the chain can break into one segment per row.")
    p.add_argument("--detector", choices=["orb", "sift"], default="orb",
                   help="primary feature detector (default: %(default)s); SIFT is "
                        "automatically retried per pair when ORB fails")
    p.add_argument("--normalize", choices=["none", "temporal", "clahe"], default="clahe",
                   help="exposure handling: none | temporal (ffmpeg normalize filter, damps "
                        "gradual drift) | clahe (colour-safe local contrast on LAB-L) "
                        "(default: %(default)s)")
    p.add_argument("--blur-threshold", type=float, default=10.0,
                   help="reject frames whose variance-of-Laplacian focus score is below this "
                        "(default: %(default)s). Eyepiece video scores are low; check "
                        "frame_metrics.csv and calibrate.")
    p.add_argument("--dedup-threshold", type=float, default=0.995,
                   help="reject a frame if its similarity to the previous accepted frame "
                        "exceeds this (0-1, default: %(default)s)")
    p.add_argument("--max-dimension", type=int, default=None,
                   help="downscale frames so the long edge is at most this many pixels "
                        "(preview/low-memory runs)")
    p.add_argument("--diagnostics", type=Path, default=None,
                   help="diagnostics directory (default: <output_dir>/diagnostics)")
    p.add_argument("--keep-intermediates", action="store_true",
                   help="keep the raw extracted candidate frames in <diagnostics>/_work")
    p.add_argument("--blend", choices=["feather", "multiband"], default="feather",
                   help="blending mode (default: %(default)s)")
    p.add_argument("--min-inliers", type=int, default=25,
                   help="minimum RANSAC inliers for a pair to be accepted (default: %(default)s)")
    p.add_argument("--min-inlier-ratio", type=float, default=0.15,
                   help="minimum inlier/match ratio (default: %(default)s)")
    p.add_argument("--max-rotation-deg", type=float, default=5.0,
                   help="reject pairs rotating more than this (default: %(default)s)")
    p.add_argument("--max-translation-frac", type=float, default=0.92,
                   help="reject pairs translating more than this fraction of the frame "
                        "(i.e. too little overlap) (default: %(default)s)")
    p.add_argument("--no-homography-fallback", action="store_true",
                   help="never fall back to a full homography (similarity model only)")
    p.add_argument("--no-bridge", action="store_true",
                   help="do not try to bridge over a single unregistrable frame")
    p.add_argument("--no-rejoin", action="store_true",
                   help="disable wide-baseline segment recovery: disconnected segments are "
                        "never re-joined (pre-0.2 behaviour)")
    p.add_argument("--rejoin-window", type=int, default=3, metavar="K",
                   help="wide-baseline recovery window (default: %(default)s): the last K "
                        "frames of a segment are matched against the first K frames of the "
                        "next one (all KxK combinations, SIFT directly)")
    p.add_argument("--rejoin-max-translation-frac", type=float, default=2.0,
                   help="translation allowance for rejoin candidates as a fraction of the "
                        "frame size (default: %(default)s). Deliberately larger than "
                        "--max-translation-frac because a chain break is usually caused by a "
                        "fast pan that moved the field of view by more than one frame.")
    p.add_argument("--min-segment-frames", type=int, default=3, metavar="N",
                   help="after recovery, stitch every remaining segment with at least N "
                        "frames (default: %(default)s), not just the largest. Extra segments "
                        "are written as <output>_seg02.<ext>, _seg03.<ext>, ... in descending "
                        "frame-count order; smaller segments are reported as skipped.")
    p.add_argument("--max-viz", type=int, default=80,
                   help="cap on the number of match visualisation PNGs (default: %(default)s)")
    p.add_argument("--memory-limit-gb", type=float, default=3.0,
                   help="abort with advice if the canvas would need more working memory "
                        "than this (default: %(default)s GiB)")
    p.add_argument("--version", action="version", version=f"MicroSlideStitcher {__version__}")
    return p


def segment_output_path(base: Path, rank: int) -> Path:
    """`base` for the largest segment (rank 1), `base_seg02.ext`, `base_seg03.ext`, ... after."""
    if rank <= 1:
        return base
    return base.with_name(f"{base.stem}_seg{rank:02d}{base.suffix}")


def segment_suffix(rank: int) -> str:
    """Diagnostics filename suffix: '' for the primary segment, '_seg02' etc. after."""
    return "" if rank <= 1 else f"_seg{rank:02d}"


def _segment_span(seg: Segment, input_mode: str) -> dict:
    """Frame indices plus a truthful source coordinate span for one segment."""
    result = {
        "n_frames": seg.length,
        "frame_indices": [f.index for f in seg.frames],
    }
    if input_mode == "images":
        result["image_index_first"] = min(f.index for f in seg.frames)
        result["image_index_last"] = max(f.index for f in seg.frames)
    else:
        result["time_start_s"] = round(min(f.timestamp_s for f in seg.frames), 3)
        result["time_end_s"] = round(max(f.timestamp_s for f in seg.frames), 3)
    return result


def _verdict(counts: dict, stats: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    stitched = counts["frames_stitched"]
    accepted = counts["accepted"]
    written = counts.get("frames_written_total", stitched)
    n_written = counts.get("segments_written", 1)
    if stitched < 2:
        return "FAILED", ["fewer than two frames could be chained together"]
    frac = stitched / max(accepted, 1)
    med_inl = stats["inliers"].get("median", 0)
    if counts["segments"] == 1 and frac >= 0.9 and med_inl >= 100:
        return "SUCCESSFUL", notes
    if n_written > 1:
        notes.append(
            f"the scan could NOT be chained into a single panorama: it is written as "
            f"{n_written} separate pieces ({counts['segments']} disconnected segment(s) "
            f"remained after wide-baseline rejoin). The break(s) happen where consecutive "
            f"sampled frames stopped overlapping (fast pan and/or defocus); the rejoin pass "
            f"could not find a plausible transform across them either - see "
            f"rejoin_attempts.json. Pan more slowly or raise --sampling-fps to avoid this.")
        if written >= accepted:
            notes.append(f"all {accepted} accepted frames are accounted for in the "
                         f"{n_written} written panorama(s)")
            return "PARTIALLY SUCCESSFUL", notes
        notes.append(f"{written}/{accepted} accepted frames ({written / max(accepted, 1):.0%}) "
                     f"ended up in a written panorama; the rest are in segments smaller than "
                     f"--min-segment-frames or were bridged over")
        return "PARTIALLY SUCCESSFUL", notes
    if frac >= 0.5:
        notes.append(f"{stitched}/{accepted} accepted frames ({frac:.0%}) ended up in the "
                     f"stitched segment; {counts['segments']} segment(s) found")
        return "PARTIALLY SUCCESSFUL", notes
    notes.append(f"only {stitched}/{accepted} accepted frames ({frac:.0%}) could be chained")
    return "PARTIALLY SUCCESSFUL", notes


def run(args: argparse.Namespace) -> int:
    t0 = time.time()
    timing: dict[str, float] = {}
    out_path: Path = args.output.expanduser().resolve()
    diag: Path = (args.diagnostics or out_path.parent / "diagnostics").expanduser().resolve()
    diag.mkdir(parents=True, exist_ok=True)
    log = setup_logger(diag / "pipeline.log")
    log.info("MicroSlideStitcher %s", __version__)
    log.info("command: %s", " ".join(sys.argv))

    workdir = diag / "_work"
    accepted_dir = diag / "accepted_frames"
    matches_dir = diag / "matches"
    warnings: list[str] = []
    outputs: dict[str, str] = {}
    input_mode: str = args.resolved_input_mode

    settings = {k: str(v) for k, v in sorted(vars(args).items())}

    serpentine = args.scan_mode == "serpentine"
    ba_enabled = (not args.no_bundle_adjust) and (args.bundle_adjust or serpentine)
    blimits = BundleLimits(
        min_overlap_frac=(args.serpentine_min_overlap if serpentine else args.ba_min_overlap),
        max_extra_pairs=args.ba_max_pairs,
        max_iterations=args.ba_max_iterations,
        max_seconds=args.ba_max_seconds,
        max_shift_frac=args.ba_max_shift_frac)
    ba_off_reason = (
        "bundle adjustment is off: --scan-mode sweep and --bundle-adjust was not given"
        if not args.no_bundle_adjust else
        "bundle adjustment was explicitly disabled with --no-bundle-adjust")
    ba_results: list[BundleResult] = []
    adjusted_segments: list[Segment] = []
    ba_seconds = 0.0
    log.info("scan mode: %s; bundle adjustment %s", args.scan_mode,
             "ENABLED" if ba_enabled else "off")
    if serpentine:
        log.info("serpentine scan: non-adjacent (cross-row) match discovery uses "
                 "--serpentine-min-overlap %.3f and the row-turn translation allowance is "
                 "--serpentine-turn-translation-frac %.2f",
                 args.serpentine_min_overlap, args.serpentine_turn_translation_frac)
    if args.bundle_adjust and serpentine:
        log.info("--bundle-adjust is redundant with --scan-mode serpentine, which already "
                 "implies it")
    if args.no_bundle_adjust and not serpentine:
        msg = ("--no-bundle-adjust has no effect in --scan-mode sweep, where bundle "
               "adjustment is off anyway")
        log.warning(msg)
        warnings.append(msg)
    if args.no_bundle_adjust and serpentine:
        msg = ("--scan-mode serpentine was overridden by --no-bundle-adjust: the row-turn "
               "translation allowance still applies, but frame positions are NOT refined "
               "globally and no cross-row overlap is looked for")
        log.warning(msg)
        warnings.append(msg)

    try:
        # 1. probe -------------------------------------------------------------
        t = time.time()
        if input_mode == "images":
            meta = probe_image_sequence(args.input, args.image_order, args.image_stride,
                                        args.max_dimension)
        else:
            meta = probe_video(Path(args.input))
        write_metadata(meta, diag)
        timing["probe_s"] = round(time.time() - t, 3)

        # 2/3. extract + gate --------------------------------------------------
        t = time.time()
        if input_mode == "images":
            log.warning("--sampling-fps is ignored for image-sequence input; using "
                        "--image-stride %d", args.image_stride)
            warnings.append("--sampling-fps is ignored for image-sequence input; images are "
                            "selected with --image-stride.")
            frames = extract_image_frames(args.input, workdir, args.image_order,
                                          args.image_stride, args.max_dimension)
        else:
            frames = extract_frames(Path(args.input), workdir, args.sampling_fps, args.normalize,
                                   args.max_dimension)
        timing["extract_s"] = round(time.time() - t, 3)

        t = time.time()
        accepted, metrics = gate_frames(frames, args.blur_threshold, args.dedup_threshold,
                                        args.normalize, accepted_dir,
                                        diag / "exposure_comparison.png")
        outputs.update(write_frame_metrics(metrics, diag))
        timing["gate_s"] = round(time.time() - t, 3)

        counts = {
            "candidates": len(metrics),
            "accepted": len(accepted),
            "rejected_blur": sum(1 for m in metrics if not m.accepted and m.reason.startswith("blurry")),
            "rejected_duplicate": sum(1 for m in metrics if not m.accepted
                                      and m.reason.startswith("near-duplicate")),
            "rejected_other": sum(1 for m in metrics if not m.accepted
                                  and not m.reason.startswith(("blurry", "near-duplicate"))),
            "frames_stitched": 0,
            "segments": 0,
        }
        if len(accepted) < 2:
            raise RuntimeError(
                f"only {len(accepted)} frame(s) passed quality gating out of {len(metrics)} "
                "candidates - nothing to stitch. Lower --blur-threshold (see "
                "frame_metrics.csv for the actual focus scores) or raise --dedup-threshold.")

        # 4. registration ------------------------------------------------------
        t = time.time()
        limits = RegistrationLimits(min_inliers=args.min_inliers,
                                    min_inlier_ratio=args.min_inlier_ratio,
                                    max_rotation_deg=args.max_rotation_deg,
                                    max_translation_frac=args.max_translation_frac)
        matches_dir.mkdir(parents=True, exist_ok=True)
        segments, pairs, wr, rejoin_attempts = build_chain(
            accepted, args.detector, limits, matches_dir, max_viz=args.max_viz,
            allow_homography_fallback=not args.no_homography_fallback,
            allow_bridging=not args.no_bridge,
            allow_rejoin=not args.no_rejoin,
            rejoin_window=args.rejoin_window,
            rejoin_max_translation_frac=args.rejoin_max_translation_frac,
            turn_translation_frac=(args.serpentine_turn_translation_frac
                                   if serpentine else None))
        warnings += wr
        outputs.update(write_transforms(pairs, segments, diag))
        if rejoin_attempts:
            outputs.update(write_rejoin_attempts(rejoin_attempts, diag))
            log.info("rejoin pass: %d attempt(s), %d accepted", len(rejoin_attempts),
                     sum(1 for a in rejoin_attempts if a.accepted))
        stats = registration_stats(pairs)
        timing["register_s"] = round(time.time() - t, 3)
        log.info("registration stats: %s", json.dumps(stats["inliers"]))

        best = segments[0]
        counts["segments"] = len(segments)
        counts["frames_stitched"] = best.length
        if best.length < 2:
            raise RuntimeError(
                "no two adjacent frames could be registered to each other. Likely causes: "
                "insufficient overlap (raise --sampling-fps), heavy motion blur, or "
                "low-texture tissue (try --detector sift). See transforms.csv for per-pair "
                "reasons.")

        # 5. panorama(s) -------------------------------------------------------
        # The largest segment keeps the exact paths of a single-segment run; every further
        # segment with >= --min-segment-frames frames is written alongside it.
        t = time.time()
        to_stitch: list[Segment] = [best]
        skipped_segments: list[dict] = []
        for seg in segments[1:]:
            if seg.length >= max(args.min_segment_frames, 2):
                to_stitch.append(seg)
            else:
                info = _segment_span(seg, input_mode)
                skipped_segments.append(info)
                if input_mode == "images":
                    msg = (f"segment of {seg.length} frame(s) SKIPPED (below "
                           f"--min-segment-frames {args.min_segment_frames}): image indices "
                           f"{info['frame_indices']}")
                else:
                    msg = (f"segment of {seg.length} frame(s) SKIPPED (below "
                           f"--min-segment-frames {args.min_segment_frames}): frame indices "
                           f"{info['frame_indices']}, t = {info['time_start_s']:.1f}-"
                           f"{info['time_end_s']:.1f} s")
                log.warning(msg)
                warnings.append(msg)

        segment_reports: list[dict] = []
        primary: dict | None = None
        for rank, seg in enumerate(to_stitch, start=1):
            suffix = segment_suffix(rank)
            seg_out = segment_output_path(out_path, rank)
            preview_name = f"panorama_preview{suffix}.png"
            cam_name = f"camera_path{suffix}.png"
            if ba_enabled:
                t_ba = time.time()
                try:
                    seg, bres = bundle_adjust(
                        seg, args.detector, limits, blimits, scan_mode=args.scan_mode,
                        viz_dir=matches_dir, max_viz=args.max_viz,
                        turn_translation_frac=(args.serpentine_turn_translation_frac
                                               if serpentine else None))
                except (cv2.error, RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
                    # A failed optimisation must never cost the user a scan.
                    bres = disabled_result(args.scan_mode,
                                           reason=f"bundle adjustment raised {type(exc).__name__}: "
                                                  f"{exc}; the chained solution is kept")
                    bres.enabled = True
                    log.error("bundle adjustment FAILED for segment %d (%s); keeping the "
                              "chained solution and continuing", rank, exc)
                ba_seconds += time.time() - t_ba
                ba_results.append(bres)
                prefix = "" if rank == 1 else f"segment {rank}: "
                if bres.applied:
                    warnings.append(f"{prefix}bundle adjustment {bres.reason}")
                else:
                    warnings.append(f"{prefix}bundle adjustment did NOT change the result - "
                                    f"{bres.reason}")
                for note in bres.notes:
                    warnings.append(f"{prefix}{note}")
            else:
                ba_results.append(disabled_result(args.scan_mode, reason=ba_off_reason))
            adjusted_segments.append(seg)
            try:
                pano, mask, sres = compose(
                    seg, blend=args.blend,
                    memory_limit_bytes=int(args.memory_limit_gb * 1024 ** 3))
            except MemoryGuardError:
                if rank == 1:
                    raise
                info = _segment_span(seg, input_mode)
                info["reason"] = "segment skipped: the memory guard refused its canvas"
                skipped_segments.append(info)
                msg = (f"segment {rank} ({seg.length} frames) could not be stitched: the "
                       f"memory guard refused its canvas (raise --memory-limit-gb or use "
                       f"--max-dimension)")
                log.warning(msg)
                warnings.append(msg)
                continue
            warnings += [f"segment {rank}: {w}" if rank > 1 else w for w in sres.warnings]
            pano, mask, box = auto_crop(pano, mask)
            coverage = float((mask > 0).mean())
            log.info("Segment %d/%d (%d frames): auto-cropped to %dx%d (box %s), "
                     "coverage %.1f%%", rank, len(to_stitch), seg.length,
                     pano.shape[1], pano.shape[0], box, 100 * coverage)
            written = save_panorama(pano, seg_out, diag / preview_name)
            outputs[f"panorama{suffix}"] = written["panorama"]
            outputs[f"preview{suffix}"] = written["preview"]
            outputs[f"panorama_pixels{suffix}"] = f"{pano.shape[1]}x{pano.shape[0]}"

            first = cv2.imread(str(seg.frames[0].path))
            plot_camera_path(sres.trajectory, diag / cam_name,
                             (first.shape[1], first.shape[0]),
                             label=("stitched segment" if rank == 1
                                    else f"segment {rank}"))
            outputs[f"camera_path{suffix}"] = str(diag / cam_name)

            span = _segment_span(seg, input_mode)
            entry = {
                "rank": rank,
                "n_frames": seg.length,
                "frame_index_first": span["frame_indices"][0],
                "frame_index_last": span["frame_indices"][-1],
                "frame_indices": span["frame_indices"],
                "width": int(pano.shape[1]),
                "height": int(pano.shape[0]),
                "coverage_percent": round(100 * coverage, 2),
                "blend": sres.blend,
                "rejoin_links": seg.rejoin_links,
                "registration_stats": registration_stats(segment_pairs(seg, pairs)),
                "output": str(seg_out),
                "preview_name": preview_name,
                "camera_path_name": cam_name,
            }
            if ba_enabled:
                entry["bundle_adjustment"] = ba_results[rank - 1].to_dict()
            if input_mode == "images":
                entry["image_index_first"] = span["image_index_first"]
                entry["image_index_last"] = span["image_index_last"]
            else:
                entry["time_start_s"] = span["time_start_s"]
                entry["time_end_s"] = span["time_end_s"]
            segment_reports.append(entry)
            if rank == 1:
                primary = {"pano": pano, "mask": mask, "preview_name": preview_name,
                           "coverage": coverage, "out": seg_out}
        timing["stitch_s"] = round(max(time.time() - t - ba_seconds, 0.0), 3)
        if ba_enabled:
            timing["bundle_adjust_s"] = round(ba_seconds, 3)
        if any(r.applied for r in ba_results):
            outputs.update(write_bundle_adjusted_transforms(adjusted_segments, diag))

        if primary is None:  # pragma: no cover - the primary segment always stitches or raises
            raise RuntimeError("the largest segment could not be composed")
        pano, mask = primary["pano"], primary["mask"]
        preview_name = primary["preview_name"]
        outputs["report"] = str(diag / "report.html")
        counts["segments_written"] = len(segment_reports)
        counts["segments_skipped"] = len(skipped_segments)
        counts["frames_written_total"] = sum(e["n_frames"] for e in segment_reports)
        counts["rejoins_accepted"] = sum(1 for a in rejoin_attempts if a.accepted)
        counts["rejoin_attempts"] = len(rejoin_attempts)
        counts["non_adjacent_candidates"] = sum(r.candidates for r in ba_results)
        counts["non_adjacent_pairs_tried"] = sum(r.attempted for r in ba_results)
        counts["non_adjacent_pairs_accepted"] = sum(r.accepted for r in ba_results)
        counts["bundle_adjusted_segments"] = sum(1 for r in ba_results if r.applied)
        if len(segment_reports) > 1:
            log.warning("the scan is split into %d written panoramas: %s",
                        len(segment_reports),
                        ", ".join(f"{e['output']} ({e['n_frames']} frames, "
                                  f"{e['width']}x{e['height']})" for e in segment_reports))

        verdict, vnotes = _verdict(counts, stats)
        warnings += vnotes
        timing["total_s"] = round(time.time() - t0, 3)

        summary = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "version": __version__,
            "input": str(args.input),
            "input_mode": input_mode,
            "verdict": verdict,
            "settings": settings,
            "input_metadata": meta.to_dict(),
            **({"video_metadata": meta.to_dict()} if input_mode == "video" else {}),
            "counts": counts,
            "registration_stats": stats,
            "timing": timing,
            "warnings": warnings,
            "outputs": outputs,
            "preview_name": preview_name,
            "coverage_fraction": round(float((mask > 0).mean()), 4),
            "segments": segment_reports,
            "skipped_segments": skipped_segments,
            "scan_mode": args.scan_mode,
            "bundle_adjustment": {
                **(ba_results[0].to_dict() if ba_results
                   else disabled_result(args.scan_mode, reason=ba_off_reason).to_dict()),
                "per_segment": [r.to_dict() for r in ba_results],
            },
            "rejoin": {
                "enabled": not args.no_rejoin,
                "window": args.rejoin_window,
                "max_translation_frac": args.rejoin_max_translation_frac,
                "attempts": [a.to_dict() for a in rejoin_attempts],
            },
        }
        (diag / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        write_report(diag, summary)

        log.info("VERDICT: %s", verdict)
        log.info("panorama: %s (%dx%d)", out_path, pano.shape[1], pano.shape[0])
        for e in segment_reports[1:]:
            if input_mode == "images":
                log.info("additional panorama: %s (%dx%d, %d images, indices %d-%d)",
                         e["output"], e["width"], e["height"], e["n_frames"],
                         e["image_index_first"], e["image_index_last"])
            else:
                log.info("additional panorama: %s (%dx%d, %d frames, t=%.1f-%.1f s)",
                         e["output"], e["width"], e["height"], e["n_frames"],
                         e["time_start_s"], e["time_end_s"])
        log.info("diagnostics: %s", diag)
        for w in warnings:
            log.warning("summary warning: %s", w)
        if not args.keep_intermediates and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        return 0

    except MultimediaToolMissingError as exc:
        log.error("PIPELINE FAILED: %s", exc)
        log.error("all diagnostics produced so far are preserved in %s", diag)
        (diag / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return 5
    except (ProbeError, MemoryGuardError, RuntimeError, ValueError) as exc:
        log.error("PIPELINE FAILED: %s", exc)
        log.error("all diagnostics produced so far are preserved in %s", diag)
        (diag / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sampling_fps <= 0:
        parser.error("--sampling-fps must be > 0")
    if args.image_stride < 1:
        parser.error("--image-stride must be >= 1")
    if args.rejoin_window < 1:
        parser.error("--rejoin-window must be >= 1")
    if args.rejoin_max_translation_frac <= 0:
        parser.error("--rejoin-max-translation-frac must be > 0")
    if args.min_segment_frames < 2:
        parser.error("--min-segment-frames must be >= 2")
    if args.bundle_adjust and args.no_bundle_adjust:
        parser.error("--bundle-adjust and --no-bundle-adjust contradict each other; give "
                     "only one (--scan-mode serpentine already implies --bundle-adjust)")
    if args.ba_max_iterations < 1:
        parser.error("--ba-max-iterations must be >= 1")
    if args.ba_max_seconds <= 0:
        parser.error("--ba-max-seconds must be > 0")
    if args.ba_max_pairs < 0:
        parser.error("--ba-max-pairs must be >= 0")
    if not 0.0 < args.ba_min_overlap <= 1.0:
        parser.error("--ba-min-overlap must be > 0 and <= 1")
    if not 0.0 < args.ba_max_shift_frac <= 10.0:
        parser.error("--ba-max-shift-frac must be > 0 and <= 10")
    if not 0.0 < args.serpentine_min_overlap <= 1.0:
        parser.error("--serpentine-min-overlap must be > 0 and <= 1")
    if args.serpentine_turn_translation_frac <= 0:
        parser.error("--serpentine-turn-translation-frac must be > 0")
    if args.output.suffix.lower() not in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
        parser.error("--output must end in .tif, .tiff or .png")
    source = Path(args.input).expanduser()
    has_glob = glob.has_magic(args.input)
    if args.input_mode == "video":
        if has_glob or not source.is_file():
            parser.error("--input-mode video requires one existing regular video file")
        args.resolved_input_mode = "video"
    elif args.input_mode == "images":
        try:
            probe_image_sequence(args.input, args.image_order, args.image_stride,
                                 args.max_dimension)
        except ProbeError as exc:
            parser.error(str(exc))
        args.resolved_input_mode = "images"
    elif source.is_dir() or has_glob or (source.is_file() and is_image_path(source)):
        try:
            probe_image_sequence(args.input, args.image_order, args.image_stride,
                                 args.max_dimension)
        except ProbeError as exc:
            parser.error(str(exc))
        args.resolved_input_mode = "images"
    elif source.is_file():
        args.resolved_input_mode = "video"
    else:
        suffixes = ", ".join(sorted(IMAGE_SUFFIXES))
        parser.error(f"input not found: {args.input} (image extensions: {suffixes})")
    if args.resolved_input_mode == "images" and args.normalize == "temporal":
        parser.error("--normalize temporal is only available for video input; use none or clahe "
                     "for image sequences")
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
