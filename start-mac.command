#!/bin/bash
# SkimWatch launcher for macOS and Linux.
# Double-click this file (macOS) or run ./start-mac.command in a terminal.
# The first run creates a private Python environment in .venv and installs
# the requirements; later runs start straight away.

cd "$(dirname "$0")" || exit 1

fail() {
    echo
    echo "ERROR: $1"
    echo
    read -r -p "Press Enter to close this window." _
    exit 1
}

if [ ! -x .venv/bin/python ]; then
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    done
    [ -n "$PY" ] || fail "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
    echo "First run: creating a private Python environment (.venv)..."
    "$PY" -m venv .venv || fail "Could not create the Python environment."
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
    echo "Installing requirements (this takes a few minutes the first time)..."
    .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
    .venv/bin/python -m pip install -r requirements.txt || fail "Could not install the requirements. Check your internet connection and run this again."
    cp requirements.txt .venv/requirements.installed
fi

# Deno is the JavaScript runtime the downloader needs for YouTube links.
# Fetch a pinned, checksummed copy into .venv unless one is already available.
if ! .venv/bin/python -c 'import sys, local_transcribe; sys.exit(0 if local_transcribe.find_js_runtime() else 1)' 2>/dev/null; then
    DENO_VERSION="$(tr -d '[:space:]' < packaging/deno-version.txt)"
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)  DENO_ASSET="deno-aarch64-apple-darwin.zip" ;;
        Darwin-x86_64) DENO_ASSET="deno-x86_64-apple-darwin.zip" ;;
        Linux-x86_64)  DENO_ASSET="deno-x86_64-unknown-linux-gnu.zip" ;;
        Linux-aarch64) DENO_ASSET="deno-aarch64-unknown-linux-gnu.zip" ;;
        *) DENO_ASSET="" ;;
    esac
    echo "Downloading Deno (used for YouTube links)..."
    if [ -n "$DENO_ASSET" ] &&
       curl -fsSL -o .venv/deno.zip "https://github.com/denoland/deno/releases/download/$DENO_VERSION/$DENO_ASSET" &&
       echo "$(grep " $DENO_ASSET\$" packaging/deno-sha256.txt | cut -d' ' -f1)  .venv/deno.zip" | shasum -a 256 -c - >/dev/null &&
       unzip -oq .venv/deno.zip -d .venv/bin; then
        chmod +x .venv/bin/deno
    else
        echo "WARNING: Could not download Deno. YouTube links may fail until you install it from https://deno.com"
    fi
    rm -f .venv/deno.zip
fi

.venv/bin/python webapp.py "$@" || fail "The program stopped unexpectedly."
