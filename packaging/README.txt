MicroSlideStitcher 0.7.0 for Linux x86_64
==========================================

This is a practical helper for making one wide picture from a slow smartphone-through-the-
microscope sweep. It is NOT a validated diagnostic whole-slide-imaging device. Do not use it
for primary diagnosis or measurement; return to the glass slide for diagnostic work.

Which one should I use?
------------------------
* MicroSlideStitcher-0.7.0-x86_64.AppImage is usually easiest: make it executable and run it.
  It starts faster and carries ffmpeg/ffprobe for video input. Its bundled ffmpeg/ffprobe are
  GPLv3; the application's own code remains MIT licensed (see the AppImage NOTICE).
* The MicroSlideStitcher-linux-x86_64.tar.gz archive is a smaller single-file program. Extract
  it and run ./MicroSlideStitcher. It needs system ffmpeg only for video input.

Quick start: single-file archive
--------------------------------
1. Extract MicroSlideStitcher-0.7.0-linux-x86_64.tar.gz.
2. In the extracted folder, run:  ./MicroSlideStitcher
3. Or open a video/image folder from a terminal:  ./MicroSlideStitcher /path/to/input
4. Optional: run ./install.sh to add a menu entry for your own user account. No sudo is used.

Video needs ffmpeg and ffprobe (not bundled in the single-file archive):
  Manjaro / Arch:  sudo pacman -S --needed ffmpeg
  Debian / Ubuntu: sudo apt install ffmpeg
  Fedora:          sudo dnf install ffmpeg
  openSUSE:        sudo zypper install ffmpeg
On Arch/Manjaro, if a Qt window will not start under X11, install xcb-util-cursor.

Quick start: AppImage
---------------------
chmod +x MicroSlideStitcher-0.7.0-x86_64.AppImage
./MicroSlideStitcher-0.7.0-x86_64.AppImage
If your Linux system does not provide FUSE 2, use:
./MicroSlideStitcher-0.7.0-x86_64.AppImage --appimage-extract-and-run
The AppImage includes static ffmpeg and ffprobe, so video mode works without installing ffmpeg.

The manual is inside the program
--------------------------------
Press F1, or use Help -> User manual, or click the "User manual (F1)" button at the bottom
right. That opens a ten-section handbook written for pathologists: how to record a scan that
will stitch, choosing between a single sweep and a serpentine (back-and-forth) scan, video
versus still images, a step-by-step first run, how to read the verdict and the report, how to
calibrate the blur threshold, troubleshooting, and every setting the program has. It has a clickable contents list, a Find box and a "Save manual as HTML..." button so you
can e-mail it to a colleague. Read it before your first real scan.

Two ways to work
----------------
VIDEO: choose a video, then use Quick preview first. If it looks sensible, use Full resolution.
IMAGES: choose image folder. This accepts PNG, JPEG, TIFF, BMP or WebP stills and needs no ffmpeg.

New in 0.7.0: tell it how you scanned
-------------------------------------
There is a "Scan type" setting with two options. Leave it on "Single sweep - one continuous
pan" if you panned across the slide once; that is exactly what earlier versions did and the
result is unchanged. Choose "Serpentine - back and forth in rows" if you covered an area by
panning a row, stepping down about half a field so the new row overlaps the row above, and
panning back the other way. Serpentine looks for fields that overlap even though they were not
recorded one after the other, then solves for all the field positions together, which stops
small errors accumulating along one long chain. It takes noticeably longer to process, and if
the refinement fails or would make things worse the program keeps the ordinary result and still
writes your panorama. Section 3 of the built-in manual explains how to record one.

For phone capture: lock focus; set manual white balance against empty glass; pan slowly with
about 50% overlap; and consider still-image bursts when motion blur is a problem. Stills can
produce a better result than video.

Where things go and how to read them
------------------------------------
The panorama goes to the selected Panorama file. A nearby diagnostics folder contains
report.html, a preview, pipeline.log, frame metrics and registration details. Open report.html
when you want the visual summary. SUCCESSFUL means the accepted frames formed a panorama;
PARTIALLY SUCCESSFUL means the report explains an incomplete or split scan; a failure preserves
its diagnostics so you can inspect the exact cause.

Compatibility
-------------
This release's measured packaged-library floor is GLIBC_2.39. It needs Linux x86_64 with glibc
2.39 or newer. Ubuntu 24.04 (glibc 2.39) and Fedora 40+ (2.39 or newer) are supported by that
floor. It excludes Ubuntu 22.04 (2.35), Debian 12 (2.36), and RHEL/Rocky/Alma 9 (2.34), as well
as older releases. AppImage does not lower this requirement; rebuilding on an older base image is
needed for wider compatibility.
