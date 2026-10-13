#!/bin/bash
# Build "Local Transcribe.app" for macOS: a double-click app that needs no
# Python on the user's computer.
#
#   ./packaging/build-mac.sh
#
# Result: dist/Local-Transcribe-<version>-mac-<arch>.zip
# The app only runs on the kind of Mac it was built on (Apple Silicon or
# Intel). It is not signed with an Apple developer certificate, so people who
# download it must approve it once in System Settings > Privacy & Security.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
NAME="Local Transcribe"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' local_transcribe.py)"
ARCH="$(uname -m)"
WORK="${BUILD_DIR:-${TMPDIR:-/tmp}/local-transcribe-build}"

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

echo "==> Drawing the icon"
"$WORK/venv/bin/python" packaging/make_icon.py "$WORK/icon.iconset"
iconutil -c icns -o "$WORK/icon.icns" "$WORK/icon.iconset"

echo "==> Running PyInstaller"
"$WORK/venv/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN \
    --windowed \
    --name "$NAME" \
    --icon "$WORK/icon.icns" \
    --osx-bundle-identifier "io.github.tonyb82.localtranscribe" \
    --add-data "$ROOT/web:web" \
    --collect-data faster_whisper \
    --exclude-module torch \
    --exclude-module tkinter \
    --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
    app.py

APP="$WORK/dist/$NAME.app"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP/Contents/Info.plist" 2>/dev/null ||
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 11.0" "$APP/Contents/Info.plist" 2>/dev/null || true
# Editing Info.plist invalidates PyInstaller's signature; sign again (ad hoc).
codesign --force --deep --sign - "$APP"

mkdir -p "$ROOT/dist"
ZIP="$ROOT/dist/Local-Transcribe-$VERSION-mac-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Done: $ZIP ($(du -h "$ZIP" | cut -f1))"
echo "    App for testing: $APP"
