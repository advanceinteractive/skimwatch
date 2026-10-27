"""Unit tests for the formatting and validation helpers (no network needed)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import local_transcribe as lt


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class HelperTests(unittest.TestCase):
    def test_sanitize_filename(self):
        self.assertEqual(lt.sanitize_filename('My: Video / "Title"?'), "My_Video_Title")
        self.assertEqual(lt.sanitize_filename("???"), "transcript")
        self.assertEqual(lt.sanitize_filename(""), "transcript")
        self.assertEqual(len(lt.sanitize_filename("a" * 500)), 120)
        self.assertEqual(lt.sanitize_filename("Über café 日本語"), "Über_café_日本語")

    def test_format_timestamp_srt(self):
        self.assertEqual(lt.format_timestamp_srt(0), "00:00:00,000")
        self.assertEqual(lt.format_timestamp_srt(3661.5), "01:01:01,500")
        self.assertEqual(lt.format_timestamp_srt(59.9996), "00:01:00,000")
        self.assertEqual(lt.format_timestamp_srt(None), "00:00:00,000")
        self.assertEqual(lt.format_timestamp_srt(-3), "00:00:00,000")

    def test_format_length(self):
        self.assertEqual(lt.format_length(0), "0 minutes")
        self.assertEqual(lt.format_length(61), "1 minute")
        self.assertEqual(lt.format_length(45 * 60), "45 minutes")
        self.assertEqual(lt.format_length(3600), "1 hour")
        self.assertEqual(lt.format_length(2 * 3600 + 30 * 60), "2 hours 30 minutes")
        self.assertEqual(lt.format_length(3 * 3600), "3 hours")

    def test_format_clock(self):
        self.assertEqual(lt.format_clock(5), "0:05")
        self.assertEqual(lt.format_clock(125.9), "2:05")
        self.assertEqual(lt.format_clock(3725), "1:02:05")
        self.assertEqual(lt.format_clock(None), "0:00")

    def test_validate_url(self):
        self.assertEqual(lt.validate_url("  https://youtu.be/abc  "), "https://youtu.be/abc")
        self.assertEqual(lt.validate_url("youtube.com/watch?v=abc"),
                         "https://youtube.com/watch?v=abc")
        for bad in ("", "   ", "file:///etc/passwd", "ftp://example.com/a",
                    "not a link", "https://localhost"):
            with self.assertRaises(lt.TranscribeError, msg=bad):
                lt.validate_url(bad)

    def test_resolve_model(self):
        self.assertEqual(lt.resolve_model("Base"), "base")
        self.assertEqual(lt.resolve_model("large"), "large-v3")
        self.assertEqual(lt.resolve_model(""), lt.DEFAULT_MODEL)
        with self.assertRaises(lt.TranscribeError):
            lt.resolve_model("enormous")


class ParagraphTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(lt.build_paragraphs([]), [])
        self.assertEqual(lt.build_paragraphs([seg(0, 1, "   ")]), [])

    def test_breaks_on_pause_after_sentence(self):
        paras = lt.build_paragraphs([
            seg(0, 2, "Hello there."),
            seg(2.1, 4, "This is one thought."),
            seg(8, 10, "After a pause, another."),
        ])
        self.assertEqual([p["text"] for p in paras],
                         ["Hello there. This is one thought.", "After a pause, another."])
        self.assertEqual([p["start"] for p in paras], [0.0, 8.0])

    def test_no_break_mid_sentence_on_pause(self):
        paras = lt.build_paragraphs([seg(0, 2, "Hello there and"), seg(9, 10, "welcome.")])
        self.assertEqual(len(paras), 1)

    def test_breaks_when_long_enough(self):
        segments = [seg(i, i + 1, "This sentence has some words in it.") for i in range(40)]
        paras = lt.build_paragraphs(segments, target_chars=200)
        self.assertGreater(len(paras), 3)
        for p in paras:
            self.assertTrue(p["text"].endswith("."))
            self.assertLess(len(p["text"]), 300)
        self.assertEqual(" ".join(p["text"] for p in paras),
                         " ".join(s["text"] for s in segments))

    def test_force_split_without_punctuation(self):
        segments = [seg(i, i + 1, "no punctuation here at all") for i in range(100)]
        paras = lt.build_paragraphs(segments, max_chars=500)
        self.assertGreater(len(paras), 3)
        self.assertTrue(all(len(p["text"]) < 560 for p in paras))

    def test_sentence_end_inside_quotes(self):
        paras = lt.build_paragraphs([seg(0, 1, 'He said "stop."'), seg(5, 6, "Then left.")])
        self.assertEqual(len(paras), 2)

    def test_unspaced_language(self):
        paras = lt.build_paragraphs([seg(0, 1, "こんにちは"), seg(1, 2, "世界。")], language="ja")
        self.assertEqual(paras[0]["text"], "こんにちは世界。")


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.segments = [seg(0, 1.5, " Hello there. "), seg(1.5, 3, "General Kenobi.")]

    def test_format_srt(self):
        self.assertEqual(
            lt.format_srt(self.segments),
            "1\n00:00:00,000 --> 00:00:01,500\nHello there.\n\n"
            "2\n00:00:01,500 --> 00:00:03,000\nGeneral Kenobi.\n",
        )

    def test_format_transcript(self):
        paras = [{"start": 0, "text": "One."}, {"start": 65, "text": "Two."}]
        self.assertEqual(lt.format_transcript(paras), "One.\n\nTwo.\n")
        self.assertEqual(
            lt.format_transcript(paras, "Title", "https://x.test/v", timestamps=True),
            "Title\n=====\nSource: https://x.test/v\n\n[0:00] One.\n\n[1:05] Two.\n",
        )

    def test_write_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "dir"
            txt, srt = lt.write_outputs({"segments": self.segments, "language": "en"},
                                        out, "name", "My Title", "https://x.test/v")
            self.assertEqual(txt.read_text(encoding="utf-8"),
                             "My Title\n========\nSource: https://x.test/v\n\n"
                             "Hello there. General Kenobi.\n")
            self.assertTrue(srt.read_text(encoding="utf-8").startswith("1\n00:00:00,000"))


class ChunkTests(unittest.TestCase):
    def test_short_audio_is_one_piece(self):
        import numpy as np
        audio = np.ones(lt.SAMPLE_RATE * 100, dtype=np.float32)
        self.assertEqual(lt.chunk_bounds(audio, chunk_seconds=600), [(0, len(audio))])
        self.assertEqual(lt.chunk_bounds(audio[:0]), [(0, 0)])

    def test_cuts_in_the_quiet_spot(self):
        import numpy as np
        rate = lt.SAMPLE_RATE
        audio = np.ones(rate * 250, dtype=np.float32)
        audio[rate * 52:rate * 53] = 0.0     # a pause inside the first search window
        audio[rate * 111:rate * 112] = 0.0   # and inside the second
        bounds = lt.chunk_bounds(audio, chunk_seconds=60, search_seconds=10)
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], len(audio))
        for (_, end), (begin, _) in zip(bounds, bounds[1:]):
            self.assertEqual(end, begin)     # contiguous, nothing dropped
        self.assertTrue(52 <= bounds[0][1] / rate <= 53)
        self.assertTrue(111 <= bounds[1][1] / rate <= 112)
        self.assertTrue(all(end - begin <= 70 * rate for begin, end in bounds))


class DownloadTests(unittest.TestCase):
    def fake_downloader(self, failures, message="HTTP Error 403: Forbidden"):
        calls = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True):
                calls.append(self.opts)
                if len(calls) <= failures:
                    raise RuntimeError("ERROR: unable to download video data: " + message)
                path = Path(self.opts["outtmpl"].replace("%(id)s.%(ext)s", "abc.webm"))
                path.write_bytes(b"audio")
                return {"id": "abc", "title": "A Title", "duration": 12,
                        "requested_downloads": [{"filepath": str(path)}]}

        return FakeYoutubeDL, calls

    def test_options_and_result(self):
        fake, calls = self.fake_downloader(failures=0)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("yt_dlp.YoutubeDL", fake):
            media = lt.download_audio("https://a.test/v", Path(tmp))
            self.assertTrue(media["path"].exists())
        self.assertEqual((media["id"], media["title"], media["duration"]), ("abc", "A Title", 12))
        self.assertEqual(media["webpage_url"], "https://a.test/v")
        self.assertTrue(calls[0]["noplaylist"])
        self.assertEqual(calls[0]["playlist_items"], "1")
        self.assertNotIn("postprocessors", calls[0])  # no ffmpeg step

    def test_retries_once_on_403(self):
        fake, calls = self.fake_downloader(failures=1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("yt_dlp.YoutubeDL", fake):
            lt.download_audio("https://a.test/v", Path(tmp))
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_second_403(self):
        fake, calls = self.fake_downloader(failures=5)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("yt_dlp.YoutubeDL", fake):
            with self.assertRaises(lt.TranscribeError) as ctx:
                lt.download_audio("https://a.test/v", Path(tmp))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("ERROR:", str(ctx.exception))

    def test_refuses_long_and_live_videos_by_reason(self):
        fake, calls = self.fake_downloader(failures=0)

        class Filtering(fake):
            info = {"duration": 4 * 3600}

            def extract_info(self, url, download=True):
                reason = self.opts["match_filter"](self.info, incomplete=False)
                if reason:
                    calls.append(self.opts)
                    return None
                return super().extract_info(url, download)

        with tempfile.TemporaryDirectory() as tmp, mock.patch("yt_dlp.YoutubeDL", Filtering):
            with self.assertRaises(lt.TranscribeError) as ctx:
                lt.download_audio("https://a.test/v", Path(tmp), max_seconds=3 * 3600)
            self.assertIn("4 hours long", str(ctx.exception))
            self.assertIn("up to 3 hours", str(ctx.exception))
            # Without a limit the same video is fine.
            self.assertEqual(lt.download_audio("https://a.test/v", Path(tmp))["id"], "abc")
            Filtering.info = {"is_live": True, "duration": 10}
            with self.assertRaises(lt.TranscribeError) as ctx:
                lt.download_audio("https://a.test/v", Path(tmp))
            self.assertIn("live stream", str(ctx.exception))
        self.assertEqual(len(calls), 3)

    def test_other_errors_are_not_retried(self):
        fake, calls = self.fake_downloader(failures=5, message="This video is unavailable")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("yt_dlp.YoutubeDL", fake):
            with self.assertRaises(lt.TranscribeError):
                lt.download_audio("https://a.test/v", Path(tmp))
        self.assertEqual(len(calls), 1)


class PipelineValidationTests(unittest.TestCase):
    def test_rejects_bad_input_before_downloading(self):
        with self.assertRaises(lt.TranscribeError):
            lt.run_pipeline("not a link")
        with self.assertRaises(lt.TranscribeError):
            lt.run_pipeline("https://example.com/v", model_name="enormous")


if __name__ == "__main__":
    unittest.main()
