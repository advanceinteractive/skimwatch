"""Tests for the local web server, using a fake pipeline (no network needed)."""

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webapp
import local_transcribe as lt

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Hello there."},
    {"start": 2.0, "end": 4.0, "text": "This is a test."},
]


def fake_pipeline(url, model_name="base", language=None, out_dir=None, progress=None,
                  on_info=None, on_segment=None, should_cancel=None, **_):
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
        cls.server = webapp.create_server(0, pipeline=fake_pipeline)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

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
        self.assertIn(b"Welcome to Local Transcribe", body)

    def test_config(self):
        status, config = self.json("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(config["default_model"], lt.DEFAULT_MODEL)
        self.assertEqual([m["name"] for m in config["models"]], lt.MODEL_NAMES)
        self.assertEqual(config["languages"][0]["code"], "auto")

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


if __name__ == "__main__":
    unittest.main()
