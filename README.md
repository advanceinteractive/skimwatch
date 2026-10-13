# Local Transcribe

Paste a link to a YouTube video (or a video from most other sites) and get a
clean, readable transcript — in your browser, on your own computer.

- **Private.** The audio is downloaded and transcribed locally with
  [Whisper](https://github.com/openai/whisper). Nothing is uploaded, and no
  account or API key is needed.
- **Simple.** Double-click one file to start. A page opens in your browser;
  paste a link and press **Transcribe**.
- **Readable.** The text is grouped into paragraphs, with optional timestamps,
  a copy button, and `.txt` / `.srt` (subtitle) downloads.
- **Works on macOS, Windows and Linux.** ffmpeg is not required.

## Quick start

### Mac app (no Python needed)

1. Download `Local-Transcribe-<version>-mac-arm64.zip` from the
   [Releases](../../releases) page and unzip it. (Apple Silicon Macs, M1 or
   newer, on macOS 11 or later.)
2. Drag **Local Transcribe** to your Applications folder and open it.
3. The app is not signed with a paid Apple certificate, so macOS blocks it the
   first time. Open **System Settings → Privacy & Security**, scroll down, and
   click **Open Anyway**. (On macOS 14 and earlier: right-click the app and
   choose **Open**.) You only do this once.
4. Your browser opens the app's page. Paste a video link and press
   **Transcribe**.

Click the Dock icon to bring the page back; quit from the Dock or with
`Cmd+Q`. Transcripts are saved to `Documents/Local Transcribe`.

### Run from source (macOS, Windows, Linux)

You need **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/)).
On Windows, tick **"Add python.exe to PATH"** in the installer.

1. Download this project (green **Code** button → **Download ZIP**) and unzip it.
2. Start it:
   - **macOS:** double-click `start-mac.command`. If macOS blocks it, approve
     it under **System Settings → Privacy & Security → Open Anyway**.
   - **Windows:** double-click `start-windows.bat`.
     If SmartScreen appears, choose **More info → Run anyway**.
   - **Linux:** run `./start-mac.command` in a terminal.
3. Your browser opens at `http://127.0.0.1:8765/`. Paste a video link and press
   **Transcribe**.

The first start takes a few minutes while it installs what it needs into a
private `.venv` folder inside the project. To stop the program, close its
terminal window or press `Ctrl+C` in it.

### Either way

The first time you use each quality level, its speech model is downloaded
once. After that, transcription itself works offline (the video still has to
be downloaded).

## Quality levels

| Quality    | Download | Notes                                          |
|------------|----------|------------------------------------------------|
| `tiny`     | ~75 MB   | Fastest, rough accuracy                        |
| `base`     | ~145 MB  | Fast, decent accuracy (default)                |
| `small`    | ~480 MB  | Balanced speed and accuracy                    |
| `medium`   | ~1.5 GB  | Slow, very good accuracy                       |
| `turbo`    | ~1.6 GB  | Near-best accuracy, far faster than `large-v3` |
| `large-v3` | ~3 GB    | Slowest, best accuracy                         |

As a rough guide, `base` transcribes a 3½-minute talk in about 20 seconds on an
Apple Silicon Mac. Bigger models and older computers are slower.

The spoken language is detected automatically. If a video starts with music or
switches language, pick the language yourself for a better result.

## Where transcripts go

Every finished transcript is also saved as `<title>_<video id>.txt` and `.srt`:
in `Documents/Local Transcribe` for the Mac app, or in the `transcripts` folder
next to the program when run from source. **Show in folder** on the page opens
it.

## Command line

The same engine works without the browser:

```
python local_transcribe.py "https://www.youtube.com/watch?v=..." --model small
python local_transcribe.py "<url>" --language es --out ~/Desktop/transcripts --keep-audio
```

Run it with the Python inside `.venv` (`.venv/bin/python` on macOS/Linux,
`.venv\Scripts\python` on Windows), or install the requirements yourself with
`pip install -r requirements.txt`.

Web app options: `python webapp.py --port 9000 --no-browser --out <folder>`.

## Troubleshooting

- **"Could not download that video"** — sites change often, and the downloader
  ([yt-dlp](https://github.com/yt-dlp/yt-dlp)) is updated to keep up.
  - *Mac app:* download the latest release; each one ships the current
    downloader.
  - *From source:* `.venv/bin/python -m pip install -U yt-dlp` (Windows:
    `.venv\Scripts\python -m pip install -U yt-dlp`).
- **YouTube still fails after updating** — the downloader increasingly relies
  on a JavaScript runtime for YouTube. Installing [Deno](https://deno.com/)
  (free, one command) is its recommended fix; no other setup is needed.
- **Private, members-only, age-restricted or login-only videos** are not
  supported. Some sites (Vimeo, for example) require a login for most videos.
- **Live streams** can be transcribed once they have ended.
- **A playlist link** transcribes the first video only. Paste the link of the
  individual video you want.
- **The page doesn't open** — look in the program's window for the address; if
  port 8765 is busy it uses the next free one.

## How it works

- `local_transcribe.py` — the engine and command-line tool: downloads the
  audio with yt-dlp, transcribes it with
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and formats the
  result.
- `webapp.py` — a small web server (Python standard library only) that answers
  on `127.0.0.1` only, so nothing else on your network can reach it.
- `web/index.html` — the page you see.
- `app.py` — entry point of the packaged app (Dock icon, single instance).
- `tests/` — run with `python -m unittest discover -s tests`.

## Building the Mac app

```
./packaging/build-mac.sh
```

This uses [PyInstaller](https://pyinstaller.org/) in a throw-away environment
and writes `dist/Local-Transcribe-<version>-mac-<arch>.zip`. The app runs only
on the kind of Mac it was built on (Apple Silicon or Intel). Upload the zip to
a GitHub release to share it.

## Responsible use

Only transcribe videos you have the right to use, and respect the terms of
service of the sites you download from. Transcripts are machine-generated and
will contain mistakes; check anything important against the original.

## License

[MIT](LICENSE) © 2026 Anthony Boutsikoudis
