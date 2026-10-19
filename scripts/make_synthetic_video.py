#!/usr/bin/env python3
"""Generate a synthetic 'microscope scan' video with KNOWN frame-to-frame translations.

A large fake H&E-like texture is generated, then a window is panned across it in a
raster pattern.  Small brightness variation (phone auto-exposure) and optional
motion blur on a few frames are added, so the gating logic has something to reject.
The ground-truth per-step translations are written next to the video as JSON.

Usage:
    python scripts/make_synthetic_video.py --out examples/synthetic.mp4 \
        --frame-size 640 480 --steps 12 --step-x 120 --fps 30 --hold 15
"""
from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np


def make_slide(width: int, height: int, seed: int = 7) -> np.ndarray:
    """Fake H&E slide: pale pink background + purple 'nuclei' + fibre streaks."""
    rng = np.random.default_rng(seed)
    img = np.zeros((height, width, 3), np.float32)
    img[..., 0] = 210  # B
    img[..., 1] = 190  # G
    img[..., 2] = 225  # R  -> pinkish
    # eosinophilic blotches
    for _ in range(int(width * height / 9000)):
        cx, cy = rng.integers(0, width), rng.integers(0, height)
        r = rng.integers(20, 90)
        col = (rng.integers(150, 200), rng.integers(120, 170), rng.integers(200, 240))
        cv2.circle(img, (int(cx), int(cy)), int(r), tuple(float(c) for c in col), -1)
    # nuclei
    for _ in range(int(width * height / 260)):
        cx, cy = rng.integers(0, width), rng.integers(0, height)
        r = rng.integers(3, 8)
        col = (rng.integers(90, 140), rng.integers(40, 90), rng.integers(110, 160))
        cv2.circle(img, (int(cx), int(cy)), int(r), tuple(float(c) for c in col), -1)
    # collagen fibres
    for _ in range(int(width * height / 4000)):
        p0 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        p1 = (p0[0] + int(rng.integers(-160, 160)), p0[1] + int(rng.integers(-60, 60)))
        cv2.line(img, p0, p1, (170, 150, 200), int(rng.integers(1, 4)))
    img = cv2.GaussianBlur(img, (3, 3), 0)
    img += rng.normal(0, 3.0, img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def insert_gap(pos: list[tuple[int, int]], gap_after: int | None, gap_x: int, gap_y: int
               ) -> list[tuple[int, int]]:
    """Shift every scan position after `gap_after` by (gap_x, gap_y).

    This fabricates one oversized jump in the middle of the scan - the situation that
    breaks the registration chain on real footage (a fast pan). A moderate gap still
    leaves overlap (recoverable by the wide-baseline rejoin pass), a huge one does not.
    """
    if gap_after is None or (gap_x == 0 and gap_y == 0):
        return pos
    return [(x + gap_x, y + gap_y) if i > gap_after else (x, y)
            for i, (x, y) in enumerate(pos)]


def build_positions(steps: int, step_x: int, step_y: int, cols: int) -> list[tuple[int, int]]:
    pos = [(0, 0)]
    x, y = 0, 0
    for i in range(steps):
        if (i + 1) % cols == 0:
            y += step_y
        else:
            x += step_x if (y // max(step_y, 1)) % 2 == 0 else -step_x
        pos.append((x, y))
    return pos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--image-dir", type=Path, default=None,
                    help="also write one lossless still per scan position into this directory")
    ap.add_argument("--frame-size", type=int, nargs=2, default=[640, 480])
    ap.add_argument("--steps", type=int, default=11)
    ap.add_argument("--step-x", type=int, default=120)
    ap.add_argument("--step-y", type=int, default=140)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hold", type=int, default=15, help="video frames held per scan position")
    ap.add_argument("--brightness-amp", type=float, default=10.0)
    ap.add_argument("--blur-positions", type=int, nargs="*", default=[],
                    help="scan positions rendered with heavy motion blur")
    ap.add_argument("--gap-after", type=int, default=None,
                    help="insert one oversized jump after this scan position (0-based)")
    ap.add_argument("--gap-x", type=int, default=0, help="extra x jump of the gap in px")
    ap.add_argument("--gap-y", type=int, default=0, help="extra y jump of the gap in px")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    fw, fh = args.frame_size
    pos = build_positions(args.steps, args.step_x, args.step_y, args.cols)
    pos = insert_gap(pos, args.gap_after, args.gap_x, args.gap_y)
    xs = [p[0] for p in pos]
    ys = [p[1] for p in pos]
    pad = 40
    slide_w = fw + (max(xs) - min(xs)) + 2 * pad
    slide_h = fh + (max(ys) - min(ys)) + 2 * pad
    slide = make_slide(slide_w, slide_h, args.seed)
    ox, oy = pad - min(xs), pad - min(ys)

    tmp = Path(tempfile.mkdtemp(prefix="synthvid_"))
    try:
        if args.image_dir is not None:
            args.image_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for i, (px, py) in enumerate(pos):
            crop = slide[oy + py: oy + py + fh, ox + px: ox + px + fw].copy()
            bright = args.brightness_amp * np.sin(i * 0.7)
            crop = np.clip(crop.astype(np.float32) + bright, 0, 255).astype(np.uint8)
            if i in args.blur_positions:
                crop = cv2.GaussianBlur(crop, (31, 31), 12)
            if args.image_dir is not None:
                # Unpadded phone-like names deliberately exercise natural image ordering.
                cv2.imwrite(str(args.image_dir / f"IMG_{i + 1}.png"), crop)
            for _ in range(args.hold):
                cv2.imwrite(str(tmp / f"f_{n:05d}.png"), crop)
                n += 1
        args.out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise SystemExit("ffmpeg not found; install it (sudo pacman -S ffmpeg)")
        cmd = [ffmpeg, "-y", "-v", "error", "-framerate", str(args.fps),
               "-i", str(tmp / "f_%05d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-crf", "18", str(args.out)]
        subprocess.run(cmd, check=True)
        gt = {
            "video": str(args.out),
            "frame_size": [fw, fh],
            "fps": args.fps,
            "hold_frames": args.hold,
            "positions": pos,
            "step_translations": [[pos[i + 1][0] - pos[i][0], pos[i + 1][1] - pos[i][1]]
                                  for i in range(len(pos) - 1)],
            "expected_panorama": [slide_w - 2 * pad, slide_h - 2 * pad],
            "blur_positions": args.blur_positions,
            "gap_after": args.gap_after,
            "gap": [args.gap_x, args.gap_y],
        }
        gt_path = args.out.with_suffix(".truth.json")
        gt_path.write_text(json.dumps(gt, indent=2), encoding="utf-8")
        print(f"wrote {args.out} ({n} video frames, {len(pos)} scan positions)")
        if args.image_dir is not None:
            print(f"wrote {len(pos)} still images to {args.image_dir}")
        print(f"wrote ground truth {gt_path}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
