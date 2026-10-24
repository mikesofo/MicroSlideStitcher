"""Content-based reconnection of pieces that time order could not join (0.8).

When the registration chain breaks - the slide moved too fast, or every frame of a turn was
blurred - the pipeline is left with several pieces.  The rejoin pass only compares the
last few frames of one piece with the first few frames of the next piece *in time*.  But
when screening a slide you constantly come back over tissue you have already seen: the row
above, or the same strip on the way back.  Those overlaps can be anywhere in the two pieces,
and nothing in 0.7 looked for them.

Here every frame of every piece is described once by its SIFT features.  For each frame of
one piece, its features vote for the frames of the other piece they resemble most (the
retrieval step of Brown & Lowe, IJCV 2007), so candidate pairs are found from image
content alone - no time order and no (possibly broken) placement is trusted.  Each
candidate is then registered with the normal matcher and must pass every normal gate,
including the overlap check (horizontal AND vertical overlap, spread matches, image
agreement).

A wrong link can fit almost perfectly and attach a piece to the wrong tissue, so two pieces
are merged only when at least ``min_links`` verified links, using different frames on both
sides, agree on where one piece sits relative to the other.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path

import cv2
import numpy as np

from .logging_utils import get_logger
from .register import (RegistrationLimits, Segment, _read_frame, _to3x3, merge_segments,
                       register_pair)


@dataclass
class ReconnectLimits:
    feature_scale: float = 0.5       # describe frames at this fraction of their size
    n_features: int = 1500
    votes_per_frame: int = 3         # candidate partners per frame
    min_votes: int = 8               # ignore weaker candidates
    max_candidates: int = 40         # verification budget per pair of pieces
    min_links: int = 2               # independent agreeing links needed to merge
    agree_frac: float = 0.03         # links agree if their offsets differ by < this x diagonal
    max_seconds: float = 120.0


@dataclass
class ReconnectLink:
    piece_a: int
    piece_b: int
    frame_a: int                     # frame index (as in frame_metrics.csv)
    frame_b: int
    votes: int
    ok: bool
    inliers: int = 0
    overlap_x: float = float("nan")
    overlap_y: float = float("nan")
    overlap_ncc: float = float("nan")
    offset_x: float = float("nan")   # where piece b's first frame lands in piece a
    offset_y: float = float("nan")
    used: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReconnectResult:
    attempted: bool
    merges: list[dict] = field(default_factory=list)
    links: list[ReconnectLink] = field(default_factory=list)
    seconds: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"attempted": self.attempted, "merges": self.merges,
                "links": [l.to_dict() for l in self.links], "seconds": self.seconds,
                "note": self.note}


def _describe(path: Path, scale: float, n: int, cache: dict):
    img = _read_frame(path, cache)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(nfeatures=n)
    _, desc = sift.detectAndCompute(small, None)
    return desc if desc is not None else np.zeros((0, 128), np.float32)


def _vote(desc_b: dict[int, np.ndarray], desc_a: dict[int, np.ndarray],
          limits: ReconnectLimits) -> list[tuple[int, int, int]]:
    """For every frame of piece B, the frames of piece A its features resemble most."""
    owners, pool = [], []
    for k, d in desc_a.items():
        if len(d):
            owners.append(np.full(len(d), k))
            pool.append(d)
    if not pool:
        return []
    owner = np.concatenate(owners)
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=48))
    flann.add([np.concatenate(pool).astype(np.float32)])
    flann.train()
    out = []
    for kb, d in desc_b.items():
        if len(d) < 2:
            continue
        counts: dict[int, int] = {}
        for m in flann.knnMatch(d.astype(np.float32), k=2):
            if len(m) == 2 and m[0].distance < 0.75 * m[1].distance:
                ka = int(owner[m[0].trainIdx])
                counts[ka] = counts.get(ka, 0) + 1
        best = sorted(counts.items(), key=lambda kv: -kv[1])[:limits.votes_per_frame]
        out += [(ka, kb, v) for ka, v in best if v >= limits.min_votes]
    return out


def _spread(cands: list[tuple[int, int, int]], budget: int) -> list[tuple[int, int, int]]:
    """Strongest candidates first, but no frame may take more than two slots."""
    chosen, use_a, use_b = [], {}, {}
    for c in sorted(cands, key=lambda c: -c[2]):
        if use_a.get(c[0], 0) >= 2 or use_b.get(c[1], 0) >= 2:
            continue
        chosen.append(c)
        use_a[c[0]] = use_a.get(c[0], 0) + 1
        use_b[c[1]] = use_b.get(c[1], 0) + 1
        if len(chosen) >= budget:
            break
    return chosen


def _agreeing(links: list[tuple[ReconnectLink, np.ndarray]], tol: float, need: int):
    """Largest group of links whose implied placement of piece B agrees, using different
    frames on both sides.  Returns the group or None."""
    best = None
    for ref, rel in links:
        t = rel[:2, 2]
        group = [(l, r) for l, r in links if np.hypot(*(r[:2, 2] - t)) < tol]
        fa = {l.frame_a for l, _ in group}
        fb = {l.frame_b for l, _ in group}
        indep = min(len(fa), len(fb))
        if indep >= need and (best is None or indep > best[0]):
            best = (indep, group)
    return best[1] if best else None


def reconnect_segments(segments: list[Segment], detector_limits: RegistrationLimits,
                       limits: ReconnectLimits | None = None
                       ) -> tuple[list[Segment], ReconnectResult, list[str]]:
    log = get_logger()
    limits = limits or ReconnectLimits()
    res = ReconnectResult(attempted=len(segments) > 1)
    warnings: list[str] = []
    if len(segments) < 2:
        return segments, res, warnings
    t0 = time.time()
    cache: dict = {}
    # A content link joins frames recorded at any time, so the movement-between-frames
    # limit of the chain does not apply; the overlap check (both directions, spread,
    # image agreement) is what decides.
    min_ov = detector_limits.min_overlap_frac or 0.10
    detector_limits = replace(detector_limits, max_translation_frac=1.0 - min_ov)
    pieces = sorted(segments, key=lambda s: s.length, reverse=True)
    desc: dict[int, dict[int, np.ndarray]] = {}

    def describe(i: int) -> dict[int, np.ndarray]:
        if i not in desc:
            desc[i] = {k: _describe(f.path, limits.feature_scale, limits.n_features, cache)
                       for k, f in enumerate(pieces[i].frames)}
        return desc[i]

    log.info("Content-based reconnection: %d pieces (sizes %s); looking for frames that show "
             "the same tissue, regardless of when they were recorded", len(pieces),
             [p.length for p in pieces])
    merged_any = True
    labels = list(range(len(pieces)))          # original piece number of each entry
    while merged_any and len(pieces) > 1 and time.time() - t0 < limits.max_seconds:
        merged_any = False
        for ib in range(1, len(pieces)):
            a, b = pieces[0], pieces[ib]
            da, db = describe(0), describe(ib)
            cands = _spread(_vote(db, da, limits), limits.max_candidates)
            verified: list[tuple[ReconnectLink, np.ndarray]] = []
            h, w = _read_frame(a.frames[0].path, cache).shape[:2]
            for ka, kb, votes in cands:
                if time.time() - t0 > limits.max_seconds:
                    break
                fa, fb = a.frames[ka], b.frames[kb]
                r = register_pair(_read_frame(fa.path, cache), _read_frame(fb.path, cache),
                                  fa.index, fb.index, detector="sift", limits=detector_limits,
                                  allow_homography_fallback=False)
                link = ReconnectLink(labels[0], labels[ib], fa.index, fb.index, votes, r.ok,
                                     r.inliers, r.overlap_x, r.overlap_y, r.overlap_ncc,
                                     reason=r.reason)
                if r.ok and r.matrix is not None:
                    rel = (a.transforms[ka] @ np.linalg.inv(_to3x3(r.matrix))
                           @ np.linalg.inv(_to3x3(b.transforms[kb])))
                    link.offset_x, link.offset_y = float(rel[0, 2]), float(rel[1, 2])
                    verified.append((link, rel))
                res.links.append(link)
            tol = limits.agree_frac * float(np.hypot(w, h))
            group = _agreeing(verified, tol, limits.min_links)
            if group is None:
                msg = (f"pieces {labels[0] + 1} and {labels[ib] + 1}: {len(cands)} candidate "
                       f"frame pair(s) found by content, {len(verified)} verified - "
                       f"not enough independent agreeing links (need {limits.min_links})")
                log.info(msg)
                continue
            for l, _ in group:
                l.used = True
                l.reason = "accepted: agrees with the other links"
            best_link, _ = max(group, key=lambda g: g[0].inliers)
            ka = next(k for k, f in enumerate(a.frames) if f.index == best_link.frame_a)
            kb = next(k for k, f in enumerate(b.frames) if f.index == best_link.frame_b)
            # place B with the median offset of the agreeing links
            rel = np.median(np.stack([r for _, r in group]), axis=0)
            new = Segment(list(a.frames) + list(b.frames),
                          list(a.transforms) + [rel @ _to3x3(T) for T in b.transforms],
                          list(a.rejoin_links) + list(b.rejoin_links))
            spread = max(np.hypot(*(r[:2, 2] - rel[:2, 2])) for _, r in group)
            info = {"piece_a": labels[0] + 1, "piece_b": labels[ib] + 1,
                    "frames_a": a.length, "frames_b": b.length, "links": len(group),
                    "links_verified": len(verified), "candidates": len(cands),
                    "link_disagreement_px": round(float(spread), 2),
                    "via_frames": [[l.frame_a, l.frame_b] for l, _ in group]}
            res.merges.append(info)
            log.info("RECONNECTED pieces %d and %d (%d + %d frames) by content: %d independent "
                     "links agree within %.1f px (%d of %d candidates verified)",
                     labels[0] + 1, labels[ib] + 1, a.length, b.length, len(group), spread,
                     len(verified), len(cands))
            pieces[0] = new
            desc[0] = {**da, **{a.length + k: d for k, d in db.items()}}
            del pieces[ib]
            del desc[ib]
            desc = {(k if k < ib else k - 1): v for k, v in desc.items()}
            del labels[ib]
            merged_any = True
            break
    res.seconds = round(time.time() - t0, 2)
    if len(pieces) > 1:
        res.note = (f"{len(pieces)} pieces remain: no independent agreeing content links "
                    "were found between them")
    pieces.sort(key=lambda s: s.length, reverse=True)
    return pieces, res, warnings
