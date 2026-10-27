# Changelog

All notable changes to this project are listed here. Versions follow
[semantic versioning](https://semver.org/).

## 1.4.0 — 2026-10-24

First public release, published by Advance Interactive.

- **Family sharing:** videos visitors submit are capped at 3 hours, and the
  message says so. Visitors now see only whether the AI assistant is on and
  which service answers; the owner's model, server address and workspace ID
  are no longer sent to them.
- **Clearer refusals:** a live stream or an over-long video now gets its own
  message instead of "Nothing could be downloaded".
- **Mac app:** declares its real minimum, macOS 14 Sonoma (the bundled
  libraries are built for it); older systems get a clear "can't open" dialog
  instead of a crash.

- **Renamed to SkimWatch** (was Local Transcribe). Settings move from
  `~/.local-transcribe` to `~/.skimwatch` automatically on first start; the
  Mac app now saves to `Documents/SkimWatch`.

- **AI settings button** at the top of the page, so the assistant can be set up
  before the first transcript.
- **Family sharing:** wrong access codes are now limited per visitor, so one
  person can no longer lock everyone out; an overall ceiling still applies.
- **Family sharing:** links from visitors that point to private or local
  network addresses are refused.
- **Family sharing:** visitors can ask the AI assistant questions only when the
  owner turns it on (off by default).
- Mac app: ships `LICENSE.txt` and an itemised `THIRD_PARTY_NOTICES.txt` with
  source links. The app download is distributed under GPL-2.0-or-later terms
  as a combined work because it bundles x264 and x265; the source code of
  SkimWatch stays MIT.
- Project: tests run on macOS, Windows and Linux in GitHub Actions;
  `SECURITY.md`, `CONTRIBUTING.md`, screenshots.

## 1.3.0 — 2026-10-13

- Family sharing: access code, visitor limits, phone-friendly resume.

## 1.2.1 — 2026-10-13

- Support Claude API keys that are not scoped to a workspace.

## 1.2.0 — 2026-10-12

- Optional "Ask about this transcript" panel (Claude, OpenAI, Ollama).

## 1.1.0 — 2026-10-12

- Packaged Mac app that needs no Python.

## 1.0.0 — 2026-10-12

- Local web interface; renamed to Local Transcribe.
- Long recordings are transcribed in ten-minute pieces.
