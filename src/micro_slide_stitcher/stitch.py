"""Panorama composition: canvas bounds, warping, blending, auto-crop, saving."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .logging_utils import get_logger
from .register import Segment


class MemoryGuardError(RuntimeError):
    pass


@dataclass
class StitchResult:
    width: int
    height: int
    frames_used: int
    crop_box: tuple[int, int, int, int]
    coverage_fraction: float
    blend: str
    warnings: list[str]
    trajectory: list[tuple[float, float]]


def _corners(w: int, h: int) -> np.ndarray:
    return np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)


def canvas_bounds(transforms: list[np.ndarray], w: int, h: int):
    pts = []
    for T in transforms:
        pts.append(cv2.perspectiveTransform(_corners(w, h), T.astype(np.float64)))
    allpts = np.concatenate(pts, axis=0).reshape(-1, 2)
    xmin, ymin = np.floor(allpts.min(axis=0)).astype(int)
    xmax, ymax = np.ceil(allpts.max(axis=0)).astype(int)
    return int(xmin), int(ymin), int(xmax), int(ymax)


def check_trajectory(transforms: list[np.ndarray], w: int, h: int) -> tuple[list[tuple[float, float]], list[str]]:
    """Return frame-centre trajectory plus warnings about suspicious jumps/drift."""
    warns: list[str] = []
    centre = np.float32([[[w / 2.0, h / 2.0]]])
    traj = [tuple(map(float, cv2.perspectiveTransform(centre, T.astype(np.float64))[0, 0]))
            for T in transforms]
    diag = float(np.hypot(w, h))
    for i in range(1, len(traj)):
        d = float(np.hypot(traj[i][0] - traj[i - 1][0], traj[i][1] - traj[i - 1][1]))
        if d > 0.95 * diag:
            warns.append(f"suspicious trajectory jump of {d:.0f} px between chain positions "
                         f"{i - 1} and {i} (frame diagonal {diag:.0f} px)")
    # cumulative scale drift
    scales = [float(np.hypot(T[0, 0], T[1, 0])) for T in transforms]
    if scales:
        lo, hi = min(scales), max(scales)
        if hi / max(lo, 1e-9) > 1.15:
            warns.append(f"cumulative scale drift detected: cumulative scale ranges "
                         f"{lo:.3f}-{hi:.3f} over the chain")
    return traj, warns


def estimate_bytes(width: int, height: int) -> int:
    """Bytes needed for accumulation: float32 BGR sum + float32 weight + uint8 output."""
    px = int(width) * int(height)
    return px * (4 * 3 + 4 + 3)


def compose(segment: Segment, blend: str = "feather", memory_limit_bytes: int = 3 * 1024 ** 3,
            feather_sharpness: float = 0.02) -> tuple[np.ndarray, np.ndarray, StitchResult]:
    """Warp all frames of `segment` into one canvas. Returns (panorama_bgr, coverage_mask, result)."""
    log = get_logger()
    first = cv2.imread(str(segment.frames[0].path), cv2.IMREAD_COLOR)
    if first is None:
        raise RuntimeError(f"cannot read {segment.frames[0].path}")
    h, w = first.shape[:2]

    traj, warns = check_trajectory(segment.transforms, w, h)
    xmin, ymin, xmax, ymax = canvas_bounds(segment.transforms, w, h)
    cw, ch = xmax - xmin, ymax - ymin
    need = estimate_bytes(cw, ch)
    log.info("Canvas bounds: x[%d,%d] y[%d,%d] -> %dx%d px (~%.2f GiB working memory)",
             xmin, xmax, ymin, ymax, cw, ch, need / 1024 ** 3)
    if need > memory_limit_bytes:
        raise MemoryGuardError(
            f"The panorama canvas would be {cw}x{ch} px and need about "
            f"{need / 1024 ** 3:.2f} GiB of working memory (limit "
            f"{memory_limit_bytes / 1024 ** 3:.2f} GiB).\n"
            "Options:\n"
            "  * add --max-dimension 960 (or 1280) for a downscaled preview run\n"
            "  * lower --sampling-fps so fewer, more widely spaced frames are used\n"
            "  * raise the limit with --memory-limit-gb if you really have the RAM\n"
            "  * split the video and stitch parts separately")

    offset = np.array([[1, 0, -xmin], [0, 1, -ymin], [0, 0, 1]], dtype=np.float64)

    if blend == "multiband":
        try:
            pano, mask = _compose_multiband(segment, offset, (cw, ch), (w, h))
        except Exception as exc:  # pragma: no cover - depends on OpenCV build
            log.warning("multiband blending failed (%s); falling back to feather", exc)
            warns.append(f"multiband blending failed ({exc}); used feather instead")
            blend = "feather"
            pano, mask = _compose_feather(segment, offset, (cw, ch), (w, h), feather_sharpness)
    else:
        pano, mask = _compose_feather(segment, offset, (cw, ch), (w, h), feather_sharpness)

    coverage = float((mask > 0).mean())
    res = StitchResult(cw, ch, len(segment.frames), (0, 0, cw, ch), coverage, blend,
                       warns, traj)
    for wmsg in warns:
        log.warning(wmsg)
    return pano, mask, res


def _frame_weight(h: int, w: int, sharpness: float) -> np.ndarray:
    """Distance-transform feather weights (max at frame centre, 0 at the border)."""
    m = np.ones((h, w), np.uint8) * 255
    m[0, :] = 0
    m[-1, :] = 0
    m[:, 0] = 0
    m[:, -1] = 0
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    dist /= max(dist.max(), 1e-6)
    return (dist ** 1.0 + 1e-3).astype(np.float32)


def _compose_feather(segment: Segment, offset, canvas_wh, frame_wh, sharpness):
    log = get_logger()
    cw, ch = canvas_wh
    w, h = frame_wh
    acc = np.zeros((ch, cw, 3), np.float32)
    wacc = np.zeros((ch, cw), np.float32)
    base_weight = _frame_weight(h, w, sharpness)

    for k, (fr, T) in enumerate(zip(segment.frames, segment.transforms)):
        img = cv2.imread(str(fr.path), cv2.IMREAD_COLOR)
        if img is None:
            log.warning("skipping unreadable frame %s", fr.path)
            continue
        M = offset @ T.astype(np.float64)
        # Warp only into the bounding box of this frame to limit memory/time.
        c = cv2.perspectiveTransform(_corners(w, h), M).reshape(-1, 2)
        x0 = max(int(np.floor(c[:, 0].min())) - 1, 0)
        y0 = max(int(np.floor(c[:, 1].min())) - 1, 0)
        x1 = min(int(np.ceil(c[:, 0].max())) + 1, cw)
        y1 = min(int(np.ceil(c[:, 1].max())) + 1, ch)
        if x1 <= x0 or y1 <= y0:
            continue
        shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
        Mloc = shift @ M
        tile_w, tile_h = x1 - x0, y1 - y0
        warped = cv2.warpPerspective(img, Mloc, (tile_w, tile_h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        wt = cv2.warpPerspective(base_weight, Mloc, (tile_w, tile_h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        acc[y0:y1, x0:x1] += warped.astype(np.float32) * wt[..., None]
        wacc[y0:y1, x0:x1] += wt
        if (k + 1) % 10 == 0:
            log.debug("blended %d/%d frames", k + 1, len(segment.frames))

    mask = (wacc > 1e-4).astype(np.uint8) * 255
    safe = np.where(wacc > 1e-4, wacc, 1.0)[..., None]
    pano = np.clip(acc / safe, 0, 255).astype(np.uint8)
    pano[mask == 0] = 0
    return pano, mask


def _compose_multiband(segment: Segment, offset, canvas_wh, frame_wh):
    cw, ch = canvas_wh
    w, h = frame_wh
    blender = cv2.detail.MultiBandBlender()
    blender.setNumBands(4)
    blender.prepare((0, 0, cw, ch))
    for fr, T in zip(segment.frames, segment.transforms):
        img = cv2.imread(str(fr.path), cv2.IMREAD_COLOR)
        if img is None:
            continue
        M = offset @ T.astype(np.float64)
        c = cv2.perspectiveTransform(_corners(w, h), M).reshape(-1, 2)
        x0 = max(int(np.floor(c[:, 0].min())) - 1, 0)
        y0 = max(int(np.floor(c[:, 1].min())) - 1, 0)
        x1 = min(int(np.ceil(c[:, 0].max())) + 1, cw)
        y1 = min(int(np.ceil(c[:, 1].max())) + 1, ch)
        shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
        Mloc = shift @ M
        tw, th = x1 - x0, y1 - y0
        warped = cv2.warpPerspective(img, Mloc, (tw, th), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        m = cv2.warpPerspective(np.full((h, w), 255, np.uint8), Mloc, (tw, th),
                                flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)
        blender.feed(warped.astype(np.int16), m, (x0, y0))
    dst, dst_mask = blender.blend(None, None)
    pano = np.clip(dst, 0, 255).astype(np.uint8)
    mask = (np.asarray(dst_mask) > 0).astype(np.uint8) * 255
    pano[mask == 0] = 0
    return pano, mask


def auto_crop(pano: np.ndarray, mask: np.ndarray, min_coverage_frac: float = 0.0
              ) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """Crop the outer empty margins using the coverage mask.

    Conservative: only the bounding box of ANY covered pixel is removed, so no
    imaged tissue can ever be cut away.
    """
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return pano, mask, (0, 0, pano.shape[1], pano.shape[0])
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    return pano[y0:y1, x0:x1], mask[y0:y1, x0:x1], (x0, y0, x1, y1)


#: JPEG cannot store an image wider or taller than 65,535 px; stay a little below it.
JPEG_MAX_SIDE = 65500


def save_jpeg(img_bgr: np.ndarray, path: Path, quality: int = 90) -> dict:
    """Write a JPEG, reducing the image only if it exceeds the JPEG size limit."""
    log = get_logger()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = img_bgr.shape[:2]
    reduced = max(h, w) > JPEG_MAX_SIDE
    if reduced:
        scale = JPEG_MAX_SIDE / max(h, w)
        img_bgr = cv2.resize(img_bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
        log.warning("the panorama (%dx%d) is larger than JPEG allows (%d px per side); the "
                    "JPEG is saved reduced to %dx%d - the full-size image is unaffected",
                    w, h, JPEG_MAX_SIDE, img_bgr.shape[1], img_bgr.shape[0])
    if img_bgr.ndim == 3 and img_bgr.shape[2] == 4:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_BGRA2BGR)
    if not cv2.imwrite(str(path), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]):
        raise OSError(f"could not write JPEG {path}")
    return {"path": str(path), "width": int(img_bgr.shape[1]), "height": int(img_bgr.shape[0]),
            "reduced": reduced, "quality": int(quality),
            "bytes": path.stat().st_size}


def read_panorama(path: Path) -> np.ndarray:
    """Read a panorama written by save_panorama (TIFF, PNG or JPEG) as BGR."""
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile
        img = tifffile.imread(str(path))
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise OSError(f"could not read {path}")
    return img


def convert_to_jpeg(src: Path, dst: Path, quality: int = 90) -> dict:
    """Convert an existing panorama file (TIFF/PNG/JPEG) to JPEG without restitching."""
    return save_jpeg(read_panorama(src), dst, quality)


def save_panorama(pano: np.ndarray, out_path: Path, preview_path: Path | None = None,
                  preview_max: int = 2000, jpeg_quality: int = 90,
                  jpeg_copy: bool = False) -> dict:
    """Save the panorama honouring the --output extension; always also write a PNG preview.

    With ``jpeg_copy`` a JPEG for sharing is written next to a TIFF/PNG output
    (same name, .jpg)."""
    log = get_logger()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ext = out_path.suffix.lower()
    written = {}
    if ext in (".tif", ".tiff"):
        import tifffile
        rgb = cv2.cvtColor(pano, cv2.COLOR_BGR2RGB)
        tifffile.imwrite(str(out_path), rgb, compression="deflate", photometric="rgb")
    elif ext == ".png":
        cv2.imwrite(str(out_path), pano, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    elif ext in (".jpg", ".jpeg"):
        info = save_jpeg(pano, out_path, jpeg_quality)
        if info["reduced"]:
            written["jpeg_reduced"] = f"{info['width']}x{info['height']}"
    else:
        raise ValueError(f"unsupported output extension '{ext}' (use .tif, .tiff, .png, "
                         ".jpg or .jpeg)")
    written["panorama"] = str(out_path)
    log.info("Wrote panorama %s (%dx%d, %.1f MB)", out_path, pano.shape[1], pano.shape[0],
             out_path.stat().st_size / 1e6)

    if jpeg_copy and ext not in (".jpg", ".jpeg"):
        info = save_jpeg(pano, out_path.with_suffix(".jpg"), jpeg_quality)
        written["jpeg"] = info["path"]
        if info["reduced"]:
            written["jpeg_reduced"] = f"{info['width']}x{info['height']}"
        log.info("Wrote JPEG copy for sharing %s (%dx%d, quality %d, %.1f MB)", info["path"],
                 info["width"], info["height"], info["quality"], info["bytes"] / 1e6)

    if preview_path is not None:
        scale = min(1.0, preview_max / max(pano.shape[1], pano.shape[0]))
        prev = cv2.resize(pano, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
            if scale < 1.0 else pano
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(preview_path), prev)
        written["preview"] = str(preview_path)
        log.info("Wrote preview %s (%dx%d)", preview_path, prev.shape[1], prev.shape[0])
    return written
