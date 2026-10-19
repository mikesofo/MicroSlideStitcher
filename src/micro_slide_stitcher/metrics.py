"""Per-frame quality metrics and colour-safe exposure normalisation."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np


@dataclass
class FrameMetric:
    index: int
    filename: str
    timestamp_s: float
    focus_score: float
    duplicate_score: float
    mean_intensity: float
    accepted: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def focus_score(image: np.ndarray) -> float:
    """Variance of the Laplacian (higher = sharper).

    Note: for a smartphone-through-eyepiece histology video values are typically
    one order of magnitude lower than for a normal photograph, because the field
    is low-contrast and heavily video-compressed. Calibrate --blur-threshold on
    your own footage; see README.
    """
    gray = to_gray(image)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def duplicate_score(a: np.ndarray, b: np.ndarray, size: int = 64) -> float:
    """Similarity of two frames in [-1, 1]; ~1.0 means near-identical.

    Cheap measure: zero-mean normalised cross-correlation of heavily downscaled
    grayscale thumbnails (equivalent to 1 - normalised MSE up to scaling), which
    is robust to brightness offsets but still spatially aware.
    """
    ga = cv2.resize(to_gray(a), (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    gb = cv2.resize(to_gray(b), (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    ga -= ga.mean()
    gb -= gb.mean()
    denom = float(np.linalg.norm(ga) * np.linalg.norm(gb))
    if denom < 1e-6:
        return 1.0
    return float(np.sum(ga * gb) / denom)


def histogram_correlation(a: np.ndarray, b: np.ndarray) -> float:
    ha = cv2.calcHist([to_gray(a)], [0], None, [64], [0, 256])
    hb = cv2.calcHist([to_gray(b)], [0], None, [64], [0, 256])
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile: int = 8) -> np.ndarray:
    """Colour-safe local contrast: CLAHE on the L channel of LAB only.

    Never applied per RGB channel, so the H&E pink/purple balance is preserved.
    """
    if image.ndim == 2:
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile, tile))
        return clahe.apply(image)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile, tile))
    l2 = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l2, a, b)), cv2.COLOR_LAB2BGR)


def side_by_side(before: np.ndarray, after: np.ndarray, max_width: int = 1600) -> np.ndarray:
    """Labelled before/after comparison image."""
    h = min(before.shape[0], after.shape[0])
    scale = min(1.0, (max_width / 2) / max(before.shape[1], 1))
    def prep(img):
        out = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if out.ndim == 2:
            out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
        return out
    b, a = prep(before), prep(after)
    h = min(b.shape[0], a.shape[0])
    canvas = np.hstack([b[:h], a[:h]])
    cv2.putText(canvas, "BEFORE", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
    cv2.putText(canvas, "AFTER", (b.shape[1] + 12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (0, 0, 255), 2)
    return canvas
