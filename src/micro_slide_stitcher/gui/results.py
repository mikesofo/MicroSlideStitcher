"""Read-only readers for the diagnostics directory the CLI writes.

Nothing here re-derives a number: every value shown by the GUI comes straight out of
``summary.json`` / ``frame_metrics.csv``.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FramePoint:
    index: int
    timestamp_s: float
    focus_score: float
    accepted: bool
    reason: str


@dataclass
class SegmentView:
    """One written panorama, as described by summary.json."""
    rank: int
    n_frames: int
    time_start_s: float
    time_end_s: float
    width: int
    height: int
    coverage_percent: float
    output: str
    preview_path: str
    camera_path: str
    frame_index_first: int = 0
    frame_index_last: int = 0
    input_mode: str = "video"
    image_index_first: int = 0
    image_index_last: int = 0

    @property
    def title(self) -> str:
        return f"Segment {self.rank} ({self.n_frames} frames)"

    @property
    def info_line(self) -> str:
        if self.input_mode == "images":
            return (f"Segment {self.rank} of the scan · {self.n_frames} images "
                    f"(image indices {self.image_index_first}–{self.image_index_last}) · "
                    f"{self.width} × {self.height} px · coverage {self.coverage_percent:.1f} %")
        return (f"Segment {self.rank} of the scan · {self.n_frames} frames "
                f"(candidate index {self.frame_index_first}–{self.frame_index_last}) · "
                f"t = {self.time_start_s:.1f}–{self.time_end_s:.1f} s · "
                f"{self.width} × {self.height} px · "
                f"coverage {self.coverage_percent:.1f} %")


@dataclass
class RunResults:
    diagnostics: Path
    summary: dict = field(default_factory=dict)
    frames: list[FramePoint] = field(default_factory=list)
    segments: list[SegmentView] = field(default_factory=list)
    error: str = ""

    # ---- convenience accessors used by the summary panel ----------------------
    @property
    def verdict(self) -> str:
        return str(self.summary.get("verdict", "—"))

    @property
    def counts(self) -> dict:
        return self.summary.get("counts", {}) or {}

    @property
    def stats(self) -> dict:
        return self.summary.get("registration_stats", {}) or {}

    @property
    def warnings(self) -> list[str]:
        return list(self.summary.get("warnings", []) or [])

    @property
    def report_html(self) -> Path | None:
        p = (self.summary.get("outputs", {}) or {}).get("report")
        path = Path(p) if p else self.diagnostics / "report.html"
        return path if path.exists() else None

    @property
    def output_dir(self) -> Path | None:
        if self.segments:
            return Path(self.segments[0].output).parent
        return self.diagnostics if self.diagnostics.exists() else None


def read_frame_metrics(diagnostics: Path) -> list[FramePoint]:
    """Parse frame_metrics.csv (written by diagnostics.write_frame_metrics)."""
    path = Path(diagnostics) / "frame_metrics.csv"
    if not path.exists():
        return []
    out: list[FramePoint] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(FramePoint(
                    index=int(row["index"]),
                    timestamp_s=float(row["timestamp_s"]),
                    focus_score=float(row["focus_score"]),
                    accepted=str(row["accepted"]).strip().lower() == "true",
                    reason=row.get("reason", ""),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _segments_from_summary(summary: dict, diagnostics: Path) -> list[SegmentView]:
    views: list[SegmentView] = []
    for entry in summary.get("segments", []) or []:
        preview = diagnostics / str(entry.get("preview_name", "panorama_preview.png"))
        campath = diagnostics / str(entry.get("camera_path_name", "camera_path.png"))
        views.append(SegmentView(
            rank=int(entry.get("rank", len(views) + 1)),
            n_frames=int(entry.get("n_frames", 0)),
            time_start_s=float(entry.get("time_start_s", 0.0)),
            time_end_s=float(entry.get("time_end_s", 0.0)),
            width=int(entry.get("width", 0)),
            height=int(entry.get("height", 0)),
            coverage_percent=float(entry.get("coverage_percent", 0.0)),
            output=str(entry.get("output", "")),
            preview_path=str(preview),
            camera_path=str(campath),
            frame_index_first=int(entry.get("frame_index_first", 0)),
            frame_index_last=int(entry.get("frame_index_last", 0)),
            input_mode=str(summary.get("input_mode", "video")),
            image_index_first=int(entry.get("image_index_first", entry.get("frame_index_first", 0))),
            image_index_last=int(entry.get("image_index_last", entry.get("frame_index_last", 0))),
        ))
    return views


def load_results(diagnostics: str | Path) -> RunResults:
    """Load everything the GUI shows after a run. Never raises."""
    diag = Path(diagnostics)
    res = RunResults(diagnostics=diag)
    res.frames = read_frame_metrics(diag)
    sfile = diag / "summary.json"
    if sfile.exists():
        try:
            res.summary = json.loads(sfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            res.error = f"summary.json could not be read: {exc}"
        else:
            res.segments = _segments_from_summary(res.summary, diag)
    else:
        failure = diag / "failure.txt"
        if failure.exists():
            try:
                res.error = failure.read_text(encoding="utf-8").strip()
            except OSError as exc:  # pragma: no cover - defensive
                res.error = str(exc)
        else:
            res.error = "no summary.json in the diagnostics directory"
    return res


def summary_rows(res: RunResults) -> list[tuple[str, str]]:
    """Flat label/value rows for the results summary panel, straight from summary.json."""
    s = res.summary
    c = res.counts
    st = res.stats
    timing = s.get("timing", {}) or {}
    rejoin = s.get("rejoin", {}) or {}
    input_mode = str(s.get("input_mode", "video"))
    meta = s.get("input_metadata", s.get("video_metadata", {})) or {}
    outputs = s.get("outputs", {}) or {}

    def num(value, fmt="{}"):
        return "—" if value is None else fmt.format(value)

    n_attempts = c.get("rejoin_attempts", len(rejoin.get("attempts", []) or []))
    n_ok = c.get("rejoins_accepted", 0)
    if not rejoin.get("enabled", True):
        rejoin_text = "disabled (--no-rejoin)"
    elif not n_attempts:
        rejoin_text = "not needed - the chain never broke"
    else:
        rejoin_text = (f"{n_attempts} wide-baseline attempt(s), {n_ok} accepted "
                       f"(window K={rejoin.get('window', '?')})")

    ba = s.get("bundle_adjustment", {}) or {}
    scan_mode = str(s.get("scan_mode", "sweep"))
    scan_text = ("serpentine - back and forth in rows" if scan_mode == "serpentine"
                 else "single sweep - one continuous pan")
    if not ba.get("enabled"):
        ba_text = f"not used - {ba.get('reason', 'off')}"
    else:
        before = (ba.get("reproj_before", {}) or {}).get("mean")
        after = (ba.get("reproj_after", {}) or {}).get("mean")
        errors = (f"reprojection error {before:.2f} → {after:.2f} px"
                  if before is not None and after is not None else "reprojection error n/a")
        head = "applied" if ba.get("applied") else "NOT applied (chained solution kept)"
        ba_text = (f"{head} - {ba.get('accepted', 0)} extra non-adjacent pair(s) accepted "
                   f"of {ba.get('attempted', 0)} matched, {ba.get('iterations', 0)} "
                   f"iteration(s) of {ba.get('optimiser', '?')}, {errors}")

    inl = (st.get("inliers", {}) or {})
    rep = (st.get("mean_reproj_error_px", {}) or {})
    rows = [
        ("Verdict", res.verdict),
        ("Input images" if input_mode == "images" else "Input video", str(s.get("input", "—"))),
        ("Image sequence" if input_mode == "images" else "Video",
         (f"{meta.get('image_count', '?')} images, {meta.get('width', '?')} × "
          f"{meta.get('height', '?')} px, {meta.get('image_order', '?')} order, "
          f"stride {meta.get('image_stride', '?')}" if input_mode == "images" else
          f"{meta.get('width', '?')} × {meta.get('height', '?')} px, "
          f"{meta.get('fps', 0):.2f} fps, {meta.get('duration_s', 0):.1f} s, "
          f"{meta.get('codec', '?')}") if meta else "—"),
        ("Candidate images" if input_mode == "images" else "Candidate frames sampled",
         num(c.get("candidates"))),
        ("Accepted / rejected", f"{c.get('accepted', '—')} accepted, "
                                f"{c.get('rejected_blur', 0)} blurry, "
                                f"{c.get('rejected_duplicate', 0)} near-duplicate, "
                                f"{c.get('rejected_other', 0)} other"),
        ("Frames in a written panorama", f"{c.get('frames_written_total', '—')} of "
                                         f"{c.get('accepted', '—')} accepted"),
        ("Segments", f"{c.get('segments', '—')} found, "
                     f"{c.get('segments_written', '—')} written, "
                     f"{c.get('segments_skipped', 0)} skipped"),
        ("Scan type", scan_text),
        ("Bundle adjustment", ba_text),
        ("Rejoin", rejoin_text),
        ("Pairs registered", f"{st.get('pairs_registered', '—')} of "
                             f"{st.get('pairs_attempted', '—')} attempted "
                             f"({st.get('pairs_failed', 0)} failed, "
                             f"{st.get('pairs_bridged', 0)} bridged, "
                             f"{st.get('sift_fallbacks', 0)} SIFT fallbacks)"),
        ("Inliers (median)", num(inl.get("median"), "{:g}")
         + (f"   min {inl.get('min'):g} / max {inl.get('max'):g}" if inl.get("n") else "")),
        ("Reprojection error (median)", (f"{rep['median']:.2f} px" if rep.get("median") is not None
                                         else "—")),
        ("Runtime", f"{timing.get('total_s', 0):.1f} s total "
                    f"(extract {timing.get('extract_s', 0):.1f} s, "
                    f"gate {timing.get('gate_s', 0):.1f} s, "
                    f"register {timing.get('register_s', 0):.1f} s, "
                    f"stitch {timing.get('stitch_s', 0):.1f} s"
                    + (f", bundle adjust {timing['bundle_adjust_s']:.1f} s"
                       if timing.get("bundle_adjust_s") is not None else "") + ")"),
    ]
    for seg in res.segments:
        span = (f"image indices {seg.image_index_first}–{seg.image_index_last}"
                if input_mode == "images" else
                f"t = {seg.time_start_s:.1f}–{seg.time_end_s:.1f} s")
        rows.append((f"Output · segment {seg.rank}",
                     f"{seg.output}  ({seg.width} × {seg.height} px, {seg.n_frames} frames, "
                     f"{span}, coverage {seg.coverage_percent:.1f} %)"))
    for skipped in s.get("skipped_segments", []) or []:
        span = (f"image indices {skipped.get('image_index_first', '?')}–"
                f"{skipped.get('image_index_last', '?')}" if input_mode == "images" else
                f"t = {skipped.get('time_start_s', 0):.1f}–"
                f"{skipped.get('time_end_s', 0):.1f} s")
        rows.append(("Skipped segment",
                     f"{skipped.get('n_frames', '?')} frame(s), "
                     f"{span} "
                     f"(below --min-segment-frames)"))
    rows.append(("Diagnostics directory", str(res.diagnostics)))
    if outputs.get("report"):
        rows.append(("HTML report", str(outputs["report"])))
    return rows
