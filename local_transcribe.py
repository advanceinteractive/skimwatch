#!/usr/bin/env python3
"""
local_transcribe.py

Download the audio from a YouTube (or other yt-dlp supported) video and
transcribe it locally with Whisper. Nothing is sent to a cloud service.

This file is both the command-line tool and the engine behind the local web
interface in webapp.py.

Usage:
    python local_transcribe.py <VIDEO_URL> [--model MODEL] [--language CODE]
                                 [--out DIR] [--keep-audio]

Examples:
    python local_transcribe.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    python local_transcribe.py "https://youtu.be/dQw4w9WgXcQ" --model small
    python local_transcribe.py "<url>" --model turbo --out ~/Desktop/transcripts

Setup (one-time, needs Python 3.10 or newer):
    python -m venv .venv
    .venv/bin/pip install -r requirements.txt        # macOS / Linux
    .venv\\Scripts\\pip install -r requirements.txt    # Windows

ffmpeg is NOT required: audio is decoded by the library bundled with
faster-whisper.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

__version__ = "1.0.0"

# Model downloads work without a Hugging Face account; hide its sign-in nag.
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

# (name, description) in the order shown to the user.
MODELS = [
    ("tiny", "Fastest, rough accuracy (~75 MB download)"),
    ("base", "Fast, decent accuracy (~145 MB download)"),
    ("small", "Balanced speed and accuracy (~480 MB download)"),
    ("medium", "Slow, very good accuracy (~1.5 GB download)"),
    ("turbo", "Near-best accuracy, far faster than large (~1.6 GB download)"),
    ("large-v3", "Slowest, best accuracy (~3 GB download)"),
]
MODEL_NAMES = [name for name, _ in MODELS]
MODEL_ALIASES = {"large": "large-v3"}
DEFAULT_MODEL = "base"

# Common languages for the web UI; the CLI accepts any Whisper language code.
LANGUAGES = [
    ("auto", "Auto-detect"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("sv", "Swedish"),
    ("uk", "Ukrainian"),
    ("ru", "Russian"),
    ("tr", "Turkish"),
    ("ar", "Arabic"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
]

# Languages written without spaces between words.
_UNSPACED_LANGUAGES = {"zh", "ja", "th", "yue"}
_SENTENCE_END = ".!?…。！？"
_CLOSERS = "\"'”’)]»"

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "transcripts"

ProgressFn = Callable[[str, Optional[float], str], None]


class TranscribeError(Exception):
    """A problem worth showing to the user as a plain message."""


class Cancelled(Exception):
    """Raised when the caller asked the running job to stop."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename across OSes."""
    name = re.sub(r"[^\w\s.-]", "", name or "", flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name[:120].rstrip("._") or "transcript"


def format_timestamp_srt(seconds: Optional[float]) -> str:
    """Convert seconds (float) into SRT timestamp HH:MM:SS,mmm."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_clock(seconds: Optional[float]) -> str:
    """Convert seconds into a short reading timestamp: M:SS or H:MM:SS."""
    total = int(seconds or 0)
    if total < 0:
        total = 0
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def validate_url(url: str) -> str:
    """Return the cleaned URL, or raise TranscribeError if it isn't a web link."""
    url = (url or "").strip()
    if not url:
        raise TranscribeError("Please paste a video link.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in (parsed.hostname or ""):
        raise TranscribeError(
            "That doesn't look like a video link. It should start with https://"
        )
    return url


def resolve_model(name: str) -> str:
    name = (name or DEFAULT_MODEL).strip().lower()
    name = MODEL_ALIASES.get(name, name)
    if name not in MODEL_NAMES:
        raise TranscribeError(
            f"Unknown model '{name}'. Choose one of: {', '.join(MODEL_NAMES)}"
        )
    return name


def _clean_error(exc: BaseException) -> str:
    """Strip yt-dlp's colour codes and 'ERROR:' prefix from an error message."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    return msg or exc.__class__.__name__


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

_PARTIAL_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp"}


class _SilentLogger:
    """Keeps yt-dlp from printing; failures are reported through exceptions."""

    def debug(self, msg: str) -> None:
        pass

    warning = error = debug


def download_audio(
    url: str,
    workdir: Path,
    progress: Optional[ProgressFn] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Download the audio track of a video into workdir.

    Returns a dict with: path, id, title, duration, uploader, webpage_url.
    """
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import match_filter_func
    except ImportError:
        raise TranscribeError(
            "yt-dlp is not installed in this Python "
            f"({sys.executable}). Install the requirements with:\n"
            f'  "{sys.executable}" -m pip install -r requirements.txt'
        )

    def hook(d: dict) -> None:
        if should_cancel and should_cancel():
            raise Cancelled()
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            progress("downloading", (done / total) if total else None, "")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        # A watch link copied from inside a playlist means "this video".
        "noplaylist": True,
        # A pure playlist link: take the first video rather than everything.
        "playlist_items": "1",
        "match_filter": match_filter_func("!is_live"),
        "progress_hooks": [hook],
        "retries": 3,
    }

    info = None
    for attempt in (1, 2):
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Cancelled:
            raise
        except Exception as exc:  # yt-dlp raises many types; all mean "no audio"
            if should_cancel and should_cancel():
                raise Cancelled()
            # Sites occasionally hand out a media address that is refused
            # moments later; asking again gets a fresh one.
            if attempt == 1 and "HTTP Error 403" in str(exc):
                continue
            raise TranscribeError(f"Could not download that video: {_clean_error(exc)}")

    if info and info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else None
    if not info:
        raise TranscribeError(
            "Nothing could be downloaded from that link. Live streams are not "
            "supported; try again once the stream has ended."
        )

    audio_path = None
    for item in info.get("requested_downloads") or []:
        candidate = item.get("filepath")
        if candidate and Path(candidate).exists():
            audio_path = Path(candidate)
            break
    if audio_path is None:
        # Fallback: whatever finished file landed in workdir.
        candidates = sorted(
            (p for p in workdir.iterdir()
             if p.is_file() and p.suffix.lower() not in _PARTIAL_SUFFIXES),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if not candidates:
            raise TranscribeError(
                "Nothing could be downloaded from that link. Live streams are "
                "not supported; try again once the stream has ended."
            )
        audio_path = candidates[0]

    video_id = str(info.get("id") or "video")
    return {
        "path": audio_path,
        "id": video_id,
        "title": info.get("title") or video_id,
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "webpage_url": info.get("webpage_url") or url,
    }


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model_cache: dict = {}  # holds at most one loaded model


def load_model(model_name: str, device: str = "auto"):
    """Load (and cache) a Whisper model. Downloads it on first use."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscribeError(
            "faster-whisper is not installed in this Python "
            f"({sys.executable}). Install the requirements with:\n"
            f'  "{sys.executable}" -m pip install -r requirements.txt'
        )

    key = (model_name, device)
    with _model_lock:
        if key in _model_cache:
            return _model_cache[key]
        _model_cache.clear()  # free the previous model before loading another
        try:
            try:
                model = WhisperModel(model_name, device=device, compute_type="auto")
            except Exception:
                if device == "cpu":
                    raise
                # e.g. an NVIDIA card without the CUDA libraries installed
                model = WhisperModel(model_name, device="cpu", compute_type="auto")
                key = (model_name, "cpu")
        except Exception as exc:
            raise TranscribeError(
                f"Could not load the '{model_name}' speech model. It is downloaded "
                "the first time it is used, so check your internet connection and "
                f"try again.\nDetails: {_clean_error(exc)}"
            )
        _model_cache[key] = model
        return model


def transcribe(
    audio_path: Path,
    model_name: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    on_segment: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Run Whisper locally.

    Returns {"text", "segments": [{"start", "end", "text"}], "language",
    "duration"}.
    """
    model_name = resolve_model(model_name)
    if language in (None, "", "auto"):
        language = None

    def run(device: str) -> dict:
        if progress:
            progress("loading_model", None, model_name)
        model = load_model(model_name, device)
        if should_cancel and should_cancel():
            raise Cancelled()

        if progress:
            progress("transcribing", 0.0, "")
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        duration = float(getattr(info, "duration", 0) or 0)
        segments = []
        for seg in segments_iter:
            if should_cancel and should_cancel():
                raise Cancelled()
            text = (seg.text or "").strip()
            if not text:
                continue
            item = {"start": float(seg.start), "end": float(seg.end), "text": text}
            segments.append(item)
            if on_segment:
                on_segment(item)
            if progress:
                progress(
                    "transcribing",
                    min(seg.end / duration, 1.0) if duration else None,
                    "",
                )
        lang = getattr(info, "language", None) or language or ""
        joiner = "" if lang in _UNSPACED_LANGUAGES else " "
        return {
            "text": joiner.join(s["text"] for s in segments),
            "segments": segments,
            "language": lang,
            "duration": duration,
        }

    try:
        return run("auto")
    except (Cancelled, TranscribeError):
        raise
    except Exception as exc:
        # A GPU that loads but fails at run time (missing cuDNN etc.): use CPU.
        if any(key[1] != "cpu" for key in _model_cache):
            try:
                return run("cpu")
            except (Cancelled, TranscribeError):
                raise
            except Exception as exc2:
                exc = exc2
        raise TranscribeError(f"Transcription failed: {_clean_error(exc)}")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def build_paragraphs(
    segments: list,
    language: str = "",
    target_chars: int = 480,
    max_chars: int = 1100,
    pause: float = 2.0,
) -> list:
    """
    Group Whisper segments into readable paragraphs.

    A paragraph ends at a sentence boundary once it is long enough or the
    speaker pauses, and is force-split if no sentence boundary turns up.
    Returns [{"start": seconds, "text": str}].
    """
    joiner = "" if language in _UNSPACED_LANGUAGES else " "
    if joiner == "":
        target_chars, max_chars = target_chars // 3, max_chars // 3

    paragraphs = []
    buf: list = []
    start = 0.0
    length = 0

    def flush() -> None:
        nonlocal buf, length
        if buf:
            paragraphs.append({"start": start, "text": joiner.join(buf)})
        buf, length = [], 0

    usable = [s for s in segments if (s.get("text") or "").strip()]
    for i, seg in enumerate(usable):
        text = seg["text"].strip()
        if not buf:
            start = float(seg.get("start") or 0.0)
        buf.append(text)
        length += len(text) + len(joiner)

        gap = 0.0
        if i + 1 < len(usable):
            gap = float(usable[i + 1].get("start") or 0.0) - float(seg.get("end") or 0.0)
        ends_sentence = text.rstrip(_CLOSERS)[-1:] in _SENTENCE_END
        if (ends_sentence and (length >= target_chars or gap >= pause)) or length >= max_chars:
            flush()
    flush()
    return paragraphs


def format_transcript(
    paragraphs: list,
    title: str = "",
    source_url: str = "",
    timestamps: bool = False,
) -> str:
    """Render paragraphs as a plain-text document."""
    lines = []
    if title:
        lines += [title, "=" * min(len(title), 78)]
    if source_url:
        lines.append(f"Source: {source_url}")
    if lines:
        lines.append("")
    for para in paragraphs:
        prefix = f"[{format_clock(para['start'])}] " if timestamps else ""
        lines += [prefix + para["text"], ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def format_srt(segments: list) -> str:
    """Render segments as an SRT subtitle file."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        start = format_timestamp_srt(seg.get("start", 0.0))
        end = format_timestamp_srt(seg.get("end", 0.0))
        blocks.append(f"{i}\n{start} --> {end}\n{(seg.get('text') or '').strip()}\n")
    return "\n".join(blocks)


def write_outputs(
    result: dict,
    out_dir: Path,
    base_name: str,
    title: str = "",
    source_url: str = "",
) -> tuple:
    """Write .txt and .srt files. Returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f"{base_name}.txt"
    srt_path = out_dir / f"{base_name}.srt"

    paragraphs = result.get("paragraphs")
    if paragraphs is None:
        paragraphs = build_paragraphs(result.get("segments") or [], result.get("language") or "")
    txt_path.write_text(
        format_transcript(paragraphs, title, source_url), encoding="utf-8"
    )
    srt_path.write_text(format_srt(result.get("segments") or []), encoding="utf-8")
    return txt_path, srt_path


# ---------------------------------------------------------------------------
# Whole pipeline (used by the CLI and the web app)
# ---------------------------------------------------------------------------

def run_pipeline(
    url: str,
    model_name: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    out_dir: Optional[Path] = None,
    keep_audio: bool = False,
    progress: Optional[ProgressFn] = None,
    on_info: Optional[Callable[[dict], None]] = None,
    on_segment: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Download, transcribe, format and save. Returns a dict with the video
    details, segments, paragraphs, text, srt and the saved file paths.
    """
    url = validate_url(url)
    model_name = resolve_model(model_name)
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR

    with tempfile.TemporaryDirectory(prefix="local_transcribe_") as tmp:
        if progress:
            progress("downloading", None, "")
        media = download_audio(url, Path(tmp), progress, should_cancel)
        if on_info:
            on_info({k: v for k, v in media.items() if k != "path"})

        result = transcribe(
            media["path"], model_name, language, progress, on_segment, should_cancel
        )
        if not result["segments"]:
            raise TranscribeError("No speech was found in that video.")

        result["paragraphs"] = build_paragraphs(result["segments"], result["language"])
        base_name = f"{sanitize_filename(media['title'])}_{sanitize_filename(media['id'])}"
        txt_path, srt_path = write_outputs(
            result, out_dir, base_name, media["title"], media["webpage_url"]
        )

        audio_out = None
        if keep_audio:
            audio_out = out_dir / f"{base_name}{media['path'].suffix}"
            shutil.move(str(media["path"]), str(audio_out))

    return {
        "id": media["id"],
        "title": media["title"],
        "uploader": media["uploader"],
        "webpage_url": media["webpage_url"],
        "duration": result["duration"] or media["duration"] or 0,
        "language": result["language"],
        "model": model_name,
        "segments": result["segments"],
        "paragraphs": result["paragraphs"],
        "text": format_transcript(result["paragraphs"]),
        "srt": format_srt(result["segments"]),
        "base_name": base_name,
        "txt_path": str(txt_path),
        "srt_path": str(srt_path),
        "audio_path": str(audio_out) if audio_out else None,
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe a YouTube (or other) video locally with Whisper."
    )
    parser.add_argument(
        "url", nargs="?", default=None,
        help="Video URL (if omitted, you'll be prompted)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        choices=MODEL_NAMES + sorted(MODEL_ALIASES),
        help=f"Whisper model size (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--language", default="auto",
        help="Spoken language code such as en, es, de (default: auto-detect)",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT_DIR),
        help="Output directory (default: ./transcripts next to this script)",
    )
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="Keep the downloaded audio file alongside the transcripts.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    url = args.url
    if not url:
        try:
            url = input("Paste video URL: ").strip()
        except EOFError:
            url = ""
        if not url:
            sys.exit("No URL provided. Exiting.")

    labels = {
        "downloading": "[1/3] Downloading audio",
        "loading_model": "[2/3] Loading Whisper model (first run downloads it)",
        "transcribing": "[3/3] Transcribing",
    }
    state = {"stage": None, "pct": -1}

    def progress(stage: str, fraction: Optional[float], detail: str) -> None:
        if stage != state["stage"]:
            if state["stage"]:
                print()
            state.update(stage=stage, pct=-1)
            print(labels.get(stage, stage) + (f": {detail}" if detail else ""),
                  end="", flush=True)
        if fraction is not None:
            pct = int(fraction * 100)
            if pct != state["pct"]:
                state["pct"] = pct
                print(f"\r{labels.get(stage, stage)}... {pct:3d}%", end="", flush=True)

    try:
        result = run_pipeline(
            url,
            model_name=args.model,
            language=args.language,
            out_dir=Path(os.path.expanduser(args.out)).resolve(),
            keep_audio=args.keep_audio,
            progress=progress,
        )
    except TranscribeError as exc:
        print()
        sys.exit(f"ERROR: {exc}")
    except KeyboardInterrupt:
        print()
        sys.exit("Stopped.")

    print("\n")
    print("Done.")
    print(f"  Title : {result['title']}")
    print(f"  Text  : {result['txt_path']}")
    print(f"  SRT   : {result['srt_path']}")
    if result["audio_path"]:
        print(f"  Audio : {result['audio_path']}")


if __name__ == "__main__":
    main()
