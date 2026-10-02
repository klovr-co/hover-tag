#!/bin/sh
# Builds a prototype "Tag.app" (unsigned) into installer/macos/build/.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
app="$here/build/Tag.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swiftc -enable-bare-slash-regex -parse-as-library -O "$here"/*.swift -o "$app/Contents/MacOS/Tag"
cp "$here/../../assets/branding/tag-icon.png" "$app/Contents/Resources/"
cp "$here"/preview-assets/*.jpg "$app/Contents/Resources/"
# Dock and Finder icon, rendered from the same artwork at every size macOS asks for.
iconset="$here/build/AppIcon.iconset"
rm -rf "$iconset" && mkdir -p "$iconset"
swiftc -O "$here/tools/make-icon.swift" -o "$here/build/make-icon"
"$here/build/make-icon" "$here/../../assets/branding/tag-icon.png" "$here/build/AppIcon-1024.png"
for size in 16 32 128 256 512; do
    sips -z $size $size "$here/build/AppIcon-1024.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) "$here/build/AppIcon-1024.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleExecutable</key><string>Tag</string>
<key>CFBundleIdentifier</key><string>team.hover.tag</string>
<key>CFBundleName</key><string>Tag</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSAppleEventsUsageDescription</key><string>Opens Terminal to run tag setup.</string>
</dict></plist>
PLIST
printf 'Built %s\n' "$app"
