import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import server

# The server only trusts loopback Host headers, so the test client has to look
# like a real local browser rather than TestClient's default "testserver".
BASE_URL = "http://127.0.0.1:8990"


def local_client(**kwargs) -> TestClient:
    return TestClient(server.app, base_url=BASE_URL, **kwargs)


class TranscriptHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        transcripts = Path(self.temporary_directory.name) / "transcripts"
        transcripts.mkdir()
        # Restore the module global afterwards; leaving it pointed at a deleted
        # temp directory would corrupt every later test in the process.
        patcher = mock.patch.object(server, "TRANSCRIPTS_DIR", transcripts)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        self.assertEqual(summaries[1]["duration_seconds"], 10)

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

    def test_damaged_history_files_do_not_break_the_list(self):
        """One unreadable file must cost its own row, not the whole history."""
        self.transcript("aaaaaaaaaaaa", 100, "Readable")
        for name, body in [
            ("bbbbbbbbbbbb.json", "[1, 2, 3]"),          # JSON, but not an object
            ("cccccccccccc.json", "null"),
            ("dddddddddddd.json", "truncated{"),
            ("eeeeeeeeeeee.json", '{"result": {"text": 5}}'),
            ("ffffffffffff.json", '{"result": {"text": "x"}, "finished_at": "soon"}'),
            (
                "111111111111.json",
                '{"id": "111111111111", "result": {"text": "finite response"}, "finished_at": NaN}',
            ),
            (
                "222222222222.json",
                '{"id": "222222222222", "result": {"text": "bounded date"}, "finished_at": 1e300}',
            ),
            (
                "333333333333.json",
                '{"id": "333333333333", "result": {"text": "huge integer date"}, '
                f'"finished_at": {"9" * 400}}}',
            ),
            (
                "444444444444.json",
                '{"id": "444444444444", "result": {"text": "overflowing exponent"}, '
                '"finished_at": 1e999}',
            ),
        ]:
            (server.TRANSCRIPTS_DIR / name).write_text(body, encoding="utf-8")

        summaries = server.list_transcripts()["transcripts"]
        listed = {item["id"] for item in summaries}
        self.assertIn("aaaaaaaaaaaa", listed)
        # The two files that are objects degrade to empty text rather than 500.
        self.assertEqual(len(summaries), 7)
        for item in summaries:
            self.assertIsInstance(item["word_count"], int)
        # Invalid numeric timestamps must not make Starlette reject the whole
        # otherwise-readable history response as non-JSON.
        response = local_client().get("/api/transcripts")
        self.assertEqual(response.status_code, 200)
        invalid_ids = {
            "111111111111",
            "222222222222",
            "333333333333",
            "444444444444",
        }
        invalid_dates = {
            item["id"]: item["finished_at"]
            for item in response.json()["transcripts"]
            if item["id"] in invalid_ids
        }
        self.assertEqual(
            invalid_dates,
            {
                "111111111111": None,
                "222222222222": None,
                "333333333333": None,
                "444444444444": None,
            },
        )

        # The same damaged dates must not break bulk export or leak Python's
        # non-standard NaN token into transcripts.json.
        import io
        import zipfile

        export = local_client().get("/api/transcripts/export")
        self.assertEqual(export.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(export.content))
        exported_json = archive.read("transcripts.json").decode("utf-8")
        self.assertNotIn("NaN", exported_json)
        json.loads(exported_json, parse_constant=lambda value: self.fail(value))

    def test_delete_all_removes_orphaned_partial_writes(self):
        self.transcript("aaaaaaaaaaaa", 100, "One")
        (server.TRANSCRIPTS_DIR / "bbbbbbbbbbbb.json.tmp").write_text("{}", encoding="utf-8")
        self.assertEqual(server.delete_all_transcripts(), {"deleted": 2})
        self.assertEqual(list(server.TRANSCRIPTS_DIR.iterdir()), [])

    def test_save_transcript_leaves_no_partial_file_when_writing_fails(self):
        job = {"id": "aaaaaaaaaaaa", "filename": "x.wav"}
        with mock.patch.object(server.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                server._save_transcript(job, {"text": "hi"}, 100)
        self.assertEqual(list(server.TRANSCRIPTS_DIR.iterdir()), [])

    def test_search_matches_filename_and_full_text_case_insensitively(self):
        self.transcript("aaaaaaaaaaaa", 100, "Revenue forecast for Berlin")
        self.transcript("bbbbbbbbbbbb", 200, "Daily sync notes")
        self.transcript("cccccccccccc", 300, "Straße update")
        client = local_client()

        def ids(q):
            return [t["id"] for t in client.get("/api/transcripts", params={"q": q}).json()["transcripts"]]

        self.assertEqual(ids("BERLIN"), ["aaaaaaaaaaaa"])              # text, any case
        self.assertEqual(
            ids("münchen"),
            ["cccccccccccc", "bbbbbbbbbbbb", "aaaaaaaaaaaa"],
        )  # filename
        self.assertEqual(ids("berlin sync"), [])                        # AND across terms
        self.assertEqual(ids("revenue berlin"), ["aaaaaaaaaaaa"])
        self.assertEqual(ids("STRASSE"), ["cccccccccccc"])             # Unicode casefold
        self.assertEqual(
            ids(""), ["cccccccccccc", "bbbbbbbbbbbb", "aaaaaaaaaaaa"]
        )  # empty = all

    def test_export_returns_zip_with_text_and_json(self):
        import io
        import zipfile

        self.transcript("aaaaaaaaaaaa", 100, "First transcript")
        self.transcript("bbbbbbbbbbbb", 200, "Second transcript")
        (server.TRANSCRIPTS_DIR / "cccccccccccc.json").write_text("damaged{", encoding="utf-8")

        response = local_client().get("/api/transcripts/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertIn("transcripts.json", names)
        self.assertEqual(len([n for n in names if n.startswith("text/")]), 2)
        text_member = [n for n in names if n.startswith("text/")][0]
        self.assertIn("transcript", archive.read(text_member).decode("utf-8"))

    def test_export_with_no_transcripts_is_a_404(self):
        self.assertEqual(local_client().get("/api/transcripts/export").status_code, 404)

    def test_rejects_unsafe_transcript_id(self):
        for unsafe in ("../../secrets", "AAAAAAAAAAAA", "aaaaaaaaaaa", "aaaaaaaaaaaaa", ""):
            with self.assertRaises(HTTPException) as error:
                server.get_transcript(unsafe)
            self.assertEqual(error.exception.status_code, 404)

    def test_deleting_a_missing_transcript_is_a_404_not_a_crash(self):
        with self.assertRaises(HTTPException) as error:
            server.delete_transcript("aaaaaaaaaaaa")
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_a_transcript_also_forgets_its_finished_job(self):
        """Otherwise GET /api/jobs/{id} keeps serving text the user deleted."""
        self.transcript("aaaaaaaaaaaa", 100, "Secret")
        with mock.patch.dict(
            server.jobs,
            {"aaaaaaaaaaaa": {"id": "aaaaaaaaaaaa", "status": "done", "result": {"text": "Secret"}}},
            clear=True,
        ):
            server.delete_transcript("aaaaaaaaaaaa")
            self.assertNotIn("aaaaaaaaaaaa", server.jobs)

    def test_running_jobs_are_never_evicted(self):
        old = time.time() - 10 * 60 * 60
        with mock.patch.dict(
            server.jobs,
            {
                "aaaaaaaaaaaa": {"id": "a" * 12, "status": "processing", "created_at": old},
                "bbbbbbbbbbbb": {"id": "b" * 12, "status": "done", "finished_at": old},
            },
            clear=True,
        ):
            with server.jobs_lock:
                server._prune_jobs_locked()
            self.assertIn("aaaaaaaaaaaa", server.jobs)
            self.assertNotIn("bbbbbbbbbbbb", server.jobs)

    def test_job_store_stays_bounded(self):
        now = time.time()
        crowd = {
            f"{index:012x}": {"id": f"{index:012x}", "status": "done", "finished_at": now - index}
            for index in range(server.MAX_REMEMBERED_JOBS + 25)
        }
        with mock.patch.dict(server.jobs, crowd, clear=True):
            with server.jobs_lock:
                server._prune_jobs_locked()
            self.assertEqual(len(server.jobs), server.MAX_REMEMBERED_JOBS)
            # The most recent ones are the ones a browser might still poll.
            self.assertIn(f"{0:012x}", server.jobs)

    def test_job_store_is_pruned_as_queued_jobs_finish(self):
        now = time.time()
        crowd = {
            f"{index:012x}": {
                "id": f"{index:012x}",
                "status": "queued",
                "created_at": now - index,
            }
            for index in range(server.MAX_REMEMBERED_JOBS + 25)
        }
        with mock.patch.dict(server.jobs, crowd, clear=True):
            for job_id in list(crowd):
                server._update(job_id, status="done")
            self.assertEqual(len(server.jobs), server.MAX_REMEMBERED_JOBS)

    def test_newly_failed_long_job_gets_a_full_retention_window(self):
        old = time.time() - 10 * 60 * 60
        with mock.patch.dict(
            server.jobs,
            {"aaaaaaaaaaaa": {"id": "a" * 12, "status": "processing", "created_at": old}},
            clear=True,
        ):
            server._update("aaaaaaaaaaaa", status="error", detail="late failure")
            self.assertIn("aaaaaaaaaaaa", server.jobs)
            self.assertGreater(server.jobs["aaaaaaaaaaaa"]["finished_at"], old)


class DictationStatusTests(unittest.TestCase):
    def setUp(self):
        self.client = local_client()
        self.addCleanup(
            lambda: server.dictation_state.update(
                last_seen=0.0, accessibility=None, input_monitoring=None, microphone=None
            )
        )

    def test_heartbeat_over_http_reports_each_macos_permission(self):
        """Exactly the URL native/DictationHelper.m builds."""
        response = self.client.post(
            "/api/dictation/heartbeat"
            "?accessibility=true&input_monitoring=false&microphone=true"
        )
        self.assertEqual(response.status_code, 200)

        status = self.client.get("/api/dictation/status").json()
        self.assertTrue(status["available"])
        self.assertTrue(status["accessibility"])
        self.assertFalse(status["input_monitoring"])
        self.assertTrue(status["microphone"])

    def test_status_goes_unavailable_when_the_helper_stops_reporting(self):
        server.dictation_state.update(
            last_seen=time.time() - 31, accessibility=True,
            input_monitoring=True, microphone=True,
        )
        self.assertFalse(self.client.get("/api/dictation/status").json()["available"])


class LocalRequestSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = local_client()

    def test_accepts_same_origin_local_request(self):
        response = self.client.get("/api/config", headers={"Origin": BASE_URL})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("connect-src 'self'", response.headers["content-security-policy"])

    def test_accepts_request_without_an_origin_header(self):
        """The native dictation helper is not a browser and sends no Origin."""
        self.assertEqual(self.client.get("/api/config").status_code, 200)

    def test_rejects_untrusted_host(self):
        for host in ("malicious.example", "testserver", "qwen-scribe.local"):
            response = self.client.get("/api/config", headers={"Host": host})
            self.assertEqual(response.status_code, 400, host)

    def test_rejects_cross_origin_browser_request(self):
        response = self.client.post(
            "/api/dictation/heartbeat",
            headers={"Origin": "https://malicious.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rejected_requests_still_carry_security_headers(self):
        response = self.client.get("/api/config", headers={"Host": "malicious.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_config_advertises_the_formats_the_server_actually_accepts(self):
        config = self.client.get("/api/config").json()
        self.assertEqual(config["extensions"], sorted(server.ALLOWED_SUFFIXES))
        self.assertIn(config["default_model"], config["models"])


if __name__ == "__main__":
    unittest.main()
