#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/macos-installer/RAGChatSetup.swift"
APP="$ROOT/RAG Chat Setup.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
EXECUTABLE="$MACOS/RAG Chat Setup"
TMPDIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

rm -rf "$APP"
mkdir -p "$MACOS" "$RESOURCES"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>RAG Chat Setup</string>
  <key>CFBundleExecutable</key>
  <string>RAG Chat Setup</string>
  <key>CFBundleIdentifier</key>
  <string>local.rag-chat.setup</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>RAG Chat Setup</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

printf 'APPL????\n' > "$CONTENTS/PkgInfo"

SDK="$(xcrun --sdk macosx --show-sdk-path)"
swiftc "$SRC" -sdk "$SDK" -target arm64-apple-macos12.0 -O -o "$TMPDIR/RAG Chat Setup-arm64"
swiftc "$SRC" -sdk "$SDK" -target x86_64-apple-macos12.0 -O -o "$TMPDIR/RAG Chat Setup-x86_64"
lipo -create "$TMPDIR/RAG Chat Setup-arm64" "$TMPDIR/RAG Chat Setup-x86_64" -output "$EXECUTABLE"
chmod +x "$EXECUTABLE"

codesign --force --deep --sign - "$APP"
xattr -d -r com.apple.quarantine "$APP" >/dev/null 2>&1 || true

echo "Built $APP"
