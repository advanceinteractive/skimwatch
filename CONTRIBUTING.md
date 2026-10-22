# Contributing

Thanks for helping. Bug reports, fixes and small improvements are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on
  the approach.
- Keep the project's promises: transcription stays on the user's computer, the
  web server stays standard-library only, and the page stays usable by people
  who are not technical.

## Development

```
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # Windows: .venv\Scripts\python
.venv/bin/python -m unittest discover -s tests
.venv/bin/python webapp.py
```

The tests need no network access: downloads, Whisper and AI providers are
replaced with fakes. Please add or update tests with your change; CI runs them
on macOS, Windows and Linux.

## Pull requests

- One topic per pull request, with a short description of what changed and why.
- User-facing text should be plain and friendly; avoid jargon in the page and
  in error messages.
- By contributing you agree that your work is released under the
  [MIT License](LICENSE).
