#!/bin/bash
# Builds installer/LinkDrop-Installer-mac.dmg (Install LinkDrop.app on a branded background).
# Needs: macOS, Google Chrome (icon/background renders already in build/), Finder automation allowed.
set -e
cd "$(dirname "$0")"
B=build
APP="$B/Install LinkDrop.app"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' ../LinkDrop.py | head -1)"

rm -rf "$APP"
mkdir -p "$B/swift" "$APP/Contents/MacOS" "$APP/Contents/Resources"
for a in arm64 x86_64; do
  swiftc -O -parse-as-library -target $a-apple-macos13.0 InstallerApp.swift -o "$B/swift/inst-$a"
done
lipo -create "$B/swift/inst-arm64" "$B/swift/inst-x86_64" -output "$APP/Contents/MacOS/Install LinkDrop"
R="$APP/Contents/Resources"
cp install-mac.sh ../LinkDrop.py "$R/"
chmod +x "$R/install-mac.sh"
cp "$B/LinkDropFull.icns" "$R/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Install LinkDrop</string>
  <key>CFBundleDisplayName</key><string>Install LinkDrop</string>
  <key>CFBundleExecutable</key><string>Install LinkDrop</string>
  <key>CFBundleIdentifier</key><string>com.gabrielxreis.linkdrop.installer</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>by @gabrielxreis_</string>
</dict></plist>
PLIST
codesign --force --deep -s - "$APP" 2>/dev/null

rm -rf "$B/stage" "$B/rw.dmg" LinkDrop-Installer-mac.dmg
mkdir -p "$B/stage/.background"
cp -R "$APP" "$B/stage/"
cp "$B/background.tiff" "$B/stage/.background/"
hdiutil create -quiet -srcfolder "$B/stage" -volname "LinkDrop" -fs HFS+ -format UDRW -size 60m "$B/rw.dmg"
DEV=$(hdiutil attach -readwrite -noverify -noautoopen "$B/rw.dmg" 2>/dev/null | grep -E '^/dev/' | head -1 | awk '{print $1}')
osascript <<'EOF'
tell application "Finder"
  tell disk "LinkDrop"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 840, 588}
    set viewOptions to the icon view options of container window
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 128
    set text size of viewOptions to 13
    set background picture of viewOptions to file ".background:background.tiff"
    set position of item "Install LinkDrop.app" of container window to {320, 214}
    update without registering applications
    delay 1
    close
  end tell
end tell
EOF
SetFile -a V /Volumes/LinkDrop/.background 2>/dev/null || true
sync
hdiutil detach -quiet "$DEV"
hdiutil convert -quiet "$B/rw.dmg" -format UDZO -imagekey zlib-level=9 -o LinkDrop-Installer-mac.dmg
rm -f "$B/rw.dmg"
rm -rf "$B/stage"
echo "Built LinkDrop-Installer-mac.dmg (LinkDrop $VERSION)"
