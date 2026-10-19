"""Frame extraction (ffmpeg) plus blur / near-duplicate gating."""
from __future__ import annotations

import functools
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .environment import child_environment
from .logging_utils import get_logger
from .metrics import FrameMetric, apply_clahe, duplicate_score, focus_score, side_by_side
from .probe import VideoMetadata, ProbeError, _require


@dataclass
class Frame:
    index: int          # index within the sampled candidate sequence (0-based)
    timestamp_s: float  # timestamp in the source video
    path: Path          # path of the (possibly normalised) frame on disk


def build_extract_filter(sampling_fps: float, normalize: str, max_dimension: int | None) -> str:
    parts = [f"fps={sampling_fps}"]
    if normalize == "temporal":
        # Conservative temporal exposure normalisation. independence=0 keeps the
        # three channels linked so hue is not shifted (H&E colour safety).
        parts.append("normalize=blackpt=black:whitept=white:smoothing=50:independence=0")
    if max_dimension:
        parts.append(
            f"scale='if(gt(iw,ih),min({max_dimension},iw),-2)':"
            f"'if(gt(iw,ih),-2,min({max_dimension},ih))'"
        )
    return ",".join(parts)


@functools.lru_cache(maxsize=4)
def frame_rate_mode_args(ffmpeg: str) -> tuple[str, ...]:
    """Return the frame-rate-mode flag this ffmpeg build understands.

    We ask ffmpeg to pass every filtered frame through instead of duplicating or
    dropping frames to fit a constant output rate. The option for that was renamed:

    * ffmpeg >= 5 : ``-fps_mode passthrough``
    * older builds: ``-vsync 0`` (removed in ffmpeg 8, where it is a hard error)

    Arch/Manjaro ship ffmpeg 8, so hard-coding ``-vsync`` breaks extraction there.
    Probed once per process from ``ffmpeg -h full``; if neither option is advertised
    we pass nothing, which is still correct for the ``fps`` filter.
    """
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-h", "full"],
                              capture_output=True, text=True, timeout=60,
                              env=child_environment())
        text = (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        text = ""
    if "-fps_mode" in text:
        return ("-fps_mode", "passthrough")
    if "-vsync" in text:
        return ("-vsync", "0")
    return ()


def extract_frames(video: Path, workdir: Path, sampling_fps: float, normalize: str,
                   max_dimension: int | None = None) -> list[Frame]:
    """Extract candidate frames at `sampling_fps` into `workdir` as lossless PNGs."""
    log = get_logger()
    ffmpeg = _require("ffmpeg")
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("cand_*.png"):
        old.unlink()

    vf = build_extract_filter(sampling_fps, normalize, max_dimension)

    def _run(mode: tuple[str, ...]) -> tuple[list[str], subprocess.CompletedProcess]:
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(video),
               "-vf", vf, *mode, "-an", "-sn",
               str(workdir / "cand_%05d.png")]
        return cmd, subprocess.run(cmd, capture_output=True, text=True,
                                   env=child_environment())

    mode = frame_rate_mode_args(ffmpeg)
    log.info("Extracting frames at %.3f fps (filter: %s%s)", sampling_fps, vf,
             f", {' '.join(mode)}" if mode else "")
    cmd, proc = _run(mode)
    if proc.returncode != 0 and "nrecognized option" in proc.stderr:
        # Defensive: the help output disagreed with what this build actually accepts.
        for fallback in (("-vsync", "0"), ()):
            if fallback == mode:
                continue
            log.warning("ffmpeg rejected %s; retrying with %s",
                        " ".join(mode) or "(no frame-rate flag)",
                        " ".join(fallback) or "(no frame-rate flag)")
            cmd, proc = _run(fallback)
            if proc.returncode == 0:
                break
    if proc.returncode != 0:
        raise ProbeError("ffmpeg frame extraction failed.\n"
                         f"command: {' '.join(cmd)}\nstderr: {proc.stderr.strip()}\n"
                         "If the error mentions an unrecognized option, your ffmpeg "
                         "version differs from the tested one -- please report it.")
    files = sorted(workdir.glob("cand_*.png"))
    if not files:
        raise ProbeError("ffmpeg produced no frames. Is --sampling-fps larger than the "
                         "video duration allows, or is the video empty?")
    frames = [Frame(index=i, timestamp_s=i / float(sampling_fps), path=p)
              for i, p in enumerate(files)]
    log.info("Extracted %d candidate frames into %s", len(frames), workdir)
    return frames


def gate_frames(frames: list[Frame], blur_threshold: float, dedup_threshold: float,
                normalize: str, accepted_dir: Path,
                exposure_compare_path: Path | None = None
                ) -> tuple[list[Frame], list[FrameMetric]]:
    """Score every candidate, reject blurry and near-duplicate frames.

    CLAHE (if requested) is applied here, after extraction, on the L channel of LAB.
    Returns (accepted frames, metrics for every candidate).
    """
    log = get_logger()
    accepted_dir.mkdir(parents=True, exist_ok=True)
    for old in accepted_dir.glob("*.png"):
        old.unlink()

    metrics: list[FrameMetric] = []
    accepted: list[Frame] = []
    prev_accepted_img: np.ndarray | None = None
    wrote_compare = False

    for fr in frames:
        img = cv2.imread(str(fr.path), cv2.IMREAD_COLOR)
        if img is None:
            metrics.append(FrameMetric(fr.index, fr.path.name, fr.timestamp_s, 0.0, 0.0,
                                       0.0, False, "unreadable frame file"))
            continue

        raw = img
        if normalize == "clahe":
            img = apply_clahe(img)
            if exposure_compare_path is not None and not wrote_compare:
                cv2.imwrite(str(exposure_compare_path), side_by_side(raw, img))
                wrote_compare = True
        elif exposure_compare_path is not None and not wrote_compare:
            # temporal/none: ffmpeg already did (or skipped) it; show frame vs itself-CLAHE
            # reference so the user can still judge illumination.
            cv2.imwrite(str(exposure_compare_path), side_by_side(raw, apply_clahe(raw)))
            wrote_compare = True

        fscore = focus_score(img)
        dscore = 0.0 if prev_accepted_img is None else duplicate_score(prev_accepted_img, img)

        reason = "accepted"
        ok = True
        if fscore < blur_threshold:
            ok, reason = False, f"blurry: focus {fscore:.2f} < threshold {blur_threshold:.2f}"
        elif dscore > dedup_threshold:
            ok, reason = False, (f"near-duplicate of previous accepted frame: similarity "
                                 f"{dscore:.4f} > threshold {dedup_threshold:.4f}")

        if ok:
            out = accepted_dir / f"frame_{fr.index:05d}.png"
            cv2.imwrite(str(out), img)
            accepted.append(Frame(fr.index, fr.timestamp_s, out))
            prev_accepted_img = img

        metrics.append(FrameMetric(fr.index, fr.path.name, round(fr.timestamp_s, 4),
                                   round(fscore, 4), round(dscore, 6),
                                   round(float(img.mean()), 3), ok, reason))

    log.info("Frame gating: %d/%d accepted (%d rejected)",
             len(accepted), len(frames), len(frames) - len(accepted))
    return accepted, metrics
