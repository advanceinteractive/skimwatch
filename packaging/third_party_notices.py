#!/usr/bin/env python3
"""
Write THIRD_PARTY_NOTICES.txt for the packaged app: the name, version, license
and license text of every Python package bundled into it, plus the native
libraries that come inside those packages.

    python packaging/third_party_notices.py OUTPUT_FILE

Run it with the same Python environment PyInstaller used, so the list matches
what was bundled.
"""

import sys
from importlib import metadata
from pathlib import Path

# Bundled build tools that are not part of the app itself.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
        "altgraph", "macholib", "packaging"}

NATIVE = """\
Native libraries shipped inside the packages above
--------------------------------------------------
- FFmpeg (libavcodec, libavformat, libavutil, libswresample, ...), bundled by
  PyAV ("av"). Licensed under the GNU LGPL v2.1 or later. Source code:
  https://ffmpeg.org/download.html . You may replace these libraries inside
  "Local Transcribe.app/Contents/Resources" with your own compatible build.
- dav1d (BSD 2-Clause), libwebp (BSD 3-Clause) and other codec libraries
  bundled by PyAV; see https://github.com/PyAV-Org/pyav-ffmpeg for the exact
  build and licenses.
- OpenSSL (Apache License 2.0), bundled with Python.
- CTranslate2 (MIT), bundled by ctranslate2.
- ONNX Runtime (MIT), bundled by onnxruntime.
- Python itself (Python Software Foundation License).

Whisper model weights are downloaded at first use, not bundled. The
faster-whisper conversions by SYSTRAN of OpenAI's Whisper models are MIT
licensed.
"""


def license_text(dist: metadata.Distribution) -> str:
    texts = []
    for file in dist.files or []:
        name = file.name.upper()
        if name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            try:
                texts.append(Path(file.locate()).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(texts).strip()


def main(out: str) -> None:
    dists = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name and name.lower() not in SKIP:
            dists[name.lower()] = dist
    parts = [
        "Local Transcribe includes the following third-party software.\n"
        "Local Transcribe itself is MIT licensed; see LICENSE.\n",
        NATIVE,
    ]
    for key in sorted(dists):
        dist = dists[key]
        meta = dist.metadata
        lic = (meta.get("License-Expression") or meta.get("License") or "").strip()
        if not lic or len(lic) > 200:
            classifiers = [c.split("::")[-1].strip()
                           for c in meta.get_all("Classifier") or [] if c.startswith("License")]
            lic = ", ".join(classifiers) or lic[:200] or "see text below"
        header = f"{meta['Name']} {dist.version} -- {lic}"
        url = meta.get("Home-page") or ""
        parts.append("=" * len(header) + "\n" + header + "\n" + "=" * len(header)
                     + (f"\n{url}" if url else "") + "\n\n" + (license_text(dist) or "(no license file shipped)") + "\n")
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out} ({len(dists)} packages)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
