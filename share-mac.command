#!/bin/bash
# Local Transcribe: family sharing (macOS and Linux).
# Starts the program so that people you give the access code to can use it
# from their phones through a tunnel. See "Share with your family" in README.
exec "$(dirname "$0")/start-mac.command" --share --no-browser "$@"
