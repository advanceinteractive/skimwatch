#!/usr/bin/env python3
"""
webapp.py

Local web interface for SkimWatch (local_transcribe.py is the engine). Starts a small web server on
this computer only (127.0.0.1) and opens it in your browser.

Usage:
    python webapp.py [--port 8765] [--no-browser] [--out DIR]

Uses only the Python standard library for the server; the transcription work
is done by local_transcribe.py.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

import ai_chat
import local_transcribe as lt

WEB_DIR = Path(__file__).resolve().parent / "web"
APP_NAME = "SkimWatch"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 16 * 1024
# Questions carry the whole chat so far, so they need more room.
MAX_ASK_BODY_BYTES = 512 * 1024
MAX_FINISHED_JOBS = 20
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SHARE_PATH = ai_chat.SETTINGS_PATH.parent / "share.json"
SESSION_COOKIE = "lt_session"
SESSION_DAYS = 30
MAX_LOGIN_FAILURES = 10          # wrong codes allowed from one visitor ...
LOGIN_FAILURE_WINDOW = 15 * 60    # ... within this many seconds.
# A ceiling across all visitors. Visitor addresses come from tunnel headers,
# which an attacker can vary, so this caps guessing overall. 200 tries per
# 15 minutes against ~850 billion possible codes is hopeless for a guesser,
# and one prankster cannot lock the family out with a handful of tries.
MAX_LOGIN_FAILURES_TOTAL = 200
# Headers that tunnels and proxies add; a request carrying one came from outside.
FORWARD_HEADERS = {"forwarded", "x-real-ip"}
FORWARD_PREFIXES = ("x-forwarded-", "cf-", "tailscale-")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no look-alikes (0/O, 1/I/L)
FINISHED = {"done", "error", "cancelled"}


def open_folder(path: Path) -> None:
    """Show a folder in Finder / Explorer / the Linux file manager."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, resolve: Callable = socket.getaddrinfo) -> None:
    """
    Refuse links that point into the owner's own network (router pages, other
    devices, this computer). Used for visitors only: the owner may want local
    links. Raises lt.TranscribeError with a message safe to show.
    """
    host = (urllib.parse.urlparse(url).hostname or "").strip("[]")
    try:
        addresses = {info[4][0] for info in resolve(host, None)}
    except (OSError, UnicodeError):
        raise lt.TranscribeError("Couldn't find that website. Check the link.")
    if not addresses or not all(_is_public_address(a) for a in addresses):
        raise lt.TranscribeError(
            "That link points to a private network address, which isn't allowed "
            "for shared visitors.")


def _plain_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def new_share_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def load_share(path: Optional[Path] = None, code: Optional[str] = None,
               regenerate: bool = False) -> dict:
    """
    Read (or create) the family-sharing access code. Returns {"code", "secret"}.
    Changing the code also changes the secret, which signs everyone out.
    """
    path = Path(path) if path else SHARE_PATH
    share: dict = {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored.get("code"), str) and isinstance(stored.get("secret"), str):
            share = {"code": stored["code"], "secret": stored["secret"]}
    except (OSError, ValueError, AttributeError):
        pass
    if code and len(_plain_code(code)) < 6:
        raise ValueError("The access code must be at least 6 letters or digits.")
    wanted = code or (None if share and not regenerate else new_share_code())
    if wanted and _plain_code(wanted) != _plain_code(share.get("code", "")):
        share = {"code": wanted.strip(), "secret": secrets.token_hex(32)}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(share), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows
        os.replace(tmp, path)
    return share


class Job:
    def __init__(self, url: str, model: str, language: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.model = model
        self.language = language
        self.status = "queued"
        self.progress: Optional[float] = None
        self.detail = ""
        self.error = ""
        self.info: dict = {}
        self.segments: list = []
        self.result: Optional[dict] = None
        self.cancel = threading.Event()
        self.created = time.time()
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        with self.lock:
            data = {
                "id": self.id,
                "status": self.status,
                "progress": self.progress,
                "detail": self.detail,
                "error": self.error,
                "url": self.url,
                "model": self.model,
                "title": self.info.get("title", ""),
                "uploader": self.info.get("uploader", ""),
                "webpage_url": self.info.get("webpage_url", self.url),
                "duration": self.info.get("duration") or 0,
            }
            if self.result:
                r = self.result
                data.update(
                    title=r["title"],
                    uploader=r["uploader"],
                    webpage_url=r["webpage_url"],
                    duration=r["duration"],
                    language=r["language"],
                    paragraphs=r["paragraphs"],
                    srt=r["srt"],
                    base_name=r["base_name"],
                    txt_path=r["txt_path"],
                    srt_path=r["srt_path"],
                )
            else:
                # Live preview while the transcription is still running.
                data["paragraphs"] = lt.build_paragraphs(self.segments)
            return data


class JobManager:
    """Runs jobs one at a time on a background thread."""

    def __init__(
        self,
        pipeline: Callable[..., dict],
        out_dir: Path,
        folder_opener: Callable[[Path], None] = open_folder,
    ) -> None:
        self.pipeline = pipeline
        self.out_dir = out_dir
        self.folder_opener = folder_opener
        self.jobs: dict = {}
        self.lock = threading.Lock()
        self.queue: "queue.Queue[Job]" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, url: str, model: str, language: str) -> Job:
        job = Job(url, model, language)
        with self.lock:
            finished = [j for j in self.jobs.values() if j.status in FINISHED]
            for old in sorted(finished, key=lambda j: j.created)[:-MAX_FINISHED_JOBS]:
                del self.jobs[old.id]
            self.jobs[job.id] = job
        self.queue.put(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self.lock:
            return self.jobs.get(job_id)

    def _worker(self) -> None:
        while True:
            job = self.queue.get()
            if job.cancel.is_set():
                with job.lock:
                    job.status = "cancelled"
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        def progress(stage: str, fraction: Optional[float], detail: str) -> None:
            with job.lock:
                if stage != job.status:
                    job.status, job.progress = stage, None
                job.detail = detail
                if fraction is not None:
                    job.progress = fraction

        def on_info(info: dict) -> None:
            with job.lock:
                job.info = info

        def on_segment(segment: dict) -> None:
            with job.lock:
                job.segments.append(segment)

        try:
            result = self.pipeline(
                job.url,
                model_name=job.model,
                language=job.language,
                out_dir=self.out_dir,
                progress=progress,
                on_info=on_info,
                on_segment=on_segment,
                should_cancel=job.cancel.is_set,
            )
            with job.lock:
                job.result, job.status, job.progress = result, "done", 1.0
        except lt.Cancelled:
            with job.lock:
                job.status = "cancelled"
        except lt.TranscribeError as exc:
            with job.lock:
                job.status, job.error = "error", str(exc)
        except Exception as exc:  # never let the worker thread die
            with job.lock:
                job.status = "error"
                job.error = f"Unexpected error: {exc.__class__.__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "SkimWatch/" + lt.__version__
    manager: JobManager  # set by create_server
    settings_path: Optional[Path] = None  # None means ai_chat.SETTINGS_PATH
    settings_lock = threading.Lock()
    share: Optional[dict] = None  # {"code", "secret"} while family sharing is on
    resolver: Callable = staticmethod(socket.getaddrinfo)  # tests swap this out
    login_failures: dict = {}  # visitor address -> times of wrong codes
    login_lock = threading.Lock()

    # -- helpers ----------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str,
              headers: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict, headers: Optional[dict] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _file(self, name: str, content_type: str) -> None:
        try:
            body = (WEB_DIR / name).read_bytes()
        except OSError:
            self._json(500, {"error": f"web/{name} is missing."})
            return
        self._send(200, body, content_type)

    # -- family sharing ---------------------------------------------------

    def _host_name(self) -> str:
        host = self.headers.get("Host", "")
        return host.rsplit(":", 1)[0] if not host.endswith("]") else host

    def _is_remote(self) -> bool:
        """True for a request that reached us through a tunnel, not this computer."""
        if not self.share:
            return False
        if self._host_name() not in ALLOWED_HOSTS:
            return True
        return any(
            name.lower() in FORWARD_HEADERS or name.lower().startswith(FORWARD_PREFIXES)
            for name in self.headers.keys())

    def _client_address(self) -> str:
        """Best guess at who is asking: the visitor's address as the tunnel saw it."""
        for header in ("CF-Connecting-IP", "X-Real-IP"):
            value = self.headers.get(header, "").strip()
            if value:
                return value[:64]
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded.strip():
            return forwarded.split(",")[0].strip()[:64]
        return self.client_address[0]

    def _session_token(self) -> str:
        return hmac.new(self.share["secret"].encode(), b"session", hashlib.sha256).hexdigest()

    def _logged_in(self) -> bool:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return hmac.compare_digest(value, self._session_token())
        return False

    def _gate(self, method: str, path: str) -> bool:
        """Decide whether this request may go on. Replies itself when it may not."""
        if not self.share:
            return self._host_ok()
        if not self._is_remote() or self._logged_in():
            return True
        if (method, path) == ("POST", "/api/login") or path == "/icon.png":
            return True
        if method == "GET" and path in ("/", "/index.html"):
            self._file("login.html", "text/html; charset=utf-8")
        else:
            self._json(401, {"error": "Enter the access code first.", "login": True})
        return False

    def _owner_only(self) -> bool:
        if not self._is_remote():
            return True
        self._json(403, {"error": "Only the owner can do that, on the computer "
                                  "that runs SkimWatch."})
        return False

    def _login(self, data: dict) -> None:
        if not self.share:
            self._json(404, {"error": "Not found."})
            return
        now = time.time()
        who = self._client_address()
        with self.login_lock:
            failures = self.login_failures
            for address in list(failures):
                failures[address] = [t for t in failures[address]
                                     if now - t < LOGIN_FAILURE_WINDOW]
                if not failures[address]:
                    del failures[address]
            locked = (len(failures.get(who, ())) >= MAX_LOGIN_FAILURES or
                      sum(map(len, failures.values())) >= MAX_LOGIN_FAILURES_TOTAL)
        if locked:
            self._json(429, {"error": "Too many wrong codes. Wait 15 minutes and try again."})
            return
        given = _plain_code(str(data.get("code", "")))
        if not hmac.compare_digest(given.encode(), _plain_code(self.share["code"]).encode()):
            with self.login_lock:
                self.login_failures.setdefault(who, []).append(now)
            self._json(403, {"error": "That code isn't right. Check it and try again."})
            return
        cookie = (f"{SESSION_COOKIE}={self._session_token()}; Path=/; HttpOnly; "
                  f"SameSite=Lax; Max-Age={SESSION_DAYS * 86400}")
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            cookie += "; Secure"
        self._json(200, {"ok": True}, {"Set-Cookie": cookie})

    def _snapshot(self, job: "Job") -> dict:
        data = job.snapshot()
        if self._is_remote():
            # Folder names on the owner's computer are none of a visitor's business.
            data.pop("txt_path", None)
            data.pop("srt_path", None)
        return data

    def _host_ok(self) -> bool:
        # Refuse requests addressed to any other name, so a web page elsewhere
        # cannot reach this server through DNS tricks.
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name in ALLOWED_HOSTS:
            return True
        self._json(403, {"error": "This server only answers on localhost."})
        return False

    def _ai_reply(self, action: Callable[[], dict]) -> None:
        """Run an AI action and turn its outcome into a JSON reply."""
        try:
            payload = action()
        except ai_chat.AIError as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            # Details could include private data, so keep this one vague.
            self._json(500, {"error": "Something went wrong while talking to the AI."})
        else:
            self._json(200, payload)

    def _save_ai_settings(self, changes: dict) -> dict:
        # One at a time, so two saves cannot overwrite each other's changes.
        with self.settings_lock:
            settings = ai_chat.update_settings(
                ai_chat.load_settings(self.settings_path), changes)
            ai_chat.save_settings(settings, self.settings_path)
        return ai_chat.public_settings(settings)

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._gate("GET", path):
            return
        if path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif path == "/icon.png":
            self._file("icon.png", "image/png")
        elif path == "/api/config":
            remote = self._is_remote()
            self._json(200, {
                "app": APP_NAME,
                "version": lt.__version__,
                "models": [{"name": n, "description": d} for n, d in lt.MODELS],
                "default_model": lt.DEFAULT_MODEL,
                "languages": [{"code": c, "name": n} for c, n in lt.LANGUAGES],
                "out_dir": "" if remote else str(self.manager.out_dir),
                "remote": remote,
            })
        elif path == "/api/ai/settings":
            self._json(200, ai_chat.public_settings(ai_chat.load_settings(self.settings_path)))
        elif path == "/api/ai/models":
            if not self._owner_only():
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            provider = (query.get("provider") or [""])[0]
            self._ai_reply(lambda: {
                "models": ai_chat.list_models(
                    ai_chat.load_settings(self.settings_path), provider)})
        else:
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
            job = self.manager.get(match.group(1)) if match else None
            if job:
                self._json(200, self._snapshot(job))
            else:
                self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._gate("POST", self.path.split("?", 1)[0]):
            return
        # Browsers cannot send JSON cross-site without permission, so this
        # stops other web pages from starting jobs.
        if "application/json" not in self.headers.get("Content-Type", ""):
            self._json(415, {"error": "Send JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        path = self.path.split("?", 1)[0]
        limit = MAX_ASK_BODY_BYTES if path == "/api/ai/ask" else MAX_BODY_BYTES
        if length < 0 or length > limit:
            self._json(413, {"error": "Request too large."})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "Invalid JSON."})
            return

        if path == "/api/login":
            self._login(data)
            return

        if path == "/api/ai/settings":
            if self._owner_only():
                self._ai_reply(lambda: self._save_ai_settings(data))
            return

        if path == "/api/ai/ask":
            if self._is_remote() and not ai_chat.load_settings(
                    self.settings_path).get("visitors_can_ask"):
                self._json(403, {"error": "The owner hasn't turned on questions for "
                                          "people they share with."})
                return
            job_id = str(data.get("job_id", ""))
            job = self.manager.get(job_id) if re.fullmatch(r"[0-9a-f]{12}", job_id) else None
            if not job:
                self._json(404, {"error": "That transcript is no longer available."})
                return
            with job.lock:
                result = job.result if job.status == "done" else None
            if not result:
                self._json(400, {"error": "The transcript isn't finished yet."})
                return
            self._ai_reply(lambda: ai_chat.ask(
                ai_chat.load_settings(self.settings_path),
                result["text"], result["title"], data.get("messages")))
            return

        if path == "/api/jobs":
            try:
                url = lt.validate_url(str(data.get("url", "")))
                if self._is_remote():
                    check_public_url(url, self.resolver)
                model = lt.resolve_model(str(data.get("model") or lt.DEFAULT_MODEL))
            except lt.TranscribeError as exc:
                self._json(400, {"error": str(exc)})
                return
            language = str(data.get("language") or "auto").strip().lower()
            if not re.fullmatch(r"[a-z]{2,3}|auto", language):
                self._json(400, {"error": "Unknown language."})
                return
            job = self.manager.submit(url, model, language)
            self._json(202, self._snapshot(job))
            return

        if path == "/api/open-folder":
            if not self._owner_only():
                return
            try:
                self.manager.folder_opener(self.manager.out_dir)
            except Exception as exc:
                self._json(500, {"error": f"Could not open the folder: {exc}"})
                return
            self._json(200, {"ok": True})
            return

        match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})/cancel", path)
        job = self.manager.get(match.group(1)) if match else None
        if job:
            job.cancel.set()
            self._json(200, self._snapshot(job))
        else:
            self._json(404, {"error": "Not found."})


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # The stock version looks up this machine's full hostname, which can
        # stall for half a minute on some networks. We only serve localhost.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def create_server(
    port: int = DEFAULT_PORT,
    out_dir: Optional[Path] = None,
    pipeline: Optional[Callable[..., dict]] = None,
    port_attempts: int = 20,
    folder_opener: Callable[[Path], None] = open_folder,
    settings_path: Optional[Path] = None,
    share: Optional[dict] = None,
    resolver: Optional[Callable] = None,
) -> LocalServer:
    """Build the server, trying the next ports if the first is taken."""
    manager = JobManager(
        pipeline or lt.run_pipeline, Path(out_dir or lt.DEFAULT_OUT_DIR), folder_opener
    )
    handler = type("BoundHandler", (Handler,), {
        "manager": manager,
        "settings_path": Path(settings_path) if settings_path else None,
        "settings_lock": threading.Lock(),
        "share": share,
        "login_failures": {},
        "resolver": staticmethod(resolver or socket.getaddrinfo),
        "login_lock": threading.Lock(),
    })
    last_error: Optional[OSError] = None
    candidates = [port] if port == 0 else range(port, port + port_attempts)
    for candidate in candidates:
        try:
            server = LocalServer(("127.0.0.1", candidate), handler)
        except OSError as exc:
            last_error = exc
            continue
        server.manager = manager  # type: ignore[attr-defined]
        return server
    raise OSError(f"No free port found starting at {port}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web UI for SkimWatch.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open the browser automatically.")
    parser.add_argument("--out", default=str(lt.DEFAULT_OUT_DIR),
                        help="Where transcripts are saved (default: ./transcripts)")
    parser.add_argument("--share", action="store_true",
                        help="Family sharing: let people you give the access code to "
                             "use this through a tunnel (see README).")
    parser.add_argument("--share-code", default=None,
                        help="Set your own access code (implies --share).")
    parser.add_argument("--new-share-code", action="store_true",
                        help="Make a new access code, signing everyone out (implies --share).")
    args = parser.parse_args()

    share = None
    if args.share or args.share_code or args.new_share_code:
        try:
            share = load_share(code=args.share_code, regenerate=args.new_share_code)
        except ValueError as exc:
            parser.error(str(exc))

    out_dir = Path(os.path.expanduser(args.out)).resolve()
    server = create_server(args.port, out_dir, share=share)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    print(f"SkimWatch {lt.__version__} is running.")
    print(f"  Open in your browser : {url}")
    print(f"  Transcripts saved to : {out_dir}")
    if share:
        print()
        print("  FAMILY SHARING IS ON")
        print(f"  Access code          : {share['code']}")
        print(f"  Now start your tunnel to port {port} (see README) and send your")
        print("  family the tunnel's web address and this code.")
        print("  This computer must stay on; it will not sleep while this runs.")
        if sys.platform == "darwin":
            # Keep the Mac awake for as long as this program is alive.
            try:
                subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            except OSError:
                pass
    print("  Press Ctrl+C (or close this window) to stop.", flush=True)

    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
