#!/bin/bash
# LinkDrop for Premiere Pro: installer steps for macOS (run by "Install LinkDrop for Premiere.app" one step at a time).
# The panel comes from GitHub (premiere/ on main); yt-dlp, ffmpeg, deno and Python are shared with install-mac.sh.
# Usage: install-premiere-mac.sh <step>    steps: detect | clean | python | python-pkg | ytdlp | ffmpeg | deno | script
# by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop

set -o pipefail
STEP="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ZIP="https://codeload.github.com/gabrielxreis/LinkDrop/zip/refs/heads/main"
DATA="$HOME/Library/Application Support/LinkDrop"
BIN="$DATA/bin"
EXT_ID="com.gabrielxreis.linkdrop"
EXT="$HOME/Library/Application Support/Adobe/CEP/extensions/$EXT_ID"
TMP="${TMPDIR:-/tmp}/linkdrop-premiere-install"

case "$STEP" in
  detect)
    # prints the installed panel version, or nothing
    f="$EXT/CSXS/manifest.xml"
    if [ -f "$f" ]; then sed -n 's/.*ExtensionBundleVersion="\([^"]*\)".*/\1/p' "$f" | head -1; fi
    exit 0
    ;;
  clean)
    # removes the installed panel and the shared tools/caches; keeps the license and settings
    rm -rf "$EXT" "$BIN" "$DATA/thumbs" "$DATA/logs" "$DATA/ytdlp-updated"
    mkdir -p "$BIN"
    ;;
  python|python-pkg|ytdlp|ffmpeg|deno)
    exec /bin/bash "$HERE/install-mac.sh" "$STEP"
    ;;
  script)
    # always the latest panel from GitHub; the copy inside the app is only an offline fallback
    rm -rf "$TMP"; mkdir -p "$TMP" "$(dirname "$EXT")" || { echo "Couldn't create Adobe's extensions folder." >&2; exit 1; }
    SRC=""
    if curl -fL --retry 3 --connect-timeout 20 -sS "$REPO_ZIP" -o "$TMP/repo.zip" \
        && unzip -q "$TMP/repo.zip" -d "$TMP/repo" 2>/dev/null; then
      SRC="$(ls -d "$TMP"/repo/*/premiere 2>/dev/null | head -1)"
      [ -f "$SRC/CSXS/manifest.xml" ] || SRC=""
    fi
    if [ -z "$SRC" ] && [ -f "$HERE/premiere/CSXS/manifest.xml" ]; then SRC="$HERE/premiere"; fi
    [ -n "$SRC" ] || { echo "Couldn't download LinkDrop from GitHub. Check your internet connection and try again." >&2; exit 1; }
    rm -rf "$EXT"
    cp -R "$SRC" "$EXT" || { echo "Couldn't copy LinkDrop into Premiere's extensions folder." >&2; exit 1; }
    rm -f "$EXT/.debug"
    xattr -dr com.apple.quarantine "$EXT" 2>/dev/null
    # Premiere loads panels that aren't from the Adobe Exchange only with this switch (one per CEP version)
    for v in 9 10 11 12 13 14; do defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1; done
    rm -rf "$TMP"
    ;;
  *)
    echo "Unknown step: $STEP" >&2; exit 2 ;;
esac
