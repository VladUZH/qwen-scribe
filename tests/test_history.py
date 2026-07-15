import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

import server


class TranscriptHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        server.TRANSCRIPTS_DIR = Path(self.temporary_directory.name) / "transcripts"
        server.TRANSCRIPTS_DIR.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def transcript(self, transcript_id: str, finished_at: float, text: str):
        job = {
            "id": transcript_id,
            "filename": "München meeting.m4a",
            "created_at": finished_at - 20,
            "started_at": finished_at - 10,
            "model": "1.7b",
            "language": "auto",
            "timestamps": False,
            "turbo": False,
            "context": "",
        }
        result = {
            "text": text,
            "language": "German",
            "segments": None,
            "truncated": False,
        }
        server._save_transcript(job, result, finished_at)

    def test_save_list_read_and_delete_transcript(self):
        self.transcript("aaaaaaaaaaaa", 100, "Grüezi Zürich")
        self.transcript("bbbbbbbbbbbb", 200, "Latest transcript")

        summaries = server.list_transcripts()["transcripts"]
        self.assertEqual([item["id"] for item in summaries], ["bbbbbbbbbbbb", "aaaaaaaaaaaa"])
        self.assertEqual(summaries[1]["preview"], "Grüezi Zürich")
        self.assertEqual(summaries[1]["word_count"], 2)

        saved = server.get_transcript("aaaaaaaaaaaa")
        self.assertEqual(saved["result"]["text"], "Grüezi Zürich")

        response = server.delete_transcript("aaaaaaaaaaaa")
        self.assertEqual(response.status_code, 204)
        self.assertFalse((server.TRANSCRIPTS_DIR / "aaaaaaaaaaaa.json").exists())

    def test_delete_all_ignores_damaged_history_file(self):
        self.transcript("cccccccccccc", 100, "One")
        (server.TRANSCRIPTS_DIR / "dddddddddddd.json").write_text("not json", encoding="utf-8")

        self.assertEqual(len(server.list_transcripts()["transcripts"]), 1)
        self.assertEqual(server.delete_all_transcripts(), {"deleted": 2})
        self.assertEqual(list(server.TRANSCRIPTS_DIR.glob("*.json")), [])

    def test_rejects_unsafe_transcript_id(self):
        with self.assertRaises(HTTPException) as error:
            server.get_transcript("../../secrets")
        self.assertEqual(error.exception.status_code, 404)

    def test_dictation_status_reports_each_macos_permission(self):
        server.dictation_heartbeat(
            accessibility=True,
            input_monitoring=False,
            microphone=True,
        )
        status = server.dictation_status()
        self.assertTrue(status["available"])
        self.assertTrue(status["accessibility"])
        self.assertFalse(status["input_monitoring"])
        self.assertTrue(status["microphone"])


class LocalRequestSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_accepts_same_origin_local_request(self):
        response = self.client.get(
            "/api/config",
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("connect-src 'self'", response.headers["content-security-policy"])

    def test_rejects_untrusted_host(self):
        response = self.client.get(
            "/api/config",
            headers={"Host": "malicious.example"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_cross_origin_browser_request(self):
        response = self.client.post(
            "/api/dictation/heartbeat",
            headers={"Origin": "https://malicious.example"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
