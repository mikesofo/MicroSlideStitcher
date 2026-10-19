"""In-application user manual (Help -> User manual, or F1).

Deliberate design decisions
---------------------------
* The manual is rendered with **QTextBrowser** only.  Qt WebEngine is *not* a
  dependency of this program and must never become one: it would add hundreds of
  megabytes to the standalone build and a second browser engine to maintain.
  Everything here therefore sticks to the Qt rich-text HTML subset, which also
  happens to be plain enough that the exported file opens in any browser.
* The prose lives in :data:`MANUAL_SECTIONS`, a module-level constant.  No data
  file has to be found at runtime, so the manual is identical in a source
  checkout, a pip install and the frozen PyInstaller / AppImage build.
* The complete flag reference (section 9) is **generated from
  ``cli.build_parser()`` every time the manual is built**.  It can therefore not
  drift away from the real command line interface, exactly like the settings
  tooltips in :mod:`micro_slide_stitcher.gui.cli_spec`.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QKeySequence, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..cli import build_parser
from .about import AFFILIATION, AUTHOR, DISCLAIMER, EMAIL

#: Internal URL of the one and only document.  ``ManualBrowser.loadResource``
#: answers it, which is what makes Back/Forward and ``#anchor`` links work
#: without any file on disk.
MANUAL_URL = "mss-manual:manual.html"

FIND_HIGHLIGHT = QColor("#ffe066")      # amber: readable against black text
FIND_CURRENT = QColor("#ff9d3d")        # stronger amber for the match you are on

#: A small, deliberately conservative style sheet.  Qt supports only a subset of
#: CSS in rich text; every rule below was checked in the offscreen screenshots.
MANUAL_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; color: #16191d; background: #ffffff; }
h1 { font-size: 19pt; color: #10243a; margin-bottom: 2px; }
h2 { font-size: 14pt; color: #10243a; margin-top: 20px; margin-bottom: 4px; }
h3 { font-size: 11.5pt; color: #1c3a5e; margin-top: 14px; margin-bottom: 2px; }
p, li, td, th { line-height: 148%; }
a { color: #0b4f9c; }
code, pre { font-family: monospace; font-size: 10pt; color: #23303d; }
pre { background: #f2f4f7; padding: 8px; }
th { background: #e8edf3; color: #10243a; text-align: left; }
.lead { color: #16191d; }
"""

# --------------------------------------------------------------------------- prose
#: (anchor, title, body html).  Section 9's body is a placeholder that is
#: replaced by the runtime-generated flag table.
FLAG_TABLE_PLACEHOLDER = "<!--FLAG-TABLE-->"

_DISCLAIMER_BOX = (
    '<table border="1" cellspacing="0" cellpadding="9" width="100%" '
    'bgcolor="#fff5e6"><tr><td>'
    '<b>Important limitation &mdash; not for diagnosis.</b><br>'
    + html.escape(DISCLAIMER) +
    '</td></tr></table>'
)

MANUAL_SECTIONS: list[tuple[str, str, str]] = [
    ("s1", "1. What this does, and what it must not be used for", f"""
<p class="lead">MicroSlideStitcher takes a video you recorded by holding a mobile phone
against the eyepiece of your microscope &mdash; or a set of overlapping photographs taken
the same way &mdash; and joins the individual fields into <b>one large picture of the
slide</b>. The result is an ordinary image file (TIFF, PNG or JPEG) that you can open in
QuPath, ImageJ, GIMP, or any picture viewer, e-mail to a colleague, or put in a talk.</p>

<p>Everything happens on your own computer. Nothing is uploaded, no account is needed and
no internet connection is used at any point.</p>

<p>What it is good for:</p>
<ul>
  <li>showing a whole lesion, or a whole strip of tissue, in one picture instead of a
      dozen separate field photographs;</li>
  <li>teaching, case presentations, tumour boards, posters and papers;</li>
  <li>your personal case archive and second-opinion e-mails.</li>
</ul>

{_DISCLAIMER_BOX}

<p>In plain terms: use the panorama to <i>show</i> and to <i>remember</i>, never to
<i>measure</i> and never as the thing you sign out from. Three concrete reasons:</p>
<ul>
  <li><b>Geometry is not calibrated.</b> The program estimates how each field moved
      relative to the previous one from the picture content alone. Small errors add up
      along a long scan, so distances, areas, depth of invasion and mitoses per square
      millimetre must not be taken from the panorama.</li>
  <li><b>Colour is not calibrated.</b> Your phone decides the white balance and the
      contrast enhancement changes local contrast, so the exact pink/purple of the
      haematoxylin and eosin is indicative only.</li>
  <li><b>Focus is not calibrated.</b> Out-of-focus fields are thrown away rather than
      repaired, so a region can be missing or softer than it looked down the microscope.</li>
</ul>
<p>For diagnostic work, go back to the glass slide.</p>
"""),

    ("s2", "2. Recording a good scan", """
<p>Ninety per cent of the final quality is decided before the computer sees anything.
Work through this list in order every time; it takes about a minute.</p>

<h3>Mount the phone &mdash; do not hold it</h3>
<p>Use a phone-to-eyepiece adapter (a clamp that grips the eyepiece tube and holds the
phone so its camera sits over the eye lens). A hand-held phone drifts, rotates and
changes its distance from the eyepiece, and all three ruin the join. Centre the image so
the round field fills as much of the frame as possible, then tighten the clamp and do not
touch the phone again until the scan is finished.</p>

<h3>Lock the focus</h3>
<p>In your camera app, tap on the tissue and then <b>lock</b> focus (in most apps a long
press gives you an "AE/AF lock" padlock; in a manual or "pro" mode set the focus slider by
hand). If focus is left on automatic, the phone will re-focus every time you pan across
empty glass, and every re-focus is a sequence of blurred frames that the program has to
discard &mdash; which is exactly how a scan ends up in pieces.</p>

<h3>Set the white balance by hand, against empty glass</h3>
<p>Move to a blank area of the slide with no tissue, then set the white balance manually
(a "pro" or manual mode; choose a fixed value such as 5000&nbsp;K rather than "auto") and
lock it. <b>A yellow or blue cast cannot be fixed afterwards.</b> The program deliberately
never rebalances the colour channels, because doing so would falsify the haematoxylin and
eosin colours; so whatever cast you record is the cast you keep. Lock the exposure at the
same time, so brightness does not jump between fields.</p>

<h3>Hold still for about one second on the first field</h3>
<p>Start recording, then stay completely still on the first field for roughly a second
before you begin to move. That gives the program at least one sharp, calm frame to start
the chain from. A scan that starts while the phone is already moving usually starts with
several unusable frames.</p>

<h3>Pan slowly, with about 50&nbsp;% overlap</h3>
<p>Move the stage &mdash; not the phone &mdash; in straight strips, roughly one field
every one to two seconds. Consecutive sampled fields must share about <b>half</b> their
area. That is the single most important number in this manual: the program can only join
two fields if it can recognise the same nuclei, vessel or tissue edge in both. Overlap
neighbouring strips by about a third as well, pause briefly at the end of each strip, and
never change the objective, the phone zoom or the focus in the middle of a scan.</p>

<h3>1080p at 30 frames per second beats 120 frames per second</h3>
<p>Choose the highest <i>resolution</i> at a modest <i>frame rate</i>: 1920&times;1080 at
30&nbsp;fps is better than a slow-motion mode. High frame rates buy you nothing here
(the program samples about two frames per second anyway) and they cost resolution,
bit rate and light. 4K at 30&nbsp;fps is better still if your phone keeps a high bit rate
and does not overheat. Avoid HDR modes and any "beauty" or "AI scene" enhancement.</p>

<h3>Turn video stabilisation OFF</h3>
<p>This one surprises people. Optical and especially electronic image stabilisation
<b>warps each frame differently</b> &mdash; it stretches and shifts parts of the picture
independently to hide your hand tremor. The program assumes that a field can be aligned to
the previous one by a shift, a small rotation and a uniform scale. A non-rigid, per-frame
warp breaks that assumption, so alignment quality collapses even though the video looks
smoother to the eye. Find the setting (often "video stabilisation", "EIS", "steady shot")
and switch it off. Because the phone is clamped to the eyepiece, you do not need it.</p>
"""),

    ("s2b", "3. Choosing the scan type: single sweep or serpentine", """
<p>Before you press <i>Run</i>, tell the program <b>how you moved the stage</b>. This is
the <b>Scan type</b> setting, and it is the one choice that changes the mathematics rather
than a threshold.</p>

<h3>Single sweep &mdash; one continuous pan</h3>
<p>You panned once, in one direction, along a strip of tissue. Each field is joined to the
one before it, and that chain of joins is the answer. This is the default and it is what
every earlier version of the program did; the result is unchanged.</p>

<h3>Serpentine &mdash; back and forth in rows</h3>
<p>You covered an area rather than a strip: pan a row left to right, step down, pan back
right to left, step down again, and so on, like ploughing a field. Choose this and two
extra things happen. First, the program looks for fields that overlap even though they were
<b>not recorded one after the other</b> &mdash; typically a field in one row and the field
above it in the previous row. Second, it then adjusts <b>all</b> the field positions
together so that every one of those overlaps is satisfied as well as possible, instead of
trusting a single chain of joins from beginning to end.</p>
<p><b>Why that is geometrically better, in one sentence:</b> the overlap between rows ties
the end of the scan back to the beginning, so the program can correct the small errors that
would otherwise accumulate along one long chain, instead of carrying them forward.</p>
<p>It also tolerates the moment at the end of a row where the pan <b>reverses direction</b>
and steps down. That single step moves the field of view further than any step inside a
row, and in single-sweep mode it can look like an implausible jump and break the scan into
one panorama per row.</p>

<h3>How to record a serpentine scan</h3>
<ul>
  <li><b>Pan each row slowly</b>, as always, keeping about half a field of overlap between
      consecutive fields.</li>
  <li><b>At the end of a row, step down by roughly half a field</b> &mdash; so the new row
      clearly overlaps the row above it. That overlap is the whole point: without it there
      is nothing for the program to tie the rows together with.</li>
  <li><b>Pause for about a second</b> at each turn, before and after stepping down.</li>
  <li><b>Do not change focus, white balance, exposure, zoom or objective</b> at any point
      &mdash; not even between rows. Set and lock them once, at the start.</li>
  <li><b>Keep the rows straight and roughly the same length</b>, and do not skip a row.</li>
  <li><b>Expect it to take longer to process.</b> Looking for the extra overlaps and then
      solving for all the positions at once is real extra work &mdash; on a large scan this
      can add anything from a few seconds to a couple of minutes.</li>
</ul>

<h3>The advanced switch, and which one wins</h3>
<p>Under <i>Advanced settings</i> there is a checkbox called <b>Refine all frame positions
together (bundle adjustment)</b>. That is the low-level switch for the second half of what
serpentine mode does, and it is there for comparison: tick it with <b>Scan type</b> left on
single sweep and the program will look for non-adjacent overlaps and refine the positions
of a straight sweep too. Serpentine already implies it, so ticking it as well changes
nothing. On the command line there is also <code>--no-bundle-adjust</code>, which wins over
both: with <code>--scan-mode serpentine --no-bundle-adjust</code> you keep the wider
tolerance at the row turns but the positions are <b>not</b> refined globally. Giving both
<code>--bundle-adjust</code> and <code>--no-bundle-adjust</code> is rejected as a
contradiction rather than silently resolved.</p>

<h3>What it will and will not do for you</h3>
<p>On a single straight sweep there is usually <b>little to gain</b>. Fields two apart do
still overlap when you keep the recommended half-field overlap, but they lie along the same
line as the chain, so they cannot tell the program that the chain has drifted. The gain is
on scans that fold back on themselves &mdash; which is what a serpentine path does.</p>
<p>The refinement makes the field <i>positions</i> consistent with each other. It does not
sharpen a blurred field, rescue a focus change half way through, or invent overlap you did
not record.</p>
<p>Finally, a caution that is built into the program rather than left to you: a smaller
alignment error is <b>not</b> by itself proof of a more accurate result. A refinement can
reduce the measured error and still be geometrically wrong, so the program refuses any
solution that would scale, rotate or move a field further than the ordinary field-to-field
checks would ever accept, and it prefers the simplest fit that works. If the refinement
fails, runs out of time, would make the fit worse, or produces something implausible, the
program <b>keeps the original chained solution, explains why in the log, and still writes
your panorama</b>. A failed optimisation can never cost you a scan.</p>
"""),

    ("s3", "4. The two kinds of input: video, or a folder of photographs", """
<p>The program accepts either one video file, or a folder of overlapping still
photographs. You choose in the window with <i>Choose video&hellip;</i> or
<i>Choose image folder&hellip;</i>, or simply drag the file or the folder onto the window.
The <b>Input mode</b> setting is normally left on <code>auto</code>, which decides from
what you dropped; set it to <code>video</code> or <code>images</code> if you want to be
told plainly that your choice does not fit.</p>

<h3>When photographs beat video</h3>
<p>Still photographs are often the better choice in pathology:</p>
<ul>
  <li><b>Resolution.</b> A phone photograph uses the whole sensor (8, 12, 50&nbsp;megapixels);
      a 1080p video frame is 2&nbsp;megapixels. More real detail means more landmarks to
      match, and a sharper panorama.</li>
  <li><b>No video compression.</b> Video codecs smear fine texture between frames, which
      is exactly the texture the alignment relies on.</li>
  <li><b>No ffmpeg needed.</b> Photograph mode uses only OpenCV, so it works on a computer
      where ffmpeg and ffprobe are not installed.</li>
</ul>
<p>Video wins on convenience and on speed of acquisition, and it is more forgiving when
you cannot easily lock the camera settings for stills. If your phone gives you manual
control for photographs but not for video, use photographs.</p>

<h3>How to shoot a photographic sequence</h3>
<p>Lock focus, exposure and white balance as in section&nbsp;2, then move the stage one
field at a time and take one picture per field with about 40&ndash;70&nbsp;% overlap. Put
every picture of one slide, and nothing else, in a new folder. Do not change zoom,
objective or focus half way through the folder. All the pictures you select must have the
same pixel dimensions.</p>

<h3>Formats</h3>
<p>Photographs may be PNG, JPEG, TIFF, BMP or WebP. Video may be any format your ffmpeg
reads (MP4 and MOV in practice). The panorama itself is written as TIFF, PNG or JPEG,
chosen by the file extension you type in <b>Where the results go</b>: use
<code>.tif</code> for anything you will look at seriously (compressed, lossless, opens in
QuPath and ImageJ), <code>.png</code> for quick previews, <code>.jpg</code> only for
e-mail.</p>

<h3>Order and stride</h3>
<p>The order of the photographs <i>is</i> the scan path, so it matters.
<b>Image ordering</b> <code>name</code> sorts by file name the way a human would:
<code>IMG_2.jpg</code> comes before <code>IMG_10.jpg</code>, not after it. Choose
<code>mtime</code> for a burst whose file names are unhelpful but whose modification times
are in shooting order. <b>Image stride</b> takes every Nth picture: leave it at
<code>1</code> normally; set it to <code>2</code> if you photographed with a very generous
overlap and want a faster run.</p>

<p>Two settings do not apply to photographs: <b>Sampling rate</b> (that is frames per
second of video &mdash; it is ignored, with a warning in the log), and
<code>--normalize temporal</code>, which is an ffmpeg video filter. For photographs use
exposure handling <code>clahe</code> or <code>none</code>.</p>
"""),

    ("s4", "5. Your first run, step by step", """
<p>Do a fast, small preview first, always. It takes seconds and tells you whether the scan
can be joined at all; a full-resolution run on a hopeless recording only wastes minutes.</p>

<ol>
  <li><b>Start the program.</b> The window opens with four numbered panels down the left
      side and the results on the right.</li>
  <li><b>Load the scan.</b> Drag your video file, or the folder of photographs, anywhere
      onto the window. (Or panel&nbsp;1, <i>Choose video&hellip;</i> /
      <i>Choose image folder&hellip;</i>; <code>Ctrl+O</code> chooses a video.) Panel&nbsp;1
      then reports what was found: for a video its size, frame rate and duration and how
      many frames will be sampled; for a folder the number of pictures, their dimensions
      and whether they are all the same size.</li>
  <li><b>Check where the results will go</b> (panel&nbsp;2). The panorama path is filled in
      for you next to your input; edit it if you like. The <b>Diagnostics folder</b> follows
      the panorama name unless you override it &mdash; that folder is where the report and
      every measurement of the run are written.</li>
  <li><b>Leave the settings alone for the first run</b> (panel&nbsp;3). The defaults
      &mdash; two frames per second, ORB features, <code>clahe</code> exposure handling,
      feather blending &mdash; are the tested starting point.</li>
  <li><b>Press <i>Quick preview</i></b> (<code>Ctrl+P</code>). This forces a small
      version of the run: the long edge of every frame is reduced to 960 pixels and the
      result is a PNG in its own <code>&hellip;_preview_diagnostics</code> folder, so it
      cannot overwrite a real result. Watch the <b>Run log</b> and the progress bar. Press
      <code>Esc</code> to cancel at any time.</li>
  <li><b>Read the verdict</b> in <b>Results summary</b> on the right, and look at the
      <b>Panorama</b> tab: drag to pan, use the wheel to zoom, <i>Fit to window</i> and
      <i>1:1</i> to reset. Is the tissue continuous? Is it one piece? Are there obvious
      seams or a bend?</li>
  <li><b>If the preview looks right, press <i>Full resolution</i></b>
      (<code>Ctrl+R</code>). This is your own settings at the real frame size, writing the
      panorama path from panel&nbsp;2. It takes minutes rather than seconds and needs
      considerably more memory.</li>
  <li><b>Open the report.</b> In the <b>Panorama</b> tab press <i>Open report.html</i>;
      it opens in your normal web browser. <i>Open folder</i> opens the output directory in
      your file manager.</li>
</ol>

<p>The read-only <b>Equivalent command line</b> box always shows the exact command that
the window is about to run. You can copy it into a terminal and get an identical run, which
is also how you would put a scan into a script or run it on another machine over
<code>ssh</code>. Your settings, the last output path and the last input folder are
remembered for next time.</p>
"""),

    ("s5", "6. Reading the results", """
<h3>The verdict</h3>
<table border="1" cellspacing="0" cellpadding="7" width="100%">
<tr><th width="27%">Verdict</th><th>What it means</th></tr>
<tr><td><b>SUCCESSFUL</b></td>
    <td>The whole scan was joined into <b>one</b> panorama, at least 90&nbsp;% of the
        frames that passed the quality check were actually used, and the alignment was
        strong (median 100 or more matching points per pair). This is the outcome to aim
        for.</td></tr>
<tr><td><b>PARTIALLY SUCCESSFUL</b></td>
    <td>A panorama was written, but with a reservation that is spelled out in the
        summary: the scan is split into several pieces, or many frames were dropped, or
        the alignment was weak. The output is usable; read the note before you trust
        it.</td></tr>
<tr><td><b>FAILED</b><br>(exit code 3)</td>
    <td>No panorama could be produced &mdash; fewer than two frames could be chained
        together. The program still writes the complete diagnostics folder plus a
        <code>failure.txt</code> explaining what stopped it, and the window shows a red
        banner with the diagnostics folder one click away. Nothing is deleted on failure:
        those files are exactly what you need in order to fix the recording. (Exit code 2
        is different &mdash; it means the input or a setting was wrong, for example a
        video path that does not exist. Exit code 5 means ffmpeg was not found.)</td></tr>
</table>

<h3>Inliers, and the reprojection error</h3>
<p>To join two fields the program finds distinctive small patches in both, pairs them up,
and then looks for the single shift-rotation-scale that the largest possible number of
those pairs agree on. The pairs that agree are the <b>inliers</b>. So:</p>
<ul>
  <li><b>Inliers</b> = how many landmarks genuinely agreed on the alignment of a pair of
      fields. More is better. A median of 100 or more is comfortable; 25 is the minimum
      accepted by default; if your medians sit near that floor the joins are fragile and
      the panorama may bend.</li>
  <li><b>Reprojection error</b> = how far, in pixels, those agreeing landmarks still miss
      each other after alignment. Smaller is better; a fraction of a pixel up to about two
      pixels is normal. A large value with plenty of inliers usually means the frame itself
      was warped &mdash; the classic cause being video stabilisation left switched on
      (section&nbsp;2).</li>
</ul>

<h3>Segments, and why a scan splits into pieces</h3>
<p>A <b>segment</b> is a run of fields that the program managed to chain together without a
break. Ideally there is exactly one. A break happens when two consecutive sampled fields
no longer share enough tissue to be recognised as neighbours &mdash; nearly always a fast
pan, often combined with a re-focus. The program then does two things before giving up:
it tries to <b>bridge</b> over a single bad frame, and if that fails it tries a wider
<b>rejoin</b>, comparing the last few frames of one piece against the first few frames of
the next with the stronger SIFT detector and a much larger allowance for movement. Every
attempt, with its numbers and the reason it was accepted or rejected, is recorded in
<code>rejoin_attempts.json</code> and pictured in <code>matches/rejoin_*.png</code>.</p>
<p>If the rejoin also fails, nothing is thrown away: every remaining piece with at least
three frames is written as its own panorama &mdash; <code>pano.tif</code>,
<code>pano_seg02.tif</code>, <code>pano_seg03.tif</code>&hellip; in order of decreasing
size &mdash; and the verdict becomes PARTIALLY SUCCESSFUL with the number of pieces. The
selector at the top of the <b>Panorama</b> tab switches between them and shows each one's
frame count, time range, pixel size and coverage, so you can tell which piece is which
part of the slide and join them by hand later if you wish.</p>
<p>To avoid the split next time: pan more slowly (a field per one to two seconds), keep
focus locked, and raise the <b>Sampling rate</b> from 2 to 3 or 5 frames per second so the
frames either side of a fast pan are closer together.</p>

<h3>What is in report.html</h3>
<p>One self-contained page, readable in any browser and easy to send to a colleague. It
contains: every setting the run used and the program version; timings for each stage; how
many frames were sampled, accepted and rejected and the reason for each rejection;
the alignment statistics (inliers, reprojection error, which detector was used, where SIFT
or a homography had to be fallen back on); the list of segments found, written and skipped
with their frame ranges; every warning and stated limitation; and embedded pictures &mdash;
the panorama preview, the reconstructed path of the scan across the slide, the
before/after exposure comparison, and the per-pair match pictures in which green lines are
agreeing landmarks of an accepted pair and red lines a rejected one.</p>
<p>The other files in the same folder are described in the README; the two you will use
most are <code>frame_metrics.csv</code> (one row per sampled frame) and
<code>pipeline.log</code> (the full log of the run).</p>
"""),

    ("s6", "7. Calibrating the blur threshold on your own microscope", """
<p>Every frame is given a <b>focus score</b>: the variance of the Laplacian, which is
simply a number that is large when the picture has crisp edges and small when it is
smooth and blurred. Frames scoring below the <b>Blur threshold</b> are rejected before any
alignment is attempted, because a blurred field has no reliable landmarks.</p>

<p>The important point: <b>eyepiece video scores much lower than ordinary photographs.</b>
On the test material, raw frames scored between 3 and 36, and between 21 and 103 after
<code>clahe</code> contrast enhancement. There is no universal correct value &mdash; it
depends on your objective, your phone and your camera settings &mdash; so calibrate it
once for your own setup and then leave it.</p>

<h3>The quick way, in the window</h3>
<ol>
  <li>Run a quick preview of a typical scan.</li>
  <li>Open the <b>Frame quality</b> tab. Every sampled frame appears as a dot: focus score
      up the side, time along the bottom, <b>green</b> for accepted and <b>red</b> for
      rejected. The horizontal blue line is the current threshold.</li>
  <li>Look at the shape of the cloud. Well-focused fields form a band; the genuinely
      blurred moments (a re-focus, a fast pan) fall well below it.</li>
  <li><b>Drag the blue line</b> (or click it and use the arrow keys) to just under the
      bottom of that band. The label tells you live how many frames would be kept and how
      many rejected, and the settings panel and the <b>Equivalent command line</b> follow
      the line automatically.</li>
  <li>Re-run the preview. Aim to keep nearly all the sharp fields while still dropping the
      obvious blur. If the log says <i>only N frame(s) passed quality gating</i>, your
      threshold is too high.</li>
</ol>
<p>You can load an old run's diagnostics folder to look at its timeline again without
re-running anything.</p>

<h3>The exact way, with frame_metrics.csv</h3>
<p>The same numbers are in <code>frame_metrics.csv</code> in the diagnostics folder, one
row per sampled candidate frame, with the columns index, timestamp, <b>focus_score</b>,
duplicate score, mean intensity, accepted, and reason. Open it in LibreOffice Calc, sort by
<code>focus_score</code>, and look at where the values jump. Cross-check a few against the
pictures in <code>accepted_frames/</code>: find the lowest score you would still call
usable, and set the threshold a little below it &mdash; roughly 70&nbsp;% of the typical
in-focus value is a good first guess.</p>
<p>From a terminal, the fifth column of the file is the focus score:</p>
<pre>sort -t, -k3 -g diagnostics/frame_metrics.csv | head -20</pre>
<p>Two mistakes to avoid: setting the threshold from a photograph-mode run and then reusing
it for video (photographs score far higher), and setting it to 0. Zero accepts everything,
including frames so blurred that they will break the chain in a way that is much harder to
diagnose than a rejected frame.</p>
"""),

    ("s7", "8. When something goes wrong", """
<h3>"The scan came out in several pieces" / "N disconnected segments"</h3>
<p>The chain broke. See section&nbsp;5 for what that means. Look at
<code>matches/rejoin_*.png</code>: if the two halves show <i>different</i> tissue, no
program can join them and the recording has to be repeated more slowly. If they show the
same tissue but few matching lines, it is a texture or focus problem: use the SIFT
detector, raise the sampling rate, and if the tissue is pale (fat, mucin, pale dermis)
lower the minimum inliers to about 20. Raising <b>Sampling rate</b> from 2 to 3 or 5 is the
single most effective fix.</p>

<h3>Out of memory, or "the memory guard aborted the run"</h3>
<p>Before it allocates the big canvas, the program estimates how much working memory the
panorama will need (about 20 bytes per pixel of canvas) and refuses to start if that
exceeds the limit &mdash; 3&nbsp;GiB by default. This is deliberate: an honest message with
advice is far better than the operating system killing the run half way through. Any of
these fixes it: set <b>Downscale long edge to</b> to 1280 or 960; lower the sampling rate;
record the slide in two shorter scans; or, if you genuinely have the memory, raise
<code>--memory-limit-gb</code> on the command line.</p>

<h3>"ffmpeg/ffprobe was not found"</h3>
<p>Video input needs both programs; they come together in one package. On Manjaro or
Arch:</p>
<pre>sudo pacman -S --needed ffmpeg</pre>
<p>On Debian or Ubuntu, <code>sudo apt install ffmpeg</code>. Two notes. First, the
AppImage carries its own copy, so it never shows this message. Second, <b>photograph mode
does not need ffmpeg at all</b> &mdash; if you only have stills, this message never
applies to you. Any ffmpeg from version 5 to 8 and later works: the program asks your
ffmpeg which frame-rate option it understands rather than assuming.</p>

<h3>The window does not start: an xcb or xcb-util-cursor error</h3>
<p>A message such as <i>"could not load the Qt platform plugin xcb"</i> means a system
library that Qt needs to talk to X11 is missing. On Manjaro or Arch:</p>
<pre>sudo pacman -S --needed xcb-util-cursor libxkbcommon-x11</pre>
<p>On Debian or Ubuntu, <code>sudo apt install libxcb-cursor0 libxkbcommon-x11-0</code>.
If you are connecting over <code>ssh</code>, remember to use <code>ssh -X</code>. To prove
that the rest of the program is fine while you sort the display out, the command line works
without any graphics at all.</p>

<h3><i>Open report.html</i> or <i>Open folder</i> does nothing</h3>
<p>First: does the file exist? Those buttons check, and will tell you to run the pipeline
first rather than blaming your desktop. If the file does exist and nothing opens, the
program never pretends it succeeded &mdash; it shows the full path in a red banner with a
<i>Copy path</i> button, so you can paste it into your file manager or browser. The usual
cause on a minimal installation is a missing desktop opener:</p>
<pre>sudo pacman -S --needed xdg-utils</pre>
<p>The status line always names the mechanism that accepted the path, which is what to
quote if you report the problem.</p>

<h3>Exposure seams, visible rectangles, or a bent panorama</h3>
<p>Seams: set exposure handling to <code>clahe</code>, or <code>temporal</code> for video,
and blending to <code>multiband</code>. Better still, lock the exposure while recording,
which removes the cause. A gentle bend over a long scan is accumulated alignment error;
this version has no global correction pass, so record shorter strips and stitch them
separately. The log and the report say plainly when they detect
<i>cumulative scale drift</i> or a <i>suspicious trajectory jump</i>.</p>

<h3>Still stuck? Send a good bug report</h3>
<p>Open <b>Help &rarr; About MicroSlideStitcher</b>, press <i>Copy diagnostics</i>, and
paste that block into an e-mail together with <code>pipeline.log</code>,
<code>summary.json</code> and <code>frame_metrics.csv</code> from the diagnostics folder.
Those five things answer almost every question that would otherwise need a conversation.</p>
"""),

    ("s8", "9. Every setting there is", FLAG_TABLE_PLACEHOLDER),

    ("s9", "10. Credits and licence", f"""
<p>Created by <b>{html.escape(AUTHOR)}</b> &mdash; {html.escape(AFFILIATION)}.<br>
Contact: <a href="mailto:{EMAIL}">{html.escape(EMAIL)}</a>.</p>
<p>MicroSlideStitcher {html.escape(__version__)} is released under the <b>MIT Licence</b>,
&copy;&nbsp;2026 {html.escape(AUTHOR)}. In short: you may use, copy, change and pass on
the program, including commercially, provided the copyright notice and the licence text
travel with it; and it comes with no warranty of any kind. The full text is in the
<code>LICENSE</code> file and under <i>Show licence</i> in <b>Help &rarr; About
MicroSlideStitcher</b>.</p>
<p>It stands on free software: <b>Python</b>, <b>OpenCV</b> (feature detection, alignment
and blending), <b>NumPy</b>, <b>tifffile</b> (TIFF writing), <b>matplotlib</b> (the scan
path picture), <b>ffmpeg</b> (reading video; GPL&nbsp;3, bundled unchanged in the AppImage
with its own licence text) and <b>Qt</b> via <b>PySide6</b> (this window; LGPL&nbsp;3).
This manual is displayed with Qt's own small rich-text engine: no web browser engine is
bundled, which is why the program stays a download of a few hundred megabytes rather than
a gigabyte.</p>

{_DISCLAIMER_BOX}
"""),
]


# ------------------------------------------------------------------ runtime flag table
def _is_meta_action(action) -> bool:
    """--help / --version: they print something and exit, they are not settings."""
    return type(action).__name__ in {"_HelpAction", "_VersionAction"}


def _default_text(action) -> str:
    """How a flag's default should read to a non-programmer."""
    if _is_meta_action(action):
        return "&mdash;"
    if action.nargs == 0 or action.const is True:      # store_true
        return "off"
    if action.default is None:
        return "not set"
    if action.default == "":
        return "empty"
    return f"<code>{html.escape(str(action.default))}</code>"


def _accepts_text(action) -> str:
    if _is_meta_action(action):
        return "prints and exits"
    if action.choices:
        return " | ".join(f"<code>{html.escape(str(c))}</code>" for c in action.choices)
    if action.nargs == 0:
        return "a switch (on/off)"
    kind = getattr(action.type, "__name__", None)
    return {"float": "a number", "int": "a whole number", "Path": "a path",
            None: "text", "str": "text"}.get(kind, "text")


def _gui_location() -> dict[str, str]:
    """Which flags the settings panel exposes, so the table can say where to find them."""
    from . import cli_spec
    where = {}
    for spec in cli_spec.SPECS:
        where[spec.flag] = ("Settings &rarr; Advanced settings" if spec.advanced
                            else "Settings panel")
    return where


def flag_actions() -> list:
    """Every option of the real CLI parser, in the parser's own order."""
    return [a for a in build_parser()._actions if a.option_strings]   # noqa: SLF001


def flag_table_html() -> str:
    """Build section 9's table from ``cli.build_parser()`` at runtime.

    Nothing here is hard-coded, so a flag that is added, renamed or re-defaulted in
    the CLI appears in the manual on the next start.  ``tests/test_gui.py``
    asserts that every option string of the parser reaches this table.
    """
    where = _gui_location()
    rows = [
        "<p>This table is generated from the program's own command line definition every "
        "time the manual is opened, so it can never fall out of date. The left column is "
        "the name used on the command line and in the <b>Equivalent command line</b> box; "
        "the last column says where to find the same thing in the window.</p>",
        '<table border="1" cellspacing="0" cellpadding="6" width="100%">',
        "<tr><th>Setting</th><th>Accepts</th><th>Default</th><th>What it does</th>"
        "<th>In the window</th></tr>",
    ]
    for action in flag_actions():
        flags = ", ".join(f"<code>{html.escape(f)}</code>" for f in action.option_strings)
        raw = " ".join((action.help or "").split())
        try:
            helptext = raw % {"default": action.default}
        except (KeyError, ValueError, TypeError):       # pragma: no cover - defensive
            helptext = raw
        # The CLI help already states the default in a machine-readable way; the
        # table has its own column for it, so drop the duplicate and tidy up the
        # punctuation that removing it leaves behind ("of video . Raise").
        helptext = helptext.replace(f"(default: {action.default})", "")
        helptext = helptext.replace(f", default: {action.default}", "")
        helptext = " ".join(helptext.split())
        # Close only the gap the removal itself opened -- a space between a word or
        # a closing bracket and its punctuation.  ", .png" must stay untouched.
        helptext = re.sub(r"(?<=[\w\)])\s+([.,;:])", r"\1", helptext).strip(" ;,")
        seat = where.get(action.option_strings[0], "command line only")
        rows.append(
            f"<tr><td>{flags}</td><td>{_accepts_text(action)}</td>"
            f"<td>{_default_text(action)}</td><td>{html.escape(helptext).strip() or '&mdash;'}</td>"
            f"<td>{seat}</td></tr>")
    rows.append("</table>")
    rows.append(
        "<p>Exit codes, for scripts: <b>0</b> a panorama was written, <b>2</b> the input or "
        "a setting was wrong, <b>3</b> the pipeline could not produce a panorama (the "
        "diagnostics are still there), <b>4</b> the graphical extra is not installed, "
        "<b>5</b> ffmpeg/ffprobe was not found.</p>")
    return "\n".join(rows)


# ----------------------------------------------------------------------- document build
def table_of_contents_html() -> str:
    items = "\n".join(
        f'<li><a href="#{anchor}">{html.escape(title)}</a></li>'
        for anchor, title, _ in MANUAL_SECTIONS)
    return ('<h2 style="margin-top:6px">Contents</h2>\n'
            f'<ul>\n{items}\n</ul>\n<hr>')


def manual_html(standalone: bool = False) -> str:
    """The whole manual as one HTML document.

    ``standalone=True`` adds the ``<style>`` block, so the saved file looks the
    same in a normal web browser (inside the program the same rules are applied
    with ``QTextDocument.setDefaultStyleSheet``).
    """
    parts = [f'<h1>MicroSlideStitcher {html.escape(__version__)} &mdash; user manual</h1>',
             '<p class="lead">A guide for pathologists: how to record a scan that will '
             'stitch, how to run it, and how to read what comes out.</p>',
             table_of_contents_html()]
    for anchor, title, body in MANUAL_SECTIONS:
        body = flag_table_html() if body == FLAG_TABLE_PLACEHOLDER else body
        parts.append(f'<h2><a name="{anchor}"></a>{html.escape(title)}</h2>')
        parts.append(body)
        parts.append('<p><a href="#top">Back to contents</a></p>')
    body_html = "\n".join(parts)
    style = f"<style>{MANUAL_CSS}</style>" if standalone else ""
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>MicroSlideStitcher {html.escape(__version__)} user manual</title>"
        f"{style}</head>\n<body><a name=\"top\"></a>\n{body_html}\n</body></html>\n")


def anchors() -> list[str]:
    """Every internal anchor target the table of contents links to."""
    return ["top"] + [anchor for anchor, _, _ in MANUAL_SECTIONS]


# ------------------------------------------------------------------------------ widgets
class ManualBrowser(QTextBrowser):
    """A QTextBrowser that serves the manual from memory.

    Answering :data:`MANUAL_URL` in ``loadResource`` is what lets the ordinary
    QTextBrowser history work: clicking a ``#anchor`` link becomes a real
    ``setSource`` navigation, so Back and Forward behave as expected without a
    file on disk and without a web engine.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._html = manual_html()
        self.document().setDefaultStyleSheet(MANUAL_CSS)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(True)
        self.setReadOnly(True)
        self.document().setDocumentMargin(14)
        self.anchorClicked.connect(self._on_anchor_clicked)
        self._margin = -1
        self.sourceChanged.connect(lambda _url: self._reapply_margins())

    def _reapply_margins(self, force: bool = True) -> None:
        """Pad the root frame so the text column stays a readable width."""
        # Derived from the widget width, never from the viewport width, so the
        # padding cannot feed back into itself and oscillate.
        pad = min(max(0, (self.width() - 980) // 2), 200)
        if pad == self._margin and not force:
            return
        self._margin = pad
        root = self.document().rootFrame()
        fmt = root.frameFormat()
        fmt.setLeftMargin(pad)
        fmt.setRightMargin(pad)
        root.setFrameFormat(fmt)

    def resizeEvent(self, event) -> None:                        # noqa: N802 - Qt name
        """Keep the text column readable on a wide screen.

        Unconstrained, a 1280-pixel-wide window gives lines of well over 200
        characters, which is tiring to read.  Padding the document's root frame
        instead of fixing a width means nothing is clipped when the window is
        narrow: below about 980 pixels the padding is simply zero.

        The padding is reset whenever the document is reloaded (Back/Forward), so
        ``sourceChanged`` re-applies it via ``_reapply_margins``.
        """
        super().resizeEvent(event)
        self._reapply_margins(force=False)

    def loadResource(self, resource_type: int, name: QUrl):      # noqa: N802 - Qt name
        if name.toString().startswith("mss-manual:"):
            return self._html
        return super().loadResource(resource_type, name)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        """Open mailto: links in the mail client; everything else stays internal."""
        if url.scheme() in {"mailto", "http", "https"}:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)


class ManualWindow(QDialog):
    """Resizable, non-modal manual window with contents, history, find and export."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"MicroSlideStitcher {__version__} — user manual")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(720, 460)
        self.resize(1000, 760)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Two rows, deliberately: at the 720-pixel minimum width a single row
        # clipped the "Save manual as HTML..." button and squeezed the find field.
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_back = QToolButton(self)
        self.btn_back.setText("◀ Back")
        self.btn_back.setToolTip("Back to the previous place in the manual")
        self.btn_forward = QToolButton(self)
        self.btn_forward.setText("Forward ▶")
        self.btn_forward.setToolTip("Forward again")
        self.btn_contents = QToolButton(self)
        self.btn_contents.setText("Contents")
        self.btn_contents.setToolTip("Jump back to the table of contents")
        for button in (self.btn_back, self.btn_forward, self.btn_contents):
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            bar.addWidget(button)
        bar.addStretch(1)
        self.btn_save = QPushButton("Save manual as HTML…", self)
        self.btn_save.setToolTip(
            "Write this manual to a single .html file you can e-mail to a colleague")
        bar.addWidget(self.btn_save)
        outer.addLayout(bar)

        find_row = QHBoxLayout()
        find_row.setSpacing(6)
        find_label = QLabel("Find:", self)
        find_row.addWidget(find_label)
        self.ed_find = QLineEdit(self)
        self.ed_find.setClearButtonEnabled(True)
        self.ed_find.setPlaceholderText("type a word to highlight it; Enter for the next match")
        self.ed_find.setMinimumWidth(150)
        find_row.addWidget(self.ed_find, 1)
        self.btn_prev = QToolButton(self)
        self.btn_prev.setText("Previous")
        self.btn_next = QToolButton(self)
        self.btn_next.setText("Next")
        for button in (self.btn_prev, self.btn_next):
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            find_row.addWidget(button)
        self.lbl_matches = QLabel("", self)
        self.lbl_matches.setObjectName("matches")
        self.lbl_matches.setMinimumWidth(96)
        find_row.addWidget(self.lbl_matches)
        outer.addLayout(find_row)

        self.view = ManualBrowser(self)
        outer.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        self.lbl_hint = QLabel(
            "F1 opens this manual · click a heading in Contents to jump · "
            "Ctrl+F goes to the Find box", self)
        self.lbl_hint.setObjectName("hint")
        self.lbl_hint.setWordWrap(True)
        bottom.addWidget(self.lbl_hint, 1)
        self.btn_close = QPushButton("Close", self)
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_close)
        outer.addLayout(bottom)

        self.btn_back.clicked.connect(self.view.backward)
        self.btn_forward.clicked.connect(self.view.forward)
        self.btn_contents.clicked.connect(lambda: self.go_to_anchor("top"))
        self.btn_save.clicked.connect(self.save_as_html)
        self.ed_find.textChanged.connect(self.update_find)
        self.ed_find.returnPressed.connect(self.find_next)
        self.btn_next.clicked.connect(self.find_next)
        self.btn_prev.clicked.connect(self.find_previous)
        self.view.historyChanged.connect(self._sync_history_buttons)
        self.view.sourceChanged.connect(lambda _url: self.update_find())

        self.setStyleSheet("""
            QLabel#hint { color: #3d4650; }
            QLabel#matches { color: #16191d; }
            QDialog { background: #f4f6f8; }
            QTextBrowser { background: #ffffff; border: 1px solid #b9c2cc; }
        """)

        self.view.setSource(QUrl(MANUAL_URL))
        self._sync_history_buttons()

    # ------------------------------------------------------------------ navigation
    def go_to_anchor(self, anchor: str) -> None:
        """Navigate to an internal anchor through the history, like a link click."""
        url = QUrl(MANUAL_URL)
        url.setFragment(anchor)
        self.view.setSource(url)
        # setSource scrolls before the root-frame padding is re-applied, which
        # shifts everything; ask again afterwards so the heading really lands at
        # the top of the view.
        self.view.scrollToAnchor(anchor)

    def _sync_history_buttons(self) -> None:
        self.btn_back.setEnabled(self.view.isBackwardAvailable())
        self.btn_forward.setEnabled(self.view.isForwardAvailable())

    # ------------------------------------------------------------------------ find
    def match_count(self) -> int:
        return len(self._find_ranges(self.ed_find.text()))

    def _find_ranges(self, needle: str) -> list[tuple[int, int]]:
        if len(needle) < 2:
            return []
        doc = self.view.document()
        ranges: list[tuple[int, int]] = []
        cursor = QTextCursor(doc)
        while True:
            cursor = doc.find(needle, cursor, QTextDocument.FindFlags())
            if cursor.isNull():
                break
            ranges.append((cursor.selectionStart(), cursor.selectionEnd()))
            if len(ranges) > 5000:                     # pragma: no cover - safety valve
                break
        return ranges

    def update_find(self) -> None:
        """Highlight every match of the current text and jump to the first one."""
        needle = self.ed_find.text()
        ranges = self._find_ranges(needle)
        selections = []
        fmt = QTextCharFormat()
        fmt.setBackground(FIND_HIGHLIGHT)
        fmt.setForeground(QColor("#16191d"))
        doc = self.view.document()
        for start, end in ranges:
            selection = QTextBrowser.ExtraSelection()
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.view.setExtraSelections(selections)
        if not needle:
            self.lbl_matches.setText("")
        elif not ranges:
            self.lbl_matches.setText("no match" if len(needle) > 1 else "keep typing…")
        else:
            self.lbl_matches.setText(f"{len(ranges)} match{'es' if len(ranges) > 1 else ''}")
            cursor = QTextCursor(doc)
            cursor.setPosition(ranges[0][0])
            self.view.setTextCursor(cursor)
            self.view.ensureCursorVisible()
            self.view.find(needle)
            self._centre_on_cursor()

    def _centre_on_cursor(self) -> None:
        """Put the current match about a third down the view, not on the bottom edge."""
        viewport_height = self.view.viewport().height()
        bar = self.view.verticalScrollBar()
        target = bar.value() + self.view.cursorRect().top() - viewport_height // 3
        bar.setValue(max(0, min(bar.maximum(), target)))

    def _step_find(self, backwards: bool) -> bool:
        needle = self.ed_find.text()
        if len(needle) < 2:
            return False
        flags = QTextDocument.FindBackward if backwards else QTextDocument.FindFlags()
        if self.view.find(needle, flags):
            self._centre_on_cursor()
            return True
        # Wrap around.
        cursor = QTextCursor(self.view.document())
        if backwards:
            cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)
        found = bool(self.view.find(needle, flags))
        if found:
            self._centre_on_cursor()
        return found

    def find_next(self) -> bool:
        return self._step_find(False)

    def find_previous(self) -> bool:
        return self._step_find(True)

    def focus_find(self) -> None:
        self.ed_find.setFocus()
        self.ed_find.selectAll()

    # ------------------------------------------------------------------------ export
    def save_html(self, path: str | Path) -> Path:
        """Write the manual as one standalone HTML file (used by the button and tests)."""
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".html")
        target.write_text(manual_html(standalone=True), encoding="utf-8")
        return target

    def save_as_html(self) -> None:
        suggested = str(Path.home() / f"MicroSlideStitcher-{__version__}-manual.html")
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save the manual as an HTML file", suggested, "Web page (*.html *.htm)")
        if not chosen:
            return
        try:
            written = self.save_html(chosen)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save the manual",
                                f"Writing {chosen} failed:\n{exc}")
            return
        self.lbl_hint.setText(f"Manual saved to {written} — it opens in any web browser.")

    # ----------------------------------------------------------------------- events
    def keyPressEvent(self, event) -> None:                        # noqa: N802 - Qt name
        if event.matches(QKeySequence.Find):
            self.focus_find()
            return
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
