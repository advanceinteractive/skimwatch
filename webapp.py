#!/usr/bin/env python3
"""
webapp.py

Local web interface for local_transcribe.py. Starts a small web server on
this computer only (127.0.0.1) and opens it in your browser.

Usage:
    python webapp.py [--port 8765] [--no-browser] [--out DIR]

Uses only the Python standard library for the server; the transcription work
is done by local_transcribe.py.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import socketserver
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

import local_transcribe as lt

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 16 * 1024
MAX_FINISHED_JOBS = 20
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
FINISHED = {"done", "error", "cancelled"}


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

    def __init__(self, pipeline: Callable[..., dict], out_dir: Path) -> None:
        self.pipeline = pipeline
        self.out_dir = out_dir
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
    server_version = "LocalTranscribe/" + lt.__version__
    manager: JobManager  # set by create_server

    # -- helpers ----------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        # Refuse requests addressed to any other name, so a web page elsewhere
        # cannot reach this server through DNS tricks.
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name in ALLOWED_HOSTS:
            return True
        self._json(403, {"error": "This server only answers on localhost."})
        return False

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                body = (WEB_DIR / "index.html").read_bytes()
            except OSError:
                self._json(500, {"error": "web/index.html is missing."})
                return
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/config":
            self._json(200, {
                "version": lt.__version__,
                "models": [{"name": n, "description": d} for n, d in lt.MODELS],
                "default_model": lt.DEFAULT_MODEL,
                "languages": [{"code": c, "name": n} for c, n in lt.LANGUAGES],
                "out_dir": str(self.manager.out_dir),
            })
        else:
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
            job = self.manager.get(match.group(1)) if match else None
            if job:
                self._json(200, job.snapshot())
            else:
                self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._host_ok():
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
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "Request too large."})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "Invalid JSON."})
            return

        path = self.path.split("?", 1)[0]
        if path == "/api/jobs":
            try:
                url = lt.validate_url(str(data.get("url", "")))
                model = lt.resolve_model(str(data.get("model") or lt.DEFAULT_MODEL))
            except lt.TranscribeError as exc:
                self._json(400, {"error": str(exc)})
                return
            language = str(data.get("language") or "auto").strip().lower()
            if not re.fullmatch(r"[a-z]{2,3}|auto", language):
                self._json(400, {"error": "Unknown language."})
                return
            job = self.manager.submit(url, model, language)
            self._json(202, job.snapshot())
            return

        match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})/cancel", path)
        job = self.manager.get(match.group(1)) if match else None
        if job:
            job.cancel.set()
            self._json(200, job.snapshot())
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
) -> LocalServer:
    """Build the server, trying the next ports if the first is taken."""
    manager = JobManager(pipeline or lt.run_pipeline, Path(out_dir or lt.DEFAULT_OUT_DIR))
    handler = type("BoundHandler", (Handler,), {"manager": manager})
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
    parser = argparse.ArgumentParser(description="Local web UI for Local Transcribe.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open the browser automatically.")
    parser.add_argument("--out", default=str(lt.DEFAULT_OUT_DIR),
                        help="Where transcripts are saved (default: ./transcripts)")
    args = parser.parse_args()

    out_dir = Path(os.path.expanduser(args.out)).resolve()
    server = create_server(args.port, out_dir)
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    print(f"Local Transcribe {lt.__version__} is running.")
    print(f"  Open in your browser : {url}")
    print(f"  Transcripts saved to : {out_dir}")
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
