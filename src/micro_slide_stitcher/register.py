"""Pairwise registration of adjacent frames and chain/segment construction."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

import cv2
import numpy as np

from .extract import Frame
from .logging_utils import get_logger


@dataclass
class PairResult:
    src_index: int
    dst_index: int
    detector: str
    model: str                     # 'affine_partial' | 'homography' | 'none'
    ok: bool
    reason: str
    matches: int = 0
    inliers: int = 0
    inlier_ratio: float = 0.0
    mean_reproj_error: float = float("nan")
    tx: float = 0.0
    ty: float = 0.0
    scale: float = 0.0
    rotation_deg: float = 0.0
    matrix: list = field(default_factory=list)
    bridged: bool = False
    rejoin: bool = False           # produced by the wide-baseline segment rejoin pass
    viz: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RejoinAttempt:
    """One wide-baseline registration attempt between two disconnected segments."""
    seg_a: int                     # chain-order position of the earlier segment (0-based)
    seg_b: int                     # chain-order position of the later segment
    src_index: int                 # frame index taken from the tail of segment a
    dst_index: int                 # frame index taken from the head of segment b
    detector: str
    model: str
    matches: int
    inliers: int
    inlier_ratio: float
    mean_reproj_error: float
    tx: float
    ty: float
    scale: float
    rotation_deg: float
    accepted: bool                 # True only for the candidate actually used to merge
    reason: str
    viz: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RegistrationLimits:
    min_inliers: int = 25
    min_inlier_ratio: float = 0.15
    scale_min: float = 0.9
    scale_max: float = 1.1
    max_rotation_deg: float = 5.0
    max_translation_frac: float = 0.92
    ransac_thresh: float = 3.0
    lowe_ratio: float = 0.75
    max_reproj_error: float = 6.0


_DETECTOR_CACHE: dict[str, object] = {}


def make_detector(name: str, nfeatures: int = 4000):
    key = f"{name}:{nfeatures}"
    if key not in _DETECTOR_CACHE:
        if name == "orb":
            _DETECTOR_CACHE[key] = cv2.ORB.create(nfeatures=nfeatures)
        elif name == "sift":
            _DETECTOR_CACHE[key] = cv2.SIFT.create(nfeatures=nfeatures)
        else:
            raise ValueError(f"unknown detector '{name}' (expected 'orb' or 'sift')")
    return _DETECTOR_CACHE[key]


def detect(img: np.ndarray, name: str, nfeatures: int = 4000):
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    det = make_detector(name, nfeatures)
    kp, desc = det.detectAndCompute(gray, None)
    return kp, desc


def _match(desc_a, desc_b, name: str, ratio: float):
    if desc_a is None or desc_b is None or len(desc_a) < 2 or len(desc_b) < 2:
        return []
    norm = cv2.NORM_HAMMING if name == "orb" else cv2.NORM_L2
    bf = cv2.BFMatcher(norm)
    raw = bf.knnMatch(desc_a, desc_b, k=2)
    good = []
    for pair in raw:
        if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
            good.append(pair[0])
    return good


def decompose(M: np.ndarray) -> tuple[float, float, float, float]:
    """Return (tx, ty, scale, rotation_deg) for a 2x3 affine / 3x3 homography."""
    a, b = float(M[0, 0]), float(M[1, 0])
    scale = math.hypot(a, b)
    rot = math.degrees(math.atan2(b, a))
    return float(M[0, 2]), float(M[1, 2]), scale, rot


def _reproj_error(src, dst, M, mask) -> float:
    if mask is None or mask.sum() == 0:
        return float("nan")
    m = mask.ravel().astype(bool)
    s = src.reshape(-1, 2)[m]
    d = dst.reshape(-1, 2)[m]
    if M.shape == (2, 3):
        pred = (M[:, :2] @ s.T).T + M[:, 2]
    else:
        h = np.hstack([s, np.ones((len(s), 1), np.float32)])
        p = (M @ h.T).T
        pred = p[:, :2] / np.clip(p[:, 2:3], 1e-9, None)
    return float(np.mean(np.linalg.norm(pred - d, axis=1)))


def _validate(M, inliers, matches, err, shape, limits: RegistrationLimits) -> tuple[bool, str]:
    if M is None:
        return False, "model estimation returned no transform"
    if inliers < limits.min_inliers:
        return False, f"too few inliers ({inliers} < {limits.min_inliers})"
    ratio = inliers / max(matches, 1)
    if ratio < limits.min_inlier_ratio:
        return False, f"inlier ratio too low ({ratio:.3f} < {limits.min_inlier_ratio})"
    tx, ty, scale, rot = decompose(np.asarray(M))
    if not (limits.scale_min <= scale <= limits.scale_max):
        return False, f"implausible scale {scale:.4f} (allowed {limits.scale_min}-{limits.scale_max})"
    if abs(rot) > limits.max_rotation_deg:
        return False, f"implausible rotation {rot:.2f} deg (max {limits.max_rotation_deg})"
    h, w = shape[:2]
    if abs(tx) > limits.max_translation_frac * w or abs(ty) > limits.max_translation_frac * h:
        return False, (f"implausible translation ({tx:.1f}, {ty:.1f}) px vs frame {w}x{h} "
                       f"(max {limits.max_translation_frac:.2f} of each dimension) - "
                       "probably insufficient overlap")
    if not math.isnan(err) and err > limits.max_reproj_error:
        return False, f"mean reprojection error too high ({err:.2f} px)"
    return True, "accepted"


def register_pair(img_a: np.ndarray, img_b: np.ndarray, idx_a: int, idx_b: int,
                  detector: str = "orb", limits: RegistrationLimits | None = None,
                  allow_homography_fallback: bool = True,
                  viz_path: Path | None = None) -> PairResult:
    """Register frame b onto frame a. Tries the requested detector, then SIFT, then
    (optionally, explicitly logged) a full homography."""
    log = get_logger()
    limits = limits or RegistrationLimits()

    attempts: list[tuple[str, str]] = [(detector, "affine_partial")]
    if detector != "sift":
        attempts.append(("sift", "affine_partial"))
    if allow_homography_fallback:
        attempts.append((detector, "homography"))

    best: PairResult | None = None
    for det_name, model in attempts:
        kp_a, desc_a = detect(img_a, det_name)
        kp_b, desc_b = detect(img_b, det_name)
        good = _match(desc_a, desc_b, det_name, limits.lowe_ratio)
        res = PairResult(idx_a, idx_b, det_name, model, False, "not attempted",
                         matches=len(good))
        if len(good) < 4:
            res.reason = f"only {len(good)} ratio-test matches (need >= 4)"
        else:
            src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            if model == "affine_partial":
                M, mask = cv2.estimateAffinePartial2D(
                    src, dst, method=cv2.RANSAC,
                    ransacReprojThreshold=limits.ransac_thresh, maxIters=5000,
                    confidence=0.995)
            else:
                M, mask = cv2.findHomography(src, dst, cv2.RANSAC, limits.ransac_thresh,
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
            if ok and viz_path is not None:
                _draw_matches(img_a, kp_a, img_b, kp_b, good, mask, viz_path)
                res.viz = viz_path.name
            if ok:
                if det_name != detector or model != "affine_partial":
                    log.warning("pair %d->%d: fell back to detector=%s model=%s (%d inliers)",
                                idx_a, idx_b, det_name, model, inl)
                return res
        if best is None or res.inliers > best.inliers:
            best = res

    assert best is not None
    if viz_path is not None:
        # Rejected pairs also get a visualisation (raw ratio-test matches).
        kp_a, desc_a = detect(img_a, best.detector)
        kp_b, desc_b = detect(img_b, best.detector)
        good = _match(desc_a, desc_b, best.detector, limits.lowe_ratio)
        _draw_matches(img_a, kp_a, img_b, kp_b, good, None, viz_path)
        best.viz = viz_path.name
    log.warning("pair %d->%d REJECTED: %s (matches=%d inliers=%d)",
                idx_a, idx_b, best.reason, best.matches, best.inliers)
    return best


def _draw_matches(img_a, kp_a, img_b, kp_b, good, mask, out_path: Path, max_draw: int = 80):
    if mask is not None:
        sel = [m for m, keep in zip(good, mask.ravel()) if keep]
    else:
        sel = list(good)
    sel = sorted(sel, key=lambda m: m.distance)[:max_draw]
    scale = 0.4
    a = cv2.resize(img_a, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    b = cv2.resize(img_b, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    kpa = [cv2.KeyPoint(k.pt[0] * scale, k.pt[1] * scale, max(k.size * scale, 1.0)) for k in kp_a]
    kpb = [cv2.KeyPoint(k.pt[0] * scale, k.pt[1] * scale, max(k.size * scale, 1.0)) for k in kp_b]
    vis = cv2.drawMatches(a, kpa, b, kpb, sel, None,
                          matchColor=(0, 255, 0) if mask is not None else (0, 0, 255),
                          singlePointColor=(180, 180, 180),
                          flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    label = f"{'INLIERS' if mask is not None else 'REJECTED'} n={len(sel)}"
    cv2.putText(vis, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), vis)


@dataclass
class Segment:
    frames: list[Frame]                 # frames in chain order
    transforms: list[np.ndarray]        # 3x3 cumulative transforms into segment frame 0
    rejoin_links: list[dict] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.frames)


def _to3x3(M: np.ndarray) -> np.ndarray:
    M = np.asarray(M, dtype=np.float64)
    if M.shape == (3, 3):
        return M
    out = np.eye(3)
    out[:2, :] = M
    return out


def _read_frame(path: Path, cache: dict) -> np.ndarray:
    key = str(path)
    if key not in cache:
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
        img = cv2.imread(key, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"could not read frame {path}")
        cache[key] = img
    return cache[key]


def merge_segments(a: Segment, b: Segment, ia: int, ib: int, matrix) -> Segment:
    """Merge segment `b` into segment `a` given a transform mapping frame a[ia] -> b[ib].

    `a.transforms[k]` maps frame a[k] coordinates into a[0] coordinates, so the relative
    transform that puts b[0] into a[0] is  T_a[ia] @ inv(M) @ inv(T_b[ib]).
    """
    M3 = _to3x3(matrix)
    rel = a.transforms[ia] @ np.linalg.inv(M3) @ np.linalg.inv(_to3x3(b.transforms[ib]))
    frames = list(a.frames) + list(b.frames)
    transforms = list(a.transforms) + [rel @ _to3x3(T) for T in b.transforms]
    return Segment(frames, transforms, list(a.rejoin_links) + list(b.rejoin_links))


def rejoin_segments(segments: list[Segment], limits: RegistrationLimits,
                    window: int = 3, max_translation_frac: float = 2.0,
                    viz_dir: Path | None = None, max_viz: int = 80
                    ) -> tuple[list[Segment], list[RejoinAttempt], list[str],
                               list[PairResult]]:
    """Wide-baseline recovery of disconnected segments (feature A).

    For every adjacent pair of segments (in chain order) all `window` x `window`
    combinations of the last frames of segment i and the first frames of segment i+1 are
    registered with **SIFT directly** (no ORB pass - ORB is what failed in the first
    place) using the same partial-affine + RANSAC model and the same plausibility gates as
    the adjacent-pair path, except that the translation allowance is deliberately widened
    to `max_translation_frac` (default 2.0 frame widths/heights instead of ~0.92) because a
    chain break is typically caused by a fast pan that moved the field of view by more than
    one frame. Overlap therefore still exists between *some* pair in the two windows even
    though the directly adjacent frames no longer overlap.

    The best candidate by inlier count wins. Returns
    (segments in chain order, every attempt, warnings, accepted rejoin pair results).
    """
    log = get_logger()
    attempts: list[RejoinAttempt] = []
    warnings: list[str] = []
    accepted_pairs: list[PairResult] = []
    if len(segments) < 2:
        return segments, attempts, warnings, accepted_pairs

    window = max(1, int(window))
    rj_limits = replace(limits, max_translation_frac=max_translation_frac)
    log.info("Wide-baseline rejoin pass: %d segment boundaries, window K=%d, SIFT, "
             "max translation %.2f of the frame (adjacent-pair limit is %.2f)",
             len(segments) - 1, window, max_translation_frac, limits.max_translation_frac)

    cache: dict[str, np.ndarray] = {}
    viz_count = 0
    merged: list[Segment] = [segments[0]]
    for seg_b_pos, nxt in enumerate(segments[1:], start=1):
        cur = merged[-1]
        seg_a_pos = seg_b_pos - 1
        tail = list(range(max(0, cur.length - window), cur.length))
        head = list(range(0, min(window, nxt.length)))
        cands: list[tuple[int, int, PairResult]] = []
        for ia in tail:
            img_a = _read_frame(cur.frames[ia].path, cache)
            for ib in head:
                img_b = _read_frame(nxt.frames[ib].path, cache)
                viz = None
                if viz_dir is not None and viz_count < max_viz:
                    viz = viz_dir / (f"rejoin_{cur.frames[ia].index:05d}_"
                                     f"{nxt.frames[ib].index:05d}.png")
                    viz_count += 1
                r = register_pair(img_a, img_b, cur.frames[ia].index, nxt.frames[ib].index,
                                  detector="sift", limits=rj_limits,
                                  allow_homography_fallback=False, viz_path=viz)
                r.rejoin = True
                cands.append((ia, ib, r))

        passing = [c for c in cands if c[2].ok]
        best = max(passing, key=lambda c: c[2].inliers) if passing else None
        for ia, ib, r in cands:
            chosen = best is not None and ia == best[0] and ib == best[1]
            if chosen:
                reason = "accepted: best candidate by inlier count"
            elif r.ok:
                reason = (f"passed the gates but another candidate had more inliers "
                          f"({best[2].inliers})")
            else:
                reason = r.reason
            attempts.append(RejoinAttempt(
                seg_a=seg_a_pos, seg_b=seg_b_pos,
                src_index=r.src_index, dst_index=r.dst_index,
                detector=r.detector, model=r.model, matches=r.matches, inliers=r.inliers,
                inlier_ratio=r.inlier_ratio, mean_reproj_error=r.mean_reproj_error,
                tx=r.tx, ty=r.ty, scale=r.scale, rotation_deg=r.rotation_deg,
                accepted=chosen, reason=reason, viz=r.viz))

        if best is None:
            best_try = max(cands, key=lambda c: c[2].inliers)[2] if cands else None
            msg = (f"segments {seg_a_pos} and {seg_b_pos} could NOT be rejoined: "
                   f"{len(cands)} wide-baseline SIFT attempt(s) around frames "
                   f"{cur.frames[tail[0]].index}..{cur.frames[tail[-1]].index} -> "
                   f"{nxt.frames[head[0]].index}..{nxt.frames[head[-1]].index} all failed"
                   + (f" (best: {best_try.inliers} inliers, {best_try.reason})"
                      if best_try is not None else ""))
            log.warning(msg)
            warnings.append(msg)
            merged.append(nxt)
            continue

        ia, ib, r = best
        link = {
            "src_index": r.src_index, "dst_index": r.dst_index, "detector": r.detector,
            "matches": r.matches, "inliers": r.inliers, "inlier_ratio": r.inlier_ratio,
            "mean_reproj_error": r.mean_reproj_error, "tx": r.tx, "ty": r.ty,
            "scale": r.scale, "rotation_deg": r.rotation_deg,
        }
        msg = (f"REJOINED segments {seg_a_pos} and {seg_b_pos} ({cur.length} + {nxt.length} "
               f"frames) via wide-baseline frame pair {r.src_index}->{r.dst_index}: "
               f"detector={r.detector} matches={r.matches} inliers={r.inliers} "
               f"ratio={r.inlier_ratio:.3f} reproj={r.mean_reproj_error:.2f} px "
               f"translation=({r.tx:.1f}, {r.ty:.1f}) px")
        log.info(msg)
        warnings.append(msg)
        accepted_pairs.append(r)
        new_seg = merge_segments(cur, nxt, ia, ib, r.matrix)
        new_seg.rejoin_links = list(cur.rejoin_links) + list(nxt.rejoin_links) + [link]
        merged[-1] = new_seg

    return merged, attempts, warnings, accepted_pairs


def build_chain(frames: list[Frame], detector: str, limits: RegistrationLimits,
                viz_dir: Path | None, max_viz: int = 80,
                allow_homography_fallback: bool = True,
                allow_bridging: bool = True,
                allow_rejoin: bool = True,
                rejoin_window: int = 3,
                rejoin_max_translation_frac: float = 2.0,
                turn_translation_frac: float | None = None
                ) -> tuple[list[Segment], list[PairResult], list[str],
                           list[RejoinAttempt]]:
    """Register consecutive accepted frames, bridging single failures, splitting on
    unrecoverable breaks, then attempting a wide-baseline rejoin of the pieces.
    Returns (segments sorted by descending length, all pair results, warnings,
    rejoin attempts).

    ``turn_translation_frac`` (serpentine scans only) is a *second chance* for a
    consecutive pair that the ordinary ``limits.max_translation_frac`` gate rejected as an
    implausible translation.  In a serpentine scan that pair is usually the row turn: the
    pan reverses direction and steps down, so the field of view moves further in one step
    than anywhere inside a row, and the ordinary gate would break the chain into one
    segment per row.  Only the translation allowance is widened - the inlier count, inlier
    ratio, scale, rotation and reprojection-error gates are untouched, so a genuinely
    unrelated pair of frames is still rejected."""
    log = get_logger()
    warnings: list[str] = []
    pairs: list[PairResult] = []
    segments: list[Segment] = []

    if not frames:
        return [], [], ["no accepted frames to register"]

    cache: dict[int, np.ndarray] = {}

    def load(i: int) -> np.ndarray:
        if i not in cache:
            if len(cache) > 4:
                cache.pop(next(iter(cache)))
            img = cv2.imread(str(frames[i].path), cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError(f"could not read frame {frames[i].path}")
            cache[i] = img
        return cache[i]

    viz_count = 0
    capped_logged = False

    cur_frames = [frames[0]]
    cur_tf = [np.eye(3)]
    i = 0
    n = len(frames)
    while i < n - 1:
        target = i + 1
        result = None
        while target < n:
            nonlocal_viz = None
            if viz_dir is not None:
                if viz_count < max_viz:
                    nonlocal_viz = viz_dir / f"match_{frames[i].index:05d}_{frames[target].index:05d}.png"
                    viz_count += 1
                elif not capped_logged:
                    log.info("match visualisation cap of %d reached; further pairs are logged "
                             "numerically only", max_viz)
                    warnings.append(f"match visualisations capped at {max_viz} images")
                    capped_logged = True
            r = register_pair(load(i), load(target), frames[i].index, frames[target].index,
                              detector=detector, limits=limits,
                              allow_homography_fallback=allow_homography_fallback,
                              viz_path=nonlocal_viz)
            if (not r.ok and turn_translation_frac is not None
                    and r.reason.startswith("implausible translation")):
                wide = replace(limits, max_translation_frac=float(turn_translation_frac))
                r_turn = register_pair(load(i), load(target), frames[i].index,
                                       frames[target].index, detector=detector, limits=wide,
                                       allow_homography_fallback=allow_homography_fallback,
                                       viz_path=nonlocal_viz)
                if r_turn.ok:
                    msg = (f"row turn accepted between frames {frames[i].index} and "
                           f"{frames[target].index}: translation ({r_turn.tx:.0f}, "
                           f"{r_turn.ty:.0f}) px exceeds --max-translation-frac "
                           f"{limits.max_translation_frac:.2f} but is within the serpentine "
                           f"allowance {turn_translation_frac:.2f} ({r_turn.inliers} "
                           f"inliers, reproj {r_turn.mean_reproj_error:.2f} px) - without "
                           f"--scan-mode serpentine this pair would have broken the chain")
                    log.info(msg)
                    warnings.append(msg)
                    r = r_turn
            r.bridged = target != i + 1
            pairs.append(r)
            if r.ok:
                result = r
                break
            if not allow_bridging or target > i + 2:
                break
            target += 1
        if result is not None:
            if result.bridged:
                msg = (f"bridged over frame(s) {frames[i].index + 1}..{frames[target].index - 1}: "
                       f"direct pair could not be registered")
                log.warning(msg)
                warnings.append(msg)
            cum = cur_tf[-1] @ np.linalg.inv(_to3x3(result.matrix))
            # transforms map frame coords -> segment-0 coords; the estimated matrix maps
            # frame i -> frame target, so its inverse maps target -> i.
            cur_frames.append(frames[target])
            cur_tf.append(cum)
            i = target
        else:
            msg = (f"chain break after frame {frames[i].index}: could not register onto "
                   f"frame {frames[i + 1].index} or the one after")
            log.warning(msg)
            warnings.append(msg)
            segments.append(Segment(cur_frames, cur_tf))
            i += 1
            cur_frames = [frames[i]]
            cur_tf = [np.eye(3)]
    segments.append(Segment(cur_frames, cur_tf))

    log.info("Built %d raw segment(s) (sizes %s) before rejoin", len(segments),
             [s.length for s in segments])

    rejoin_attempts: list[RejoinAttempt] = []
    if len(segments) > 1:
        if allow_rejoin:
            segments, rejoin_attempts, rj_warn, rj_pairs = rejoin_segments(
                segments, limits, window=rejoin_window,
                max_translation_frac=rejoin_max_translation_frac,
                viz_dir=viz_dir, max_viz=max_viz)
            warnings += rj_warn
            pairs += rj_pairs
        else:
            msg = ("wide-baseline segment rejoin is disabled (--no-rejoin); the "
                   f"{len(segments)} disconnected segments are kept as they are")
            log.warning(msg)
            warnings.append(msg)

    segments.sort(key=lambda s: s.length, reverse=True)
    log.info("Built %d segment(s); longest has %d frames", len(segments), segments[0].length)
    if len(segments) > 1:
        warnings.append(
            f"registration produced {len(segments)} disconnected segments "
            f"(sizes {[s.length for s in segments]}) that could not be rejoined into "
            "a single chain")
    return segments, pairs, warnings, rejoin_attempts
