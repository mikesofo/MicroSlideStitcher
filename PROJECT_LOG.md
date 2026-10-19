# PROJECT_LOG

## Stage 14 — 2026-08-25 — scan modes and global bundle adjustment (0.7.0)

**What was added.** `src/micro_slide_stitcher/bundle.py` (new, ~840 lines): non-adjacent
(loop-closure) match discovery plus joint refinement of every frame transform in a segment,
reached from the CLI through `--scan-mode {sweep,serpentine}` and
`--bundle-adjust` / `--no-bundle-adjust`. `--scan-mode sweep` is the default and is
byte-identical to 0.6.0 — nothing is discovered, nothing is optimised and nothing extra is
written.

- **Discovery** (`shortlist_pairs`, `discover_non_adjacent_pairs`): the chained placement is
  used as the initial guess, `predicted_overlap` computes the polygon intersection of every
  non-consecutive pair (pure arithmetic, microseconds), pairs above `--ba-min-overlap`
  (`--serpentine-min-overlap` in serpentine mode) are sorted by predicted overlap and the best
  `--ba-max-pairs` are really matched through the existing `match_pair` matcher with the
  existing inlier, scale, rotation and reprojection gates. The cap is what keeps this out of
  O(n²); the log reports how many candidates were dropped by it.
- **Optimisation** (`bundle_adjust`): `cv2.detail.BundleAdjusterAffinePartial` is tried first,
  driven as a black box — synthetic `ImageFeatures`/`MatchesInfo` are built from our
  correspondences, both the R and inv(R) conventions are fed in and both interpretations read
  back, each gauge-normalised, and the one with the lowest error *under our own metric* wins.
  When OpenCV cannot be used (its `findMaxSpanningTree` asserts on constraint graphs it does
  not like) a numpy-only Levenberg-Marquardt with a Huber loss takes over: normal equations
  accumulated pair by pair, frame 0 fixed as the gauge, dense solve. **scipy is not used, so
  the PyInstaller exclusion list and the bundle size are unchanged.**
- **Two guards that came out of measurement, not theory.** Both were added because the
  evaluation caught the failure, and both are worth remembering:
  1. *A lower reprojection error is not proof of a better result.* On a serpentine scan whose
     row turns had contributed no constraint, the LM found the degenerate optimum where every
     linear part is zero — every pixel maps to one point, every correspondence agrees, mean
     reprojection error fell 81 % and the frames were 183 px out against ground truth.
     `_implausible()` now re-uses the pairwise path's own gates (scale 0.9–1.1, rotation
     ≤ 5°) inside the cost function *and* as an acceptance check, and `--ba-max-shift-frac`
     (default 0.5 of the frame diagonal) refuses a re-solve dressed up as a refinement.
  2. *The simplest model that fits should win.* A slide scan is a near-pure translation, so
     the similarity model's scale freedom can buy a lower robust cost with a scale change that
     is worse in absolute position. The LM is therefore fitted twice — translation-only and
     similarity — and the similarity fit is only taken if it lowers the robust cost by more
     than 10 % (`_RICHER_MODEL_MARGIN`).
- **The row turn.** `register.build_chain` gained `turn_translation_frac`: when a consecutive
  pair fails specifically with `"implausible translation"` and serpentine mode is on, it is
  retried once with `--serpentine-turn-translation-frac` (default 1.5) and every other gate
  unchanged, so a reverse-and-step-down turn does not break the chain. The same allowance had
  to be threaded into `bundle.adjacent_correspondences`, because a turn accepted by
  `build_chain` and then re-rejected inside the bundle leaves two rows with no consecutive
  constraint at all — which is precisely how guard 1 above got triggered.
- **Safety.** Every failure path — too few constraints, an optimiser that raises, a timeout, a
  cost that does not improve, an implausible solution — returns the *input* segment with
  `applied=False` and a human-readable `reason`, and `cli.run()` wraps the call in
  `try/except (cv2.error, RuntimeError, ValueError, LinAlgError)`. A failed optimisation
  cannot cost the user a panorama.
- **Reporting.** `summary.json` gains `scan_mode` and a `bundle_adjustment` block (primary
  plus `per_segment`), `counts` gains the non-adjacent candidate/tried/accepted totals and
  `bundle_adjusted_segments`, `timing` gains `bundle_adjust_s` (subtracted from `stitch_s`).
  `report.html` gains a "Scan type and global bundle adjustment" section with the accepted
  loop-closure table and before/after error. The GUI gains a plain-language **Scan type**
  control, an advanced bundle-adjustment checkbox and iteration cap, and two results rows.
  Long captions in the settings panel are no longer clipped: the label-bearing choice rows
  span the full panel width, and a checkbox row now carries its "label (--flag)" caption in a
  word-wrapping label beside the box (`_CheckRow`/`_CaptionLabel`), because `QCheckBox` never
  wraps its own text and cut `--bundle-adjust` off mid-flag at the panel edge. The caption
  stays clickable, so the row still behaves like an ordinary checkbox.
  When bundle adjustment changed the geometry, `diag/transforms_bundle_adjusted.json` records
  the transforms the panorama was actually composed from; `transforms.json` keeps the chained
  solution so a default run's diagnostics are unchanged.
- **A wrong assumption, corrected.** The spec expected "a single straight sweep has no
  non-adjacent overlaps". At the recommended ~50 % overlap that is false: frames i and i+2
  still share about 20–60 % of their area, and discovery finds plenty of them. What a straight
  sweep lacks is *loop closure* — those extra pairs are collinear with the chain and so barely
  constrain drift. The log message and the README say this properly now.

**Measured.** `scripts/evaluate_bundle_adjustment.py` builds three synthetic sequences with
ground truth (short sweep, long sweep, serpentine) and scores estimated frame positions against
it. Numbers, including the cases where bundle adjustment does nothing, are in
`MicroSlideStitcher_BUNDLE_ADJUSTMENT_FINDINGS.md`.


## Stage 13 — 2026-08-25 — in-app user manual (0.6.0)

**What was added.** `src/micro_slide_stitcher/gui/manual.py` (new): a `ManualWindow`
(resizable, modeless, minimum 720x460, opens at 1000x760) driven by a `ManualBrowser`
subclass of **QTextBrowser**. Reachable from **Help -> User manual**, from a
**User manual (F1)** status-bar button, and from the **F1** key (the QAction's own shortcut —
deliberately only one binding, so Qt never reports an ambiguous shortcut overload and
swallows the key press).

- **Content**: nine sections written for a histopathologist, not a programmer — (1) what the
  tool does plus the not-for-diagnosis limitation reusing `about.DISCLAIMER` verbatim so the
  wording cannot diverge from the README and the About dialog, (2) recording a good scan
  (eyepiece mount, locked focus, manual white balance against empty glass, hold ~1 s on the
  first field, pan slowly with ~50 % overlap, 1080p30 over 120 fps, stabilisation OFF because
  it warps frames non-rigidly), (3) video versus still images and image ordering/stride,
  (4) step-by-step first run — quick preview then full resolution, (5) reading the results
  (verdicts, inliers, reprojection error, segments, `report.html`, why a scan splits),
  (6) calibrating `--blur-threshold` from the Frame quality timeline and the `focus_score`
  column of `frame_metrics.csv`, (7) troubleshooting (chain breaks, memory guard, missing
  ffmpeg, Qt/xcb-util-cursor, folder not opening), (8) the complete flag reference,
  (9) credits and licence consistent with the About dialog.
- **Storage**: the prose is the module-level constant `MANUAL_SECTIONS`. No package data file
  and no `importlib.resources` lookup, therefore nothing to add to the `.spec` `datas` and
  nothing that can fail to be found in the frozen build — the frozen self-test builds the
  document and counts its characters.
- **Section 8 is generated at runtime** from `cli.build_parser()`: one row per option string
  with what it accepts, its default, the CLI's own help text and where the setting lives in
  the window (from `cli_spec.SPECS`). It matches the existing tooltip contract, so the manual
  cannot drift from the real CLI. The help text is de-duplicated against the Default column
  and its punctuation repaired.
- **Navigation**: `ManualBrowser.loadResource()` answers the internal URL
  `mss-manual:manual.html`, which is what makes `#anchor` links real `setSource()`
  navigations — so the clickable table of contents, Back and Forward all work through
  QTextBrowser's own history without a file on disk and without a web engine.
- **Find**: live highlighting of every match via `setExtraSelections` (amber behind the
  document's near-black text), a match counter, Enter/Next/Previous stepping with wrap-around,
  and the current match parked a third of the way down the view instead of on the bottom edge.
- **Export**: *Save manual as HTML…* writes one self-contained document (with the style sheet
  inlined, so it also looks right in Firefox) for e-mailing to a colleague.
- **No QtWebEngine.** QTextBrowser only; the existing `.spec` exclusions for
  `PySide6.QtWebEngine*` are unchanged, and a test walks every module under `gui/` to assert
  no web-engine import ever appears.

**Verified** 91 tests pass (83 pre-existing, unchanged, plus 8 new), full suite run twice.
Twelve offscreen screenshots at 1280x800 and at the 720x460 minimum were inspected one by one;
four defects were found and fixed: the single toolbar row clipped the *Save manual as HTML…*
button at 720 px (split into two rows), 1280-px-wide prose ran to over 200 characters per line
(the root frame is now padded from the widget width — derived from the widget, never the
viewport, so it cannot oscillate), anchors landed mid-view because `setSource()` scrolls before
the padding is re-applied (`scrollToAnchor()` is now repeated afterwards), and a match found
inside the flag table sat on the very bottom edge. Stray punctuation left behind by stripping
the duplicated default out of each CLI help string was also fixed.

**Limitations**
- The manual is English only and there is no help-topic-per-widget mapping: F1 always opens at
  the top rather than at the section for whatever is focused.
- Find is plain substring matching, case-sensitive, with no regular expressions and no
  whole-word option; two characters are needed before highlighting starts.
- Qt's rich-text engine supports only a subset of CSS, so the manual is deliberately plain:
  no collapsible sections and no illustrations.
- The exported HTML is a snapshot; a colleague who opens it later sees the flag table of the
  version that exported it, not of their own installation.

## Stage 12 — 2026-08-24 — About dialog and support diagnostics (0.5.0)

- Added a modeless **About MicroSlideStitcher** dialog, available from both the new Help menu
  and a compact window button. It reads the application version from `__version__`, names
  Michail Sofopoulos, includes the editable affiliation constant and a selectable `mailto:`
  contact link.
- The dialog makes the diagnostic-use limitation visually distinct, keeps the full MIT Licence
  in-app, and provides a **Copy diagnostics** button with Python, PySide6/Qt, OpenCV, platform
  and frozen-PyInstaller state for useful bug reports.
- Corrected author/credit metadata and licence attribution to Michail Sofopoulos and documented
  the exact affiliation-constant line in the README.

## Stage 11 — 2026-08-24 — hardened frozen-build opening (0.4.2)

- In a PyInstaller onefile build the Panorama buttons now skip
  `QDesktopServices.openUrl()` entirely and log `frozen build: using sanitised external
  handlers` before using the existing detached FileManager1/`xdg-open`/`gio`/file-manager or
  browser fallback chain. This avoids launching Dolphin or a browser with PyInstaller's bundled
  Qt/OpenCV library environment, which can make Qt report a false opening success while the
  handler immediately fails.
- Reveal now verifies that its resolved report or output folder exists before trying any handler.
  A missing target explicitly says to run the pipeline first; it is not reported as a no-handler
  failure and starts no desktop launcher.
- Moved the sanitised child-environment helper into the dependency-free core and retained its
  `gui.runner` import. The GUI's background `ffprobe` metadata probe and the core ffprobe/ffmpeg
  commands now use it too, in addition to the pipeline runner and detached desktop handlers.
- Added offscreen coverage for frozen Qt bypass plus external fallback, missing-target feedback
  with no launcher, and the retained non-frozen Qt-first named-success path.

## Stage 10 — 2026-08-24 — reliable report/folder opening (0.4.1)

- Replaced the Panorama tab's unconditional `QDesktopServices.openUrl()` success messages with
  one shared path-reveal helper. It honours Qt's boolean result and only reports success after
  a handler accepts the path; every successful status/log line names the mechanism used.
- Directory fallback order is FileManager1 D-Bus `ShowFolders` (when `gdbus` and the service are
  available), `xdg-open`, `gio open`, then the first installed `dolphin`, `nautilus`, `thunar`,
  `nemo`, `pcmanfm` or `xdg-open`. Reports use `xdg-open`, `gio open`, `$BROWSER`, then the
  first installed `firefox`, `chromium`, `google-chrome-stable` or `brave`.
- Fallback commands run in a worker and are detached with a short immediate-failure check, so an
  unavailable desktop handler cannot block the GUI. They use `runner.child_environment()` to
  remove PyInstaller `_PYI*` state and restore `LD_LIBRARY_PATH_ORIG`; the pipeline runner uses
  the same helper.
- All-handler failure shows a non-modal banner with the exact absolute path, Arch's
  `sudo pacman -S --needed xdg-utils` hint and a *Copy path* button; no success is claimed.
- Added five offscreen tests for Qt-to-fallback flow, clear all-failed feedback, named success,
  the sanitised subprocess environment and normal non-PyInstaller environment preservation.

## Stage 9 — 2026-08-23 — image-sequence input mode (0.4.0)

- Added auto-detected video/image input with explicit `--input-mode {auto,video,images}`.
  Image input accepts a folder, one supported still or a quoted glob; PNG, JPEG, TIFF, BMP and
  WebP suffixes are case-insensitive. Names use natural order (`IMG_2` before `IMG_10`) by
  default, with `--image-order mtime` and `--image-stride N` for phone bursts.
- Added `images.py`: OpenCV-only image metadata and candidate preparation. Image mode neither
  finds nor invokes ffmpeg/ffprobe; it reads dimensions from every selected still, rejects
  mixed final dimensions, and applies `--max-dimension` with OpenCV. `temporal` normalization
  now fails as a usage error for stills; video behavior is retained.
- Reports, summaries and GUI results now use image indices honestly for still inputs rather than
  inventing video durations/fps. Diagnostics write `image_metadata.json`, retaining
  `video_metadata.json` for video compatibility.
- GUI adds *Choose image folder…*, accepts folder drops, shows image count/order/uniformity in
  the metadata panel, and makes the command preview explicit with `--input-mode images`.
- Synthetic-generator support (`--image-dir`) and automated tests cover end-to-end images,
  natural ordering, mtime order, stride, glob input, mixed-dimension refusal, temporal rejection,
  no-ffmpeg execution, matching video registration and GUI folder load/preview.

## 2026-08-20 — Stage 0: environment
- Created `/home/user/workspace/MicroSlideStitcher/` and a dedicated venv at `.venv/`
  (`python3 -m venv`, Python 3.14.3). No global site-packages touched, no
  `--break-system-packages`.
- Installed into the venv: `opencv-contrib-python-headless 5.0.0.93`, `numpy 2.5.2`,
  `tifffile 2026.8.16`, `matplotlib 3.11.1`, `pytest 9.1.1`.
- Verified the OpenCV 5 API surface actually used (`ORB.create`, `SIFT.create`, `BFMatcher`,
  `estimateAffinePartial2D`, `createCLAHE`, `detail::MultiBandBlender`).
- Probed the real test video: H.264, 1920x1080, 25.33 s, 30 fps, 761 frames, AAC audio.

## 2026-08-20 — Stage 1: feasibility check on the real video
- Sampled the real video at 2 fps (51 candidates) into a scratch dir and measured
  variance-of-Laplacian: **3.2 – 36.0** on raw frames. The spec's example
  `--blur-threshold 80` would have rejected every single frame, so the default was set to
  `10` (and CLAHE, applied before scoring, lifts the scores to 21–103).
- Quick ORB + `estimateAffinePartial2D` sweep over consecutive 2 fps pairs: ~46/50 pairs
  registered with 50–1900 inliers, translations mostly 100–700 px horizontally with a few
  vertical steps → a single mostly-horizontal strip scan with good overlap. Two/three pairs
  fail on heavy motion blur. Conclusion: the video *is* stitchable; bridging over single
  bad frames is required.

## 2026-08-20 — Stage 2: implementation
- Wrote `probe.py` (ffprobe → `video_metadata.json`, clear failure messages),
  `extract.py` (ffmpeg `fps=`/`normalize=`/`scale=` filter chain, blur + near-duplicate
  gating), `metrics.py` (Laplacian focus, thumbnail NCC duplicate score, LAB-L-only CLAHE,
  before/after image), `register.py` (ORB→SIFT→homography cascade, RANSAC similarity model,
  plausibility validation, match visualisations, chain building with bridging and
  segmentation), `stitch.py` (canvas bounds, memory guard, per-frame bounding-box warping,
  feather + multiband blending, coverage-mask auto-crop, TIFF/PNG writing),
  `diagnostics.py`, `report.py` (HTML), `logging_utils.py`, `cli.py`.
- `pyproject.toml` + editable install; `python -m micro_slide_stitcher --help` works.

## 2026-08-20 — Stage 3: synthetic data + tests
- `scripts/make_synthetic_video.py` builds a fake H&E slide, pans a window over it in a
  raster pattern with known translations, adds sinusoidal brightness variation and optional
  motion-blurred positions, encodes with ffmpeg and writes `*.truth.json`.
- End-to-end run on `examples/synthetic.mp4` (12 positions, one blurred): 12/12 frames
  accepted, 1 pair rejected (20 < 25 inliers, the blurred position) and bridged, panorama
  **1241x621** vs ground truth 1240x620. Visually inspected: seamless, correct raster layout.
- `pytest`: 22 tests, all passing (registration accuracy within 2 px for ORB and SIFT over
  four known translations, no-overlap rejection, blur/dedup gating, CLAHE colour safety,
  probe error handling, full-res and `--max-dimension` end-to-end runs, memory guard,
  clean failure when the blur threshold is absurd).

## 2026-08-20 — Stage 4: real video runs
- Preview run (`--max-dimension 960 --sampling-fps 2 --normalize clahe --blend feather
  --blur-threshold 3`): 51/51 accepted, 50/50 pairs registered, 1 segment of 51 frames,
  panorama **7425x1512**, 20 s wall clock.
- Full-resolution run (`--sampling-fps 2 --detector orb --normalize clahe --blend feather`):
  51/51 accepted, 48/50 pairs registered, 2 pairs bridged (motion blur, 9 and 16 inliers),
  2 SIFT fallbacks, 0 homography fallbacks, 1 segment of 49 frames, median 449 inliers,
  median inlier ratio 0.63, median reprojection error 1.54 px. Panorama
  **15309x2999** TIFF (46.7 MB), 44 s wall clock.
- Bare-defaults run (only `--input/--output/--diagnostics`) also succeeds: identical
  15309x2999 panorama, 0 frames rejected.
- `--blend multiband --normalize temporal` preview run also completes (7036x1473) but shows
  faint band artefacts over the empty glass, so `feather` stays the default.
- Visually inspected the preview PNG and two 1800x1000 crops of the full-res TIFF: nuclei are
  crisp, the epidermis runs continuously across the whole panorama, no ghosting or doubled
  structures at the seams; only mild brightness steps between fields remain.

## 2026-08-20 — Stage 5: docs
- `README.md` (Manjaro/pacman prerequisites, venv install, headless-vs-GUI OpenCV note,
  basic + advanced examples, full parameter table, 10x/20x recommendations, diagnostics
  inventory, troubleshooting, not-a-medical-device disclaimer),
  `scripts/setup_manjaro.sh` (idempotent, prints its plan, prompts before pacman),
  this log.
- Dropped the always-zero `keypoints` column from `frame_metrics.csv` rather than ship a
  misleading field.

## 2026-08-20 — second real video (VID_20260427_103554775.mp4)
1920x1080, 30 fps, 49.17 s, h264. Vertical (bottom-to-top) manual sweep of an H&E skin section.
- 2 fps -> 98 candidates, 92 accepted (6 rejected: 5 near-duplicate, 1 blur).
- 90 adjacent pairs registered, chain BREAK after frame 72 (t=36.0 s): 5/4/5 inliers onto frames 73/74/75, bridging failed -> 2 segments [70, 22].
- Longest segment stitched: output/vid2_panorama.tif 4228x18224, coverage 54.6 %, inliers median 520, 6 SIFT fallbacks, 0 homography. Verdict PARTIALLY SUCCESSFUL (only 76 % of frames used).
- Orphaned tail recovered by trimming the video from t=36.4 s and re-running: output/vid2_part2.tif 2652x5364, 24/24 frames, 1 segment, coverage 74.6 %, verdict SUCCESSFUL.
- Source video has a strong yellow/green cast (halogen illumination, phone auto-WB). Pipeline preserves it faithfully; gray-world white balance applied post-hoc removes the cast but does not restore eosin pink.
- Takeaway: pipeline needs a segment-merge / multi-segment output mode instead of discarding all but the longest chain.

## 2026-08-20 — Stage 6: wide-baseline segment recovery + multi-segment output (0.2.0)
Fixes the takeaway of the second real video: a broken chain no longer means that everything
except the longest segment is silently discarded.

**(A) Wide-baseline segment recovery** (`register.py`)
- New `rejoin_segments()` pass runs after `build_chain()` whenever more than one raw segment
  exists. For each consecutive segment boundary it tries the last K frames of segment *i*
  against the first K frames of segment *i+1* (all KxK combinations, `--rejoin-window`,
  default 3), **SIFT directly** (ORB is what just failed), the same partial-affine + RANSAC
  model, and the same min-inliers / min-inlier-ratio / scale / rotation / reprojection gates,
  but with a larger translation allowance (`--rejoin-max-translation-frac`, default 2.0 frame
  widths, vs 0.92 for adjacent pairs) because a break is normally a fast pan.
- The best candidate by inlier count wins; segments are merged via
  `merge_segments()` (`rel = T_a[ia] @ inv(M3) @ inv(T_b[ib])`), then the pass repeats so
  several boundaries can be recovered in one run. `--no-rejoin` disables it entirely.
- Every attempt (frame pair, detector, matches, inliers, ratio, mean reprojection error,
  tx/ty, accepted + reason) is recorded as a `RejoinAttempt`, dumped to
  `diagnostics/rejoin_attempts.json`, visualised as `matches/rejoin_A_B.png` (own viz budget)
  and tabulated in `report.html`. `transforms.csv` gained a `rejoin` column and
  `transforms.json` a `rejoin_links` list; `registration_stats` gained `rejoins_used`.

**(B) Multi-segment output** (`cli.py`, `diagnostics.py`, `report.py`)
- Step 5 is now a loop over every segment with >= `--min-segment-frames` frames (default 3),
  in descending frame-count order. The largest keeps the exact `--output` path and the exact
  existing diagnostics filenames; the others are written as `<base>_seg02.<ext>`,
  `_seg03`, ... with `panorama_preview_seg02.png` / `camera_path_seg02.png`. A single-segment
  run is byte-for-byte identical in naming and keys to 0.1.0.
- Segments below the threshold are reported explicitly as skipped with frame indices and time
  range (log, `summary.json:skipped_segments`, `report.html`). Only the primary segment can
  fail the memory guard fatally; a secondary segment that would not fit is skipped with a
  warning instead of killing the run.
- `summary.json` gained `segments[]` (rank, frames, index range, time range, pixels, coverage,
  per-segment registration medians, output path), `skipped_segments[]`, `rejoin{}` and the
  counts `segments_written / segments_skipped / frames_written_total / rejoins_accepted /
  rejoin_attempts`. The verdict is now honest about splitting: PARTIALLY SUCCESSFUL with
  "written as N separate pieces" plus, when true, "all X accepted frames are accounted for".

**Measured results**
- `pytest -q`: **30 passed** (22 pre-existing + 8 new in `tests/test_segment_recovery.py`).
  New synthetic fixtures use `scripts/make_synthetic_video.py --gap-after/--gap-x/--gap-y` to
  inject a deliberate gap: one clip whose gap still overlaps (rejoined into a single panorama)
  and one with no shared content (two panoramas). Tests assert the rejoined frame pair, the
  exact output filenames, the unchanged single-segment case, and that `--no-rejoin` restores
  the old behaviour.
- Real video `VID_20260427_103554775.mp4` (`--sampling-fps 2 --detector orb --normalize clahe
  --blur-threshold 10 --blend feather`): 98 candidates, 92 accepted, 90 pairs registered,
  6 SIFT fallbacks. The frame-72 break is **genuinely unrecoverable**: 9 SIFT rejoin attempts
  (frames 70-72 x 73-75), 0 accepted, best 70->75 with 18 matches / 8 inliers (< min 25); the
  `matches/rejoin_00070_00075.png` visualisation shows two different tissue regions, so there
  is no shared overlap to find. Both segments were therefore written:
  `output/vid2_v2.tif` **4228x18224** (70 frames, t=0.0-36.0 s, coverage 54.6 %, median 607
  inliers / 0.698 ratio / 1.49 px) and `output/vid2_v2_seg02.tif` **2623x5250** (22 frames,
  73-97, t=36.5-48.5 s, coverage 73.8 %, median 376 / 0.579 / 1.48 px). 92 of 92 accepted
  frames are now in an output file (previously 70). Verdict PARTIALLY SUCCESSFUL, ~80 s wall.
  Both previews inspected visually: continuous epidermis/dermis in each piece, no ghosting.
- Regression `tesx.mp4` (`--max-dimension 960`, same other flags): unchanged — 51/51 accepted,
  **1 segment of 51 frames**, one output `output/tesx_regression.tif` **7425x1512**, no
  `_seg02` files, no `rejoin_attempts.json`, 0 rejoin attempts, verdict **SUCCESSFUL**.
- Docs: README gained the four new flags in the parameter table, a "Wide-baseline segment
  recovery and multi-segment output" subsection, the new diagnostics files, two new CLI
  examples and a troubleshooting entry "my scan came out in several pieces" (pan slower,
  raise `--sampling-fps`, widen `--rejoin-window`). Version bumped to 0.2.0.

**Limitations**
- Recovery cannot invent overlap: if the fast pan genuinely skipped tissue (as in the real
  video) the scan stays in pieces by design.
- The pass costs up to K^2 SIFT registrations per boundary at full resolution.
- Still no global bundle adjustment; drift within a long segment is unchanged.

## Stage 7 — 2026-08-22 — ffmpeg 8 compatibility fix (0.2.1)
Reported from the user's Manjaro machine: 10 of 30 tests failed, every one of them at frame
extraction, with `Unrecognized option 'vsync'` (exit 3). Cause: `extract.py` hard-coded
`-vsync 0`, which ffmpeg 8 (current on Arch/Manjaro) removed in favour of `-fps_mode`.
The 20 tests that never invoke ffmpeg all passed, so the registration core was unaffected.
Fix:
- New `extract.frame_rate_mode_args(ffmpeg)` (lru_cached) probes `ffmpeg -h full` and returns
  `-fps_mode passthrough`, else `-vsync 0`, else nothing.
- `extract_frames()` builds the command with that flag, logs it, and if ffmpeg still answers
  "Unrecognized option" retries with the other variant and then with no flag at all. The final
  error message now points at a version mismatch explicitly.
- New `tests/test_ffmpeg_compat.py` (4 tests): the flag is negotiated rather than literal, the
  negotiated value agrees with the local `ffmpeg -h full`, it survives a real ffmpeg invocation,
  and a deliberately bogus negotiated flag still extracts via the fallback path.
Verified: 34 passed. Real video re-run (`--max-dimension 960`) now logs `-fps_mode passthrough`
and reproduces the previous result exactly: 93/98 accepted, raw segments [70, 23], rejoin failed,
two panoramas written 2136x9226 and 1328x2650, verdict PARTIALLY SUCCESSFUL.

## Stage 8 — 2026-08-22 — PySide6 desktop GUI (0.3.0)
A front end for daily use, added without touching pipeline behaviour: the GUI builds a command
line, runs `python -m micro_slide_stitcher` as a subprocess, and reads the diagnostics
directory. No pipeline module was modified.

**New** `src/micro_slide_stitcher/gui/`
- `cli_spec.py` — the 12 exposed flags, with defaults, choices and help text pulled out of
  `cli.build_parser()` at import time (it raises if a flag is renamed, so the GUI cannot drift
  from the CLI). Tooltips are the CLI's own help strings.
- `command.py` — argv/command-string builder. Emits only flags that differ from the CLI
  defaults unless asked for the explicit form; shlex-quoted, canonical flag order.
- `runner.py` — `PipelineRunner(QThread)`: `Popen` in its own process group, streams stdout
  (the same formatted lines that go to `pipeline.log`), classifies levels, derives a percentage
  from known log milestones, and cancels with SIGTERM → 5 s → SIGKILL plus a `cancelled.txt`
  marker.
- `results.py` — reads `summary.json` and `frame_metrics.csv` into plain dataclasses and
  formats the summary rows; never re-derives a number the pipeline already reported, and never
  raises (falls back to `failure.txt`).
- `widgets/` — `PanZoomView`/`PanoramaPanel` (off-thread image load, fit/1:1/wheel zoom, refits
  when the tab is first shown), `LogPane` (WARNING/ERROR highlighted), `SettingsPanel`
  (basic + collapsible advanced, tooltips, reset), `FrameQualityTimeline` (custom-painted
  focus-score scatter with a draggable threshold line that writes back into `--blur-threshold`).
- `mainwindow.py` — drag-and-drop input, live command preview with copy, quick-preview and
  full-resolution runs, cancel, progress, four result tabs, per-segment selector, QSettings
  persistence, error banner with a clickable `file://` diagnostics link.
- Entry points: `python -m micro_slide_stitcher.gui` and `micro-slide-stitcher-gui`; PySide6 is
  an optional extra (`.[gui]`) and its absence exits 4 with an install hint while leaving the
  CLI untouched.

**Verified** 62 tests pass (34 pre-existing + 28 new in `tests/test_gui.py`, offscreen). A real
offscreen run against the 2-segment phone video reproduced the CLI result through the GUI
(98 candidates, 93 accepted, 2 segments, 9 failed rejoin attempts, PARTIALLY SUCCESSFUL) and
ten screenshots were inspected; six layout defects found and fixed (stale command preview on
programmatic path changes, hidden command text behind a horizontal scrollbar, a wrapped
`(--diagnostics)` caption, a truncated duration string, fit-to-window not applied to an image
loaded into a hidden tab, elided viewer-button and checkbox labels, and a clipped side panel
once its scrollbar appeared).

**Limitations**
- The frame-quality timeline needs a run (or an existing diagnostics folder): there is no
  gating-only pass, deliberately, because that would duplicate pipeline logic.
- Progress percentage is inferred from log milestones, so it advances in steps, not smoothly.
- Cancel leaves the partial `_work` directory and a `cancelled.txt` behind; nothing else is
  written.
- No queue: one run at a time, and no batch mode.
