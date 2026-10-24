#!/usr/bin/env python3
"""
Write THIRD_PARTY_NOTICES.txt for the packaged app: the name, version, license
and license text of every Python package bundled into it, plus the native
libraries that come inside those packages.

    python packaging/third_party_notices.py OUTPUT_FILE [PATH_TO_APP]

With PATH_TO_APP, the native libraries inside the app are listed too, and a
warning is printed if any of them are GPL licensed or not recognised.

Run it with the same Python environment PyInstaller used, so the list matches
what was bundled.
"""

import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Optional

# Bundled build tools that are not part of the app itself.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
        "altgraph", "macholib", "packaging"}

# Native libraries found inside the built app, matched by file name.
# (prefix, component, license, where to get the source)
NATIVE_LIBS = [
    ("libav", "FFmpeg (libavcodec, libavformat, ...)", "LGPL-3.0-or-later",
     "https://ffmpeg.org/download.html and https://github.com/PyAV-Org/pyav-ffmpeg"),
    ("libsw", "FFmpeg (libswresample, libswscale)", "LGPL-3.0-or-later",
     "https://ffmpeg.org/download.html"),
    ("libx264", "x264", "GPL-2.0-or-later", "https://code.videolan.org/videolan/x264"),
    ("libx265", "x265", "GPL-2.0-or-later", "https://bitbucket.org/multicoreware/x265_git"),
    ("libmp3lame", "LAME", "LGPL-2.0-or-later", "https://lame.sourceforge.io/"),
    ("libopencore", "opencore-amr", "Apache-2.0", "https://sourceforge.net/projects/opencore-amr/"),
    ("libopus", "Opus", "BSD-3-Clause", "https://opus-codec.org/"),
    ("libvpx", "libvpx", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libvpx"),
    ("libSvtAv1", "SVT-AV1", "BSD-3-Clause-Clear", "https://gitlab.com/AOMediaCodec/SVT-AV1"),
    ("libdav1d", "dav1d", "BSD-2-Clause", "https://code.videolan.org/videolan/dav1d"),
    ("libwebp", "libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    ("libsharpyuv", "libwebp (sharpyuv)", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    ("libssl", "OpenSSL", "Apache-2.0", "https://www.openssl.org/source/"),
    ("libcrypto", "OpenSSL", "Apache-2.0", "https://www.openssl.org/source/"),
    ("liblzma", "XZ Utils (liblzma)", "0BSD", "https://tukaani.org/xz/"),
    ("libsqlite3", "SQLite", "Public domain", "https://sqlite.org/"),
    ("libmpdec", "mpdecimal", "BSD-2-Clause", "https://www.bytereef.org/mpdecimal/"),
    ("libz.", "zlib", "Zlib", "https://zlib.net/"),
    ("libbz2", "bzip2", "bzip2-1.0.6", "https://sourceware.org/bzip2/"),
    ("libffi", "libffi", "MIT", "https://sourceware.org/libffi/"),
    ("libctranslate2", "CTranslate2", "MIT", "https://github.com/OpenNMT/CTranslate2"),
    ("libonnxruntime", "ONNX Runtime", "MIT", "https://github.com/microsoft/onnxruntime"),
    ("libpng", "libpng", "libpng-2.0", "http://www.libpng.org/"),
    ("libfreetype", "FreeType", "FTL OR GPL-2.0-or-later", "https://freetype.org/"),
    ("libharfbuzz", "HarfBuzz", "MIT", "https://github.com/harfbuzz/harfbuzz"),
    ("libfribidi", "GNU FriBidi", "LGPL-2.1-or-later", "https://github.com/fribidi/fribidi"),
    ("libass", "libass", "ISC", "https://github.com/libass/libass"),
    ("libvmaf", "libvmaf", "BSD-2-Clause-Patent", "https://github.com/Netflix/vmaf"),
]
COPYLEFT = ("GPL-2.0-or-later", "GPL-3.0-or-later")

WEIGHTS = """\
Whisper model weights are downloaded at first use, not bundled. The
faster-whisper conversions by SYSTRAN of OpenAI's Whisper models are MIT
licensed. Python itself is under the Python Software Foundation License.
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


def ffmpeg_version(app: Path) -> str:
    """The FFmpeg version string embedded in the bundled libavcodec, or ""."""
    for lib in app.rglob("libavcodec*"):
        if lib.is_file() and lib.stat().st_size > 100_000:
            data = lib.read_bytes()
            match = re.search(rb"FFmpeg version ([0-9][0-9.]*[0-9])", data)
            if match:
                return match.group(1).decode()
    return ""


def native_section(app: Optional[Path]) -> tuple:
    """List the native libraries actually inside the app. Returns (text, problems)."""
    if not app:
        return "", []
    names = sorted({f.name for f in app.rglob("*.dylib")} | {f.name for f in app.rglob("*.so.*")})
    found, unknown = {}, []
    for name in names:
        match = next((entry for entry in NATIVE_LIBS if name.startswith(entry[0])), None)
        if match:
            found.setdefault(match[1], match)
        else:
            unknown.append(name)
    lines = ["Native libraries bundled in this app", "------------------------------------"]
    for _, component, lic, source in sorted(found.values(), key=lambda e: e[1].lower()):
        lines.append(f"- {component}: {lic}. Source: {source}")
    version = ffmpeg_version(app)
    if version:
        lines += ["",
                  f"The FFmpeg libraries and the codec libraries above are FFmpeg {version}",
                  "as built by the PyAV project. The exact sources and build scripts are",
                  f"published at https://github.com/PyAV-Org/pyav-ffmpeg/releases/tag/{version}-1",
                  "(see that page for the matching build number)."]
    gpl = [c for _, c, lic, _ in found.values() if lic in COPYLEFT]
    if gpl:
        lines += ["",
                  "GPL notice: " + ", ".join(sorted(gpl)) + " are licensed under the GNU GPL",
                  "version 2 or later. Because they are part of this app, the app as a whole",
                  "is distributed to you under the terms of the GNU GPL version 2 or later",
                  "as a combined work. The source code of SkimWatch itself remains",
                  "available under the MIT License at",
                  "https://github.com/advanceinteractive/skimwatch , and the source code of",
                  "every bundled component is available at the links in this file.",
                  "The full text of the GPL is in GPL-2.0.txt next to this file."]
    if unknown:
        lines += ["", "Not identified (check before release): " + ", ".join(unknown)]
    problems = ([f"GPL components bundled: {', '.join(sorted(gpl))}"] if gpl else []) + \
               ([f"unidentified libraries: {', '.join(unknown)}"] if unknown else [])
    return "\n".join(lines) + "\n", problems


def main(out: str, app: Optional[Path] = None) -> None:
    dists = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name and name.lower() not in SKIP:
            dists[name.lower()] = dist
    native, problems = native_section(app)
    parts = [
        "SkimWatch, by Advance Interactive, includes the following\n"
        "third-party software. SkimWatch itself is MIT licensed; see\n"
        "LICENSE.txt.\n",
        native,
        WEIGHTS,
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
                     + (f"\n{url}" if url else "") + "\n\n"
                     + (license_text(dist) or "(no license file shipped)") + "\n")
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out} ({len(dists)} Python packages)")
    for problem in problems:
        print(f"NOTE: {problem}. Make sure the release notes carry the GPL sentence from the README.")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    main(sys.argv[1], Path(sys.argv[2]) if len(sys.argv) == 3 else None)
