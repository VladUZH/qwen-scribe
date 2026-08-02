"""Upload validation for POST /api/jobs — the app's primary endpoint.

No model is ever loaded: the worker is replaced with a double, so these tests
run on any machine, with or without MLX.
"""

import unittest
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
