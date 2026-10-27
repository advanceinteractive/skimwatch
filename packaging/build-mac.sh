#!/bin/bash
# Build "SkimWatch.app" for macOS: a double-click app that needs no
# Python on the user's computer.
#
#   ./packaging/build-mac.sh
#
# Result: dist/SkimWatch-<version>-mac-<arch>.zip
# The app only runs on the kind of Mac it was built on (Apple Silicon or
# Intel). It is not signed with an Apple developer certificate, so people who
# download it must approve it once in System Settings > Privacy & Security.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
NAME="SkimWatch"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' local_transcribe.py)"
ARCH="$(uname -m)"
WORK="${BUILD_DIR:-${TMPDIR:-/tmp}/skimwatch-build}"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    done
fi
[ -n "$PY" ] || { echo "Python 3.10 or newer is needed to build."; exit 1; }

echo "==> Building $NAME $VERSION for $ARCH in $WORK"
rm -rf "$WORK"
mkdir -p "$WORK"

# A clean environment, so nothing unrelated gets bundled.
"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install --quiet --upgrade pip
"$WORK/venv/bin/python" -m pip install --quiet -r requirements.txt pyinstaller pyobjc-framework-Cocoa

# Deno: the JavaScript runtime yt-dlp needs for YouTube. Pinned and checksummed.
DENO_VERSION="$(tr -d '[:space:]' < packaging/deno-version.txt)"
case "$ARCH" in
    arm64) DENO_ASSET="deno-aarch64-apple-darwin.zip" ;;
    x86_64) DENO_ASSET="deno-x86_64-apple-darwin.zip" ;;
    *) echo "No Deno build for $ARCH"; exit 1 ;;
esac
echo "==> Fetching Deno $DENO_VERSION"
curl -fsSL -o "$WORK/deno.zip" "https://github.com/denoland/deno/releases/download/$DENO_VERSION/$DENO_ASSET"
echo "$(grep " $DENO_ASSET\$" packaging/deno-sha256.txt | cut -d' ' -f1)  $WORK/deno.zip" | shasum -a 256 -c - >/dev/null ||
    { echo "Deno download did not match packaging/deno-sha256.txt"; exit 1; }
mkdir -p "$WORK/deno"
unzip -oq "$WORK/deno.zip" -d "$WORK/deno"
chmod +x "$WORK/deno/deno"
curl -fsSL -o "$WORK/deno/LICENSE.md" "https://raw.githubusercontent.com/denoland/deno/$DENO_VERSION/LICENSE.md"
"$WORK/deno/deno" --version | head -1

echo "==> Drawing the icon"
"$WORK/venv/bin/python" packaging/make_icon.py "$WORK/icon.iconset"
iconutil -c icns -o "$WORK/icon.icns" "$WORK/icon.iconset"

echo "==> Running PyInstaller"
"$WORK/venv/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN \
    --windowed \
    --name "$NAME" \
    --icon "$WORK/icon.icns" \
    --osx-bundle-identifier "com.advanceinteractive.skimwatch" \
    --add-data "$ROOT/web:web" \
    --collect-data faster_whisper \
    --collect-all yt_dlp_ejs \
    --add-binary "$WORK/deno/deno:bin" \
    --exclude-module torch \
    --exclude-module tkinter \
    --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
    app.py

APP="$WORK/dist/$NAME.app"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP/Contents/Info.plist" 2>/dev/null ||
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 14.0" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string \"© $(date +%Y) Advance Interactive (SQLPRO LLC). MIT License; bundled components under their own licenses, see THIRD_PARTY_NOTICES.txt.\"" "$APP/Contents/Info.plist" 2>/dev/null || true
echo "==> Writing third-party notices"
"$WORK/venv/bin/python" packaging/third_party_notices.py "$APP/Contents/Resources/THIRD_PARTY_NOTICES.txt" "$APP"
cp LICENSE "$APP/Contents/Resources/LICENSE.txt"
cp packaging/GPL-2.0.txt "$APP/Contents/Resources/GPL-2.0.txt"
cp "$WORK/deno/LICENSE.md" "$APP/Contents/Resources/DENO-LICENSE.txt"

# Editing Info.plist invalidates PyInstaller's signature; sign again (ad hoc).
codesign --force --deep --sign - "$APP"

mkdir -p "$ROOT/dist"
ZIP="$ROOT/dist/SkimWatch-$VERSION-mac-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Done: $ZIP ($(du -h "$ZIP" | cut -f1))"
echo
echo "    Release notes for GitHub: see packaging/RELEASE_NOTES_TEMPLATE.md"
echo "    App for testing: $APP"
