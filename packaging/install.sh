#!/usr/bin/env bash
# Per-user desktop integration for the extracted MicroSlideStitcher distribution.
set -euo pipefail
self_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
binary="$self_dir/MicroSlideStitcher"
icon="$self_dir/MicroSlideStitcher.png"
name=MicroSlideStitcher
local_root="$HOME/.local"
bin_dir="$local_root/bin"
app_dir="$local_root/share/applications"
icon_dir="$local_root/share/icons/hicolor/256x256/apps"
launcher="$bin_dir/$name"
desktop="$app_dir/$name.desktop"
installed_icon="$icon_dir/$name.png"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f -- "$launcher" "$desktop" "$installed_icon"
  echo "Removed MicroSlideStitcher from $local_root."
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0 [--uninstall]" >&2
  exit 2
fi
for f in "$binary" "$icon"; do
  [[ -f "$f" ]] || { echo "Missing distribution file: $f" >&2; exit 2; }
done
mkdir -p -- "$bin_dir" "$app_dir" "$icon_dir"
install -m 755 -- "$binary" "$launcher"
install -m 644 -- "$icon" "$installed_icon"
cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=MicroSlideStitcher
Comment=Stitch microscope video or overlapping still images into a panorama
Exec=$launcher --gui
Icon=MicroSlideStitcher
Terminal=false
Categories=Science;Graphics;MedicalSoftware;
MimeType=video/mp4;video/quicktime;image/png;image/jpeg;image/tiff;
EOF
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  echo "Warning: $bin_dir is not on PATH; log out and in or add it to your shell PATH."
fi
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg and ffprobe are available for video input."
else
  echo "ffmpeg/ffprobe not found: image folders work; video input needs the ffmpeg package."
fi
echo "Installed: $launcher"
echo "Desktop file: $desktop"
