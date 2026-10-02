#!/bin/bash
# LinkDrop installer steps for macOS (run by "Install LinkDrop.app" one step at a time).
# Everything is downloaded fresh: LinkDrop itself from GitHub, plus yt-dlp, ffmpeg and deno.
# Usage: install-mac.sh <step>    steps: detect | clean | python | python-pkg | ytdlp | ffmpeg | deno | script
# by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop

set -o pipefail
STEP="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_RAW="https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main"
DATA="$HOME/Library/Application Support/LinkDrop"
BIN="$DATA/bin"
UTIL="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
# Python is mirrored on the LinkDrop GitHub (release "deps"); python.org is the fallback
PY_PKG_URLS=("https://github.com/gabrielxreis/LinkDrop/releases/download/deps/python-3.12.10-macos11.pkg"
             "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg")
PY_PKG_SHA256="8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4"
TMP="${TMPDIR:-/tmp}/linkdrop-install"
mkdir -p "$BIN" "$UTIL" "$TMP" || { echo "Couldn't create the LinkDrop folders." >&2; exit 1; }

get() {
  curl -fL --retry 3 --connect-timeout 20 -sS "$1" -o "$2" \
    || { echo "Couldn't download $1. Check your internet connection and try again." >&2; exit 1; }
}

case "$(uname -m)" in
  arm64) FF_ARCH="arm64"; DENO_ARCH="aarch64" ;;
  *)     FF_ARCH="amd64"; DENO_ARCH="x86_64" ;;
esac

python3_path() {
  ls -d /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 2>/dev/null | sort -V | tail -1
}

case "$STEP" in
  detect)
    # prints the installed LinkDrop version, or nothing
    f="$UTIL/LinkDrop.py"
    if [ -f "$f" ]; then sed -n 's/^VERSION = "\(.*\)"/\1/p' "$f" | head -1; fi
    exit 0
    ;;
  clean)
    # removes the installed LinkDrop and its tools/caches; keeps the person's settings (settings.json)
    rm -f "$UTIL/LinkDrop.py" "$UTIL/Baixar Link para Timeline.py"
    rm -rf "$BIN" "$DATA/icons" "$DATA/thumbs" "$DATA/logs" "$DATA/ytdlp-updated" "$DATA/job.log"
    mkdir -p "$BIN"
    ;;
  python)
    # prints "ok" when a python.org Python 3 (what Resolve uses for scripts) is present, else "missing"
    if [ -n "$(python3_path)" ]; then echo ok; else echo missing; fi
    ;;
  python-pkg)
    # downloads the official installer; the app runs it with administrator rights
    for url in "${PY_PKG_URLS[@]}"; do
      if curl -fL --retry 3 --connect-timeout 20 -sS "$url" -o "$TMP/python.pkg" \
          && [ "$(shasum -a 256 "$TMP/python.pkg" | cut -d' ' -f1)" = "$PY_PKG_SHA256" ]; then
        echo "$TMP/python.pkg"; exit 0
      fi
    done
    echo "Couldn't download Python 3. Check your internet connection and try again." >&2; exit 1
    ;;
  ytdlp)
    # the zipapp runs on Python 3 and starts in under a second
    get "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" "$BIN/yt-dlp.pyz"
    rm -f "$BIN/yt-dlp"
    PY="$(python3_path)"; [ -n "$PY" ] || PY="$(command -v python3)"
    "$PY" "$BIN/yt-dlp.pyz" --version >/dev/null 2>&1 || { echo "yt-dlp didn't start with $PY." >&2; exit 1; }
    # browser impersonation, needed by some sites (TikTok); optional, so a failure here doesn't stop the install
    "$PY" -m pip install --user --quiet --disable-pip-version-check --upgrade curl_cffi >/dev/null 2>&1 || true
    ;;
  ffmpeg)
    for tool in ffmpeg ffprobe; do
      get "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip" "$TMP/$tool.zip"
      unzip -o -q "$TMP/$tool.zip" -d "$BIN" || { echo "Couldn't unpack $tool." >&2; exit 1; }
      chmod +x "$BIN/$tool"
    done
    xattr -dr com.apple.quarantine "$BIN" 2>/dev/null
    "$BIN/ffmpeg" -version >/dev/null 2>&1 || { echo "ffmpeg didn't start on this Mac." >&2; exit 1; }
    ;;
  deno)
    get "https://github.com/denoland/deno/releases/latest/download/deno-$DENO_ARCH-apple-darwin.zip" "$TMP/deno.zip"
    unzip -o -q "$TMP/deno.zip" -d "$BIN" || { echo "Couldn't unpack deno." >&2; exit 1; }
    chmod +x "$BIN/deno"
    xattr -dr com.apple.quarantine "$BIN" 2>/dev/null
    ;;
  script)
    # always the latest LinkDrop from GitHub; the copy inside the app is only an offline fallback
    if curl -fL --retry 3 --connect-timeout 20 -sS "$REPO_RAW/LinkDrop.py?t=$(date +%s)" -o "$TMP/LinkDrop.py" \
        && grep -q '^VERSION = ' "$TMP/LinkDrop.py"; then
      cp "$TMP/LinkDrop.py" "$UTIL/LinkDrop.py" || { echo "Couldn't copy LinkDrop into Resolve's Scripts folder." >&2; exit 1; }
    elif [ -f "$HERE/LinkDrop.py" ]; then
      cp "$HERE/LinkDrop.py" "$UTIL/LinkDrop.py" || { echo "Couldn't copy LinkDrop into Resolve's Scripts folder." >&2; exit 1; }
    else
      echo "Couldn't download LinkDrop from GitHub. Check your internet connection and try again." >&2; exit 1
    fi
    rm -rf "$TMP"
    ;;
  *)
    echo "Unknown step: $STEP" >&2; exit 2 ;;
esac
