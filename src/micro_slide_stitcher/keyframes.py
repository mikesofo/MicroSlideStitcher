"""Motion-aware frame selection: keep one sharp frame per stretch of slide movement.

0.7.0 sampled the video at a fixed rate (``--sampling-fps``) and then rejected frames
whose sharpness fell below a fixed threshold.  That fails on hand-moved slides in two
ways:

* a fixed sampling instant can land exactly on a frame that is smeared by fast stage
  movement (the score was also measured *after* CLAHE, which inflates the sharpness of
  blurred frames), and
* when the slide moves quickly, two consecutive fixed samples can be too far apart to
  overlap.

Here every frame of the video is read once at a small analysis size.  For each frame we
measure sharpness separately for horizontal and vertical detail (motion blur smears
along the direction of travel only), compare it with the frames around it, and measure
how far the field has moved since the last kept frame.  Then, walking forward from the
last kept frame, we keep the sharpest non-blurred frame among those that have moved far
enough to add new tissue but not so far that the overlap - horizontal AND vertical -
becomes too small.  Only the kept frames are then extracted at full resolution, so the
temporary files stay small.
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

import cv2
import numpy as np

from .environment import child_environment
from .extract import Frame, build_extract_filter, frame_rate_mode_args
from .logging_utils import get_logger
from .probe import ProbeError, VideoMetadata, _require

ANALYSIS_WIDTH = 480


@dataclass
class SelectionLimits:
    """Thresholds for motion-aware frame selection (all fractions of the frame size)."""
    min_step_frac: float = 0.10      # move at least this far before keeping another frame
    max_step_frac: float = 0.33      # ...and preferably no further than this, in x or in y
    fallback_step_frac: float = 0.70 # if no sharp frame is that close, accept up to this
    rel_sharpness_min: float = 0.60  # below this fraction of the sharp frames nearby = blurred
    motion_rel_anisotropy: float = 1.8   # directional smear this much above the local norm...
    motion_rel_sharpness: float = 0.85   # ...together with this loss of sharpness = motion blur
    window_s: float = 3.0            # neighbourhood the frame is compared with
    reference_percentile: float = 80.0   # "the sharp frames nearby"
    min_response: float = 0.05       # phase-correlation confidence to trust a shift


@dataclass
class FrameStat:
    n: int                  # frame number in the decoded video (0-based)
    timestamp_s: float
    sharpness: float        # gradient energy, horizontal + vertical detail
    sharp_x: float          # detail across x (vertical edges); smeared by horizontal motion
    sharp_y: float          # detail across y (horizontal edges); smeared by vertical motion
    anisotropy: float       # max/min of the two; ~1 for a sharp frame
    rel_sharpness: float = 0.0
    blurred: bool = False
    blur_reason: str = ""
    kept: bool = False
    dx_from_prev_kept: float | None = None   # analysis-scale px
    dy_from_prev_kept: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SelectionResult:
    stats: list[FrameStat]
    kept: list[int]                          # frame numbers
    gaps: list[dict] = field(default_factory=list)
    analysis_size: tuple[int, int] = (0, 0)
    fps: float = 0.0


def _sharpness(gray: np.ndarray) -> tuple[float, float]:
    """Gradient energy across x and across y on a lightly smoothed frame.

    The 3x3 smoothing keeps sensor and compression noise from counting as detail; the
    score is computed on the ORIGINAL frame, never after CLAHE (which amplifies noise in
    exactly the frames that are blurred)."""
    g = cv2.GaussianBlur(gray, (3, 3), 0.8)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx)), float(np.mean(gy * gy))


def _iter_gray_frames(video: Path, width: int):
    ffmpeg = _require("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-an", "-sn",
           "-vf", f"scale={width}:-2,format=gray", *frame_rate_mode_args(ffmpeg),
           "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    return cmd


def analyse_video(video: Path, meta: VideoMetadata, width: int = ANALYSIS_WIDTH,
                  limits: SelectionLimits | None = None) -> SelectionResult:
    """Read every frame once at analysis size, score sharpness and choose keyframes."""
    log = get_logger()
    limits = limits or SelectionLimits()
    aw = width
    ah = int(round(meta.height * width / meta.width / 2.0)) * 2
    fsize = aw * ah
    cmd = _iter_gray_frames(video, width)
    log.info("Extracting frames at the sharpest moment of each stretch of movement: reading "
             "every frame at %dx%d to measure sharpness and movement", aw, ah)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=child_environment())
    fps = meta.fps if meta.fps and meta.fps > 0 else 30.0
    stats: list[FrameStat] = []
    thumbs: list[np.ndarray] = []
    n = 0
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(fsize)
        if len(buf) < fsize:
            break
        gray = np.frombuffer(buf, np.uint8).reshape(ah, aw)
        sx, sy = _sharpness(gray)
        lo, hi = min(sx, sy), max(sx, sy)
        stats.append(FrameStat(n=n, timestamp_s=round(n / fps, 4), sharpness=sx + sy,
                               sharp_x=sx, sharp_y=sy,
                               anisotropy=hi / max(lo, 1e-6)))
        thumbs.append(gray.copy())
        n += 1
    _, err = proc.communicate()
    if proc.returncode not in (0, None) and not stats:
        raise ProbeError(f"ffmpeg could not decode the video for frame selection:\n"
                         f"{err.decode(errors='replace').strip()}")
    if len(stats) < 2:
        raise ProbeError("the video has fewer than two decodable frames")

    _flag_blur(stats, fps, limits)
    kept, gaps = _choose(stats, thumbs, aw, ah, limits)
    log.info("Frame selection: %d frames read, %d judged blurred (%d motion blur), "
             "%d kept%s", len(stats), sum(s.blurred for s in stats),
             sum(1 for s in stats if s.blur_reason.startswith("motion")), len(kept),
             f", {len(gaps)} gap(s) with no sharp overlapping frame" if gaps else "")
    for g in gaps:
        log.warning("no sharp frame overlaps the previous one between %.2f s and %.2f s "
                    "(the slide moved too fast or every frame there is blurred); the scan "
                    "may split there", g["from_s"], g["to_s"])
    return SelectionResult(stats, kept, gaps, (aw, ah), fps)


def _flag_blur(stats: list[FrameStat], fps: float, limits: SelectionLimits) -> None:
    """Judge each frame against the sharp frames around it, not against a fixed number.

    Tissue texture differs enormously between fields (dense dermis vs nearly bare glass),
    and some textures - collagen fibres, the epidermal edge - are naturally directional.
    So the reference is the 80th-percentile sharpness and the median x/y detail ratio of
    the frames within +/- window_s/2.  A frame is skipped as blurred when it has clearly
    less detail than those, and as motion-blurred when, in addition, its detail has
    become much more one-directional than usual (the smear of a moving stage)."""
    half = max(1, int(round(limits.window_s * fps / 2)))
    sharp = np.array([s.sharpness for s in stats], dtype=np.float64)
    aniso = np.array([s.anisotropy for s in stats], dtype=np.float64)
    for i, s in enumerate(stats):
        lo, hi = max(0, i - half), min(len(stats), i + half + 1)
        ref = float(np.percentile(sharp[lo:hi], limits.reference_percentile))
        ref_an = float(np.median(aniso[lo:hi]))
        s.rel_sharpness = round(s.sharpness / max(ref, 1e-6), 4)
        rel_an = s.anisotropy / max(ref_an, 1e-6)
        if rel_an > limits.motion_rel_anisotropy and s.rel_sharpness < limits.motion_rel_sharpness:
            axis = "horizontal" if s.sharp_x < s.sharp_y else "vertical"
            s.blurred, s.blur_reason = True, (
                f"motion blur: smeared along the {axis} direction (detail ratio "
                f"{s.anisotropy:.2f}, {rel_an:.1f}x the usual; sharpness "
                f"{s.rel_sharpness:.2f} of the sharp frames nearby)")
        elif s.rel_sharpness < limits.rel_sharpness_min:
            s.blurred, s.blur_reason = True, (f"blurred: {s.rel_sharpness:.2f} of the "
                                              "sharpness of the sharp frames nearby")


def _track(thumbs: list[np.ndarray], aw: int, ah: int) -> np.ndarray:
    """Position of every frame (analysis px) by summing frame-to-frame phase shifts.

    Consecutive frames of a 30 fps video overlap almost completely, so this is far more
    reliable than correlating frames that are far apart; the small drift it accumulates
    over a second or two does not matter for choosing frames."""
    win = cv2.createHanningWindow((aw, ah), cv2.CV_32F)
    pos = np.zeros((len(thumbs), 2), np.float64)
    prev = thumbs[0].astype(np.float32)
    for i in range(1, len(thumbs)):
        cur = thumbs[i].astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(prev, cur, win)
        # phaseCorrelate returns the shift of cur relative to prev; the field of view
        # moved by the opposite amount
        pos[i] = pos[i - 1] - (dx, dy)
        prev = cur
    return pos


def _choose(stats: list[FrameStat], thumbs: list[np.ndarray], aw: int, ah: int,
            limits: SelectionLimits) -> tuple[list[int], list[dict]]:
    """Walk forward from each kept frame and keep the sharpest non-blurred frame that has
    moved at least ``min_step_frac`` of the frame and at most ``max_step_frac`` in x AND
    in y (so it still overlaps the kept frame in both directions).  If there is none -
    the frames there are blurred - accept up to ``fallback_step_frac``; only if even that
    fails is a gap recorded."""
    pos = _track(thumbs, aw, ah)
    n = len(stats)
    sharp_ok = [not s.blurred for s in stats]
    first_pool = [i for i in range(min(n, 15)) if sharp_ok[i]] or list(range(min(n, 15)))
    k = max(first_pool, key=lambda i: stats[i].sharpness)
    kept = [k]
    stats[k].kept = True
    gaps: list[dict] = []
    min_d = limits.min_step_frac * min(aw, ah)

    def candidates(k: int, frac: float) -> tuple[list[int], int]:
        out: list[int] = []
        j = k + 1
        while j < n:
            dx, dy = pos[j] - pos[k]
            if abs(dx) > frac * aw or abs(dy) > frac * ah:
                break
            if math.hypot(dx, dy) >= min_d and sharp_ok[j]:
                out.append(j)
            j += 1
        return out, j

    while k < n - 1:
        cands, j = candidates(k, limits.max_step_frac)
        if not cands and j < n:
            cands, j = candidates(k, limits.fallback_step_frac)
        if cands:
            nk = max(cands, key=lambda c: (stats[c].sharpness, c))
        elif j >= n:
            break                       # end of video, nothing new to add
        else:
            nk = next((m for m in range(j, n) if sharp_ok[m]), None)
            gaps.append({"from_s": stats[k].timestamp_s,
                         "to_s": stats[nk].timestamp_s if nk is not None
                         else stats[-1].timestamp_s})
            if nk is None:
                break
        dx, dy = pos[nk] - pos[k]
        stats[nk].kept = True
        stats[nk].dx_from_prev_kept = round(float(dx), 2)
        stats[nk].dy_from_prev_kept = round(float(dy), 2)
        kept.append(nk)
        k = nk
    return kept, gaps


def extract_selected(video: Path, workdir: Path, selection: SelectionResult,
                     normalize: str, max_dimension: int | None) -> list[Frame]:
    """Decode the video once more at full size and save only the kept frames."""
    log = get_logger()
    ffmpeg = _require("ffmpeg")
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("cand_*.png"):
        old.unlink()
    # reuse the normal filter chain without its fps= stage
    vf = build_extract_filter(1.0, normalize, max_dimension).split(",", 1)
    vf = vf[1] if len(vf) > 1 else "null"
    probe_cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-an", "-sn",
                 "-vf", vf, *frame_rate_mode_args(ffmpeg), "-frames:v", "1",
                 "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    first = subprocess.run(probe_cmd, capture_output=True, env=child_environment())
    img0 = cv2.imdecode(np.frombuffer(first.stdout, np.uint8), cv2.IMREAD_COLOR)
    if img0 is None:
        raise ProbeError("ffmpeg could not decode the first frame for extraction:\n"
                         + first.stderr.decode(errors="replace").strip())
    h, w = img0.shape[:2]
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-an", "-sn",
           "-vf", vf, *frame_rate_mode_args(ffmpeg),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=child_environment())
    want = set(selection.kept)
    last = max(want)
    fsize = w * h * 3
    frames: list[Frame] = []
    n = 0
    assert proc.stdout is not None
    while n <= last:
        buf = proc.stdout.read(fsize)
        if len(buf) < fsize:
            break
        if n in want:
            img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            p = workdir / f"cand_{len(frames) + 1:05d}.png"
            cv2.imwrite(str(p), img)
            frames.append(Frame(index=len(frames), timestamp_s=selection.stats[n].timestamp_s,
                                path=p))
        n += 1
    proc.stdout.close()
    proc.terminate()
    proc.wait()
    if len(frames) < len(want):
        log.warning("only %d of %d selected frames could be extracted", len(frames), len(want))
    log.info("Extracted %d candidate frames (sharpest per stretch of movement, %dx%d) into %s",
             len(frames), w, h, workdir)
    return frames
