"""Non-adjacent (loop-closure) match discovery and global bundle adjustment.

The pairwise chain built by :mod:`micro_slide_stitcher.register` multiplies frame-to-frame
transforms along the scan, so every small per-pair error is carried forward: the placement
of frame *k* depends on all *k* estimates before it and nothing ever corrects it again.
Overlaps between frames that are *not* consecutive - the row above in a serpentine scan,
or a spot the operator revisited - are not used at all.

This module adds the two halves of the standard fix:

1. :func:`discover_non_adjacent_pairs` uses the chained placement as an initial guess,
   predicts which non-consecutive frame pairs overlap, shortlists them by predicted
   overlap area, and re-matches only the shortlisted ones with the *existing* matcher and
   the *existing* inlier gates.  The number of attempts is bounded by a flag, so the cost
   stays linear-ish instead of O(n^2) feature matchings.
2. :func:`bundle_adjust` jointly refines all frame transforms by minimising the total
   reprojection error over every inlier correspondence of every verified pair (adjacent
   and non-adjacent), holding frame 0 fixed as the reference gauge.

Optimiser preference (see BUNDLE_ADJUSTMENT findings):

* first OpenCV's own ``cv2.detail.BundleAdjusterAffinePartial`` - OpenCV is already a
  hard dependency and already bundled, so this costs nothing in package size;
* if that is unavailable, raises, or fails to reduce the error, a self-contained
  Levenberg-Marquardt with a Huber loss written directly against ``numpy``.  It
  deliberately does **not** use ``scipy``: the parameter block is only four numbers per
  frame, so the normal equations are a small dense solve and ``scipy`` (which is excluded
  from the PyInstaller bundle to save size) is not needed.

Safety rule of the whole module: a bundle adjustment that fails, times out, or ends up
worse than the chained solution is *discarded*.  The caller keeps the chained transforms
and still writes a panorama.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from .logging_utils import get_logger
from .register import (PairResult, RegistrationLimits, Segment, _draw_matches, _match,
                       _reproj_error, _validate, decompose, detect)

#: Identifier written into summary.json for the optimiser that produced the solution.
OPTIMISER_OPENCV = "opencv_detail_affine_partial"
OPTIMISER_NUMPY = "numpy_lm_huber"
OPTIMISER_NUMPY_T = "numpy_lm_huber_translation_only"
# How much better the similarity model must be, in robust cost, before its extra scale and
# rotation freedom is accepted over the translation-only fit.
_RICHER_MODEL_MARGIN = 0.10
OPTIMISER_NONE = "none"


@dataclass
class BundleLimits:
    """Bounds for discovery and for the optimiser itself (all exposed as CLI flags)."""
    min_overlap_frac: float = 0.15   # predicted overlap needed to shortlist a pair
    max_extra_pairs: int = 60        # cap on non-adjacent matching attempts
    max_iterations: int = 200        # optimiser iteration cap
    max_seconds: float = 120.0       # wall-clock cap for discovery + optimisation
    huber_delta_px: float = 3.0      # robust loss knee, in pixels
    max_shift_frac: float = 0.5      # a refinement may move a frame by at most this
                                     # fraction of the frame diagonal; more than that is a
                                     # re-solve, not a refinement, and is not trusted


@dataclass
class ExtraPair:
    """One non-adjacent pair that was shortlisted and then really matched."""
    src_index: int
    dst_index: int
    chain_distance: int              # |position difference| along the chain
    predicted_overlap: float         # from the chained placement, 0-1 of a frame
    detector: str
    matches: int
    inliers: int
    inlier_ratio: float
    mean_reproj_error: float
    tx: float
    ty: float
    scale: float
    rotation_deg: float
    accepted: bool
    reason: str
    viz: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BundleResult:
    """Everything the log, summary.json, report.html and the GUI need to show."""
    enabled: bool = False
    applied: bool = False
    optimiser: str = OPTIMISER_NONE
    reason: str = "bundle adjustment not requested"
    scan_mode: str = "sweep"
    frames: int = 0
    iterations: int = 0
    candidates: int = 0               # pairs whose predicted overlap passed the threshold
    attempted: int = 0               # pairs actually re-matched (after the cap)
    accepted: int = 0                # pairs that passed the inlier gates
    adjacent_pairs_used: int = 0
    correspondences: int = 0
    reproj_before: dict = field(default_factory=dict)
    reproj_after: dict = field(default_factory=dict)
    position_shift: dict = field(default_factory=dict)   # how far frames moved, px
    seconds: float = 0.0
    discovery_seconds: float = 0.0
    optimise_seconds: float = 0.0
    reference_frame_index: int = -1
    extra_pairs: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["extra_pairs"] = [p.to_dict() if isinstance(p, ExtraPair) else p
                              for p in self.extra_pairs]
        return out

    @property
    def improvement_percent(self) -> float:
        before = self.reproj_before.get("mean")
        after = self.reproj_after.get("mean")
        if not before or after is None or before <= 0:
            return 0.0
        return 100.0 * (before - after) / before


# --------------------------------------------------------------------- geometry helpers
def _corners(w: int, h: int) -> np.ndarray:
    return np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)


def predicted_overlap(t_a: np.ndarray, t_b: np.ndarray, w: int, h: int) -> float:
    """Fraction of a frame that two placed frames are predicted to share (0-1).

    Both quads are the frame rectangle warped by the *current* (chained) estimate, so this
    is only as good as the initial guess - which is exactly why it is used as a shortlist
    and every shortlisted pair is then verified by real feature matching.
    """
    quad_a = cv2.perspectiveTransform(_corners(w, h), np.asarray(t_a, dtype=np.float64))
    quad_b = cv2.perspectiveTransform(_corners(w, h), np.asarray(t_b, dtype=np.float64))
    area, _ = cv2.intersectConvexConvex(quad_a.reshape(-1, 2).astype(np.float32),
                                        quad_b.reshape(-1, 2).astype(np.float32))
    return float(area) / float(max(w * h, 1))


def shortlist_pairs(transforms: list[np.ndarray], w: int, h: int, limits: BundleLimits
                    ) -> tuple[list[tuple[int, int, float]], int]:
    """Non-adjacent frame pairs worth a real match attempt, best predicted overlap first.

    Returns ``(shortlist, n_candidates)``; ``shortlist`` is truncated to
    ``limits.max_extra_pairs`` while ``n_candidates`` counts everything above the overlap
    threshold, so the log can say how many candidates were dropped by the cap.
    Predicting an overlap is pure polygon arithmetic (microseconds); only the shortlisted
    pairs pay for feature detection and matching.
    """
    cands: list[tuple[int, int, float]] = []
    n = len(transforms)
    for i in range(n):
        for j in range(i + 2, n):
            ov = predicted_overlap(transforms[i], transforms[j], w, h)
            if ov >= limits.min_overlap_frac:
                cands.append((i, j, round(ov, 4)))
    cands.sort(key=lambda c: (-c[2], c[0], c[1]))
    return cands[:max(0, int(limits.max_extra_pairs))], len(cands)


# ------------------------------------------------------------------ correspondences
@dataclass
class Correspondence:
    """Inlier point pairs of one verified frame pair, in the two frames' own pixels."""
    pos_a: int                       # chain position, not frame index
    pos_b: int
    pts_a: np.ndarray                # (N, 2) float64
    pts_b: np.ndarray                # (N, 2) float64
    non_adjacent: bool = False


def match_correspondences(img_a: np.ndarray, img_b: np.ndarray, idx_a: int, idx_b: int,
                          detector: str, limits: RegistrationLimits,
                          viz_path: Path | None = None
                          ) -> tuple[PairResult, np.ndarray | None, np.ndarray | None]:
    """Match two frames and return the pair result together with its inlier points.

    Same detector cascade (requested detector, then SIFT), same ratio test, same
    partial-affine RANSAC model and the same plausibility gates as
    :func:`register.register_pair`; the only difference is that the inlier
    correspondences are handed back so bundle adjustment can use them.  A homography
    fallback is deliberately *not* offered here - the bundle adjustment parameterises a
    partial affine per frame.
    """
    attempts = [detector] + ([] if detector == "sift" else ["sift"])
    best: tuple[PairResult, np.ndarray | None, np.ndarray | None] | None = None
    for det_name in attempts:
        kp_a, desc_a = detect(img_a, det_name)
        kp_b, desc_b = detect(img_b, det_name)
        good = _match(desc_a, desc_b, det_name, limits.lowe_ratio)
        res = PairResult(idx_a, idx_b, det_name, "affine_partial", False, "not attempted",
                         matches=len(good))
        pts_a = pts_b = None
        if len(good) < 4:
            res.reason = f"only {len(good)} ratio-test matches (need >= 4)"
        else:
            src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            M, mask = cv2.estimateAffinePartial2D(
                src, dst, method=cv2.RANSAC, ransacReprojThreshold=limits.ransac_thresh,
                maxIters=5000, confidence=0.995)
            inl = int(mask.sum()) if mask is not None else 0
            err = _reproj_error(src, dst, np.asarray(M), mask) if M is not None else float("nan")
            ok, reason = _validate(M, inl, len(good), err, img_a.shape, limits)
            res.inliers = inl
            res.inlier_ratio = round(inl / max(len(good), 1), 4)
            res.mean_reproj_error = round(err, 4) if not math.isnan(err) else float("nan")
            res.ok, res.reason = ok, reason
            if M is not None:
                Mn = np.asarray(M, dtype=np.float64)
                tx, ty, sc, rot = decompose(Mn)
                res.tx, res.ty = round(tx, 3), round(ty, 3)
                res.scale, res.rotation_deg = round(sc, 5), round(rot, 4)
                res.matrix = Mn.tolist()
            if ok and mask is not None:
                keep = mask.ravel().astype(bool)
                pts_a = src.reshape(-1, 2)[keep].astype(np.float64)
                pts_b = dst.reshape(-1, 2)[keep].astype(np.float64)
                if viz_path is not None:
                    _draw_matches(img_a, kp_a, img_b, kp_b, good, mask, viz_path)
                    res.viz = viz_path.name
                return res, pts_a, pts_b
        if best is None or res.inliers > best[0].inliers:
            best = (res, pts_a, pts_b)
    assert best is not None
    return best


def _read(path: Path, cache: dict) -> np.ndarray:
    key = str(path)
    if key not in cache:
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
        img = cv2.imread(key, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"could not read frame {path}")
        cache[key] = img
    return cache[key]


def discover_non_adjacent_pairs(segment: Segment, detector: str,
                                limits: RegistrationLimits, blimits: BundleLimits,
                                viz_dir: Path | None = None, max_viz: int = 80,
                                deadline: float | None = None
                                ) -> tuple[list[Correspondence], list[ExtraPair], int, int]:
    """Find and verify overlaps between frames that are not consecutive.

    Returns ``(correspondences, attempts, n_candidates, n_shortlisted)``.
    """
    log = get_logger()
    first = cv2.imread(str(segment.frames[0].path), cv2.IMREAD_COLOR)
    if first is None:
        raise RuntimeError(f"cannot read {segment.frames[0].path}")
    h, w = first.shape[:2]
    shortlist, n_candidates = shortlist_pairs(segment.transforms, w, h, blimits)
    log.info("loop-closure search: %d non-adjacent pair(s) predicted to overlap by >= "
             "%.0f%% of a frame; attempting the best %d (cap --ba-max-pairs %d)",
             n_candidates, 100 * blimits.min_overlap_frac, len(shortlist),
             blimits.max_extra_pairs)
    if n_candidates > len(shortlist):
        log.info("%d further overlapping candidate(s) were dropped by the attempt cap",
                 n_candidates - len(shortlist))

    cache: dict[str, np.ndarray] = {}
    out: list[Correspondence] = []
    attempts: list[ExtraPair] = []
    viz_count = 0
    for pos_a, pos_b, ov in shortlist:
        if deadline is not None and time.time() > deadline:
            log.warning("loop-closure search stopped early: wall-clock cap of %.0f s reached "
                        "after %d attempt(s)", blimits.max_seconds, len(attempts))
            break
        fa, fb = segment.frames[pos_a], segment.frames[pos_b]
        viz = None
        if viz_dir is not None and viz_count < max_viz:
            viz = viz_dir / f"loop_{fa.index:05d}_{fb.index:05d}.png"
            viz_count += 1
        res, pts_a, pts_b = match_correspondences(
            _read(fa.path, cache), _read(fb.path, cache), fa.index, fb.index,
            detector, limits, viz_path=viz)
        attempts.append(ExtraPair(
            src_index=fa.index, dst_index=fb.index, chain_distance=pos_b - pos_a,
            predicted_overlap=ov, detector=res.detector, matches=res.matches,
            inliers=res.inliers, inlier_ratio=res.inlier_ratio,
            mean_reproj_error=res.mean_reproj_error, tx=res.tx, ty=res.ty,
            scale=res.scale, rotation_deg=res.rotation_deg,
            accepted=bool(res.ok), reason=res.reason, viz=res.viz))
        if res.ok and pts_a is not None and pts_b is not None:
            out.append(Correspondence(pos_a, pos_b, pts_a, pts_b, non_adjacent=True))
            log.info("loop closure ACCEPTED %d->%d (chain distance %d, predicted overlap "
                     "%.0f%%): %s, %d inliers, reproj %.2f px", fa.index, fb.index,
                     pos_b - pos_a, 100 * ov, res.detector, res.inliers,
                     res.mean_reproj_error)
        else:
            log.info("loop closure rejected %d->%d (predicted overlap %.0f%%): %s",
                     fa.index, fb.index, 100 * ov, res.reason)
    return out, attempts, n_candidates, len(shortlist)


def adjacent_correspondences(segment: Segment, detector: str, limits: RegistrationLimits,
                             turn_translation_frac: float | None = None
                             ) -> list[Correspondence]:
    """Inlier correspondences of the consecutive pairs that built the chain.

    ``register.build_chain`` keeps only the estimated matrices, not the point pairs, so
    the consecutive pairs are matched once more here.  That is the price of joint
    refinement and it is reported as ``ba_s`` in the timing block.

    ``turn_translation_frac`` mirrors ``register.build_chain``'s serpentine row-turn
    allowance.  It matters: a row turn that ``build_chain`` accepted under the wider
    allowance would otherwise be re-rejected here, and the two rows would be left with no
    consecutive constraint linking them at all - which lets the refinement pull them apart.
    """
    log = get_logger()
    cache: dict[str, np.ndarray] = {}
    out: list[Correspondence] = []
    for pos in range(len(segment.frames) - 1):
        fa, fb = segment.frames[pos], segment.frames[pos + 1]
        res, pts_a, pts_b = match_correspondences(
            _read(fa.path, cache), _read(fb.path, cache), fa.index, fb.index,
            detector, limits)
        if (not res.ok and turn_translation_frac is not None
                and res.reason.startswith("implausible translation")):
            res, pts_a, pts_b = match_correspondences(
                _read(fa.path, cache), _read(fb.path, cache), fa.index, fb.index, detector,
                replace(limits, max_translation_frac=float(turn_translation_frac)))
            if res.ok:
                log.info("bundle adjustment: consecutive pair %d->%d accepted as a row turn "
                         "under the wider serpentine translation allowance", fa.index, fb.index)
        if res.ok and pts_a is not None and pts_b is not None:
            out.append(Correspondence(pos, pos + 1, pts_a, pts_b, non_adjacent=False))
        else:
            log.warning("bundle adjustment: consecutive pair %d->%d could not be re-matched "
                        "(%s); it contributes no constraint", fa.index, fb.index, res.reason)
    return out


# ------------------------------------------------------------------------ error metric
def _apply(t: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ np.asarray(t)[:2, :2].T + np.asarray(t)[:2, 2]


def reprojection_errors(transforms: list[np.ndarray], obs: list[Correspondence]) -> np.ndarray:
    """Euclidean reprojection residual of every correspondence, in panorama pixels."""
    if not obs:
        return np.zeros(0)
    parts = []
    for c in obs:
        d = _apply(transforms[c.pos_a], c.pts_a) - _apply(transforms[c.pos_b], c.pts_b)
        parts.append(np.linalg.norm(d, axis=1))
    return np.concatenate(parts)


def error_stats(errors: np.ndarray) -> dict:
    if errors.size == 0:
        return {"n": 0}
    return {"n": int(errors.size),
            "mean": round(float(errors.mean()), 4),
            "median": round(float(np.median(errors)), 4),
            "p95": round(float(np.percentile(errors, 95)), 4),
            "max": round(float(errors.max()), 4)}


def _huber_cost(errors: np.ndarray, delta: float) -> float:
    e = np.abs(errors)
    small = e <= delta
    return float(np.sum(np.where(small, 0.5 * e ** 2, delta * (e - 0.5 * delta))))


# ------------------------------------------------------------------- OpenCV BA driver
def _to_similarity(t: np.ndarray) -> np.ndarray:
    """Nearest partial-affine (similarity) 3x3 to an arbitrary chained transform."""
    t = np.asarray(t, dtype=np.float64)
    a = 0.5 * (t[0, 0] + t[1, 1])
    b = 0.5 * (t[1, 0] - t[0, 1])
    if abs(a) < 1e-12 and abs(b) < 1e-12:
        a, b = 1.0, 0.0
    return np.array([[a, -b, t[0, 2]], [b, a, t[1, 2]], [0.0, 0.0, 1.0]])


def _gauge(transforms: list[np.ndarray]) -> list[np.ndarray]:
    """Re-fix frame 0 as the reference: transforms[0] becomes exactly the identity."""
    inv0 = np.linalg.inv(np.asarray(transforms[0], dtype=np.float64))
    return [inv0 @ np.asarray(t, dtype=np.float64) for t in transforms]


def _position_shift(before: list[np.ndarray], after: list[np.ndarray]) -> dict:
    """How far each frame's origin moved between two solutions, in pixels."""
    shift = [float(np.hypot(*(np.asarray(a)[:2, 2] - np.asarray(b)[:2, 2])))
             for a, b in zip(after, before)]
    return {"n": len(shift), "mean": round(float(np.mean(shift)), 3),
            "max": round(float(np.max(shift)), 3)}


def _implausible(mats: list[np.ndarray], limits: RegistrationLimits) -> str | None:
    """Why a candidate solution is not a plausible refinement, or None if it is.

    A similarity whose linear part has collapsed (or exploded, or rotated) is a *global*
    minimum of the reprojection cost that is geometrically nonsense: if every frame maps
    all of its pixels onto one point, every correspondence agrees perfectly.  Real slide
    scans are near-pure translations, which is why the pairwise path already gates on
    scale 0.9-1.1 and a few degrees of rotation - the same gate is applied here so a
    degenerate optimum can never be accepted.
    """
    for i, m in enumerate(mats):
        a, b = float(m[0, 0]), float(m[1, 0])
        scale = math.hypot(a, b)
        if not math.isfinite(scale) or not (limits.scale_min <= scale <= limits.scale_max):
            return (f"frame {i} would be scaled by {scale:.4g}, outside the accepted "
                    f"{limits.scale_min:g}-{limits.scale_max:g} range")
        rot = abs(math.degrees(math.atan2(b, a)))
        if rot > limits.max_rotation_deg:
            return (f"frame {i} would be rotated by {rot:.2f} deg, more than the accepted "
                    f"{limits.max_rotation_deg:g} deg")
        if not np.all(np.isfinite(m)):
            return f"frame {i} has a non-finite transform"
    return None


def _ba_opencv(transforms: list[np.ndarray], obs: list[Correspondence],
               frame_wh: tuple[int, int], blimits: BundleLimits
               ) -> tuple[list[np.ndarray] | None, int, str]:
    """Drive ``cv2.detail.BundleAdjusterAffinePartial``.

    OpenCV's detail adjusters take the stitching pipeline's own containers, so synthetic
    ``ImageFeatures`` / ``MatchesInfo`` are built from our correspondences (descriptors are
    not read by the adjuster - only keypoint coordinates and match indices are).

    The sign/inversion convention of ``CameraParams.R`` in the affine adjusters has
    differed between OpenCV releases, so the adjuster is driven as a black box: it is fed
    both plausible input conventions, and each result is interpreted both ways.  The
    candidate with the lowest reprojection error *under our own metric* wins, and the
    caller still refuses anything that is not better than the chained solution.  That
    makes the code correct without depending on an OpenCV implementation detail.
    """
    adjuster_cls = getattr(getattr(cv2, "detail", None), "BundleAdjusterAffinePartial", None)
    if adjuster_cls is None:  # pragma: no cover - depends on the OpenCV build
        return None, 0, "cv2.detail.BundleAdjusterAffinePartial is not available"
    n = len(transforms)
    w, h = frame_wh

    # keypoint tables + DMatch indices per pair
    kp: list[list[tuple[float, float]]] = [[] for _ in range(n)]
    pair_matches: dict[tuple[int, int], list[cv2.DMatch]] = {}
    for c in obs:
        ms = []
        for p, q in zip(c.pts_a, c.pts_b):
            ia = len(kp[c.pos_a]); kp[c.pos_a].append((float(p[0]), float(p[1])))
            ib = len(kp[c.pos_b]); kp[c.pos_b].append((float(q[0]), float(q[1])))
            ms.append(cv2.DMatch(ia, ib, 0.0))
        pair_matches.setdefault((c.pos_a, c.pos_b), []).extend(ms)

    features = []
    for i in range(n):
        f = cv2.detail.ImageFeatures()
        f.img_idx = i
        f.img_size = (w, h)
        f.keypoints = tuple(cv2.KeyPoint(x, y, 1.0) for x, y in kp[i])
        features.append(f)

    matches = []
    for i in range(n):
        for j in range(n):
            mi = cv2.detail.MatchesInfo()
            mi.src_img_idx = i
            mi.dst_img_idx = j
            ms = pair_matches.get((i, j))
            if ms:
                mi.matches = tuple(ms)
                mi.inliers_mask = np.ones(len(ms), np.uint8)
                mi.num_inliers = len(ms)
                mi.H = np.eye(3, dtype=np.float64)
                mi.confidence = 10.0
            matches.append(mi)

    results: list[list[np.ndarray]] = []
    for invert_input in (False, True):
        cams = []
        for t in transforms:
            sim = _to_similarity(t)
            r = np.linalg.inv(sim) if invert_input else sim
            cam = cv2.detail.CameraParams()
            cam.R = np.ascontiguousarray(r, dtype=np.float32)
            cam.focal = 1.0
            cam.aspect = 1.0
            cam.ppx = 0.0
            cam.ppy = 0.0
            cam.t = np.zeros((3, 1), np.float32)
            cams.append(cam)
        try:
            ba = adjuster_cls()
            ba.setConfThresh(1.0)
            ba.setRefinementMask(np.ones((3, 3), np.uint8))
            ba.setTermCriteria((cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                                int(blimits.max_iterations), 1e-8))
            ok, refined = ba.apply(tuple(features), tuple(matches), tuple(cams))
        except cv2.error as exc:
            return None, 0, f"OpenCV bundle adjuster raised: {exc}"
        if not ok or refined is None:
            continue
        raw = [np.asarray(c.R, dtype=np.float64) for c in refined]
        if any(not np.all(np.isfinite(m)) or abs(np.linalg.det(m[:2, :2])) < 1e-9
               for m in raw):
            continue
        results.append(_gauge(raw))
        results.append(_gauge([np.linalg.inv(m) for m in raw]))

    if not results:
        return None, 0, "the OpenCV bundle adjuster did not return a usable solution"
    best = min(results, key=lambda ts: _huber_cost(reprojection_errors(ts, obs),
                                                   blimits.huber_delta_px))
    return best, int(blimits.max_iterations), "ok"


# --------------------------------------------------------- numpy Levenberg-Marquardt
def _params_from(transforms: list[np.ndarray]) -> np.ndarray:
    """4 parameters (a, b, tx, ty) per frame; the matrix is [[a,-b,tx],[b,a,ty]]."""
    out = np.zeros((len(transforms), 4))
    for i, t in enumerate(transforms):
        sim = _to_similarity(t)
        out[i] = (sim[0, 0], sim[1, 0], sim[0, 2], sim[1, 2])
    return out


def _mats_from(params: np.ndarray) -> list[np.ndarray]:
    mats = []
    for a, b, tx, ty in params:
        mats.append(np.array([[a, -b, tx], [b, a, ty], [0.0, 0.0, 1.0]]))
    return mats


def _ba_numpy(transforms: list[np.ndarray], obs: list[Correspondence],
              blimits: BundleLimits, deadline: float | None,
              limits: RegistrationLimits | None = None, model: str = "similarity"
              ) -> tuple[list[np.ndarray] | None, int, str]:
    """Sparse-structured Levenberg-Marquardt with a Huber loss, numpy only.

    ``model`` is ``"similarity"`` (four parameters per frame: scale, rotation and the two
    translations) or ``"translation"`` (two parameters per frame, the linear part frozen at
    the chained value).  The translation-only model exists because a slide scan is a
    near-pure translation: giving the optimiser scale and rotation freedom lets it buy a
    lower reprojection error with a small scale change that is *worse* in absolute position,
    so the simpler model is preferred unless the richer one is clearly better.

    Frame 0 is held fixed as the gauge, so the parameter vector has ``npar * (n - 1)``
    entries.  The normal equations are accumulated pair by pair (never materialising the
    full Jacobian) and solved densely - with four parameters per frame even a 500-frame
    scan is a 1996x1996 solve, which is why no sparse library is needed.
    """
    log = get_logger()
    n = len(transforms)
    params = _params_from(transforms)
    delta = float(blimits.huber_delta_px)
    free = n - 1                                    # frame 0 is the fixed gauge
    npar = 2 if model == "translation" else 4
    # Column indices of the parameters this model is allowed to move, within each frame's
    # 4-vector (a, b, tx, ty).
    cols = np.array([2, 3]) if npar == 2 else np.array([0, 1, 2, 3])

    gate = limits or RegistrationLimits()

    def cost(p: np.ndarray) -> float:
        """Huber cost, or infinity for a geometrically implausible parameter vector.

        Making the degenerate basin infinitely expensive is what stops the search walking
        into it: without this, collapsing every linear part to zero is a perfect fit.
        """
        mats = _mats_from(p)
        if _implausible(mats, gate) is not None:
            return float("inf")
        return _huber_cost(reprojection_errors(mats, obs), delta)

    cur_cost = cost(params)
    if not math.isfinite(cur_cost):
        return None, 0, "initial cost is not finite"
    lam = 1e-3
    iterations = 0
    for _ in range(max(1, int(blimits.max_iterations))):
        if deadline is not None and time.time() > deadline:
            return (_gauge(_mats_from(params)), iterations,
                    f"wall-clock cap of {blimits.max_seconds:.0f} s reached")
        mats = _mats_from(params)
        jtj = np.zeros((npar * free, npar * free))
        jtr = np.zeros(npar * free)
        for c in obs:
            pa, pb = c.pts_a, c.pts_b
            res = _apply(mats[c.pos_a], pa) - _apply(mats[c.pos_b], pb)   # (N,2)
            norm = np.linalg.norm(res, axis=1)
            wgt = np.where(norm <= delta, 1.0, delta / np.maximum(norm, 1e-12))
            sw = np.sqrt(wgt)
            # per-point 2x4 blocks
            def block(pts):
                nb = len(pts)
                jb = np.zeros((nb, 2, 4))
                jb[:, 0, 0] = pts[:, 0]; jb[:, 0, 1] = -pts[:, 1]; jb[:, 0, 2] = 1.0
                jb[:, 1, 0] = pts[:, 1]; jb[:, 1, 1] = pts[:, 0]; jb[:, 1, 3] = 1.0
                return jb[:, :, cols]
            blocks: dict[int, np.ndarray] = {}
            if c.pos_a > 0:
                blocks[c.pos_a] = block(pa) * sw[:, None, None]
            if c.pos_b > 0:
                blocks[c.pos_b] = -block(pb) * sw[:, None, None]
            rw = res * sw[:, None]
            for ka, ja in blocks.items():
                sa = npar * (ka - 1)
                jtr[sa:sa + npar] += np.einsum("nij,ni->j", ja, rw)
                for kb, jb in blocks.items():
                    sb = npar * (kb - 1)
                    jtj[sa:sa + npar, sb:sb + npar] += np.einsum("nij,nik->jk", ja, jb)
        if not np.all(np.isfinite(jtj)) or free == 0:
            return None, iterations, "no free parameters or non-finite normal equations"
        diag = np.clip(np.diag(jtj).copy(), 1e-12, None)
        improved = False
        for _try in range(8):
            try:
                step = np.linalg.solve(jtj + lam * np.diag(diag), -jtr)
            except np.linalg.LinAlgError:
                lam *= 10.0
                continue
            cand = params.copy()
            cand[1:, cols] += step.reshape(free, npar)
            new_cost = cost(cand)
            iterations += 1
            if math.isfinite(new_cost) and new_cost < cur_cost:
                rel = (cur_cost - new_cost) / max(cur_cost, 1e-12)
                params, cur_cost = cand, new_cost
                lam = max(lam / 3.0, 1e-12)
                improved = True
                if rel < 1e-8:
                    return _gauge(_mats_from(params)), iterations, "converged"
                break
            lam *= 5.0
        if not improved:
            return _gauge(_mats_from(params)), iterations, "no further improvement possible"
    return _gauge(_mats_from(params)), iterations, "iteration cap reached"


# ------------------------------------------------------------------------- public entry
def bundle_adjust(segment: Segment, detector: str, limits: RegistrationLimits,
                  blimits: BundleLimits, scan_mode: str = "sweep",
                  viz_dir: Path | None = None, max_viz: int = 80,
                  turn_translation_frac: float | None = None
                  ) -> tuple[Segment, BundleResult]:
    """Globally refine ``segment``'s transforms; never returns a worse solution.

    Returns ``(segment_to_use, result)``.  On any failure - too few constraints, an
    optimiser that raises, a solution that is not better than the chained one, a timeout -
    the *input* segment is returned unchanged and ``result.applied`` is False with a
    human-readable ``reason``.
    """
    log = get_logger()
    t0 = time.time()
    deadline = t0 + max(1.0, float(blimits.max_seconds))
    res = BundleResult(enabled=True, scan_mode=scan_mode, frames=segment.length,
                       reference_frame_index=segment.frames[0].index)
    if segment.length < 3:
        res.reason = (f"only {segment.length} frame(s) in the segment - there is nothing for "
                      "a global refinement to redistribute")
        log.warning("bundle adjustment skipped: %s", res.reason)
        res.seconds = round(time.time() - t0, 3)
        return segment, res

    first = cv2.imread(str(segment.frames[0].path), cv2.IMREAD_COLOR)
    if first is None:
        raise RuntimeError(f"cannot read {segment.frames[0].path}")
    h, w = first.shape[:2]

    # 1. loop-closure discovery -------------------------------------------------------
    t_disc = time.time()
    extra, attempts, n_cands, n_short = discover_non_adjacent_pairs(
        segment, detector, limits, blimits, viz_dir=viz_dir, max_viz=max_viz,
        deadline=deadline)
    res.discovery_seconds = round(time.time() - t_disc, 3)
    res.candidates, res.attempted, res.accepted = n_cands, len(attempts), len(extra)
    res.extra_pairs = attempts
    if not extra:
        note = ("no non-adjacent overlap could be verified, so bundle adjustment has only "
                "the consecutive pairs to work with and cannot correct accumulated drift; "
                "this is the expected outcome for a single straight sweep")
        log.warning("bundle adjustment: %s", note)
        res.notes.append(note)

    # 2. constraints ------------------------------------------------------------------
    adj = adjacent_correspondences(segment, detector, limits,
                                   turn_translation_frac=turn_translation_frac)
    obs = adj + extra
    res.adjacent_pairs_used = len(adj)
    res.correspondences = int(sum(len(c.pts_a) for c in obs))
    if len(obs) < 2 or res.correspondences < 20:
        res.reason = (f"too few verified constraints ({len(obs)} pair(s), "
                      f"{res.correspondences} correspondences) for a global refinement")
        log.warning("bundle adjustment skipped: %s; keeping the chained solution",
                    res.reason)
        res.seconds = round(time.time() - t0, 3)
        return segment, res

    chained = [np.asarray(t, dtype=np.float64) for t in segment.transforms]
    before = reprojection_errors(chained, obs)
    res.reproj_before = error_stats(before)
    cost_before = _huber_cost(before, blimits.huber_delta_px)
    log.info("bundle adjustment input: %d frame(s), %d pair(s) (%d consecutive + %d "
             "loop closure), %d correspondences; chained reprojection error mean "
             "%.3f px / median %.3f px / max %.3f px", segment.length, len(obs), len(adj),
             len(extra), res.correspondences, res.reproj_before.get("mean", float("nan")),
             res.reproj_before.get("median", float("nan")),
             res.reproj_before.get("max", float("nan")))

    if time.time() > deadline:
        res.reason = (f"wall-clock cap of {blimits.max_seconds:.0f} s was already reached "
                      "during loop-closure discovery; the chained solution is kept")
        log.warning("bundle adjustment: %s", res.reason)
        res.seconds = round(time.time() - t0, 3)
        return segment, res

    # 3. optimise: OpenCV first, then the numpy LM ------------------------------------
    t_opt = time.time()
    tried: list[tuple[str, list[np.ndarray], int, float]] = []
    solution, iters, why = _ba_opencv(chained, obs, (w, h), blimits)
    if solution is None:
        log.warning("OpenCV bundle adjuster unusable (%s); trying the built-in "
                    "Levenberg-Marquardt", why)
        res.notes.append(f"OpenCV adjuster unusable: {why}")
    else:
        tried.append((OPTIMISER_OPENCV, solution, iters,
                      _huber_cost(reprojection_errors(solution, obs),
                                  blimits.huber_delta_px)))
    if not tried or tried[0][3] >= cost_before:
        if tried:
            log.info("OpenCV bundle adjuster did not improve the robust cost "
                     "(%.4g -> %.4g); trying the built-in Levenberg-Marquardt",
                     cost_before, tried[0][3])
        # The translation-only model is fitted FIRST and is the default choice.  A slide
        # scan is a near-pure translation, so the extra scale/rotation freedom of the
        # similarity model is only worth taking if it buys a clearly better fit - see the
        # margin rule below.  Chasing the last few per cent of reprojection error with a
        # richer model is exactly how a solution ends up with lower error and worse geometry.
        for label, model in ((OPTIMISER_NUMPY_T, "translation"), (OPTIMISER_NUMPY, "similarity")):
            alt, alt_iters, alt_why = _ba_numpy(chained, obs, blimits, deadline, limits,
                                                model=model)
            if alt is None:
                res.notes.append(f"numpy Levenberg-Marquardt ({model}) unusable: {alt_why}")
                continue
            tried.append((label, alt, alt_iters,
                          _huber_cost(reprojection_errors(alt, obs),
                                      blimits.huber_delta_px)))
            log.info("built-in Levenberg-Marquardt (%s model): %d iteration(s), %s",
                     model, alt_iters, alt_why)
    res.optimise_seconds = round(time.time() - t_opt, 3)

    usable = [c for c in tried if math.isfinite(c[3])]
    if not usable:
        res.reason = "no optimiser produced a usable solution; the chained solution is kept"
        res.optimiser = OPTIMISER_NONE
        log.warning("bundle adjustment FAILED: %s", res.reason)
        res.reproj_after = dict(res.reproj_before)
        res.seconds = round(time.time() - t0, 3)
        return segment, res

    # Two plausibility gates before the cost comparison.  A refinement is only allowed to
    # nudge a near-pure-translation solution; anything that changes scale or rotation beyond
    # the gates the pairwise path already enforces, or that moves a frame by more than
    # ``max_shift_frac`` of the frame diagonal, is a re-solve and is not trusted - even if
    # its reprojection cost is lower.  Reprojection error alone is NOT a proxy for accuracy:
    # a wrong-but-self-consistent solution can score beautifully and be metres out.
    diag_px = math.hypot(w, h)
    shift_cap = float(blimits.max_shift_frac) * diag_px
    plausible = []
    for cand in usable:
        why = _implausible(cand[1], limits)
        if why is not None:
            note = f"{cand[0]} produced an implausible solution: {why}"
            log.warning("bundle adjustment: %s; that solution is discarded", note)
            res.notes.append(note)
            continue
        moved = _position_shift(chained, cand[1])
        if moved["max"] > shift_cap:
            note = (f"{cand[0]} moved a frame by {moved['max']:.1f} px, more than the "
                    f"{shift_cap:.0f} px allowance ({blimits.max_shift_frac:g} of the "
                    f"{diag_px:.0f} px frame diagonal)")
            log.warning("bundle adjustment: %s; that solution is discarded", note)
            res.notes.append(note)
            continue
        plausible.append(cand)
    if not plausible:
        res.optimiser = OPTIMISER_NONE
        res.reproj_after = dict(res.reproj_before)
        res.reason = ("every optimiser produced a geometrically implausible solution, so the "
                      "chained solution is KEPT unchanged")
        log.warning("bundle adjustment REJECTED: %s", res.reason)
        res.seconds = round(time.time() - t0, 3)
        return segment, res
    usable = plausible

    # Model selection: prefer the simplest solution that fits.  The similarity fit is only
    # chosen over the translation-only fit if it lowers the robust cost by more than
    # ``_RICHER_MODEL_MARGIN``; otherwise the two are treated as equally good and the
    # constrained one wins, because it cannot invent scale drift.
    by_name = {c[0]: c for c in usable}
    simple, rich = by_name.get(OPTIMISER_NUMPY_T), by_name.get(OPTIMISER_NUMPY)
    if simple is not None and rich is not None:
        if rich[3] > simple[3] * (1.0 - _RICHER_MODEL_MARGIN):
            log.info("keeping the translation-only refinement: the similarity model's robust "
                     "cost (%.4g) is not more than %.0f%% better than %.4g",
                     rich[3], _RICHER_MODEL_MARGIN * 100.0, simple[3])
            usable = [c for c in usable if c[0] != OPTIMISER_NUMPY]

    name, solution, iters, cost_after = min(usable, key=lambda c: c[3])
    after = reprojection_errors(solution, obs)
    res.iterations = iters

    if cost_after >= cost_before:
        res.optimiser = OPTIMISER_NONE
        res.reproj_after = dict(res.reproj_before)
        res.reason = (f"the best optimiser ({name}) did not reduce the robust reprojection "
                      f"cost ({cost_before:.4g} -> {cost_after:.4g}), so the chained "
                      f"solution is KEPT unchanged")
        log.warning("bundle adjustment REJECTED: %s", res.reason)
        res.seconds = round(time.time() - t0, 3)
        return segment, res

    res.applied = True
    res.optimiser = name
    res.reproj_after = error_stats(after)
    res.position_shift = _position_shift(chained, solution)
    res.reason = (f"applied: mean reprojection error {res.reproj_before['mean']:.3f} -> "
                  f"{res.reproj_after['mean']:.3f} px "
                  f"({res.improvement_percent:+.1f}%), robust cost "
                  f"{cost_before:.4g} -> {cost_after:.4g}")
    log.info("bundle adjustment APPLIED (%s, %d iteration(s)): mean reprojection error "
             "%.3f -> %.3f px, median %.3f -> %.3f px, max %.3f -> %.3f px; frames moved "
             "on average %.1f px (max %.1f px)", name, iters,
             res.reproj_before["mean"], res.reproj_after["mean"],
             res.reproj_before["median"], res.reproj_after["median"],
             res.reproj_before["max"], res.reproj_after["max"],
             res.position_shift["mean"], res.position_shift["max"])
    res.seconds = round(time.time() - t0, 3)
    out = Segment(list(segment.frames), [np.asarray(t, dtype=np.float64) for t in solution],
                  list(segment.rejoin_links))
    return out, res


def disabled_result(scan_mode: str = "sweep", reason: str | None = None) -> BundleResult:
    """The ``summary.json`` block for a run without bundle adjustment."""
    return BundleResult(enabled=False, scan_mode=scan_mode,
                        reason=reason or "bundle adjustment is off (--bundle-adjust not "
                                         "given and --scan-mode is not serpentine)")
