#!/bin/bash
# LinkDrop installer for DaVinci Resolve on macOS
# by @gabrielxreis_  ·  https://github.com/gabrielxreis/LinkDrop
#
# Installs everything LinkDrop needs into its own folder (no Homebrew, no admin rights
# unless Python has to be installed):
#   ~/Library/Application Support/LinkDrop/bin  ->  yt-dlp, ffmpeg, ffprobe, deno
#   Resolve's Scripts/Utility folder            ->  LinkDrop.py

REPO_RAW="https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main"
DATA="$HOME/Library/Application Support/LinkDrop"
BIN="$DATA/bin"
UTIL="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
PY_PKG="https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "\n  \033[31m✗ %s\033[0m\n\n" "$1"; read -n 1 -s -r -p "  Press any key to close..."; exit 1; }
get()  { curl -fL --retry 3 --connect-timeout 20 -sS "$1" -o "$2" || fail "Couldn't download $1. Check your internet connection and try again."; }

clear
echo ""
bold "  LinkDrop for DaVinci Resolve"
echo "  Paste a link, get the clip on your timeline."
echo ""

case "$(uname -m)" in
  arm64) FF_ARCH="arm64"; DENO_ARCH="aarch64" ;;
  *)     FF_ARCH="amd64"; DENO_ARCH="x86_64" ;;
esac

[ -d "/Applications/DaVinci Resolve" ] || echo "  Note: DaVinci Resolve wasn't found in /Applications. LinkDrop will still be installed."

mkdir -p "$BIN" "$UTIL" || fail "Couldn't create the LinkDrop folders."

# 1. Python 3 from python.org (Resolve needs it to run .py scripts)
if ls /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 >/dev/null 2>&1; then
  ok "Python 3 is installed"
else
  echo "  Installing Python 3 (Resolve needs it for scripts). macOS will ask for your password."
  get "$PY_PKG" "$TMP/python.pkg"
  osascript -e "do shell script \"installer -pkg '$TMP/python.pkg' -target /\" with administrator privileges" >/dev/null \
    || fail "Python wasn't installed. Run the installer again and enter your password when asked."
  ok "Python 3 installed"
fi

# 2. yt-dlp (downloader)
echo "  Downloading yt-dlp..."
get "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos" "$BIN/yt-dlp"
chmod +x "$BIN/yt-dlp"
ok "yt-dlp $("$BIN/yt-dlp" --version 2>/dev/null)"

# 3. ffmpeg + ffprobe (merging and converting)
echo "  Downloading ffmpeg..."
for tool in ffmpeg ffprobe; do
  get "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip" "$TMP/$tool.zip"
  unzip -o -q "$TMP/$tool.zip" -d "$BIN" || fail "Couldn't unpack $tool."
  chmod +x "$BIN/$tool"
done
"$BIN/ffmpeg" -version >/dev/null 2>&1 || fail "ffmpeg didn't start on this Mac."
ok "ffmpeg and ffprobe"

# 4. deno (YouTube needs a JavaScript runtime)
echo "  Downloading deno..."
get "https://github.com/denoland/deno/releases/latest/download/deno-$DENO_ARCH-apple-darwin.zip" "$TMP/deno.zip"
unzip -o -q "$TMP/deno.zip" -d "$BIN" || fail "Couldn't unpack deno."
chmod +x "$BIN/deno"
ok "deno"

xattr -dr com.apple.quarantine "$BIN" 2>/dev/null

# 5. LinkDrop itself
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SRC_DIR/LinkDrop.py" ]; then
  cp "$SRC_DIR/LinkDrop.py" "$UTIL/LinkDrop.py"
else
  get "$REPO_RAW/LinkDrop.py" "$UTIL/LinkDrop.py"
fi
ok "LinkDrop added to DaVinci Resolve"

echo ""
bold "  All set!"
echo "  In DaVinci Resolve, open:  Workspace > Scripts > LinkDrop"
echo "  (If Resolve was open, restart it once.)"
echo ""
echo "  LinkDrop updates itself every time it opens."
echo "  Made by @gabrielxreis_  ·  instagram.com/gabrielxreis_"
echo ""
read -n 1 -s -r -p "  Press any key to close..."
echo ""
