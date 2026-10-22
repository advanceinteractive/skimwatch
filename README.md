# Local Transcribe

![tests](https://github.com/tonyb82/local-transcribe/actions/workflows/tests.yml/badge.svg)

Paste a link to a YouTube video (or a video from most other sites) and get a
clean, readable transcript — in your browser, on your own computer.

- **Private.** The audio is downloaded and transcribed locally with
  [Whisper](https://github.com/openai/whisper). Nothing is uploaded, and no
  account or API key is needed.
- **Ask questions (optional).** Connect Claude, ChatGPT or a local Ollama
  model to summarise a transcript or answer questions about it.
- **Simple.** Double-click one file to start. A page opens in your browser;
  paste a link and press **Transcribe**.
- **Readable.** The text is grouped into paragraphs, with optional timestamps,
  a copy button, and `.txt` / `.srt` (subtitle) downloads.
- **Works on macOS, Windows and Linux.** ffmpeg is not required.

![A finished transcript in Local Transcribe](docs/screenshot-transcript.png)

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

## Ask questions about a transcript (optional)

Click **AI settings** at the top of the page (or **Set up AI assistant** under a
finished transcript), choose an assistant, paste your API key and press
**Save**. Then ask for a summary, the key points, or anything else about the
video in the **Ask about this transcript** panel.

<img src="docs/screenshot-ai-settings.png" alt="AI settings panel" width="560">

| Assistant           | What you need                                                        | Where the text goes        |
|---------------------|----------------------------------------------------------------------|----------------------------|
| Claude (Anthropic)  | An API key from [console.anthropic.com](https://console.anthropic.com/) | Sent to Anthropic          |
| ChatGPT (OpenAI)    | An API key from [platform.openai.com](https://platform.openai.com/)  | Sent to OpenAI             |
| Ollama              | [Ollama](https://ollama.com/) installed, with a model pulled         | Stays on this computer     |

This is the one feature that can send text off your computer, and only when
you set it up and ask a question: the transcript and your questions go to the
assistant you chose, and usage is billed to your own API key. Choose Ollama to
keep everything local and free. Transcribing never uses any of these.

Your key is stored only on this computer, in `~/.local-transcribe/settings.json`
(readable only by you), and is never shown again in the page. Under
**Advanced** you can point the ChatGPT option at any OpenAI-compatible service.

## Share with your family (phones included)

You can let people who don't live with you use your copy from their phones.
The program keeps running on your computer at home, so YouTube links work, and
they reach it through a private web address protected by an access code.

1. **Start sharing.** Double-click `share-mac.command` (or run
   `python webapp.py --share`). It prints an **access code** such as
   `KQ7M-2XDF` and keeps the Mac awake while it runs.
2. **Open a tunnel** from the internet to the program. Either:
   - **[Tailscale](https://tailscale.com/) Funnel** (free; the address stays
     the same): install Tailscale, sign in, then run `tailscale funnel 8765`.
     It shows an address like `https://your-mac.your-name.ts.net`.
   - **[Cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) quick tunnel**
     (free, no account; the address changes every time you start it):
     `cloudflared tunnel --url http://127.0.0.1:8765`.
3. **Send your family the address and the access code.** They open the
   address, type the code once, and can then paste video links. On an iPhone,
   **Share → Add to Home Screen** makes it open like an app.

Good to know:

- Your computer must be on, awake and running both the program and the tunnel.
- It transcribes one video at a time; others wait their turn.
- Visitors can transcribe. They can also ask the AI assistant questions if you
  tick **Let people I share with ask questions too** in **AI settings** (off by
  default; their questions are billed to your key). They cannot see or change
  your settings, your API key, or where files are stored, and they cannot submit
  links to addresses on your home network. Their transcripts are also saved in
  your transcripts folder.
- Anyone with both the address and the code can use it. To lock everyone out
  and get a new code, run `python webapp.py --share --new-share-code`. Ten
  wrong codes in 15 minutes from one visitor pauses sign-in for that visitor
  for 15 minutes; one person mistyping does not lock out the others.
- Sharing is available when running from source, not in the packaged Mac app.

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
- `ai_chat.py` — the optional connection to Claude, OpenAI or Ollama.
- `web/login.html`, `share-mac.command` — the access-code page and launcher for
  family sharing.
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

Local Transcribe is a tool; you are responsible for how you use it.

- **Site terms.** Many sites, YouTube included, do not allow downloading
  except through their own download features or with permission, and do not
  allow automated access. Using this program on such a site may break its
  terms of service, and the site may block or rate-limit you.
- **Copyright.** A transcript is a copy of someone's words. Transcribing your
  own videos, public-domain or Creative Commons material, or videos you have
  permission to use is the safe case. Keeping a private transcript to study,
  quote briefly or make a video accessible may be allowed where you live (for
  example as fair use in the US), but publishing or selling transcripts of
  other people's videos generally needs their permission.
- **Accuracy.** Transcripts are machine-generated and will contain mistakes;
  check anything important against the original.

This is general information, not legal advice. This project is not affiliated
with or endorsed by YouTube or Google; "YouTube" is a trademark of Google LLC.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately
as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Anthony Boutsikoudis

The source code in this repository is MIT licensed. The packaged Mac app also
contains third-party libraries under their own licenses, listed in
`THIRD_PARTY_NOTICES.txt` inside the app (`Contents/Resources`). Some of those,
pulled in by the audio library's FFmpeg build, are GPL licensed; see that file
for details and source links.
