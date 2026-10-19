"""Blur / near-duplicate gating and exposure normalisation behaviour."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_synthetic_video import make_slide  # noqa: E402

from micro_slide_stitcher.extract import Frame, gate_frames
from micro_slide_stitcher.metrics import apply_clahe, duplicate_score, focus_score


def _write(dirpath: Path, name: str, img) -> Path:
    p = dirpath / name
    cv2.imwrite(str(p), img)
    return p


def test_focus_score_drops_with_blur():
    slide = make_slide(600, 500, seed=2)
    sharp = focus_score(slide)
    blurred = focus_score(cv2.GaussianBlur(slide, (31, 31), 12))
    assert sharp > blurred * 5
    assert blurred >= 0


def test_duplicate_score_identity_and_difference():
    slide = make_slide(900, 700, seed=4)
    a = slide[0:400, 0:400]
    assert duplicate_score(a, a) == pytest.approx(1.0, abs=1e-4)
    b = slide[250:650, 400:800]
    assert duplicate_score(a, b) < 0.9
    # brightness-only change must stay near-duplicate
    a_bright = np.clip(a.astype(np.float32) + 25, 0, 255).astype(np.uint8)
    assert duplicate_score(a, a_bright) > 0.99


def test_clahe_is_colour_safe():
    slide = make_slide(400, 400, seed=6)
    out = apply_clahe(slide)
    assert out.shape == slide.shape
    # hue must be broadly preserved: mean channel ordering B<G<R for H&E pink stays
    m_in, m_out = slide.reshape(-1, 3).mean(0), out.reshape(-1, 3).mean(0)
    assert np.argsort(m_in).tolist() == np.argsort(m_out).tolist()


def test_gate_rejects_blurry_and_duplicates(tmp_path):
    slide = make_slide(1200, 700, seed=8)
    work = tmp_path / "work"
    work.mkdir()
    imgs = [
        slide[0:400, 0:500],                                   # 0 sharp, keep
        slide[0:400, 0:500],                                   # 1 exact duplicate -> reject
        cv2.GaussianBlur(slide[0:400, 250:750], (31, 31), 14),  # 2 blurry -> reject
        slide[0:400, 300:800],                                 # 3 sharp, keep
    ]
    frames = [Frame(i, i * 0.5, _write(work, f"cand_{i:05d}.png", im))
              for i, im in enumerate(imgs)]
    accepted, metrics = gate_frames(frames, blur_threshold=60.0, dedup_threshold=0.995,
                                    normalize="none", accepted_dir=tmp_path / "acc",
                                    exposure_compare_path=tmp_path / "exp.png")
    assert [m.accepted for m in metrics] == [True, False, False, True]
    assert "near-duplicate" in metrics[1].reason
    assert "blurry" in metrics[2].reason
    assert len(accepted) == 2
    assert (tmp_path / "exp.png").exists()
    assert all(p.exists() for p in (f.path for f in accepted))
