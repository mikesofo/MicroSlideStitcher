# MicroSlideStitcher

Stitch a smartphone video **or overlapping still-image sequence** recorded through a microscope
eyepiece (e.g. Nikon Eclipse 80i) into **one high-resolution panorama** of a histology slide
(H&E and friends).

CLI and optional desktop GUI, all processing local — nothing is uploaded anywhere.

> **Not a medical device.** MicroSlideStitcher is a research / personal workflow aid. It is
> **not** a validated diagnostic whole-slide-imaging device and must **not** be used for primary
> diagnosis or measurement. Geometry, colour and focus are not calibrated; always go back to
> the glass slide for diagnostic work.

---

## 1. Prerequisites (Manjaro / Arch)

```bash
# system packages
sudo pacman -Syu                          # keep Manjaro consistent (full upgrade)
sudo pacman -S --needed python ffmpeg     # Python 3.10+ and ffmpeg/ffprobe

# optional, only if you want Manjaro's own OpenCV instead of a pip wheel
sudo pacman -S --needed opencv python-numpy
```

`ffprobe` ships with the `ffmpeg` package — **video input** needs both. Image-sequence input
uses OpenCV only and works without either program.

Check:

```bash
python --version   # >= 3.10
ffmpeg -version
ffprobe -version
```

**ffmpeg 5 through 8+ are all supported.** ffmpeg 8 (what Arch/Manjaro currently ship)
**removed** the old `-vsync` option in favour of `-fps_mode`. The extractor therefore asks
your ffmpeg which of the two it understands (`ffmpeg -h full`, probed once per run) and
falls back at runtime if the answer turns out to be wrong. The chosen flag is printed in
the `Extracting frames ...` log line. Versions before 0.2.1 hard-coded `-vsync 0` and
failed on ffmpeg 8 with `Unrecognized option 'vsync'`.

## 2. Install

```bash
git clone <your-copy> ~/MicroSlideStitcher      # or just unpack it there
cd ~/MicroSlideStitcher

python -m venv .venv                            # dedicated venv, nothing global
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .                      # installs the `micro-slide-stitcher` entry point
```

Or let the helper script walk you through it (it prints what it will do and never runs
`pacman` without asking):

```bash
bash scripts/setup_manjaro.sh
```

### OpenCV variant — the one difference between this repo and your machine

This project was **developed and tested on Ubuntu inside a headless sandbox**, so
`requirements.txt` pins:

```
opencv-contrib-python-headless==5.0.0.93
```

`headless` has no GUI/X11 dependencies, which is correct here because the tool never opens a
window. On a Manjaro **desktop** you may prefer the full build:

```bash
.venv/bin/pip uninstall -y opencv-contrib-python-headless
.venv/bin/pip install opencv-contrib-python
```

Both expose the identical `cv2` API that is used (`ORB`, `SIFT`, `estimateAffinePartial2D`,
`detail::MultiBandBlender`) — pick either. If you would rather use Manjaro's system OpenCV
(`sudo pacman -S opencv python-numpy`), create the venv with
`python -m venv --system-site-packages .venv` and drop the OpenCV line from `requirements.txt`.

Everything else is pure pip: `numpy`, `tifffile` (TIFF writing), `matplotlib`
(**only** used to draw `diagnostics/camera_path.png`), `pytest` (tests only).

## 3. Quick start

```bash
cd ~/MicroSlideStitcher

# 1) always do a fast downscaled preview first - it takes seconds and tells you
#    whether the scan is stitchable at all
.venv/bin/python -m micro_slide_stitcher \
    --input ~/videos/scan_10x.mp4 \
    --output ~/pano_preview.png \
    --max-dimension 960 \
    --diagnostics ~/msc_diag_preview

# 2) then the full-resolution run
.venv/bin/python -m micro_slide_stitcher \
    --input ~/videos/scan_10x.mp4 \
    --output ~/pano.tif \
    --sampling-fps 2 \
    --detector orb \
    --normalize clahe \
    --blur-threshold 10 \
    --diagnostics ~/msc_diag
```

Open `~/msc_diag/report.html` in a browser afterwards — it summarises settings, timings,
accepted/rejected frames, registration quality, warnings and limitations, and embeds the
panorama preview, camera path and match visualisations.

### Image sequence input

If your phone’s useful microscope lens is available reliably for **photos** but not for a
manual-control video app, take overlapping stills instead. In the stock Camera app or a GCam
port, lock focus/exposure if possible, move the slide one field at a time with roughly
40–70 % overlap, and keep all photos in one new folder. Do not change zoom, objective or focus
halfway through a folder.

This is often better than video for pathology: phone stills can retain the sensor’s full
resolution (for example 8 MP rather than 1080p) and avoid inter-frame video compression, both
of which give registration more real tissue detail to match. Use the folder directly:

```bash
.venv/bin/python -m micro_slide_stitcher \
    --input ~/Pictures/slide_10x_burst \
    --output ~/panoramas/slide_10x.tif \
    --normalize clahe \
    --blur-threshold 10

# or a quoted glob, including mixed-case file extensions:
.venv/bin/python -m micro_slide_stitcher \
    --input '~/Pictures/slide_10x_burst/IMG_*.JPG' \
    --output ~/panoramas/slide_10x.tif \
    --input-mode images --image-order name --image-stride 1
```

Supported still formats are PNG, JPEG, TIFF, BMP and WebP. Name order is a natural sort:
`IMG_2.jpg` comes before `IMG_10.jpg`; use `--image-order mtime` for a phone burst whose
modification time is the intended order. `--image-stride 2` takes every second image.
Image sequences do not have a real video duration or fps: reports label their source coordinate
as an **image index**. `--sampling-fps` is ignored with one warning. `--normalize temporal`
is video-only; use `none` or the colour-safe `clahe` for stills.

### Advanced examples

```bash
# difficult footage: low-texture tissue, slight blur, exposure seams
.venv/bin/python -m micro_slide_stitcher --input scan.mov --output pano.tif \
    --detector sift --sampling-fps 3 --blur-threshold 4 --dedup-threshold 0.999 \
    --blend multiband --keep-intermediates --diagnostics diag_hard

# very fast panning: sample more often so consecutive frames still overlap
.venv/bin/python -m micro_slide_stitcher --input fast.mp4 --output pano.tif --sampling-fps 5

# a scan with one fast pan in the middle: search wider when trying to rejoin the pieces,
# and write every remaining piece of >= 5 frames as its own panorama
.venv/bin/python -m micro_slide_stitcher --input scan.mp4 --output pano.tif \
    --rejoin-window 4 --rejoin-max-translation-frac 2.5 --min-segment-frames 5

# pre-0.2 behaviour: no wide-baseline recovery, longest piece only
.venv/bin/python -m micro_slide_stitcher --input scan.mp4 --output pano.tif \
    --no-rejoin --min-segment-frames 100000

# strict geometry (reject anything that is not an almost pure translation)
.venv/bin/python -m micro_slide_stitcher --input scan.mp4 --output pano.tif \
    --max-rotation-deg 2 --max-translation-frac 0.7 --min-inliers 60 \
    --no-homography-fallback

# huge scan on a small machine
.venv/bin/python -m micro_slide_stitcher --input long_scan.mp4 --output pano.tif \
    --sampling-fps 1 --max-dimension 1280 --memory-limit-gb 6
```

`--help` prints the same worked examples.

## 3a. Blurred frames and the overlap check (0.7.1)

**Which frames are used.** By default (`--frame-selection motion`) every frame of the video
is checked. Frames smeared by a moving slide or out of focus are skipped, and the sharpest
frame of each stretch of movement is used. Sharpness is judged against the frames around it,
so bare glass is not mistaken for blur. `frame_selection.csv` in the diagnostics folder lists
every frame and why it was kept or skipped. If the slide moved so fast that no sharp frame
overlaps the previous one, the log names the time in the video where that happened.
`--frame-selection fixed --sampling-fps 2` gives the old fixed-rate behaviour.

**The overlap check.** Two frames are only joined if they share at least `--min-overlap`
(10 %) of the frame horizontally **and** vertically, the matched points are spread over the
shared area, and the two images look alike there (`--min-overlap-agreement`, 0.60). This
applies to every join, including joins across a turn and joins between pieces, and is never
relaxed.

## 3b. Reconnecting pieces by content (0.8.0)

If the scan still breaks into pieces - the slide moved too fast at a turn, or every frame there
was blurred - the program looks for frames that show the same tissue in different pieces,
whenever they were recorded: the row above, or the same strip on the way back. Two pieces are
joined only if at least two such matches, from different frames, agree on where they fit, and
every match passes the full overlap check. All frame positions are then refined together so the
overlapping passes line up. Details are in `reconnect.json`; `--no-reconnect` turns it off.

Practical consequence for recording: going back over tissue you have already covered is
helpful, not harmful. It gives the program the overlaps it needs to join pieces.

## 3c. JPEG for sharing (0.8.1)

Next to the full-quality TIFF (or PNG) the program also saves a JPEG with the same name, about a
tenth of the size, for e-mail, presentations or a phone. `--jpeg-quality` sets its quality
(default 90); `--jpeg-copy no` turns it off. In the GUI, "Save as JPEG..." in the Panorama tab
converts a finished panorama, including one from an earlier run, without stitching again. Keep
the TIFF as the master copy: JPEG discards some fine detail.

## 4. Serpentine scans and global bundle adjustment (0.7.0)

### Which scan type do I have?

- **Single sweep** — you panned across the slide once, in one continuous direction. This is
  the default (`--scan-mode sweep`) and it behaves exactly as every earlier version did.
- **Serpentine** — you panned across a row, stepped down, panned back the other way, stepped
  down again, and so on, covering an area rather than a strip. Choose `--scan-mode serpentine`.

```bash
# an area scan recorded back and forth in rows
.venv/bin/python -m micro_slide_stitcher \
    --input ~/videos/area_scan_10x.mp4 \
    --output ~/area_pano.tif \
    --scan-mode serpentine \
    --diagnostics ~/msc_diag_area
```

### Why the serpentine setting exists

In a serpentine scan the frame you are recording now often overlaps a frame from the row
*above*, recorded a minute earlier. A chained, frame-to-frame solution never uses that
overlap, so the small error in each consecutive step adds up along the path and the last row
can end up visibly displaced from the first. Serpentine mode looks for those cross-row
overlaps and then solves for **all** frame positions at once so that every verified overlap
is satisfied simultaneously, which pins the rows to each other instead of letting them
drift apart.

### How to record a serpentine scan

- Pan **one row slowly** and steadily from one side to the other. Slow is better than fast;
  the program can only match what it can see sharply.
- At the end of a row, **step down by roughly half a field** so the new row clearly overlaps
  the row you just finished. If the rows do not overlap, the whole point is lost.
- Then pan **back the other way**. Reversing direction is expected and handled.
- Keep the **same focus, objective, zoom and white balance** throughout. Do not touch the
  focus knob mid-scan.
- Expect it to **take longer to process** — often two to five times a plain sweep, because
  it searches for extra overlaps and then runs a global refinement.

### The flags, and which one wins

| You typed | What happens |
|---|---|
| nothing | `--scan-mode sweep`; no discovery, no refinement; identical to 0.6.0 output |
| `--scan-mode serpentine` | cross-row discovery **and** bundle adjustment on |
| `--scan-mode sweep --bundle-adjust` | refinement on for a plain sweep (advanced; usually little or no gain) |
| `--scan-mode serpentine --no-bundle-adjust` | discovery settings for a serpentine path, refinement **off** |
| `--bundle-adjust --no-bundle-adjust` | rejected with an error; the two contradict |

`--no-bundle-adjust` always wins over the scan mode, and `--bundle-adjust` always wins over
`sweep`: the explicit low-level flag beats the convenience setting. Everything the refinement
did — how many cross-row overlaps were found and accepted, how many iterations it ran, and the
reprojection error before and after — is written to the log, `summary.json` and `report.html`.

### It cannot cost you a scan

If the refinement fails, hits its iteration or wall-clock cap, produces a *worse* fit than the
chained solution, or produces a geometrically implausible one (a frame scaled, rotated or moved
further than the ordinary pairwise gates would ever allow), the chained solution is kept, the
reason is logged and warned about, and the panorama is still written. Turning this on can cost
you time; it cannot cost you output.

### Honest limitations

- On a **single straight sweep there is usually little to gain**. Frames two apart do still
  overlap at the recommended 50 %, but they lie along the same line as the chain, so they
  barely constrain drift. Real gains need a path that comes back near itself.
- A lower reprojection error does **not** prove a more accurate result. The program therefore
  refuses any refinement that changes a frame's scale or rotation beyond the gates the ordinary
  pairwise path enforces, or that moves a frame further than `--ba-max-shift-frac`, and it
  prefers a translation-only fit unless a scale-and-rotation fit is clearly better.
- The refinement only makes frame *placement* consistent. It does not sharpen blurred frames,
  fix a focus change halfway through, or invent overlap that was never recorded.
- Measured results for this project's synthetic test scans are in
  `MicroSlideStitcher_BUNDLE_ADJUSTMENT_FINDINGS.md`.

## 5. Every parameter explained

| Flag | Default | What it does |
|---|---|---|
| `--input` | required | Source video (`.mp4`, `.mov`, anything ffmpeg reads), image folder, one still image or quoted image glob. **Opened read-only, never modified or moved.** |
| `--input-mode` | `auto` | Detect video versus images from `--input`. Use `video` or `images` to force the interpretation and get a clear usage error if it does not fit. |
| `--output` | required | Panorama path. Extension picks the format: `.tif`/`.tiff` → deflate-compressed RGB TIFF via `tifffile` (widely compatible, e.g. QuPath/ImageJ), `.png`, `.jpg`. A PNG preview is *always* written into the diagnostics dir as well. |
| `--sampling-fps` | `2` | Frames sampled per second of video. This is the single most important knob: it must be high enough that consecutive sampled frames still overlap (aim for 40–70 % overlap), low enough that you do not waste time on near-duplicates. |
| `--image-order` | `name` | Still-image ordering: `name` is a natural sort (`IMG_2` before `IMG_10`); `mtime` follows modification time and natural-sorts any ties. |
| `--image-stride` | `1` | Still-image sampling: use every Nth ordered image. In image mode this replaces video’s `--sampling-fps`, which is ignored with a warning. |
| `--detector` | `orb` | Primary feature detector. `orb` is fast and free; `sift` is slower but stronger on low-contrast tissue. SIFT is *automatically* retried on any pair ORB fails, and that fallback is logged. |
| `--normalize` | `clahe` | `none` = raw frames. `temporal` = conservative ffmpeg `normalize` filter with `independence=0` (channels linked, hue preserved) to damp gradual auto-exposure drift. `clahe` = OpenCV CLAHE on the **L channel of LAB only** — never per RGB channel — so the H&E pink/purple balance is preserved. A before/after image lands in `diagnostics/exposure_comparison.png`. |
| `--blur-threshold` | `10` | Reject a frame if its focus score (variance of Laplacian) is below this. **Calibrate on your own footage:** phone-through-eyepiece video is soft and heavily compressed, so scores are much lower than for normal photos (raw frames of the test scan measured 3–36; after CLAHE 21–103). Look at `focus_score` in `diagnostics/frame_metrics.csv` and pick a value just under the typical in-focus value. |
| `--dedup-threshold` | `0.995` | Reject a frame if its similarity to the previous *accepted* frame exceeds this. Similarity = zero-mean normalised cross-correlation of 64×64 thumbnails, so pure brightness changes do not fool it. Lower it to drop more near-still frames; raise it (e.g. `0.999`) if slow scans are being over-pruned. |
| `--max-dimension` | off | Downscale frames/images so the long edge is at most N px (ffmpeg for video; OpenCV for image sequences). Use for previews and for scans that do not fit in RAM. |
| `--diagnostics DIR` | `<output_dir>/diagnostics` | Where all diagnostics go (see below). Preserved even when the run fails. |
| `--keep-intermediates` | off | Keep the raw extracted candidate frames in `<diagnostics>/_work` instead of deleting them at the end. |
| `--blend` | `feather` | `feather` = distance-transform weighted average (robust, the recommended default). `multiband` = OpenCV `detail::MultiBandBlender`; hides exposure steps better on tissue but can produce faint band artefacts over empty glass. Falls back to feather automatically if it fails. |
| `--min-inliers` | `25` | Minimum RANSAC inliers for a pair to count as registered. |
| `--min-inlier-ratio` | `0.15` | Minimum inliers / ratio-test-matches. |
| `--max-rotation-deg` | `5` | Reject a pair whose estimated rotation exceeds this (manual panning barely rotates). |
| `--max-translation-frac` | `0.92` | Reject a pair whose estimated translation exceeds this fraction of the frame width/height — i.e. essentially no overlap left. |
| `--no-homography-fallback` | off | Never try a full 8-DOF homography. By default a homography is only attempted after both ORB and SIFT similarity fits failed, and its use is logged loudly (`homography_fallbacks` in the report). |
| `--no-bridge` | off | Do not try to bridge over a single unregistrable frame; break the chain instead. |
| `--no-rejoin` | off | Disable **wide-baseline segment recovery**. By default, whenever the chain breaks the pipeline tries to re-join the two pieces before giving up (see below). With this flag the pieces stay disconnected (pre-0.2 behaviour). |
| `--rejoin-window K` | `3` | Recovery window: the **last K** frames of a segment are matched against the **first K** frames of the next one — all K×K combinations, **SIFT directly** (ORB is what already failed), same partial-affine + RANSAC model and same inlier/scale/rotation/reprojection gates as everywhere else. The best candidate by inlier count wins. |
| `--rejoin-max-translation-frac` | `2.0` | Translation allowance for recovery candidates, as a fraction of the frame width/height. **Deliberately much larger than `--max-translation-frac` (0.92)**: a chain break is normally caused by a fast pan that moved the field of view by *more* than one frame, so a plausible recovery transform is legitimately larger than anything the adjacent-pair path accepts. Everything else (scale 0.9–1.1, rotation, min inliers, min inlier ratio, max reprojection error) is unchanged, so an implausible fit is still rejected. |
| `--min-segment-frames N` | `3` | After recovery, stitch **every** remaining segment that has at least N frames, not just the largest one. The largest segment keeps the exact `--output` path; the others are written as `<output>_seg02.<ext>`, `<output>_seg03.<ext>`, … in descending frame-count order, each with its own preview, camera path and report row. Segments smaller than N are listed explicitly as skipped (frame indices + time range) in the log, `summary.json` and `report.html`. If the whole scan is one segment nothing changes: one file, no suffixes. |
| `--scan-mode` | `sweep` | How the slide was scanned. `sweep` = one continuous pan, registered as a pairwise chain — identical to 0.6.0. `serpentine` = back and forth in rows; enables non-adjacent (cross-row) match discovery **and** global bundle adjustment, and searches harder for row-to-row overlap. See section 4. |
| `--bundle-adjust` | off | Refine **all** frame positions together by minimising total reprojection error instead of trusting the pairwise chain. Implied by `--scan-mode serpentine`; give it explicitly to force it on a plain sweep (advanced — usually little or no gain). |
| `--no-bundle-adjust` | off | Never bundle adjust, even in serpentine mode. Wins over both `--scan-mode serpentine` and `--bundle-adjust`; giving `--bundle-adjust` and `--no-bundle-adjust` together is a usage error. |
| `--ba-max-iterations` | `200` | Iteration cap for the refinement. |
| `--ba-max-seconds` | `120.0` | Wall-clock cap in seconds for discovery plus refinement. On expiry the best solution so far is used if it is better than the chain, otherwise the chain is kept. |
| `--ba-max-pairs` | `60` | Cap on how many non-adjacent pairs are really feature-matched. Candidates are shortlisted by predicted overlap first, so this bounds the cost instead of trying all N² pairs. If the cap is hit, the log says how many candidates were dropped. |
| `--ba-min-overlap` | `0.15` | A non-adjacent pair is only worth matching if the current placement predicts it shares at least this fraction of a frame. |
| `--ba-max-shift-frac` | `0.5` | How far the refinement may move a frame, as a fraction of the frame diagonal. A refinement nudges; anything larger is a re-solve and is discarded in favour of the chained solution. This guard exists because a lower reprojection error is **not** by itself proof of a more accurate result — see the findings document. |
| `--serpentine-min-overlap` | `0.06` | The same threshold in serpentine mode — deliberately lower, because a row that was stepped down by half a field may share little area with the row above once drift is included. |
| `--serpentine-turn-translation-frac` | `1.5` | Serpentine mode only: translation allowance for a pair that the ordinary `--max-translation-frac` gate rejects, so a row turn (reverse direction plus step down in one move) does not break the chain. All other gates are unchanged. |
| `--max-viz` | `80` | Cap on match-visualisation PNGs. When the cap is hit it is written to the log and the report. |
| `--memory-limit-gb` | `3.0` | Before allocating the canvas the required working memory is estimated; if it exceeds this the run aborts with concrete advice instead of being OOM-killed. |
| `--version`, `--help` | — | Version / usage with worked examples. |

### Model used for registration

Adjacent accepted frames are matched (feature detector → BFMatcher KNN → Lowe ratio test 0.75)
and fitted with **`estimateAffinePartial2D` + RANSAC** — a 4-DOF similarity (translation,
rotation, uniform scale). That is the physically right model for a slide panned under a
microscope. Implausible fits (scale outside 0.9–1.1, rotation > `--max-rotation-deg`, huge
translation, too few inliers, high reprojection error) are rejected rather than accepted
silently. Cumulative transforms are composed along the chain; drift and suspicious trajectory
jumps are detected and reported.

#### Wide-baseline segment recovery and multi-segment output

A break in the chain (two consecutive sampled frames that cannot be registered, and that a
1-frame bridge cannot skip) used to mean that everything except the longest chain was thrown
away. Since 0.2 the pipeline instead:

1. **tries to re-join the pieces** with a wider search than the adjacent-pair bridge: K×K
   frame combinations across the break, SIFT, and a larger translation allowance
   (`--rejoin-max-translation-frac`, default 2.0 frame widths) because the break is normally
   a fast pan. Every attempt — frame pair, detector, matches, inliers, inlier ratio, mean
   reprojection error, accepted/rejected and the reason — is written to
   `diagnostics/rejoin_attempts.json`, visualised as `matches/rejoin_A_B.png` and summarised
   in `report.html`. On success the two chains are merged by composing the recovered
   transform and the run continues as if the break had never happened.
2. **writes every surviving segment** with at least `--min-segment-frames` frames instead of
   only the largest, so nothing is silently discarded. The verdict is then
   `PARTIALLY SUCCESSFUL` with an explicit statement of how many pieces the scan is split
   into and why.

## 6. Typical settings

| Situation | Suggested flags |
|---|---|
| Slow, careful 10× scan (a field per ~1–2 s) | `--sampling-fps 1.5 --normalize clahe --blend feather` |
| Normal 10× manual scan | `--sampling-fps 2` (defaults) |
| 20× scan (smaller field → the same hand speed means more relative motion) | `--sampling-fps 3 --detector orb`, and if pairs start failing `--sampling-fps 4 --detector sift` |
| 20× with weak texture (fat, mucin, pale dermis) | `--detector sift --sampling-fps 4 --min-inliers 20 --min-inlier-ratio 0.1` |
| Long scan (> 1 min) on a laptop | first `--max-dimension 1280 --sampling-fps 1.5`, then decide |
| Visible exposure steps between fields | `--normalize temporal` (or `clahe`) plus `--blend multiband` |

**Recording tips that matter more than any flag:** move slowly and in straight strips,
pause briefly at the end of each strip, keep the same objective and do not touch the focus
or the phone zoom mid-strip, disable phone HDR if you can, and overlap strips by ~30 %.

## 7. Diagnostics directory

```
<diagnostics>/
  video_metadata.json        ffprobe result for a video (size, fps, duration, codec, frame count, audio)
  image_metadata.json        image count, dimensions, uniform-size result, order and stride for stills
  frame_metrics.csv/.json    EVERY sampled candidate: index, timestamp, focus score,
                             duplicate score, mean intensity, accepted, reason
  accepted_frames.txt        accepted list
  rejected_frames.txt        rejected list WITH the reason
  accepted_frames/           the accepted source frames as PNG (post-normalisation)
  matches/match_A_B.png      per-pair match visualisation (green = inliers of an accepted
                             pair, red = a rejected pair), capped by --max-viz
  matches/rejoin_A_B.png     per-attempt visualisation of the wide-baseline segment
                             recovery pass (only written when the chain broke)
  transforms.json/.csv       per-pair detector, model, inliers, inlier ratio, mean
                             reprojection error, tx/ty/scale/rotation, whether the pair was
                             bridged or came from the rejoin pass, and the cumulative
                             transform of every frame in every segment
  rejoin_attempts.json       EVERY wide-baseline rejoin attempt with its metrics and the
                             accept/reject reason (only written when the chain broke)
  camera_path.png            the reconstructed scan trajectory (matplotlib)
  camera_path_seg02.png ...  the same for each additional written segment
  exposure_comparison.png    before/after exposure normalisation
  panorama_preview.png       downscaled panorama (<= 2000 px)
  panorama_preview_seg02.png downscaled panorama of each additional written segment
  pipeline.log               full run log
  summary.json               machine-readable summary incl. the verdict
  report.html                human-readable report of the whole run
  failure.txt                written instead of a panorama if the run could not finish
```

Nothing is cleaned up on failure — the diagnostics of a failed run are exactly what you need
to fix the recording or the flags.

## 8. Troubleshooting

**`Unrecognized option 'vsync'` / `Unrecognized option 'fps_mode'`** — a mismatch between
your ffmpeg version and the frame-rate flag. Fixed in 0.2.1, which negotiates the flag
instead of hard-coding it; upgrade, or confirm with `ffmpeg -h full | grep -E 'fps_mode|vsync'`
and report the output. Nothing else in the pipeline depends on the ffmpeg major version.

**I only have still photos / ffmpeg is not installed** — pass the folder of overlapping PNG,
JPEG, TIFF, BMP or WebP images to `--input` (or force `--input-mode images`). That mode does
not run or require ffmpeg/ffprobe. Ensure all selected images have the same dimensions; use
`--max-dimension` only when it makes their resulting dimensions uniform.

**"only N frame(s) passed quality gating"** — `--blur-threshold` is too high for your footage.
Open `frame_metrics.csv`, read the real `focus_score` column, and set the threshold just below
the typical in-focus value. Eyepiece video legitimately scores in the tens, not the hundreds.

**"no two adjacent frames could be registered" / many `too few inliers`** —
*insufficient overlap* is the usual cause: raise `--sampling-fps` (2 → 3 → 5). Look at the red
`matches/*.png` images: if the two halves show completely different tissue, the phone was moved
too fast for that sampling rate. If they show the same tissue but few matches, it is a texture
or focus problem → `--detector sift`, lower `--min-inliers`.

**Low-texture tissue** (fat, mucin, pale dermis, mostly empty glass) — SIFT plus a higher
sampling rate; and while recording, keep some structure (an edge, a vessel, a nucleus-rich
band) in the field rather than panning across blank glass.

**Blurry video / focus drift** — the tool rejects blurry frames, it cannot restore them. Bridge
gaps are reported as `bridged over frame(s) …`. Refocus between strips, use a phone holder
rather than a free hand, and tap-to-lock focus and exposure before starting.

**Exposure seams / visible rectangles** — `--normalize clahe` (colour safe) or
`--normalize temporal`, and `--blend multiband`. Locking the phone's exposure before recording
removes the problem at the source.

**Excessive drift / the panorama bends** — long chains accumulate small errors; this build has
no global bundle adjustment. Warnings such as `cumulative scale drift detected` and
`suspicious trajectory jump` appear in the log and the report. Record shorter strips and stitch
them separately, or lower `--sampling-fps` so fewer transforms are chained.

**"chain break" / "N disconnected segments"** — the pipeline first tries a wide-baseline
rejoin (see the next entry); whatever cannot be joined is still written out. Check
`transforms.csv` and `rejoin_attempts.json` for the break, then re-record that stretch or
raise `--sampling-fps`.

**"my scan came out in several pieces"** — the panorama came out as `pano.tif` *plus*
`pano_seg02.tif` (…`_seg03`…) and the verdict is `PARTIALLY SUCCESSFUL` saying the scan is
split into N pieces. What happened: somewhere in the video two consecutive sampled frames no
longer overlapped (a fast pan, usually combined with defocus), the 1-frame bridge could not
skip it, and the **wide-baseline rejoin** pass — K×K SIFT attempts across the break with a
translation allowance of `--rejoin-max-translation-frac` frame widths — could not find a
plausible transform either. That is not a silent failure: each piece is a valid panorama of
its stretch of the slide, `report.html` lists every piece (frames, time range, pixels,
coverage, registration quality, output path) and `rejoin_attempts.json` shows exactly how
close each attempt came (typically `too few inliers`, i.e. genuinely no shared tissue).
What to do:
* look at `matches/rejoin_*.png`: if the two halves show *different* tissue, no software can
  join them — **pan more slowly** (a field per 1–2 s), keep the focus locked, and overlap
  fields by ~30–50 %;
* re-run with a higher `--sampling-fps` (2 → 3 → 5): the frames on either side of a fast pan
  are then closer together and often still overlap;
* widen the search a bit: `--rejoin-window 5 --rejoin-max-translation-frac 2.5`, and if the
  tissue is low-texture also `--detector sift --min-inliers 20 --min-inlier-ratio 0.1`;
* if you would rather have fewer, larger files, raise `--min-segment-frames`; the skipped
  segments are then reported with their frame indices and time range;
* the pieces can always be joined by hand afterwards in QuPath/ImageJ/GIMP — the per-segment
  time ranges in `report.html` tell you which piece comes first.

Use `--no-rejoin` if you want the old "longest segment only" behaviour back (together with a
large `--min-segment-frames`).

**Out of memory / the memory guard aborts** — use `--max-dimension 960` (or `1280`), lower
`--sampling-fps`, split the video, or raise `--memory-limit-gb` if you genuinely have the RAM.
The guard estimates `W*H*20` bytes for accumulation before allocating anything.

## 9. Desktop GUI (optional)

The GUI is a **front end for the CLI**, not a second implementation. It builds a command line,
runs `python -m micro_slide_stitcher` as a child process, and reads the diagnostics directory
that run produced. Anything the GUI can do, the CLI can do; the command box shows you exactly
how.

### Built-in user manual (Help → User manual, or F1)

Since 0.6.0 the whole manual travels inside the program. **Help → User manual**, the
**User manual (F1)** button in the status bar, or simply **F1** opens a resizable window
containing nine sections written for a pathologist rather than a programmer: what the tool
does and the not-for-diagnosis limitation, how to record a scan that will actually stitch
(mount, lock focus, manual white balance, ~50 % overlap, 1080p30, stabilisation off), video
versus still images, a step-by-step first run, how to read the verdict / inliers /
reprojection error / segments / `report.html`, how to calibrate `--blur-threshold`,
troubleshooting, the complete flag reference, and credits.

It has a clickable table of contents (internal anchors), Back/Forward navigation, a live
**Find** box that highlights every match and jumps between them, and a **Save manual as
HTML…** button that writes one self-contained file you can e-mail to a colleague.

Two deliberate constraints:

* It is rendered by **QTextBrowser**, not Qt WebEngine. No browser engine is bundled — that
  is what keeps the standalone download at a few hundred megabytes, and a test asserts that
  nothing under `src/micro_slide_stitcher/gui/` ever imports `QtWebEngine*`.
* Section 8, the complete flag table, is **generated from `cli.build_parser()` at runtime**,
  exactly like the settings tooltips in `gui/cli_spec.py`. It therefore cannot drift from the
  real CLI, and `tests/test_gui.py` fails if any parser option string is missing from it.

The prose lives in `MANUAL_SECTIONS` in `src/micro_slide_stitcher/gui/manual.py` as a
module-level constant, so no data file has to be located at runtime and the manual is
identical in a source checkout, a pip install and the frozen PyInstaller/AppImage build.

The **Help → About MicroSlideStitcher** menu item and the window's **About** button show the
version, author, licence, diagnostic-use limitation and a copyable support-diagnostics block.
To edit the affiliation displayed there, change this exact line in
`src/micro_slide_stitcher/gui/about.py`:

```python
AFFILIATION = "Histopathology / Dermatopathology · Athens, Greece"
```

### Install

```bash
cd ~/MicroSlideStitcher
.venv/bin/pip install -e '.[gui]'
```

Manjaro note: **PySide6 comes from pip**, as a self-contained wheel with its own bundled Qt 6.
You do **not** need `pacman -S qt6-base` (or `pyside6`) and you should not mix the two. The
CLI does not depend on PySide6 at all — if the extra is missing the pipeline keeps working and
only the GUI refuses to start, with a one-line message telling you what to install (exit 4).

### Launch

```bash
.venv/bin/python -m micro_slide_stitcher.gui            # or
.venv/bin/micro-slide-stitcher-gui                      # console script
.venv/bin/micro-slide-stitcher-gui ~/videos/scan.mp4    # open a video straight away
```

Minimum useful window is 1180x720; it is designed for 1280x800 and larger. Keyboard:
`Ctrl+O` choose video, *Choose image folder…* selects a still sequence, `Ctrl+P` quick
preview, `Ctrl+R` full resolution, `Esc` cancel, `F1` the built-in user manual.

### What each panel does

| Panel | What it is for |
| --- | --- |
| **1 · Input video or image sequence** | Drag a video file or an image folder anywhere onto the window, use *Choose video…*, or use *Choose image folder…*. Video metadata comes from `ffprobe`; image folders show image count, dimensions, ordering, uniform-size status and candidate count without ffmpeg. Runs off the UI thread. |
| **2 · Where the results go** | `--output` panorama path (the extension picks the format) and the `--diagnostics` folder, which is derived from the output name unless you override it. |
| **3 · Settings** | Every exposed CLI flag, with the CLI's own defaults and the CLI's own help text as the tooltip. The seven rarely-touched flags (`--blend`, `--dedup-threshold`, `--min-inliers`, `--min-segment-frames`, `--rejoin-window`, `--no-rejoin`, `--keep-intermediates`) sit in a collapsed *Advanced settings* group. *Reset all settings to the CLI defaults* does what it says. |
| **Equivalent command line** | Read-only, selectable, updates live, with *Copy command*. By default it lists only the flags that differ from the CLI defaults (the shortest command that reproduces the run); tick *Show every flag* for the fully explicit form. Paste it into a terminal and you get an identical run. |
| **Run** | *Quick preview* forces `--max-dimension 960` and a `.png` output into a `<name>_preview_diagnostics` folder — seconds instead of minutes. *Full resolution* runs your settings as shown. Both run in a background thread; the progress bar and stage text are derived from the pipeline's own log lines. *Cancel run* terminates the child process group and drops a `cancelled.txt` marker in the diagnostics folder. |
| **Run log** | The pipeline's log, streamed live (the same lines that land in `<diagnostics>/pipeline.log`), with WARNING and ERROR highlighted. |
| **Frame quality** | Focus score of every sampled frame against its timestamp, read from `frame_metrics.csv`, green for accepted and red for rejected. Drag the blue line (or use the arrow keys) to pick a `--blur-threshold`: the label shows how many frames would be kept or rejected, and the settings panel and the command line follow it. Populated after a run, or by loading an earlier diagnostics folder. |
| **Panorama** | Pan (drag) and zoom (wheel, `+`/`-`, *Fit to window*, *1:1*) over the fast preview PNG, with *Load full-res file* for the real TIFF/PNG (loaded off-thread). If the scan was written as several segments, the selector at the top switches between them and shows frame count, time range, pixel size and coverage. *Open report.html* opens the run's report in your normal browser (no embedded browser engine is bundled) and *Open folder* opens the output directory. |
| **Results summary** | Verdict, accepted/rejected counts, segments found/written/skipped, rejoin attempts and outcome, registration statistics (inlier median, reprojection error), runtime and output paths — all read from `summary.json`, never re-derived. Warnings from the run are listed underneath. |

Your settings, the last output path and the last input directory are remembered between
sessions (`QSettings`), so a routine scan is: drop the file, *Quick preview*, look, *Full
resolution*.

If a run fails (CLI exit code 3), a red banner shows the failure message and keeps the
diagnostics folder clickable, so `failure.txt`, `pipeline.log` and `frame_metrics.csv` are one
click away.

### Opening reports and result folders on Linux

The two Panorama buttons first check that the requested report or folder exists; if it does not,
the GUI says to run the pipeline first rather than suggesting a missing desktop handler. In a
normal Python install they then ask Qt to open the local path. In the standalone PyInstaller
onefile build they deliberately skip Qt and go straight to the sanitised external-handler chain,
so Dolphin or the browser cannot inherit bundled Qt/OpenCV libraries. That chain tries, in
order, the FileManager1 D-Bus `ShowFolders` request (folders only), `xdg-open`, `gio open`, then
the first installed file manager or browser; for a browser it also honours `$BROWSER`. The status
line names the mechanism that accepted the path.

If no handler accepts it, the GUI does **not** claim success. It shows the exact absolute path in
a non-modal red banner with a *Copy path* button. On Manjaro/Arch, install the usual desktop
opener with:

```bash
sudo pacman -S --needed xdg-utils
```

## 10. Tests

```bash
cd ~/MicroSlideStitcher
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

The suite generates a synthetic H&E-like slide, pans a window over it with **known**
translations, encodes it to mp4 with ffmpeg, and then checks that pairwise registration
recovers those translations within 2 px, that the end-to-end panorama has approximately the
expected dimensions, that blur/duplicate rejection fires, that `--help` and a real
`--max-dimension` invocation work, and that the memory guard and error paths behave.

A third family is a synthetic **serpentine** sequence — four columns by three rows, stepping
down half a field per row — used to check that cross-row loop closures are discovered and agree
with ground truth, that the row turn does not break the chain, that the refinement lowers
reprojection error on injected drift, that a deliberately sabotaged optimiser is rejected and the
chained solution kept, that the gauge frame does not move, that repeated runs are identical, and
that a default run's output stays byte-identical with the feature present.

A second family of synthetic clips has a deliberate **unregistrable gap** in the middle
(`scripts/make_synthetic_video.py --gap-after N --gap-x …`) and covers segment recovery: a
gap that still shares overlap is rejoined into one panorama (asserting the recovered frame
pair and its metrics), a gap with no shared content yields exactly two panoramas
(`pano.png` + `pano_seg02.png`) with per-segment diagnostics, `--no-rejoin` restores the old
broken-chain behaviour, small segments are reported as skipped, and a clean single-segment
run still produces exactly one file with no `_seg` suffixes anywhere.

`tests/test_gui.py` covers the front end offscreen (`QT_QPA_PLATFORM=offscreen`, no display
needed) with pytest-qt: the window constructs, the command preview matches the flags exactly
for several settings combinations, drag-and-drop and file loading fill in the metadata,
dragging the threshold line updates `--blur-threshold` and the command string, a real quick
preview run against a synthetic video populates the summary/timeline/viewer, Cancel really
kills the child process, a two-segment run produces per-segment views, and a pipeline failure
surfaces with a clickable diagnostics path. It also asserts that the CLI still runs with
PySide6 unimportable. Eight further tests cover the in-app user manual: that it opens from the
Help menu action and from `F1`, that every table-of-contents anchor really resolves (the scroll
positions must strictly increase) and fills the Back/Forward history, that the Find box
highlights exactly the matching passages and steps between them, that *Save manual as HTML…*
writes a non-empty standalone file, that the generated flag table covers **every** option
string of `cli.build_parser()`, and that no web engine is imported anywhere — including in a
fresh interpreter that builds the manual. Install the test extra with
`.venv/bin/pip install -e '.[test,gui]'`. **91 tests in total** (42 CLI/pipeline + 49 GUI);
the GUI tests skip themselves if PySide6 is not installed.

`scripts/make_synthetic_video.py` can also be run by hand to produce a test clip
(`--help` for options).

## 11. Credits and licence

Created by **Michail Sofopoulos** — Histopathology / Dermatopathology · Athens, Greece. Contact:
[msofopoulos@gmail.com](mailto:msofopoulos@gmail.com). MicroSlideStitcher is available under the
[MIT Licence](LICENSE).

## 12. Layout

```
MicroSlideStitcher/
  README.md  PROJECT_LOG.md  requirements.txt  pyproject.toml  .venv/
  src/micro_slide_stitcher/{__init__,__main__,cli,probe,extract,metrics,register,
                            stitch,bundle,diagnostics,report,logging_utils}.py
  src/micro_slide_stitcher/gui/{__init__,__main__,mainwindow,about,manual,cli_spec,command,
                                runner,results}.py
  src/micro_slide_stitcher/gui/widgets/{imageview,logpane,settings_panel,timeline}.py
  tests/     scripts/{setup_manjaro.sh,make_synthetic_video.py,gui_screenshots.py,
             gui_screenshots_manual.py,build_standalone.sh}
  packaging/ {MicroSlideStitcher.spec,MicroSlideStitcher-onedir.spec,entrypoint.py,
             gui_selftest.py,glibc_floor.py,appimage/}
  examples/  diagnostics/  output/
```
