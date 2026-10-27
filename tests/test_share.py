"""Tests for family sharing: the access code, sessions and visitor limits."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import webapp
from test_webapp import LAST_PIPELINE_CALL, fake_pipeline

CODE = "ABCD-2345"
TUNNEL = {"X-Forwarded-For": "203.0.113.9", "Host": "family.example.ts.net"}

# Offline stand-in for DNS: names map to fixed addresses.
FAKE_DNS = {"example.com": "93.184.215.14", "router.example.com": "192.168.1.1",
            "sneaky.example.com": "::ffff:10.0.0.5"}


def fake_resolve(host, port):
    if host in FAKE_DNS:
        return [(0, 0, 0, "", (FAKE_DNS[host], 0))]
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return [(0, 0, 0, "", (host, 0))]
    except ValueError:
        raise OSError("not found")


class ShareTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.opened = []
        self.server = webapp.create_server(
            0, out_dir=Path(tmp.name) / "out", pipeline=fake_pipeline,
            folder_opener=self.opened.append, settings_path=Path(tmp.name) / "settings.json",
            share={"code": CODE, "secret": "test-secret"}, resolver=fake_resolve)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
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

    def login(self, code=CODE, extra=None):
        status, headers, body = self.call("/api/login", {"code": code}, dict(TUNNEL, **(extra or {})))
        cookie = (headers.get("Set-Cookie") or "").split(";")[0]
        return status, headers.get("Set-Cookie") or "", dict(TUNNEL, Cookie=cookie)

    def test_owner_on_the_computer_needs_no_code(self):
        status, _, body = self.call("/api/config")
        config = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(config["remote"])
        self.assertTrue(config["out_dir"])
        self.assertEqual(self.call("/api/open-folder", {})[0], 200)

    def test_visitor_without_code_sees_only_the_login_page(self):
        status, _, body = self.call("/", headers=TUNNEL)
        self.assertEqual(status, 200)
        self.assertIn(b"Access code", body)
        self.assertNotIn(b"Skip the video", body)
        status, _, body = self.call("/api/config", headers=TUNNEL)
        self.assertEqual((status, json.loads(body)["login"]), (401, True))
        self.assertEqual(self.call("/api/jobs", {"url": "https://a.test/v"}, TUNNEL)[0], 401)
        self.assertEqual(self.call("/api/ai/settings", headers=TUNNEL)[0], 401)
        self.assertEqual(self.call("/icon.png", headers=TUNNEL)[0], 200)
        # A different Host name alone also counts as coming from outside.
        self.assertEqual(self.call("/api/config", headers={"Host": "x.example"})[0], 401)

    def test_wrong_and_right_code(self):
        status, cookie, _ = self.login("WRONG-CODE")
        self.assertEqual((status, cookie), (403, ""))
        status, cookie, _ = self.login("abcd 2345")  # spacing and case don't matter
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn(CODE, cookie)
        _, cookie, _ = self.login(extra={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", cookie)

    def test_forged_cookie_is_refused(self):
        forged = dict(TUNNEL, Cookie="lt_session=" + "0" * 64)
        self.assertEqual(self.call("/api/config", headers=forged)[0], 401)

    def test_visitor_with_code_can_transcribe_but_not_manage(self):
        _, _, visitor = self.login()
        status, _, body = self.call("/", headers=visitor)
        self.assertIn(b"Skip the video", body)
        config = json.loads(self.call("/api/config", headers=visitor)[2])
        self.assertTrue(config["remote"])
        self.assertEqual(config["out_dir"], "")

        status, _, body = self.call("/api/jobs", {"url": "https://example.com/v"}, visitor)
        self.assertEqual(status, 202)
        job_id = json.loads(body)["id"]
        for _ in range(100):
            job = json.loads(self.call("/api/jobs/" + job_id, headers=visitor)[2])
            if job["status"] == "done":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["paragraphs"][0]["text"], "Hello there. This is a test.")
        self.assertNotIn("txt_path", job)
        self.assertNotIn("srt_path", job)
        # The owner still sees where it was saved.
        self.assertIn("txt_path", json.loads(self.call("/api/jobs/" + job_id)[2]))
        # Visitors' videos are capped in length; the owner's are not.
        self.assertEqual(LAST_PIPELINE_CALL["max_seconds"], webapp.VISITOR_MAX_SECONDS)
        own = json.loads(self.call("/api/jobs", {"url": "https://example.com/mine"})[2])
        for _ in range(100):
            if json.loads(self.call("/api/jobs/" + own["id"])[2])["status"] == "done":
                break
            time.sleep(0.05)
        self.assertIsNone(LAST_PIPELINE_CALL["max_seconds"])

        status, _, raw = self.call("/api/ai/settings", headers=visitor)
        self.assertEqual(status, 200)
        seen = json.loads(raw)
        self.assertIn("visitors_can_ask", seen)
        for provider in seen["providers"]:
            self.assertEqual(set(provider),
                             {"id", "name", "needs_key", "has_key", "sends_data_off_device"})
        # The owner's own page still gets the full picture.
        self.assertIn("base_url", json.loads(self.call("/api/ai/settings")[2])["providers"][0])
        self.assertEqual(self.call("/api/open-folder", {}, visitor)[0], 403)
        self.assertEqual(self.opened, [])
        self.assertEqual(
            self.call("/api/ai/settings", {"provider": "ollama"}, visitor)[0], 403)
        self.assertEqual(self.call("/api/ai/models?provider=ollama", headers=visitor)[0], 403)

    def test_lockout_after_repeated_wrong_codes(self):
        for _ in range(webapp.MAX_LOGIN_FAILURES):
            self.assertEqual(self.login("NOPE-NOPE")[0], 403)
        self.assertEqual(self.login("NOPE-NOPE")[0], 429)
        self.assertEqual(self.login()[0], 429)  # even the right code has to wait

    def test_one_visitors_lockout_does_not_lock_out_others(self):
        for _ in range(webapp.MAX_LOGIN_FAILURES):
            self.login("NOPE-NOPE")
        self.assertEqual(self.login()[0], 429)
        other = {"X-Forwarded-For": "198.51.100.7"}
        self.assertEqual(self.login(extra=other)[0], 200)
        cloudflare = {"CF-Connecting-IP": "198.51.100.8"}
        self.assertEqual(self.login(extra=cloudflare)[0], 200)

    def test_overall_ceiling_on_wrong_codes(self):
        with mock.patch.object(webapp, "MAX_LOGIN_FAILURES_TOTAL", 5):
            for i in range(5):
                self.login("NOPE-NOPE", extra={"X-Forwarded-For": "198.51.100.%d" % i})
            self.assertEqual(self.login(extra={"X-Forwarded-For": "198.51.100.99"})[0], 429)

    def test_visitor_cannot_reach_private_network(self):
        _, _, visitor = self.login()
        for url in ("http://192.168.1.1/x.mp4", "http://127.0.0.1:9000/a.wav",
                    "http://[::1]/a", "https://router.example.com/v",
                    "https://sneaky.example.com/v", "http://169.254.169.254/latest",
                    "https://no-such-host.example/v"):
            status, _, body = self.call("/api/jobs", {"url": url}, visitor)
            self.assertEqual(status, 400, url)
        self.assertEqual(self.call("/api/jobs", {"url": "https://example.com/v"}, visitor)[0], 202)
        # The owner, on the computer itself, may use local links.
        self.assertEqual(self.call("/api/jobs", {"url": "http://192.168.1.1/x.mp4"})[0], 202)

    def test_visitor_questions_are_off_until_the_owner_allows_them(self):
        _, _, visitor = self.login()
        status, _, body = self.call("/api/ai/ask", {"job_id": "0" * 12}, visitor)
        self.assertEqual(status, 403)
        settings = json.loads(self.call("/api/ai/settings")[2])
        self.assertFalse(settings["visitors_can_ask"])
        reply = self.call("/api/ai/settings", {"provider": "ollama", "visitors_can_ask": True})
        self.assertEqual(reply[0], 200)
        self.assertTrue(json.loads(reply[2])["visitors_can_ask"])
        # Now the request gets past the gate (and fails only on the unknown job).
        self.assertEqual(self.call("/api/ai/ask", {"job_id": "0" * 12}, visitor)[0], 404)
        # Visitors still cannot change it.
        self.assertEqual(self.call("/api/ai/settings",
                                   {"provider": "ollama", "visitors_can_ask": False},
                                   visitor)[0], 403)


class ShareCodeTests(unittest.TestCase):
    def test_code_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "share.json"
            first = webapp.load_share(path)
            self.assertRegex(first["code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(webapp.load_share(path), first)
            own = webapp.load_share(path, code="family-2026")
            self.assertEqual(own["code"], "family-2026")
            self.assertNotEqual(own["secret"], first["secret"])  # signs everyone out
            self.assertEqual(webapp.load_share(path, code="FAMILY 2026"), own)
            fresh = webapp.load_share(path, regenerate=True)
            self.assertNotEqual(fresh["code"], own["code"])
            with self.assertRaises(ValueError):
                webapp.load_share(path, code="abc")

    def test_login_route_absent_when_not_sharing(self):
        server = webapp.create_server(0, pipeline=fake_pipeline)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/login" % server.server_address[1],
                data=b'{"code": "x"}', headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
