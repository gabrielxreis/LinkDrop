#!/bin/bash
# LinkDrop installer for DaVinci Resolve (macOS)
# by @gabrielxreis_  ·  https://github.com/gabrielxreis/LinkDrop

set -e
REPO_RAW="https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main/LinkDrop.py"
DEST="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

echo ""
echo "  ⬇  LinkDrop for DaVinci Resolve"
echo "  ---------------------------------"
echo ""

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)"
  done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "  Installing Homebrew (it may ask for your Mac password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)"
  done
fi

# 2. yt-dlp, ffmpeg and deno (YouTube needs a JS runtime)
for pkg in yt-dlp ffmpeg deno; do
  if brew list "$pkg" >/dev/null 2>&1; then
    echo "  ✓ $pkg already installed, updating..."
    brew upgrade "$pkg" >/dev/null 2>&1 || true
  else
    echo "  Installing $pkg..."
    brew install "$pkg"
  fi
done

# 3. The script itself
mkdir -p "$DEST"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SRC_DIR/LinkDrop.py" ]; then
  cp "$SRC_DIR/LinkDrop.py" "$DEST/LinkDrop.py"
else
  curl -fsSL "$REPO_RAW" -o "$DEST/LinkDrop.py"
fi
echo "  ✓ LinkDrop installed"

echo ""
echo "  All set! In DaVinci Resolve open:"
echo "  Workspace > Scripts > Utility > LinkDrop"
echo ""
echo "  (Restart Resolve if it was open.)"
echo ""
read -n 1 -s -r -p "  Press any key to close..."
