#!/usr/bin/env bash
# Rebuild MicroSlideStitcher's onefile tar.gz and AppImage from a clean checkout.
# The version is read from src/micro_slide_stitcher/__init__.py, so it cannot drift from the
# package after a version bump.
# Requires Linux x86_64, Python >=3.10, curl, tar and a compiler-free pip environment.
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=${WORK:-"$ROOT/.build-standalone"}
PYTHON=${PYTHON:-python3}
VENV="$WORK/venv"
TOOLS="$WORK/tools"
APPDIR="$ROOT/packaging/appimage/AppDir"
OUT_ONE="$ROOT/dist/onefile"
OUT_DIR="$ROOT/dist/onedir"
MSS_VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/src/micro_slide_stitcher/__init__.py")
[[ -n "$MSS_VERSION" ]] || { echo "cannot read __version__" >&2; exit 1; }
APPIMAGE_OUT="${APPIMAGE_OUT:-$ROOT/../MicroSlideStitcher-$MSS_VERSION-x86_64.AppImage}"
TARBALL_OUT="${TARBALL_OUT:-$ROOT/../MicroSlideStitcher-$MSS_VERSION-linux-x86_64.tar.gz}"
mkdir -p "$WORK" "$TOOLS"

# PyInstaller 6.22.2 was proven here on Python 3.14.3. If this command fails on a newer Python,
# rerun with PYTHON=python3.12 (or another supported CPython); do not use a system pip.
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$ROOT/requirements.txt" "$ROOT[gui,test]" 'pyinstaller>=6.22'
printf 'print("PyInstaller hello")\n' > "$WORK/hello.py"
"$VENV/bin/pyinstaller" --noconfirm --clean --onefile --distpath "$WORK/hello-dist" \
  --workpath "$WORK/hello-build" "$WORK/hello.py"
"$WORK/hello-dist/hello"

rm -rf "$ROOT/build/onefile" "$OUT_ONE" "$ROOT/build/onedir" "$OUT_DIR"
MSS_PACKAGING_STAGE=final "$VENV/bin/pyinstaller" --noconfirm --clean --distpath "$OUT_ONE" --workpath "$ROOT/build/onefile" \
  "$ROOT/packaging/MicroSlideStitcher.spec"
MSS_PACKAGING_STAGE=final "$VENV/bin/pyinstaller" --noconfirm --clean --distpath "$OUT_DIR" --workpath "$ROOT/build/onedir" \
  "$ROOT/packaging/MicroSlideStitcher-onedir.spec"

APPIMAGETOOL="$TOOLS/appimagetool-x86_64.AppImage"
FFMPEG_TAR="$TOOLS/ffmpeg-release-amd64-static.tar.xz"
if [[ ! -x "$APPIMAGETOOL" ]]; then
  curl -L --fail -o "$APPIMAGETOOL" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$APPIMAGETOOL"
fi
if [[ ! -f "$FFMPEG_TAR" ]]; then
  curl -L --fail -o "$FFMPEG_TAR" https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
fi
rm -rf "$APPDIR" "$WORK/ffmpeg"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/bin" "$APPDIR/usr/share/doc/MicroSlideStitcher" \
  "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$WORK/ffmpeg"
tar -xJf "$FFMPEG_TAR" -C "$WORK/ffmpeg"
FFDIR=$(find "$WORK/ffmpeg" -mindepth 1 -maxdepth 1 -type d | head -1)
cp -a "$OUT_DIR/MicroSlideStitcher" "$APPDIR/usr/lib/MicroSlideStitcher"
install -m755 "$FFDIR/ffmpeg" "$APPDIR/usr/bin/ffmpeg"; install -m755 "$FFDIR/ffprobe" "$APPDIR/usr/bin/ffprobe"
install -m644 "$FFDIR/GPLv3.txt" "$APPDIR/usr/share/doc/MicroSlideStitcher/FFMPEG-GPL-3.0.txt"
install -m644 "$ROOT/LICENSE" "$APPDIR/usr/share/doc/MicroSlideStitcher/LICENSE"
install -m644 "$ROOT/packaging/appimage/NOTICE.txt" "$APPDIR/usr/share/doc/MicroSlideStitcher/NOTICE.txt"
install -m755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m644 "$ROOT/packaging/appimage/MicroSlideStitcher.desktop" "$APPDIR/MicroSlideStitcher.desktop"
install -m644 "$ROOT/packaging/MicroSlideStitcher.png" "$APPDIR/MicroSlideStitcher.png"
install -m644 "$ROOT/packaging/appimage/MicroSlideStitcher.desktop" "$APPDIR/usr/share/applications/MicroSlideStitcher.desktop"
install -m644 "$ROOT/packaging/MicroSlideStitcher.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/MicroSlideStitcher.png"
rm -f "$APPIMAGE_OUT"
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$APPIMAGE_OUT"
chmod +x "$APPIMAGE_OUT"
# The distributable tar.gz around the onefile executable: the binary plus the plain-language
# README, licence, changelog, desktop entry, icon and the ~/.local-only install.sh.
STAGE="$WORK/tarball/MicroSlideStitcher-linux-x86_64"
rm -rf "$WORK/tarball"; mkdir -p "$STAGE"
install -m755 "$OUT_ONE/MicroSlideStitcher" "$STAGE/MicroSlideStitcher"
install -m644 "$ROOT/packaging/README.txt" "$STAGE/README.txt"
install -m644 "$ROOT/LICENSE" "$STAGE/LICENSE"
install -m644 "$ROOT/CHANGELOG.txt" "$STAGE/CHANGELOG.txt"
install -m644 "$ROOT/packaging/MicroSlideStitcher.desktop" "$STAGE/MicroSlideStitcher.desktop"
install -m644 "$ROOT/packaging/MicroSlideStitcher.png" "$STAGE/microslidestitcher.png"
install -m755 "$ROOT/packaging/install.sh" "$STAGE/install.sh"
rm -f "$TARBALL_OUT"
tar -czf "$TARBALL_OUT" -C "$WORK/tarball" MicroSlideStitcher-linux-x86_64

printf 'version: %s\n' "$MSS_VERSION"
printf 'onefile: '; stat -c '%s bytes %n' "$OUT_ONE/MicroSlideStitcher"
printf 'tarball: '; stat -c '%s bytes %n' "$TARBALL_OUT"
printf 'AppImage: '; stat -c '%s bytes %n' "$APPIMAGE_OUT"
sha256sum "$OUT_ONE/MicroSlideStitcher" "$TARBALL_OUT" "$APPIMAGE_OUT"
