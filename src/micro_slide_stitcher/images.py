"""Image-sequence discovery, metadata and OpenCV-only candidate preparation."""
from __future__ import annotations

import glob
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from .extract import Frame
from .logging_utils import get_logger
from .probe import ProbeError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_NATURAL_PARTS = re.compile(r"(\d+)")


@dataclass
class ImageSequenceMetadata:
    """Metadata which is meaningful for a still-image input sequence."""

    path: str
    input_type: str
    image_count: int
    candidate_count: int
    width: int
    height: int
    uniformly_sized: bool
    image_order: str
    image_stride: int

    def to_dict(self) -> dict:
        return asdict(self)


def is_image_path(path: Path) -> bool:
    """Whether `path` has one of the supported still-image extensions."""
    return path.suffix.lower() in IMAGE_SUFFIXES


def natural_sort_key(path: Path) -> tuple:
    """Case-insensitive natural filename key: IMG_2.jpg sorts before IMG_10.jpg."""
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in _NATURAL_PARTS.split(path.name))


def _sort_paths(paths: list[Path], image_order: str) -> list[Path]:
    if image_order == "name":
        return sorted(paths, key=lambda p: (natural_sort_key(p), str(p).casefold()))
    if image_order == "mtime":
        # Filename is a deterministic tie-breaker for phone bursts with equal mtimes.
        return sorted(paths, key=lambda p: (p.stat().st_mtime_ns, natural_sort_key(p),
                                            str(p).casefold()))
    raise ValueError(f"unknown image ordering: {image_order}")


def resolve_image_paths(source: str | Path, image_order: str = "name") -> list[Path]:
    """Resolve a directory, one image, or a glob to ordered supported image files."""
    text = str(source)
    expanded = Path(text).expanduser()
    if expanded.is_dir():
        candidates = [p for p in expanded.iterdir() if p.is_file() and is_image_path(p)]
    elif glob.has_magic(text):
        candidates = [Path(p) for p in glob.glob(str(expanded), recursive=False)
                      if Path(p).is_file() and is_image_path(Path(p))]
    elif expanded.is_file() and is_image_path(expanded):
        candidates = [expanded]
    else:
        candidates = []
    if not candidates:
        raise ProbeError(
            f"No supported images were found for '{source}'. Expected a directory, an image "
            "glob, or an image ending in .png, .jpg/.jpeg, .tif/.tiff, .bmp or .webp.")
    return _sort_paths(candidates, image_order)


def _scaled_size(width: int, height: int, max_dimension: int | None) -> tuple[int, int]:
    if not max_dimension or max(width, height) <= max_dimension:
        return width, height
    scale = max_dimension / float(max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def probe_image_sequence(source: str | Path, image_order: str, image_stride: int,
                         max_dimension: int | None) -> ImageSequenceMetadata:
    """Read image dimensions and reject a sequence that cannot have one frame shape."""
    paths = resolve_image_paths(source, image_order)
    selected = paths[::image_stride]
    if not selected:
        raise ProbeError("Image stride selected no images.")
    raw_sizes: list[tuple[int, int]] = []
    scaled_sizes: list[tuple[int, int]] = []
    for path in selected:
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ProbeError(f"Could not read image '{path}'.")
        height, width = image.shape[:2]
        raw_sizes.append((width, height))
        scaled_sizes.append(_scaled_size(width, height, max_dimension))
    if len(set(scaled_sizes)) != 1:
        examples = ", ".join(f"{w}x{h}" for w, h in sorted(set(raw_sizes))[:4])
        suffix = ("; --max-dimension still leaves different aspect ratios"
                  if max_dimension else "")
        raise ProbeError(
            "Image sequence has mixed dimensions "
            f"({examples}){suffix}. Registration requires every image to have the same size.")
    width, height = scaled_sizes[0]
    log = get_logger()
    rule = ("natural filename order (numbers sort numerically, so IMG_2 precedes IMG_10)"
            if image_order == "name" else
            "modification time order (natural filename order breaks timestamp ties)")
    log.info("Resolved %d image(s), taking every %d: %s", len(paths), image_stride, rule)
    log.info("Image sequence metadata: %d input image(s), %d candidate(s), %dx%d px, "
             "uniform=%s", len(paths), len(selected), width, height,
             len(set(raw_sizes)) == 1)
    return ImageSequenceMetadata(
        path=str(source), input_type="images", image_count=len(paths),
        candidate_count=len(selected), width=width, height=height,
        uniformly_sized=len(set(raw_sizes)) == 1, image_order=image_order,
        image_stride=image_stride)


def extract_image_frames(source: str | Path, workdir: Path, image_order: str,
                         image_stride: int, max_dimension: int | None) -> list[Frame]:
    """Prepare image candidates as PNGs using OpenCV only; ffmpeg is never consulted."""
    log = get_logger()
    paths = resolve_image_paths(source, image_order)
    selected = list(enumerate(paths))[::image_stride]
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("cand_*.png"):
        old.unlink()
    frames: list[Frame] = []
    for source_index, path in selected:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ProbeError(f"Could not read image '{path}'.")
        height, width = image.shape[:2]
        out_w, out_h = _scaled_size(width, height, max_dimension)
        if (out_w, out_h) != (width, height):
            image = cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_AREA)
        out = workdir / f"cand_{source_index:05d}.png"
        if not cv2.imwrite(str(out), image):  # pragma: no cover - disk errors are rare
            raise ProbeError(f"Could not prepare candidate image '{out}'.")
        # timestamp_s is deliberately an ordinal in image mode, never elapsed video time.
        frames.append(Frame(index=source_index, timestamp_s=float(source_index), path=out))
    log.info("Prepared %d image candidate(s) with OpenCV into %s", len(frames), workdir)
    return frames
