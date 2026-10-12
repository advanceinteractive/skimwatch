#!/usr/bin/env python3
"""
youtube_transcribe.py

Download the audio from a YouTube video and transcribe it locally with
OpenAI Whisper. Outputs both a plain-text transcript (.txt) and a
timestamped subtitle file (.srt).

Usage:
    python youtube_transcribe.py <YOUTUBE_URL> [--model MODEL] [--out DIR]

Examples:
    python youtube_transcribe.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    python youtube_transcribe.py "https://youtu.be/dQw4w9WgXcQ" --model small
    python youtube_transcribe.py "<url>" --model medium --out ~/Desktop/transcripts

Models (size vs. accuracy vs. speed):
    tiny    ~75 MB    fastest, lowest accuracy
    base    ~140 MB   fast, decent accuracy            [default]
    small   ~460 MB   slower, better accuracy
    medium  ~1.5 GB   slow, very good accuracy
    large   ~3 GB     slowest, best accuracy

First-run setup (one-time):
    # Python deps
    pip install --break-system-packages -U yt-dlp openai-whisper

    # ffmpeg (required by Whisper to decode audio)
    brew install ffmpeg               # macOS
    # or: sudo apt-get install ffmpeg # Debian/Ubuntu
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename across OSes."""
    name = re.sub(r"[^\w\s.-]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name[:120] or "transcript"


def format_timestamp_srt(seconds: float) -> str:
    """Convert seconds (float) into SRT timestamp HH:MM:SS,mmm."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def download_audio(url: str, workdir: Path) -> tuple[Path, str]:
    """
    Download the audio track of a YouTube video as an .mp3.

    Returns (audio_path, video_title).
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        sys.exit(
            "ERROR: yt-dlp is not installed in this Python.\n"
            f"  Python being used: {sys.executable}\n"
            f"  Install with:\n"
            f"    \"{sys.executable}\" -m pip install -U yt-dlp openai-whisper"
        )

    out_template = str(workdir / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    print(f"[1/3] Downloading audio from: {url}")
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "audio")
        title = info.get("title", video_id)

    audio_path = workdir / f"{video_id}.mp3"
    if not audio_path.exists():
        # Fallback: pick whatever audio file landed in workdir
        candidates = list(workdir.glob(f"{video_id}.*"))
        if not candidates:
            sys.exit("ERROR: Could not locate downloaded audio file.")
        audio_path = candidates[0]

    return audio_path, title


def transcribe(audio_path: Path, model_name: str) -> dict:
    """Run Whisper transcription locally. Returns the full result dict."""
    try:
        import whisper
    except ImportError:
        sys.exit(
            "ERROR: openai-whisper is not installed.\n"
            "  Install it with:  pip install --break-system-packages -U openai-whisper"
        )

    print(f"[2/3] Loading Whisper model: {model_name} "
          f"(first run downloads it; this may take a minute)")
    model = whisper.load_model(model_name)

    print("[3/3] Transcribing... (this can take a while on CPU)")
    # fp16=False avoids a warning on CPU-only machines.
    result = model.transcribe(str(audio_path), fp16=False, verbose=False)
    return result


def write_outputs(result: dict, out_dir: Path, base_name: str) -> tuple[Path, Path]:
    """Write .txt and .srt files. Returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_path = out_dir / f"{base_name}.txt"
    srt_path = out_dir / f"{base_name}.srt"

    # Plain text
    text = (result.get("text") or "").strip()
    txt_path.write_text(text + "\n", encoding="utf-8")

    # SRT (timestamped subtitles)
    segments = result.get("segments", []) or []
    with srt_path.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            start = format_timestamp_srt(seg.get("start", 0.0))
            end = format_timestamp_srt(seg.get("end", 0.0))
            seg_text = (seg.get("text") or "").strip()
            f.write(f"{i}\n{start} --> {end}\n{seg_text}\n\n")

    return txt_path, srt_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe a YouTube video locally with Whisper."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="YouTube video URL (if omitted, you'll be prompted)",
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "transcripts"),
        help="Output directory (default: ./transcripts next to this script)",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        help="Keep the downloaded .mp3 file alongside the transcripts.",
    )
    args = parser.parse_args()

    out_dir = Path(os.path.expanduser(args.out)).resolve()

    url = args.url
    if not url:
        try:
            url = input("Paste YouTube URL: ").strip()
        except EOFError:
            url = ""
        if not url:
            sys.exit("No URL provided. Exiting.")

    with tempfile.TemporaryDirectory(prefix="yt_transcribe_") as tmp:
        workdir = Path(tmp)
        audio_path, title = download_audio(url, workdir)
        result = transcribe(audio_path, args.model)

        base_name = sanitize_filename(title)
        txt_path, srt_path = write_outputs(result, out_dir, base_name)

        if args.keep_audio:
            kept = out_dir / f"{base_name}.mp3"
            kept.write_bytes(audio_path.read_bytes())

    print()
    print("Done.")
    print(f"  Title : {title}")
    print(f"  Text  : {txt_path}")
    print(f"  SRT   : {srt_path}")


if __name__ == "__main__":
    main()
