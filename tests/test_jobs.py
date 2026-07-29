"""Upload validation for POST /api/jobs — the app's primary endpoint.

No model is ever loaded: the worker is replaced with a double, so these tests
run on any machine, with or without MLX.
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import server

BASE_URL = "http://127.0.0.1:8990"
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


class JobUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url=BASE_URL)
        # Never start a real transcription; that would download model weights.
        submit = mock.patch.object(server.executor, "submit")
        self.submit = submit.start()
        self.addCleanup(submit.stop)
        self.addCleanup(server.jobs.clear)

    def post(self, filename="clip.wav", content=WAV, **fields):
        data = {"model": "1.7b", "language": "English", **fields}
        return self.client.post(
            "/api/jobs", data=data, files={"file": (filename, content)}
        )

    def leftover_uploads(self):
        return sorted(path.name for path in server.UPLOAD_DIR.iterdir())

    def test_accepts_a_wav_upload_and_queues_one_job(self):
        before = self.leftover_uploads()
        response = self.post()
        self.assertEqual(response.status_code, 200)

        job_id = response.json()["id"]
        self.assertIn(job_id, server.jobs)
        self.assertEqual(server.jobs[job_id]["status"], "queued")
        self.assertEqual(server.jobs[job_id]["size"], len(WAV))
        self.submit.assert_called_once_with(server._run_job, job_id)

        staged = set(self.leftover_uploads()) - set(before)
        self.assertEqual(staged, {f"{job_id}.wav"})
        (server.UPLOAD_DIR / f"{job_id}.wav").unlink()

    def test_rejects_unknown_model(self):
        response = self.post(model="70b")
        self.assertEqual(response.status_code, 400)
        self.assertIn("70b", response.json()["detail"])

    def test_rejects_unsupported_language(self):
        response = self.post(language="Klingon")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Klingon", response.json()["detail"])

    def test_rejects_disallowed_extension(self):
        response = self.post(filename="payload.exe")
        self.assertEqual(response.status_code, 400)
        self.assertIn(".exe", response.json()["detail"])

    def test_rejects_a_file_with_no_extension(self):
        self.assertEqual(self.post(filename="recording").status_code, 400)

    def test_non_wav_upload_requires_ffmpeg(self):
        with mock.patch.object(server.shutil, "which", return_value=None):
            response = self.post(filename="clip.m4a")
            self.assertEqual(response.status_code, 400)
            self.assertIn("ffmpeg", response.json()["detail"])
            # WAV still works without ffmpeg.
            self.assertEqual(self.post().status_code, 200)

    def test_oversize_upload_is_rejected_and_leaves_no_temporary_file(self):
        before = self.leftover_uploads()
        with mock.patch.object(server, "MAX_UPLOAD_BYTES", 8):
            response = self.post(content=b"x" * 4096)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.leftover_uploads(), before)
        self.submit.assert_not_called()

    def test_oversize_upload_is_refused_before_the_body_is_read(self):
        """A declared Content-Length over the cap must not touch the disk."""
        before = self.leftover_uploads()
        response = self.client.post(
            "/api/jobs",
            headers={"Content-Length": str(server.MAX_UPLOAD_BYTES + 8 * 1024 * 1024)},
            content=b"ignored",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.leftover_uploads(), before)

    def test_unknown_job_is_a_404(self):
        self.assertEqual(self.client.get("/api/jobs/deadbeefdead").status_code, 404)

    def test_job_status_never_exposes_the_staged_upload_path(self):
        response = self.post()
        job_id = response.json()["id"]
        body = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertNotIn("path", body)
        self.assertEqual(body["id"], job_id)
        (server.UPLOAD_DIR / f"{job_id}.wav").unlink()


class JobShutdownTests(unittest.TestCase):
    def test_shutdown_marks_unfinished_jobs_as_failed(self):
        """Queued work must not silently drain after the server is stopped."""
        self.addCleanup(server.stopping.clear)
        self.addCleanup(server.jobs.clear)
        with mock.patch.object(server.executor, "submit"), mock.patch.object(
            server.executor, "shutdown"
        ) as shutdown:
            with mock.patch.dict(
                server.jobs,
                {
                    "aaaaaaaaaaaa": {"id": "a" * 12, "status": "queued"},
                    "bbbbbbbbbbbb": {"id": "b" * 12, "status": "done"},
                },
                clear=True,
            ):
                with TestClient(server.app, base_url=BASE_URL):
                    pass
                self.assertEqual(server.jobs["aaaaaaaaaaaa"]["status"], "error")
                self.assertEqual(server.jobs["bbbbbbbbbbbb"]["status"], "done")
        self.assertTrue(server.stopping.is_set())
        shutdown.assert_called_once_with(wait=False, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
