"""ffprobe-based video probing."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .environment import child_environment
from .logging_utils import get_logger


class ProbeError(RuntimeError):
    pass


class MultimediaToolMissingError(ProbeError):
    """Raised when the external ffmpeg package required for video input is absent."""


@dataclass
class VideoMetadata:
    path: str
    width: int
    height: int
    fps: float
    duration_s: float
    codec: str
    pix_fmt: str
    frame_count_estimate: int
    nb_frames_reported: int | None
    bit_rate: int | None
    has_audio: bool
    rotation: float
    tags: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _require(tool: str) -> str:
    p = shutil.which(tool)
    if p is None:
        raise MultimediaToolMissingError(
            f"'{tool}' was not found on PATH. Video input needs the system ffmpeg package.\n"
            "Install it with one of:\n"
            "    sudo pacman -S --needed ffmpeg       # Arch / Manjaro\n"
            "    sudo apt install ffmpeg              # Debian / Ubuntu\n"
            "    sudo dnf install ffmpeg              # Fedora (RPM Fusion may be needed)\n"
            "    sudo zypper install ffmpeg           # openSUSE\n"
            "Image-sequence input does not require ffmpeg or ffprobe."
        )
    return p


def _parse_rate(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return 0.0
        return num_f / den_f if den_f else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_video(path: Path) -> VideoMetadata:
    """Run ffprobe and return structured metadata. Raises ProbeError with a clear message."""
    log = get_logger()
    path = Path(path)
    if not path.exists():
        raise ProbeError(f"Input video does not exist: {path}")
    if not path.is_file():
        raise ProbeError(f"Input path is not a file: {path}")

    ffprobe = _require("ffprobe")
    cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams",
           "-print_format", "json", str(path)]
    log.debug("probe: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_environment())
    if proc.returncode != 0:
        raise ProbeError(
            f"ffprobe could not read '{path}'.\nffprobe said: {proc.stderr.strip() or '(no output)'}\n"
            "The file may be corrupt, truncated, or not a video."
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ProbeError(f"Could not parse ffprobe output for '{path}': {exc}") from exc

    streams = data.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    if not vstreams:
        raise ProbeError(f"'{path}' contains no video stream (streams found: "
                         f"{[s.get('codec_type') for s in streams]}).")
    v = vstreams[0]
    fmt = data.get("format", {})

    fps = _parse_rate(v.get("r_frame_rate")) or _parse_rate(v.get("avg_frame_rate"))
    duration = 0.0
    for candidate in (v.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    nb_frames = v.get("nb_frames")
    try:
        nb_frames_i = int(nb_frames)
    except (TypeError, ValueError):
        nb_frames_i = None

    rotation = 0.0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rotation = float(sd["rotation"])
            except (TypeError, ValueError):
                pass

    width, height = int(v.get("width", 0)), int(v.get("height", 0))
    if width <= 0 or height <= 0:
        raise ProbeError(f"'{path}' reports an invalid frame size {width}x{height}.")

    br = fmt.get("bit_rate") or v.get("bit_rate")
    try:
        br_i = int(br)
    except (TypeError, ValueError):
        br_i = None

    meta = VideoMetadata(
        path=str(path),
        width=width,
        height=height,
        fps=round(fps, 6),
        duration_s=round(duration, 6),
        codec=v.get("codec_name", "unknown"),
        pix_fmt=v.get("pix_fmt", "unknown"),
        frame_count_estimate=nb_frames_i if nb_frames_i else int(round(fps * duration)),
        nb_frames_reported=nb_frames_i,
        bit_rate=br_i,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        rotation=rotation,
        tags={**(fmt.get("tags") or {}), **(v.get("tags") or {})},
    )
    log.info("Probed %s: %dx%d, %.3f fps, %.2f s, codec=%s, ~%d frames, audio=%s",
             path.name, meta.width, meta.height, meta.fps, meta.duration_s,
             meta.codec, meta.frame_count_estimate, meta.has_audio)
    return meta


def write_metadata(meta: VideoMetadata | object, diagnostics_dir: Path) -> Path:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    input_type = getattr(meta, "input_type", "video")
    out = diagnostics_dir / ("image_metadata.json" if input_type == "images"
                             else "video_metadata.json")
    out.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")  # type: ignore[attr-defined]
    return out
