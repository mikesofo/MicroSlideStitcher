"""Self-contained HTML run report."""
from __future__ import annotations

import html
import json
from pathlib import Path

CSS = """
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1d2026}
main{max-width:1100px;margin:0 auto;padding:28px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px;border-bottom:1px solid #dfe3e8;padding-bottom:6px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}
td,th{border:1px solid #e2e5ea;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#eef1f5;width:34%}
code{background:#eef1f5;padding:1px 5px;border-radius:3px;font-size:13px}
pre{background:#fff;border:1px solid #e2e5ea;padding:10px;overflow:auto;font-size:12.5px}
img{max-width:100%;border:1px solid #ccd2da;background:#fff}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;color:#fff;font-size:13px}
.ok{background:#1f8b4c}.warn{background:#c78a00}.bad{background:#b3261e}
.small{color:#5a6472;font-size:13px}
ul{margin:6px 0 6px 18px}
"""

LIMITATIONS = [
    "This is a research / personal workflow aid, NOT a validated whole-slide imaging (WSI) "
    "diagnostic device. Do not use the panorama for primary diagnosis.",
    "Geometry is estimated with a similarity (partial affine) model per frame pair; residual "
    "cumulative drift over long scans is expected and grows with chain length.",
    "Optical distortion of the eyepiece/phone-lens combination, focus drift and rolling shutter "
    "are not corrected, so seams and local blur can persist.",
    "Regions the operator never dwelled on, or panned across too fast (motion blur), can be "
    "missing or soft in the panorama.",
]


def _badge(status: str) -> str:
    cls = {"SUCCESSFUL": "ok", "PARTIALLY SUCCESSFUL": "warn", "FAILED": "bad"}.get(status, "warn")
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _table(rows: list[tuple[str, object]]) -> str:
    out = ["<table>"]
    for k, v in rows:
        out.append(f"<tr><th>{html.escape(str(k))}</th><td>{v}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def _segments_html(diag: Path, summary: dict) -> str:
    """Per-segment table (feature B) plus the skipped-segment accounting."""
    segs = summary.get("segments") or []
    if not segs:
        return ""
    image_mode = summary.get("input_mode") == "images"
    source_label = "image indices" if image_mode else "time range (s)"
    head = ("<tr><th style='width:auto'>#</th><th style='width:auto'>frames</th>"
            f"<th style='width:auto'>frame indices</th><th style='width:auto'>{source_label}</th>"
            "<th style='width:auto'>pixels</th><th style='width:auto'>coverage</th>"
            "<th style='width:auto'>registration (median inliers / ratio / reproj px)</th>"
            "<th style='width:auto'>output</th></tr>")
    rows = []
    for e in segs:
        st = e.get("registration_stats", {})
        inl = st.get("inliers", {})
        rat = st.get("inlier_ratio", {})
        rep = st.get("mean_reproj_error_px", {})
        source_span = (f"<td>{e.get('image_index_first', e['frame_index_first'])}&ndash;"
                       f"{e.get('image_index_last', e['frame_index_last'])}</td>"
                       if image_mode else
                       f"<td>{e['time_start_s']:.1f}&ndash;{e['time_end_s']:.1f} s</td>")
        rows.append(
            "<tr>"
            f"<td>{e['rank']}</td><td>{e['n_frames']}</td>"
            f"<td>{e['frame_index_first']}&ndash;{e['frame_index_last']}</td>"
            + source_span
            + f"<td>{e['width']}x{e['height']}</td>"
            f"<td>{e['coverage_percent']:.1f} %</td>"
            f"<td>{inl.get('median', '-')} / {rat.get('median', '-')} / "
            f"{rep.get('median', '-')}"
            + (f" &middot; {len(e['rejoin_links'])} recovered join(s)"
               if e.get("rejoin_links") else "")
            + f"</td><td><code>{html.escape(str(e['output']))}</code></td></tr>")
    table = f"<table>{head}{''.join(rows)}</table>"

    note = ""
    if len(segs) > 1:
        note = (f"<p>The scan is split into <b>{len(segs)} panoramas</b>: the largest keeps the "
                f"requested output path, the others are suffixed <code>_seg02</code>, "
                f"<code>_seg03</code>, ... in descending frame-count order.</p>")

    skipped = summary.get("skipped_segments") or []
    skip_html = ""
    if skipped:
        items = "".join(
            f"<li>{k['n_frames']} frame(s), indices {html.escape(str(k['frame_indices']))}, "
            + (f"image indices {k.get('image_index_first', '?')}&ndash;"
               f"{k.get('image_index_last', '?')}" if image_mode else
               f"t = {k['time_start_s']:.1f}&ndash;{k['time_end_s']:.1f} s")
            + (f" &mdash; {html.escape(str(k['reason']))}" if k.get("reason") else
               " &mdash; below --min-segment-frames, not written")
            + "</li>" for k in skipped)
        skip_html = f"<h3>Skipped segments</h3><ul>{items}</ul>"

    per_seg_imgs = []
    for e in segs[1:]:
        for label, name in [(f"Segment {e['rank']} panorama preview", e.get("preview_name")),
                            (f"Segment {e['rank']} camera path", e.get("camera_path_name"))]:
            if name and (diag / name).exists():
                per_seg_imgs.append(f"<h3>{html.escape(label)}</h3>"
                                    f"<img src='{html.escape(name)}' alt='{html.escape(label)}'>")
    return note + table + skip_html + "".join(per_seg_imgs)


def _bundle_html(summary: dict) -> str:
    """Scan type, non-adjacent match discovery and global bundle adjustment (0.7.0)."""
    ba = summary.get("bundle_adjustment") or {}
    mode = str(summary.get("scan_mode", "sweep"))
    if not ba:
        return ""
    mode_html = ("<p><b>Scan type:</b> "
                 + ("serpentine &mdash; back and forth in rows, each row stepped down so it "
                    "overlaps the row above." if mode == "serpentine" else
                    "single sweep &mdash; one continuous pan.") + "</p>")
    if not ba.get("enabled"):
        return (mode_html + "<p class='small'>Frame positions come from the pairwise chain "
                f"only: {html.escape(str(ba.get('reason', 'bundle adjustment off')))}.</p>")
    before = (ba.get("reproj_before") or {})
    after = (ba.get("reproj_after") or {})
    shift = (ba.get("position_shift") or {})

    def px(value):
        return "&mdash;" if value is None else f"{float(value):.3f}"

    rows = [
        ("Optimiser", html.escape(str(ba.get("optimiser", "?")))),
        ("Outcome", "<b>APPLIED</b>" if ba.get("applied") else
                    "<b>NOT applied</b> &mdash; the chained solution was kept"),
        ("Reason", html.escape(str(ba.get("reason", "")))),
        ("Frames refined", ba.get("frames", 0)),
        ("Reference frame held fixed (gauge)", ba.get("reference_frame_index", -1)),
        ("Non-adjacent candidates shortlisted", ba.get("candidates", 0)),
        ("Non-adjacent pairs really matched", ba.get("attempted", 0)),
        ("Non-adjacent pairs accepted", ba.get("accepted", 0)),
        ("Adjacent pairs re-used as constraints", ba.get("adjacent_pairs_used", 0)),
        ("Correspondences optimised", ba.get("correspondences", 0)),
        ("Iterations", ba.get("iterations", 0)),
        ("Mean reprojection error before &rarr; after",
         f"{px(before.get('mean'))} &rarr; {px(after.get('mean'))} px"),
        ("Median reprojection error before &rarr; after",
         f"{px(before.get('median'))} &rarr; {px(after.get('median'))} px"),
        ("Worst reprojection error before &rarr; after",
         f"{px(before.get('max'))} &rarr; {px(after.get('max'))} px"),
        ("Frames moved by (mean / max)",
         f"{px(shift.get('mean'))} / {px(shift.get('max'))} px"),
        ("Time spent", f"{ba.get('seconds', 0.0):.2f} s "
                       f"(discovery {ba.get('discovery_seconds', 0.0):.2f} s, "
                       f"optimise {ba.get('optimise_seconds', 0.0):.2f} s)"),
    ]
    notes = ba.get("notes") or []
    notes_html = ("<ul class='small'>"
                  + "".join(f"<li>{html.escape(str(n))}</li>" for n in notes)
                  + "</ul>") if notes else ""
    pairs = ba.get("extra_pairs") or []
    pairs_html = ""
    if pairs:
        head = ("<tr><th style='width:auto'>frames</th>"
                "<th style='width:auto'>chain distance</th>"
                "<th style='width:auto'>predicted overlap</th>"
                "<th style='width:auto'>detector</th><th style='width:auto'>inliers</th>"
                "<th style='width:auto'>ratio</th><th style='width:auto'>reproj px</th>"
                "<th style='width:auto'>result</th></tr>")
        body = "".join(
            "<tr>"
            f"<td>{p.get('src_index')}&rarr;{p.get('dst_index')}</td>"
            f"<td>{p.get('chain_distance', '')}</td>"
            f"<td>{float(p.get('predicted_overlap', 0.0)):.3f}</td>"
            f"<td>{html.escape(str(p.get('detector', '')))}</td>"
            f"<td>{p.get('inliers', 0)}</td>"
            f"<td>{float(p.get('inlier_ratio', 0.0)):.3f}</td>"
            f"<td>{float(p.get('mean_reproj_error', 0.0)):.2f}</td>"
            f"<td>{'<b>ACCEPTED</b>' if p.get('accepted') else 'rejected'}: "
            f"{html.escape(str(p.get('reason', '')))}</td></tr>"
            for p in pairs)
        pairs_html = ("<p class='small'>Non-adjacent (loop-closure) pairs that were really "
                      f"feature-matched:</p><table>{head}{body}</table>")
    return mode_html + _table(rows) + notes_html + pairs_html


def _rejoin_html(summary: dict) -> str:
    """Wide-baseline segment recovery section (feature A)."""
    rj = summary.get("rejoin") or {}
    attempts = rj.get("attempts") or []
    if not rj:
        return ""
    if not rj.get("enabled", True):
        return ("<p class='small'>Wide-baseline segment recovery was disabled "
                "(<code>--no-rejoin</code>).</p>")
    cfg = (f"<p class='small'>window K={rj.get('window')}, SIFT, translation allowance "
           f"{rj.get('max_translation_frac')} of the frame size.</p>")
    if not attempts:
        return cfg + ("<p class='small'>The chain never broke, so no rejoin attempt was "
                      "necessary.</p>")
    ok = [a for a in attempts if a.get("accepted")]
    head = ("<tr><th style='width:auto'>segments</th><th style='width:auto'>frame pair</th>"
            "<th style='width:auto'>detector</th><th style='width:auto'>matches</th>"
            "<th style='width:auto'>inliers</th><th style='width:auto'>ratio</th>"
            "<th style='width:auto'>reproj px</th><th style='width:auto'>tx, ty</th>"
            "<th style='width:auto'>result</th></tr>")
    rows = "".join(
        "<tr>"
        f"<td>{a['seg_a']}&rarr;{a['seg_b']}</td>"
        f"<td>{a['src_index']}&rarr;{a['dst_index']}</td>"
        f"<td>{html.escape(str(a['detector']))}</td><td>{a['matches']}</td>"
        f"<td>{a['inliers']}</td><td>{a['inlier_ratio']}</td>"
        f"<td>{a['mean_reproj_error']}</td><td>{a['tx']:.0f}, {a['ty']:.0f}</td>"
        f"<td>{'<b>ACCEPTED</b>' if a['accepted'] else 'rejected'}: "
        f"{html.escape(str(a['reason']))}</td></tr>"
        for a in attempts)
    verdict = (f"<p><b>{len(ok)}</b> of {len(attempts)} wide-baseline attempt(s) rejoined a "
               f"broken chain.</p>" if ok else
               f"<p><b>None</b> of the {len(attempts)} wide-baseline attempt(s) could rejoin "
               f"the broken chain(s); the pieces are written separately.</p>")
    return cfg + verdict + f"<table>{head}{rows}</table>"


def write_report(diag: Path, summary: dict) -> Path:
    diag.mkdir(parents=True, exist_ok=True)
    s = summary
    imgs = []
    for label, name in [("Panorama preview", s.get("preview_name")),
                        ("Camera path", "camera_path.png"),
                        ("Exposure before/after", "exposure_comparison.png")]:
        if name and (diag / name).exists():
            imgs.append(f"<h3>{html.escape(label)}</h3><img src='{html.escape(name)}' alt='{html.escape(label)}'>")

    matches = sorted(p.name for p in diag.glob("matches/match_*.png"))
    rejoin_viz = sorted(p.name for p in diag.glob("matches/rejoin_*.png"))
    match_html = ""
    if matches or rejoin_viz:
        shown = rejoin_viz[:12] + matches[:12]
        thumbs = "".join(
            f"<div style='display:inline-block;margin:4px;width:320px'>"
            f"<img src='matches/{html.escape(m)}'><div class='small'>{html.escape(m)}</div></div>"
            for m in shown)
        match_html = (f"<p class='small'>{len(matches)} adjacent-pair and {len(rejoin_viz)} "
                      f"wide-baseline rejoin visualisations in <code>matches/</code>; "
                      f"the rejoin ones plus the first 12 adjacent ones are shown.</p>{thumbs}")

    settings_rows = [(k, f"<code>{html.escape(str(v))}</code>") for k, v in s["settings"].items()]
    counts = s["counts"]
    stats = s["registration_stats"]

    warn_html = ("<ul>" + "".join(f"<li>{html.escape(w)}</li>" for w in s["warnings"]) + "</ul>"
                 ) if s["warnings"] else "<p class='small'>none</p>"

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>MicroSlideStitcher report</title><style>{CSS}</style></head><body><main>
<h1>MicroSlideStitcher run report</h1>
<p class="small">{html.escape(s['timestamp'])} &middot; input <code>{html.escape(s['input'])}</code></p>
<p>Registration verdict: {_badge(s['verdict'])} &nbsp; total runtime
<b>{s['timing']['total_s']:.1f} s</b></p>

<h2>Outputs</h2>
{_table([(k, f"<code>{html.escape(str(v))}</code>") for k, v in s['outputs'].items()])}

<h2>Settings</h2>
{_table(settings_rows)}

<h2>{'Image-sequence metadata' if s.get('input_mode') == 'images' else 'Video metadata'}</h2>
<pre>{html.escape(json.dumps(s.get('input_metadata', s.get('video_metadata', {})), indent=2))}</pre>

<h2>Frame accounting</h2>
{_table([
    ("Candidate images" if s.get('input_mode') == 'images' else "Candidate frames sampled", counts['candidates']),
    ("Accepted", counts['accepted']),
    ("Rejected (blurry)", counts['rejected_blur']),
    ("Rejected (near-duplicate)", counts['rejected_duplicate']),
    ("Rejected (other)", counts['rejected_other']),
    ("Frames in stitched segment", counts['frames_stitched']),
    ("Segments found", counts['segments']),
    ("Segments written", counts.get('segments_written', 1)),
    ("Frames in written panoramas", counts.get('frames_written_total',
                                               counts['frames_stitched'])),
    ("Segments skipped (too small)", counts.get('segments_skipped', 0)),
    ("Wide-baseline rejoins used", counts.get('rejoins_accepted', 0)),
    ("Non-adjacent pairs accepted", counts.get('non_adjacent_pairs_accepted', 0)),
])}

<h2>Segments</h2>
{_segments_html(diag, s) or "<p class='small'>none</p>"}

<h2>Scan type and global bundle adjustment</h2>
{_bundle_html(s) or "<p class='small'>not applicable</p>"}

<h2>Wide-baseline segment recovery</h2>
{_rejoin_html(s) or "<p class='small'>not applicable</p>"}

<h2>Registration quality</h2>
<pre>{html.escape(json.dumps(stats, indent=2))}</pre>

<h2>Timing</h2>
<pre>{html.escape(json.dumps(s['timing'], indent=2))}</pre>

<h2>Warnings</h2>
{warn_html}

<h2>Images</h2>
{''.join(imgs)}

<h2>Match visualisations</h2>
{match_html or "<p class='small'>none written</p>"}

<h2>Limitations</h2>
<ul>{''.join(f'<li>{html.escape(x)}</li>' for x in LIMITATIONS)}</ul>
</main></body></html>"""
    out = diag / "report.html"
    out.write_text(doc, encoding="utf-8")
    return out
