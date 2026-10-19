"""Registration accuracy on synthetic pairs with KNOWN translations."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_synthetic_video import make_slide  # noqa: E402

from micro_slide_stitcher.register import RegistrationLimits, decompose, register_pair


def _pair(slide, x0, y0, dx, dy, w=480, h=360, brightness=0.0):
    a = slide[y0:y0 + h, x0:x0 + w].copy()
    b = slide[y0 + dy:y0 + dy + h, x0 + dx:x0 + dx + w].copy()
    if brightness:
        b = np.clip(b.astype(np.float32) + brightness, 0, 255).astype(np.uint8)
    return a, b


@pytest.mark.parametrize("detector", ["orb", "sift"])
@pytest.mark.parametrize("dx,dy", [(90, 0), (0, 80), (-120, 40), (200, -30)])
def test_pairwise_translation_recovered(detector, dx, dy):
    slide = make_slide(1400, 1200, seed=11)
    a, b = _pair(slide, 400, 400, dx, dy, brightness=12.0)
    res = register_pair(a, b, 0, 1, detector=detector,
                        limits=RegistrationLimits(min_inliers=15))
    assert res.ok, res.reason
    # The estimated transform maps frame a -> frame b; a shift of the window by
    # (dx, dy) means image content moves by (-dx, -dy).
    assert res.tx == pytest.approx(-dx, abs=2.0)
    assert res.ty == pytest.approx(-dy, abs=2.0)
    assert res.scale == pytest.approx(1.0, abs=0.01)
    assert abs(res.rotation_deg) < 0.5
    assert res.mean_reproj_error < 2.0
    assert res.inliers >= 15


def test_insufficient_overlap_is_rejected():
    slide = make_slide(2200, 900, seed=5)
    a, b = _pair(slide, 100, 100, 1400, 0, w=480, h=360)  # no overlap at all
    res = register_pair(a, b, 0, 1, detector="orb", limits=RegistrationLimits())
    assert not res.ok
    assert res.reason


def test_decompose_identity():
    tx, ty, s, r = decompose(np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -7.0]]))
    assert (tx, ty) == (5.0, -7.0)
    assert s == pytest.approx(1.0)
    assert r == pytest.approx(0.0)
