"""Upload validation for POST /api/jobs — the app's primary endpoint.

No model is ever loaded: the worker is replaced with a double, so these tests
run on any machine, with or without MLX.
"""

import sys
import tempfile
import threading
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server

BASE_URL = "http://127.0.0.1:8990"
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "
SAMPLE_RATE = 16000


class FakeResult:
    """The subset of an mlx_qwen3_asr result that _run_job reads."""

    def __init__(self, text="hello", language="English", segments=None, truncated=False):
        self.text = text
        self.language = language
        self.segments = segments
        self.truncated = truncated


class WorkerTestCase(unittest.TestCase):
    """Base class that runs _run_job against a fake model and a fake decoder.

    CI has no MLX, and even where it exists a real transcription would download
    weights, so both the audio helpers and the session are replaced. The audio
    helpers are imported inside _run_job, which means they have to be stubbed
    in sys.modules rather than on an already-imported module object.
    """

    def setUp(self):
        self.addCleanup(server.jobs.clear)
        transcripts = tempfile.TemporaryDirectory()
        self.addCleanup(transcripts.cleanup)
        directory = mock.patch.object(server, "TRANSCRIPTS_DIR", Path(transcripts.name))
        directory.start()
        self.addCleanup(directory.stop)

    def install_audio_stubs(self, chunks: int, seconds_per_chunk: float = 30.0):
        samples = [0.0] * int(SAMPLE_RATE * seconds_per_chunk)
        package = types.ModuleType("mlx_qwen3_asr")
        audio = types.ModuleType("mlx_qwen3_asr.audio")
        audio.load_audio_np = lambda path, sr=SAMPLE_RATE: samples * chunks
        chunking = types.ModuleType("mlx_qwen3_asr.chunking")
        chunking.split_audio_into_chunks = lambda waveform, sr, size: [
            (samples, index * seconds_per_chunk) for index in range(chunks)
        ]
        package.audio = audio
        package.chunking = chunking
        modules = mock.patch.dict(
            sys.modules,
            {
                "mlx_qwen3_asr": package,
                "mlx_qwen3_asr.audio": audio,
                "mlx_qwen3_asr.chunking": chunking,
            },
        )
        modules.start()
        self.addCleanup(modules.stop)

    def stage_job(self, **overrides) -> str:
        """Create a job record with a real staged upload file behind it."""
        job_id = uuid.uuid4().hex[:12]
        upload = server.UPLOAD_DIR / f"{job_id}.wav"
        upload.write_bytes(WAV)
        self.addCleanup(upload.unlink, True)
        server.jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "detail": "Queued",
            "progress": 0.0,
            "filename": "clip.wav",
            "size": len(WAV),
            "model": "1.7b",
            "language": "auto",
            "timestamps": False,
            "turbo": False,
            "partial": None,
            "context": "",
            "path": str(upload),
            "cancelled": threading.Event(),
            "created_at": 1.0,
            "result": None,
            **overrides,
        }
        return job_id

    def run_worker(self, session, chunks=1, **overrides) -> dict:
        """Run _run_job to completion against `session` and return the record."""
        self.install_audio_stubs(chunks)
        job_id = self.stage_job(**overrides)
        with mock.patch.object(server, "get_session", return_value=session):
            server._run_job(job_id)
        return server.jobs[job_id]


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


class TimestampFallbackTests(WorkerTestCase):
    """A word-timestamp failure must cost the timestamps, never the transcript."""

    def test_aligner_failure_keeps_text_and_drops_timestamps(self):
        calls = []

        class FlakyAlignerSession:
            def transcribe(self, audio, **kwargs):
                calls.append(kwargs.get("return_timestamps"))
                if kwargs.get("return_timestamps"):
                    raise RuntimeError(
                        "Korean tokenization requires optional dependency `soynlp`. "
                        'Install with: pip install "mlx-qwen3-asr[aligner]"'
                    )
                return FakeResult(text="안녕하세요", language="Korean")

        job = self.run_worker(FlakyAlignerSession(), chunks=2, timestamps=True)

        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["text"], "안녕하세요 안녕하세요")
        self.assertIsNone(job["result"]["segments"])
        self.assertIn("soynlp", job["timestamps_unavailable"])
        # The failing chunk is retried without timestamps, and every later
        # chunk skips them outright rather than failing again.
        self.assertEqual(calls, [True, False, False])

    def test_a_normal_job_reports_no_timestamp_problem(self):
        class GoodSession:
            def transcribe(self, audio, **kwargs):
                return FakeResult(
                    segments=[{"text": "hello", "start": 0.0, "end": 0.5}]
                )

        job = self.run_worker(GoodSession(), timestamps=True)

        self.assertEqual(job["status"], "done")
        self.assertIsNone(job["timestamps_unavailable"])
        self.assertEqual(len(job["result"]["segments"]), 1)

    def test_a_real_transcription_failure_still_fails_the_job(self):
        """Only timestamp failures degrade; a decode failure must surface."""

        class BrokenSession:
            def transcribe(self, audio, **kwargs):
                raise RuntimeError("Metal device lost")

        job = self.run_worker(BrokenSession(), timestamps=False)

        self.assertEqual(job["status"], "error")
        self.assertIn("Metal device lost", job["detail"])


class QueueControlTests(WorkerTestCase):
    """Listing, cancelling, and retrying — the queue the UI now renders."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(server.app, base_url=BASE_URL)
        submit = mock.patch.object(server.executor, "submit")
        self.submit = submit.start()
        self.addCleanup(submit.stop)

    def test_listing_shows_every_job_newest_first_and_hides_internals(self):
        older = self.stage_job(created_at=1.0, filename="a.wav")
        newer = self.stage_job(created_at=2.0, filename="b.wav")

        listing = self.client.get("/api/jobs").json()["jobs"]

        self.assertEqual([job["id"] for job in listing], [newer, older])
        self.assertEqual([job["filename"] for job in listing], ["b.wav", "a.wav"])
        self.assertNotIn("path", listing[0])
        self.assertNotIn("cancelled", listing[0])

    def test_cancelling_a_queued_job_marks_it_and_removes_the_upload(self):
        job_id = self.stage_job()
        upload = Path(server.jobs[job_id]["path"])
        self.assertTrue(upload.exists())

        response = self.client.post(f"/api/jobs/{job_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.jobs[job_id]["status"], "cancelled")
        self.assertFalse(upload.exists())

    def test_cancelling_is_rejected_once_the_job_has_finished(self):
        job_id = self.stage_job(status="done")
        response = self.client.post(f"/api/jobs/{job_id}/cancel")
        self.assertEqual(response.status_code, 409)

    def test_cancelling_an_unknown_job_is_a_404(self):
        self.assertEqual(
            self.client.post("/api/jobs/deadbeefcafe/cancel").status_code, 404
        )

    def test_a_job_cancelled_while_queued_never_loads_a_model(self):
        """The worker must notice the flag before it touches the GPU."""
        self.install_audio_stubs(chunks=1)
        job_id = self.stage_job()
        self.client.post(f"/api/jobs/{job_id}/cancel")

        with mock.patch.object(server, "get_session") as get_session:
            server._run_job(job_id)

        get_session.assert_not_called()
        self.assertEqual(server.jobs[job_id]["status"], "cancelled")

    def test_cancelling_mid_run_stops_at_the_next_chunk_boundary(self):
        job_id = None

        class CancellingSession:
            def transcribe(inner, audio, **kwargs):
                # Cancel from inside the first chunk, as the user would.
                server.jobs[job_id]["cancelled"].set()
                return FakeResult(text="partial")

        self.install_audio_stubs(chunks=3)
        job_id = self.stage_job()
        with mock.patch.object(server, "get_session", return_value=CancellingSession()):
            server._run_job(job_id)

        job = server.jobs[job_id]
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(job["result"])
        self.assertFalse(Path(job["path"]).exists())

    def test_retry_reuses_the_upload_and_the_original_options(self):
        job_id = self.stage_job(
            status="error", filename="a.wav", language="Korean", timestamps=True
        )

        body = self.client.post(f"/api/jobs/{job_id}/retry").json()

        retried = server.jobs[body["id"]]
        self.assertNotEqual(body["id"], job_id)
        self.assertEqual(retried["filename"], "a.wav")
        self.assertEqual(retried["language"], "Korean")
        self.assertTrue(retried["timestamps"])
        self.assertEqual(retried["status"], "queued")
        self.assertTrue(Path(retried["path"]).exists())
        self.addCleanup(Path(retried["path"]).unlink, True)
        self.submit.assert_called_once_with(server._run_job, body["id"])

    def test_retry_is_rejected_for_a_running_job(self):
        job_id = self.stage_job(status="processing")
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/retry").status_code, 409)

    def test_retry_is_rejected_for_a_cancelled_job(self):
        """Cancelling deletes the upload, so Retry there could never work."""
        job_id = self.stage_job(status="cancelled")
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/retry").status_code, 409)

    def test_retry_without_the_upload_asks_for_a_fresh_one(self):
        job_id = self.stage_job(status="error")
        Path(server.jobs[job_id]["path"]).unlink()

        response = self.client.post(f"/api/jobs/{job_id}/retry")

        self.assertEqual(response.status_code, 404)
        self.assertIn("upload it again", response.json()["detail"])

    def test_a_failed_job_keeps_its_upload_so_it_can_be_retried(self):
        class BrokenSession:
            def transcribe(self, audio, **kwargs):
                raise RuntimeError("Metal device lost")

        job = self.run_worker(BrokenSession())

        self.assertEqual(job["status"], "error")
        self.assertTrue(Path(job["path"]).exists())

    def test_evicting_a_failed_job_deletes_its_retained_upload(self):
        job_id = self.stage_job(status="error", finished_at=1.0)
        upload = Path(server.jobs[job_id]["path"])

        with mock.patch.object(server, "JOB_RETENTION_SECONDS", 0):
            with server.jobs_lock:
                server._prune_jobs_locked()

        self.assertNotIn(job_id, server.jobs)
        self.assertFalse(upload.exists())


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
