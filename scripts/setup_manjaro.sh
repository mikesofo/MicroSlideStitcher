#!/usr/bin/env bash
# Idempotent setup helper for MicroSlideStitcher on Manjaro / Arch Linux.
# It PRINTS what it intends to do and never runs pacman without asking you first.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
PY="${PYTHON:-python3}"
SYS_PKGS=(python ffmpeg)

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

cat <<EOF
MicroSlideStitcher setup (Manjaro / Arch)
-----------------------------------------
This script will:
  1. check for the system packages: ${SYS_PKGS[*]}
     (offering: sudo pacman -S --needed ${SYS_PKGS[*]})
  2. create a virtual environment at:
       $VENV
  3. install the Python dependencies from requirements.txt into that venv
     (nothing is installed globally, no --break-system-packages)
  4. install MicroSlideStitcher itself in editable mode
  5. run a quick self-check (pytest -q, optional)

Project dir: $PROJECT_DIR
EOF

# ---------------------------------------------------------------- 1. system pkgs
missing=()
for p in "${SYS_PKGS[@]}"; do
  if pacman -Qi "$p" >/dev/null 2>&1; then
    info "system package present: $p"
  else
    missing+=("$p")
  fi
done

if ((${#missing[@]})); then
  warn "missing system packages: ${missing[*]}"
  echo "    proposed command: sudo pacman -S --needed ${missing[*]}"
  read -r -p "Run it now? [y/N] " ans
  if [[ ${ans:-N} =~ ^[Yy]$ ]]; then
    sudo pacman -S --needed "${missing[@]}"
  else
    warn "skipped; install them yourself before running the CLI"
  fi
fi

# ------------------------------------------------------------------- 2/3. venv
if [[ -x "$VENV/bin/python" ]]; then
  info "venv already exists: $VENV"
else
  info "creating venv at $VENV"
  "$PY" -m venv "$VENV"
fi

info "upgrading pip inside the venv"
"$VENV/bin/pip" install --upgrade pip >/dev/null

info "installing requirements into the venv"
"$VENV/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

cat <<'EOF'

NOTE on OpenCV
--------------
requirements.txt pins opencv-contrib-python-headless (no GUI libs needed, which is
what this project uses - it never opens a window).  On a Manjaro desktop you may
prefer the full build with GUI support:

    .venv/bin/pip uninstall -y opencv-contrib-python-headless
    .venv/bin/pip install opencv-contrib-python

Both provide the same `import cv2` API used here (ORB, SIFT, estimateAffinePartial2D,
detail::MultiBandBlender).  Alternatively Manjaro's own package works too:
    sudo pacman -S --needed opencv python-numpy
but then create the venv with `python -m venv --system-site-packages .venv`.
EOF

# ------------------------------------------------------------------ 4. install
info "installing MicroSlideStitcher (editable)"
"$VENV/bin/pip" install -e "$PROJECT_DIR"

# ----------------------------------------------------------------- 5. selfcheck
read -r -p "Run the test suite now (needs pytest + ffmpeg, ~1 min)? [y/N] " ans
if [[ ${ans:-N} =~ ^[Yy]$ ]]; then
  "$VENV/bin/pip" install pytest >/dev/null
  (cd "$PROJECT_DIR" && "$VENV/bin/python" -m pytest -q)
fi

cat <<EOF

Done. Try:
  cd $PROJECT_DIR
  .venv/bin/python -m micro_slide_stitcher --help
  .venv/bin/python -m micro_slide_stitcher --input ~/scan.mp4 --output ~/pano.tif \\
      --max-dimension 960 --diagnostics ~/msc_diag
EOF
