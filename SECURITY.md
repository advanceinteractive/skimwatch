# Security

## Reporting a problem

Please report security issues privately through GitHub's
**Security → Report a vulnerability** on this repository, not in a public issue.
You should get a reply within a week. Include the version (`python
local_transcribe.py --version`), your operating system, and steps to reproduce.

## How Local Transcribe is meant to be used

- **Normal use** — the web server answers only on `127.0.0.1` and rejects
  requests whose `Host` header is not `localhost`, so other devices on your
  network and malicious web pages (DNS rebinding) cannot use it.
- **Family sharing (`--share`)** — reachable from the internet through a tunnel
  you set up, protected by an access code:
  - Wrong codes are limited per visitor (10 per 15 minutes) and overall
    (200 per 15 minutes).
  - Visitors cannot change settings, see your API key or file paths, or open
    folders on your computer.
  - Links submitted by visitors that resolve to private, loopback or
    link-local addresses are refused, so visitors cannot make your computer
    fetch pages from your home network.
  - Visitors can ask the AI assistant questions only if you turn that on in
    **AI settings**; their questions are billed to your API key.

## Known limits

- The private-network check runs on the link a visitor submits. A public page
  that *redirects* to a private address is not caught, because the downloader
  follows redirects itself. Only share with people you trust.
- All visitors share one session secret. Changing the code
  (`--new-share-code`) signs everyone out.
- API keys are stored in `~/.local-transcribe/settings.json`, readable only by
  your user account, not in the operating system's keychain.
- The packaged Mac app is ad-hoc signed, not notarized by Apple.
