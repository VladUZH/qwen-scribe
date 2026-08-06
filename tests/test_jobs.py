"""Upload validation for POST /api/jobs — the app's primary endpoint.

No model is ever loaded: the worker is replaced with a double, so these tests
run on any machine, with or without MLX.
"""

import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import ModuleType, SimpleNamespace
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
        # Stage uploads somewhere of our own. Writing into the shared UPLOAD_DIR
        # would put this suite's files next to those of a server running on the
        # same machine, where its startup sweep can see them.
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        upload_directory = mock.patch.object(server, "UPLOAD_DIR", Path(uploads.name))
        upload_directory.start()
        self.addCleanup(upload_directory.stop)

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
            # A real job is created now, and the retention sweep goes by age:
            # a fixed epoch timestamp would make every staged job instantly
            # evictable and quietly change what the test under it exercises.
            "created_at": time.time(),
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
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        upload_dir = Path(self.temporary_directory.name)
        upload_patcher = mock.patch.object(server, "UPLOAD_DIR", upload_dir)
        upload_patcher.start()
        self.addCleanup(upload_patcher.stop)
        # Never start a real transcription; that would download model weights.
        submit = mock.patch.object(server.executor, "submit")
        self.submit = submit.start()
        self.addCleanup(submit.stop)
        self.addCleanup(server.jobs.clear)
        self.addCleanup(server.job_futures.clear)

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

    def test_rejects_known_oversize_before_copying_the_upload(self):
        before = self.leftover_uploads()
        with mock.patch.object(server, "MAX_UPLOAD_BYTES", 8), mock.patch(
            "starlette.datastructures.UploadFile.read"
        ) as read:
            response = self.post(content=b"x" * 4096)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.leftover_uploads(), before)
        read.assert_not_called()
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

    def test_rejects_vocabulary_hints_longer_than_the_stored_limit(self):
        """The hint is prepended to every chunk's prompt, so an unbounded one
        is paid for on every chunk of a two-hour file."""
        before = self.leftover_uploads()

        response = self.post(context="x" * (server.MAX_CONTEXT_CHARS + 1))

        self.assertEqual(response.status_code, 400)
        self.assertIn(str(server.MAX_CONTEXT_CHARS), response.json()["detail"])
        self.assertEqual(self.leftover_uploads(), before)
        self.submit.assert_not_called()

    def test_accepts_vocabulary_hints_up_to_the_limit(self):
        response = self.post(context="x" * server.MAX_CONTEXT_CHARS)
        self.assertEqual(response.status_code, 200)
        (server.UPLOAD_DIR / f"{response.json()['id']}.wav").unlink()

    def test_unknown_job_is_a_404(self):
        self.assertEqual(self.client.get("/api/jobs/deadbeefdead").status_code, 404)

    def test_job_status_never_exposes_the_staged_upload_path(self):
        response = self.post()
        job_id = response.json()["id"]
        body = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertNotIn("path", body)
        self.assertEqual(body["id"], job_id)
        (server.UPLOAD_DIR / f"{job_id}.wav").unlink()

    def test_submission_failure_removes_the_job_and_staged_upload(self):
        self.submit.side_effect = RuntimeError("executor is shutting down")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(server.jobs, {})
        self.assertEqual(self.leftover_uploads(), [])


class JobWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        transcripts = root / "transcripts"
        transcripts.mkdir()
        self.transcripts_patcher = mock.patch.object(server, "TRANSCRIPTS_DIR", transcripts)
        self.transcripts_patcher.start()
        self.addCleanup(self.transcripts_patcher.stop)
        self.addCleanup(server.jobs.clear)
        self.addCleanup(server.job_futures.clear)
        self.addCleanup(server.stopping.clear)

    def test_turbo_reuses_the_draft_session_model_and_joins_cjk_chunks(self):
        staged = Path(self.temporary_directory.name) / "staged.wav"
        staged.write_bytes(WAV)
        job_id = "aaaaaaaaaaaa"
        server.jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "detail": "Queued",
            "progress": 0.0,
            "filename": "Chinese.wav",
            "size": len(WAV),
            "model": "1.7b",
            "language": "auto",
            "timestamps": False,
            "turbo": True,
            "partial": None,
            "context": "",
            "path": str(staged),
            "cancelled": Event(),
            "created_at": 100.0,
            "result": None,
        }

        draft_model = object()
        draft_session = SimpleNamespace(model=draft_model)
        target_session = mock.Mock()
        target_session.transcribe.side_effect = [
            SimpleNamespace(text="你好", language="Chinese", segments=None, truncated=False),
            SimpleNamespace(text="世界", language="Chinese", segments=None, truncated=False),
        ]
        package = ModuleType("mlx_qwen3_asr")
        package.__path__ = []
        audio_module = ModuleType("mlx_qwen3_asr.audio")
        audio_module.load_audio_np = lambda path, sr: [0] * 32_000
        chunking_module = ModuleType("mlx_qwen3_asr.chunking")
        chunking_module.split_audio_into_chunks = lambda audio, sr, seconds: [
            ([0] * 16_000, 0.0),
            ([0] * 16_000, 1.0),
        ]

        def session_for(model):
            return target_session if model == "1.7b" else draft_session

        with mock.patch.object(server, "get_session", side_effect=session_for), mock.patch.dict(
            "sys.modules",
            {
                "mlx_qwen3_asr": package,
                "mlx_qwen3_asr.audio": audio_module,
                "mlx_qwen3_asr.chunking": chunking_module,
            },
        ):
            server._run_job(job_id)

        self.assertEqual(server.jobs[job_id]["status"], "done")
        self.assertEqual(server.jobs[job_id]["result"]["text"], "你好世界")
        self.assertFalse(staged.exists())
        self.assertEqual(target_session.transcribe.call_count, 2)
        for call in target_session.transcribe.call_args_list:
            self.assertIs(call.kwargs["draft_model"], draft_model)


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
        # Korean chunks are joined without inserted whitespace, per
        # _join_transcript_texts.
        self.assertEqual(job["result"]["text"], "안녕하세요안녕하세요")
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

    def test_a_failure_that_survives_dropping_timestamps_fails_the_job(self):
        """A dead decoder is not the aligner's fault.

        Retrying without timestamps is how the two are told apart: if that
        retry fails too, the timestamps were never the problem, and reporting
        one would end the job promising a complete transcript it never made.
        """

        class DeadSession:
            def transcribe(self, audio, **kwargs):
                raise RuntimeError("Metal device lost")

        job = self.run_worker(DeadSession(), timestamps=True)

        self.assertEqual(job["status"], "error")
        self.assertIn("Metal device lost", job["detail"])
        self.assertIsNone(job.get("timestamps_unavailable"))

    def test_a_later_chunk_failure_discards_the_earlier_timestamps(self):
        """Half a file's word timestamps are worse than none.

        The .srt would end at the last aligned chunk with nothing saying so.
        """
        aligned = []

        class LateFailureSession:
            def transcribe(inner, audio, **kwargs):
                if not kwargs.get("return_timestamps"):
                    return FakeResult(text="second")
                aligned.append(True)
                if len(aligned) > 1:
                    raise RuntimeError("aligner asset missing")
                return FakeResult(
                    text="first", segments=[{"text": "first", "start": 0.0, "end": 0.5}]
                )

        job = self.run_worker(LateFailureSession(), chunks=2, timestamps=True)

        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["text"], "first second")
        self.assertIsNone(job["result"]["segments"])
        self.assertIn("aligner asset missing", job["timestamps_unavailable"])


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

    def test_retry_stages_its_copy_without_holding_the_job_lock(self):
        """The upload can be 4 GB, and every job API waits on that one lock.

        Copying under it freezes the running job's progress, the queue view,
        new uploads, and shutdown for the length of the disk copy.
        """
        lock_was_free = []

        def watchful_copy(source, destination):
            # A plain Lock is not reentrant, so this reports the truth even
            # when the endpoint runs on this very thread.
            acquired = server.jobs_lock.acquire(blocking=False)
            lock_was_free.append(acquired)
            if acquired:
                server.jobs_lock.release()
            Path(destination).write_bytes(Path(source).read_bytes())

        job_id = self.stage_job(status="error")
        with mock.patch.object(server.shutil, "copyfile", watchful_copy):
            response = self.client.post(f"/api/jobs/{job_id}/retry")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_was_free, [True])

    def test_retrying_twice_does_not_queue_the_same_file_twice(self):
        """One worker, one GPU: a double click must not transcribe it twice."""
        job_id = self.stage_job(status="error")
        first = self.client.post(f"/api/jobs/{job_id}/retry")
        self.assertEqual(first.status_code, 200)

        second = self.client.post(f"/api/jobs/{job_id}/retry")

        self.assertEqual(second.status_code, 409)
        self.assertIn("already", second.json()["detail"].lower())
        self.assertEqual(self.submit.call_count, 1)
        self.assertEqual(server.jobs[job_id]["retried_as"], first.json()["id"])

    def test_a_second_retry_during_the_staging_copy_is_rejected(self):
        """The copy runs outside the lock, so the claim has to span it.

        This is the window a double click actually lands in: on a large file
        it is the whole length of the copy, not the microsecond a check of
        the registered jobs would cover.
        """
        copying = threading.Event()
        finish_copy = threading.Event()
        second = {}

        def slow_copy(source, destination):
            Path(destination).write_bytes(Path(source).read_bytes())
            copying.set()
            finish_copy.wait(timeout=5)

        job_id = self.stage_job(status="error")

        def retry_during_the_copy():
            # Its own client: the two requests are genuinely concurrent.
            client = TestClient(server.app, base_url=BASE_URL)
            copying.wait(timeout=5)
            second["status"] = client.post(f"/api/jobs/{job_id}/retry").status_code
            finish_copy.set()

        helper = threading.Thread(target=retry_during_the_copy)
        helper.start()
        self.addCleanup(finish_copy.set)
        with mock.patch.object(server.shutil, "copyfile", slow_copy):
            first = self.client.post(f"/api/jobs/{job_id}/retry")
        helper.join(timeout=5)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.get("status"), 409)
        self.assertEqual(self.submit.call_count, 1)
        self.assertEqual(server.retries_staging, set())

    def test_a_retry_that_cannot_be_staged_leaves_the_job_retryable(self):
        """A full disk must not consume the one retry the job had."""
        job_id = self.stage_job(status="error")
        before = sorted(server.UPLOAD_DIR.iterdir())

        with mock.patch.object(
            server.shutil, "copyfile", side_effect=OSError("No space left on device")
        ):
            failed = self.client.post(f"/api/jobs/{job_id}/retry")

        self.assertEqual(failed.status_code, 500)
        self.assertIn("No space left", failed.json()["detail"])
        self.assertIsNone(server.jobs[job_id].get("retried_as"))
        self.assertEqual(sorted(server.UPLOAD_DIR.iterdir()), before)
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/retry").status_code, 200)

    def test_a_retry_does_not_inherit_the_failure_it_repeats(self):
        job_id = self.stage_job(
            status="error",
            detail="RuntimeError: Metal device lost",
            timestamps_unavailable="Word timestamps are unavailable",
            cancel_requested=True,
        )

        retried = server.jobs[self.client.post(f"/api/jobs/{job_id}/retry").json()["id"]]

        self.assertEqual(retried["detail"], "Queued")
        self.assertIsNone(retried.get("timestamps_unavailable"))
        self.assertIsNone(retried.get("retried_as"))
        self.assertFalse(retried.get("cancel_requested"))
        self.assertFalse(retried["cancelled"].is_set())

    def test_cancelling_a_running_job_says_so_while_the_chunk_finishes(self):
        """The model call cannot be interrupted, so "cancelled" would be a lie
        until the current chunk returns. Say what is actually happening."""
        job_id = self.stage_job(status="processing", detail="Chunk 2/40")

        body = self.client.post(f"/api/jobs/{job_id}/cancel").json()

        self.assertEqual(body["status"], "cancelling")
        self.assertTrue(server.jobs[job_id]["cancel_requested"])
        self.assertTrue(server.jobs[job_id]["cancelled"].is_set())
        # Still processing: only the worker may declare it finished.
        self.assertEqual(server.jobs[job_id]["status"], "processing")
        self.assertIn("Cancelling", server.jobs[job_id]["detail"])

    def test_a_cancel_that_lands_before_the_final_save_is_honoured(self):
        """The last chunk must not complete a job the user already stopped."""
        job_id = None
        real_join = server._join_transcript_texts

        class GoodSession:
            def transcribe(inner, audio, **kwargs):
                return FakeResult()

        def cancel_then_join(texts, language):
            # Fires after the loop's last cancellation check, in the window
            # between the final chunk and the durable write.
            server.jobs[job_id]["cancelled"].set()
            return real_join(texts, language)

        self.install_audio_stubs(chunks=1)
        job_id = self.stage_job()
        with mock.patch.object(server, "get_session", return_value=GoodSession()), \
                mock.patch.object(
                    server, "_join_transcript_texts", side_effect=cancel_then_join
                ):
            server._run_job(job_id)

        job = server.jobs[job_id]
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(job["result"])
        self.assertEqual(list(server.TRANSCRIPTS_DIR.glob("*.json")), [])
        self.assertFalse(Path(job["path"]).exists())

    def test_the_worker_ignores_a_job_that_was_already_forgotten(self):
        """Cancelling while queued makes a job terminal, so the 50-job cap can
        evict it before the single worker ever reaches it."""
        self.install_audio_stubs(chunks=1)
        job_id = self.stage_job()
        server.jobs.pop(job_id)

        server._run_job(job_id)   # must not raise

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
    def setUp(self):
        self.addCleanup(server.stopping.clear)
        self.addCleanup(server.jobs.clear)
        self.addCleanup(server.job_futures.clear)

    def test_shutdown_marks_unfinished_jobs_as_failed(self):
        """Queued work must not silently drain after the server is stopped."""
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

    def test_shutdown_removes_upload_for_a_cancelled_queued_future(self):
        started = Event()
        release = Event()
        temporary_executor = ThreadPoolExecutor(max_workers=1)
        def stop_executor():
            release.set()
            temporary_executor.shutdown(wait=True, cancel_futures=True)

        self.addCleanup(stop_executor)
        blocker = temporary_executor.submit(lambda: (started.set(), release.wait()))
        self.assertTrue(started.wait(timeout=2))

        with TemporaryDirectory() as directory, mock.patch.object(
            server, "UPLOAD_DIR", Path(directory)
        ), mock.patch.object(server, "executor", temporary_executor):
            with TestClient(server.app, base_url=BASE_URL) as client:
                response = client.post(
                    "/api/jobs",
                    data={"model": "1.7b", "language": "English"},
                    files={"file": ("queued.wav", WAV)},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["id"]
                staged = server.UPLOAD_DIR / f"{job_id}.wav"
                self.assertTrue(staged.exists())
                self.assertFalse(server.job_futures[job_id].running())

            self.assertFalse(staged.exists())
            self.assertEqual(server.jobs[job_id]["status"], "error")
            self.assertIn("finished_at", server.jobs[job_id])
            self.assertTrue(server.job_futures.get(job_id) is None)

        release.set()
        blocker.result(timeout=2)

    def test_shutdown_cannot_complete_a_running_final_chunk(self):
        started = Event()
        release = Event()
        temporary_executor = ThreadPoolExecutor(max_workers=1)

        def stop_executor():
            release.set()
            temporary_executor.shutdown(wait=True, cancel_futures=True)

        self.addCleanup(stop_executor)

        target_session = mock.Mock()

        def blocked_transcribe(_audio, **_kwargs):
            started.set()
            release.wait()
            return SimpleNamespace(
                text="must not be saved",
                language="English",
                segments=None,
                truncated=False,
            )

        target_session.transcribe.side_effect = blocked_transcribe
        package = ModuleType("mlx_qwen3_asr")
        package.__path__ = []
        audio_module = ModuleType("mlx_qwen3_asr.audio")
        audio_module.load_audio_np = lambda path, sr: [0] * 16_000
        chunking_module = ModuleType("mlx_qwen3_asr.chunking")
        chunking_module.split_audio_into_chunks = lambda audio, sr, seconds: [
            (audio, 0.0),
        ]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            uploads = root / "uploads"
            transcripts = root / "transcripts"
            uploads.mkdir()
            transcripts.mkdir()
            with mock.patch.object(server, "UPLOAD_DIR", uploads), mock.patch.object(
                server, "TRANSCRIPTS_DIR", transcripts
            ), mock.patch.object(server, "executor", temporary_executor), mock.patch.object(
                server, "get_session", return_value=target_session
            ), mock.patch.dict(
                "sys.modules",
                {
                    "mlx_qwen3_asr": package,
                    "mlx_qwen3_asr.audio": audio_module,
                    "mlx_qwen3_asr.chunking": chunking_module,
                },
            ):
                with TestClient(server.app, base_url=BASE_URL) as client:
                    response = client.post(
                        "/api/jobs",
                        data={"model": "1.7b", "language": "English"},
                        files={"file": ("running.wav", WAV)},
                    )
                    self.assertEqual(response.status_code, 200)
                    job_id = response.json()["id"]
                    staged = uploads / f"{job_id}.wav"
                    worker = server.job_futures[job_id]
                    self.assertTrue(started.wait(timeout=2))

                self.assertEqual(server.jobs[job_id]["status"], "error")
                self.assertEqual(list(transcripts.iterdir()), [])
                release.set()
                worker.result(timeout=2)
                self.assertEqual(server.jobs[job_id]["status"], "error")
                self.assertEqual(list(transcripts.iterdir()), [])
                self.assertFalse(staged.exists())


if __name__ == "__main__":
    unittest.main()
