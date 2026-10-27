"""Tests for the local web server, using a fake pipeline (no network needed)."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_chat
import webapp
import local_transcribe as lt
from test_ai import FAKE_KEY, FakeProvider, unused_url

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Hello there."},
    {"start": 2.0, "end": 4.0, "text": "This is a test."},
]


LAST_PIPELINE_CALL: dict = {}


def fake_pipeline(url, model_name="base", language=None, out_dir=None, progress=None,
                  on_info=None, on_segment=None, should_cancel=None, **extra):
    LAST_PIPELINE_CALL.clear()
    LAST_PIPELINE_CALL.update(extra, url=url)
    if "fail" in url:
        raise lt.TranscribeError("Could not download that video: nope")
    if "crash" in url:
        raise RuntimeError("boom")
    progress("downloading", 0.5, "")
    on_info({"title": "Fake Video", "uploader": "Tester", "webpage_url": url,
             "duration": 4, "id": "abc"})
    progress("transcribing", 0.0, "")
    for i, segment in enumerate(SEGMENTS):
        if "slow" in url:
            for _ in range(200):
                if should_cancel():
                    raise lt.Cancelled()
                time.sleep(0.02)
        on_segment(segment)
        progress("transcribing", (i + 1) / len(SEGMENTS), "")
    paragraphs = lt.build_paragraphs(SEGMENTS)
    return {
        "id": "abc", "title": "Fake Video", "uploader": "Tester", "webpage_url": url,
        "duration": 4.0, "language": "en", "model": model_name, "segments": SEGMENTS,
        "paragraphs": paragraphs, "text": lt.format_transcript(paragraphs),
        "srt": lt.format_srt(SEGMENTS), "base_name": "Fake_Video_abc",
        "txt_path": "/tmp/Fake_Video_abc.txt", "srt_path": "/tmp/Fake_Video_abc.srt",
        "audio_path": None,
    }


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opened = []
        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings_path = Path(cls.tmp.name) / "settings.json"
        cls.fake = FakeProvider()
        cls.server = webapp.create_server(
            0, out_dir=Path("/tmp/lt-test-out"), pipeline=fake_pipeline,
            folder_opener=cls.opened.append, settings_path=cls.settings_path)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.fake.close()
        cls.tmp.cleanup()

    def setUp(self):
        # Start every test with no saved AI settings and no keys from the shell.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        self.settings_path.unlink(missing_ok=True)
        self.fake.reset()

    def finished_job(self):
        _, job = self.json("/api/jobs", {"url": "https://example.com/watch?v=ai"})
        return self.wait(job["id"])["id"]

    def request(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def json(self, path, body=None, **kwargs):
        status, _, payload = self.request(path, body, **kwargs)
        return status, json.loads(payload)

    def wait(self, job_id, statuses=("done", "error", "cancelled")):
        deadline = time.time() + 10
        while time.time() < deadline:
            _, job = self.json("/api/jobs/" + job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.05)
        self.fail("job did not reach %s" % (statuses,))

    def test_index_page(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Skip the video", body)

    def test_config(self):
        status, config = self.json("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(config["app"], "SkimWatch")
        self.assertEqual(config["default_model"], lt.DEFAULT_MODEL)
        self.assertEqual([m["name"] for m in config["models"]], lt.MODEL_NAMES)
        self.assertEqual(config["languages"][0]["code"], "auto")

    def test_open_folder(self):
        status, body = self.json("/api/open-folder", {})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(self.opened[-1], Path("/tmp/lt-test-out"))

    def test_successful_job(self):
        status, job = self.json("/api/jobs", {"url": "https://example.com/watch?v=1"})
        self.assertEqual(status, 202)
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["title"], "Fake Video")
        self.assertEqual(done["paragraphs"][0]["text"], "Hello there. This is a test.")
        self.assertIn("00:00:02,000 --> 00:00:04,000", done["srt"])
        self.assertEqual(done["base_name"], "Fake_Video_abc")

    def test_download_error_is_reported(self):
        _, job = self.json("/api/jobs", {"url": "https://example.com/fail"})
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "error")
        self.assertIn("Could not download", done["error"])

    def test_unexpected_error_does_not_kill_worker(self):
        _, job = self.json("/api/jobs", {"url": "https://example.com/crash"})
        self.assertEqual(self.wait(job["id"])["status"], "error")
        _, job = self.json("/api/jobs", {"url": "https://example.com/ok"})
        self.assertEqual(self.wait(job["id"])["status"], "done")

    def test_cancel_with_live_preview(self):
        _, job = self.json("/api/jobs", {"url": "https://example.com/slow"})
        self.wait(job["id"], statuses=("transcribing",))
        status, _ = self.json("/api/jobs/%s/cancel" % job["id"], {})
        self.assertEqual(status, 200)
        self.assertEqual(self.wait(job["id"])["status"], "cancelled")

    def test_bad_requests(self):
        self.assertEqual(self.json("/api/jobs", {"url": ""})[0], 400)
        self.assertEqual(self.json("/api/jobs", {"url": "file:///etc/passwd"})[0], 400)
        self.assertEqual(self.json("/api/jobs", {"url": "https://a.test/v", "model": "x"})[0], 400)
        self.assertEqual(
            self.json("/api/jobs", {"url": "https://a.test/v", "language": "../x"})[0], 400)
        self.assertEqual(self.json("/api/jobs", raw=b"{not json")[0], 400)
        self.assertEqual(self.json("/api/jobs", raw=b"[1]")[0], 400)
        self.assertEqual(self.json("/api/jobs/000000000000")[0], 404)
        self.assertEqual(self.json("/nope")[0], 404)

    def test_rejects_non_json_post(self):
        req = urllib.request.Request(self.base + "/api/jobs", data=b"url=https://a.test/v")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 415)

    def test_rejects_foreign_host_header(self):
        self.assertEqual(self.json("/api/config", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(
            self.json("/api/jobs", {"url": "https://a.test/v"},
                      headers={"Host": "evil.example:8765"})[0], 403)

    # -- AI questions -----------------------------------------------------

    def test_ai_settings_round_trip(self):
        status, public = self.json("/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertEqual(public["provider"], "")
        self.assertEqual([p["id"] for p in public["providers"]], ["claude", "openai", "ollama"])

        status, public = self.request(
            "/api/ai/settings",
            {"provider": "claude", "api_key": FAKE_KEY, "base_url": self.fake.url})[::2]
        self.assertEqual(status, 200)
        self.assertNotIn(FAKE_KEY.encode(), public)
        public = json.loads(public)
        self.assertEqual(public["provider"], "claude")
        self.assertTrue(public["providers"][0]["has_key"])
        self.assertEqual(public["providers"][0]["base_url"], self.fake.url)
        self.assertEqual(ai_chat.load_settings(self.settings_path)["providers"]["claude"]["api_key"],
                         FAKE_KEY)

        # An empty key keeps the saved one; the GET never reveals it.
        self.json("/api/ai/settings", {"provider": "claude", "api_key": "", "model": "claude-haiku-5-5"})
        status, _, raw = self.request("/api/ai/settings")
        self.assertNotIn(FAKE_KEY.encode(), raw)
        claude = json.loads(raw)["providers"][0]
        self.assertEqual((claude["has_key"], claude["model"]), (True, "claude-haiku-5-5"))

        _, public = self.json("/api/ai/settings", {"provider": "claude", "clear_key": True})
        self.assertFalse(public["providers"][0]["has_key"])

    def test_ai_settings_rejects_bad_input(self):
        for body in ({}, {"provider": "gemini"},
                     {"provider": "openai", "base_url": "file:///etc/passwd"}):
            status, reply = self.json("/api/ai/settings", body)
            self.assertEqual(status, 400)
            self.assertTrue(reply["error"])
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(self.json("/api/ai/settings", raw=b"[1]")[0], 400)
        self.assertEqual(
            self.json("/api/ai/settings", {"provider": "ollama"},
                      headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.json("/api/ai/settings", headers={"Host": "evil.example"})[0], 403)

    def test_ai_models(self):
        self.json("/api/ai/settings", {"provider": "ollama", "base_url": self.fake.url})
        self.assertEqual(self.json("/api/ai/models?provider=ollama"),
                         (200, {"models": ["llama3.2:latest", "qwen3:8b"]}))
        self.assertEqual(self.json("/api/ai/models?provider=nope")[0], 400)
        self.assertEqual(self.json("/api/ai/models")[0], 400)
        # No key saved yet for OpenAI.
        status, reply = self.json("/api/ai/models?provider=openai")
        self.assertEqual(status, 400)
        self.assertIn("API key", reply["error"])
        self.json("/api/ai/settings", {"provider": "ollama", "base_url": unused_url()})
        status, reply = self.json("/api/ai/models?provider=ollama")
        self.assertEqual(status, 400)
        self.assertIn("Ollama doesn't seem to be running", reply["error"])

    def test_ai_ask(self):
        job_id = self.finished_job()
        messages = [{"role": "user", "content": "What is said?"}]
        # Nothing configured yet.
        status, reply = self.json("/api/ai/ask", {"job_id": job_id, "messages": messages})
        self.assertEqual(status, 400)
        self.assertIn("AI provider", reply["error"])

        self.json("/api/ai/settings",
                  {"provider": "claude", "api_key": FAKE_KEY, "base_url": self.fake.url})
        self.fake.reset()
        status, reply = self.json("/api/ai/ask", {"job_id": job_id, "messages": messages})
        self.assertEqual(status, 200)
        self.assertEqual(reply, {
            "answer": "Claude says hi.", "provider": "claude", "model": "claude-opus-5-5"})
        # The server supplies the transcript and title itself, from the job.
        (req,) = self.fake.requests
        self.assertIn("Hello there. This is a test.", req["body"]["system"][-1]["text"])
        self.assertIn("Fake Video", req["body"]["system"][-1]["text"])
        self.assertEqual(req["body"]["messages"], messages)

        self.fake.fail_status = 401
        status, reply = self.json("/api/ai/ask", {"job_id": job_id, "messages": messages})
        self.assertEqual(status, 400)
        self.assertIn("API key was rejected", reply["error"])
        self.assertNotIn(FAKE_KEY, reply["error"])

    def test_ai_ask_bad_requests(self):
        messages = [{"role": "user", "content": "What is said?"}]
        self.json("/api/ai/settings", {"provider": "ollama", "base_url": self.fake.url})
        self.assertEqual(
            self.json("/api/ai/ask", {"job_id": "000000000000", "messages": messages})[0], 404)
        self.assertEqual(self.json("/api/ai/ask", {"messages": messages})[0], 404)
        self.assertEqual(self.json("/api/ai/ask", {"job_id": "../etc", "messages": messages})[0], 404)

        job_id = self.finished_job()
        for bad in (None, [], [{"role": "system", "content": "x"}],
                    [{"role": "user", "content": ""}]):
            status, reply = self.json("/api/ai/ask", {"job_id": job_id, "messages": bad})
            self.assertEqual(status, 400)
            self.assertTrue(reply["error"])
        self.assertEqual(self.fake.requests, [])

        _, slow = self.json("/api/jobs", {"url": "https://example.com/slow"})
        self.wait(slow["id"], statuses=("transcribing",))
        status, reply = self.json("/api/ai/ask", {"job_id": slow["id"], "messages": messages})
        self.assertEqual(status, 400)
        self.assertIn("isn't finished", reply["error"])
        self.json("/api/jobs/%s/cancel" % slow["id"], {})
        self.wait(slow["id"])

    def test_ai_ask_allows_long_chats_only_there(self):
        job_id = self.finished_job()
        self.json("/api/ai/settings", {"provider": "ollama", "base_url": self.fake.url})
        history = []
        for _ in range(10):
            history += [{"role": "user", "content": "q" * 5000},
                        {"role": "assistant", "content": "a" * 5000}]
        history.append({"role": "user", "content": "And then?"})
        body = {"job_id": job_id, "messages": history}
        self.assertGreater(len(json.dumps(body)), webapp.MAX_BODY_BYTES)
        status, reply = self.json("/api/ai/ask", body)
        self.assertEqual((status, reply["answer"]), (200, "Ollama says hi."))
        self.assertEqual(len(self.fake.requests[-1]["body"]["messages"]), len(history) + 1)
        # Other routes keep the small limit.
        self.assertEqual(self.json("/api/jobs", {"url": "https://a.test/" + "v" * 20000})[0], 413)
        self.assertEqual(self.json("/api/ai/settings",
                                   {"provider": "ollama", "model": "m" * 20000})[0], 413)

    def test_ai_unexpected_error_is_generic(self):
        job_id = self.finished_job()
        self.json("/api/ai/settings", {"provider": "ollama", "base_url": self.fake.url})
        with mock.patch.object(ai_chat, "ask", side_effect=RuntimeError("secret detail")):
            status, reply = self.json(
                "/api/ai/ask", {"job_id": job_id, "messages": [{"role": "user", "content": "q"}]})
        self.assertEqual(status, 500)
        self.assertNotIn("secret detail", reply["error"])


if __name__ == "__main__":
    unittest.main()
