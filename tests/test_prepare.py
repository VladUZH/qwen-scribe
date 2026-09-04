"""Preparing a quantized model: the catalog endpoints, the prepare job on
the single worker, and the guards around an unprepared variant.

No model and no network, as in test_jobs: the worker's conversion is
replaced with a double that writes the files a real one would.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

from fastapi.testclient import TestClient

from qwen_scribe import api, config, jobs, models, sessions, settings
from test_models import fake_hub, use_temporary_store, write_converted
from test_sessions import install_fake_modules

BASE_URL = "http://127.0.0.1:8990"
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


class PrepareTestCase(unittest.TestCase):
    def setUp(self):
        self.root = use_temporary_store(self)
        install_fake_modules(self, {"huggingface_hub": fake_hub({})})
        sessions.drop_all()
        self.addCleanup(sessions.drop_all)
        self.addCleanup(jobs.jobs.clear)
        self.addCleanup(jobs.job_futures.clear)
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        upload_dir = mock.patch.object(config, "UPLOAD_DIR", Path(uploads.name))
        upload_dir.start()
        self.addCleanup(upload_dir.stop)
        prefetch = mock.patch.object(sessions, "ensure_downloaded")
        self.prefetch = prefetch.start()
        self.addCleanup(prefetch.stop)
        self.client = TestClient(api.app, base_url=BASE_URL)

    def hold_worker(self):
        """Keep the executor from running anything; returns the submit mock."""
        submit = mock.patch.object(jobs.executor, "submit")
        started = submit.start()
        self.addCleanup(submit.stop)
        return started


class CatalogEndpointTests(PrepareTestCase):
    def test_lists_the_catalog_with_states(self):
        write_converted("1.7b-8bit")
        body = self.client.get("/api/models").json()
        self.assertEqual(body["default"], config.DEFAULT_MODEL)
        states = {item["id"]: item["state"] for item in body["models"]}
        self.assertEqual(states, {
            "1.7b": "downloadable", "1.7b-8bit": "ready",
            "0.6b": "downloadable", "0.6b-4bit": "needs_conversion",
        })

    def test_config_no_longer_claims_a_quantized_shim(self):
        body = self.client.get("/api/config").json()
        self.assertNotIn("quantized", body)
        self.assertEqual(body["models"], ["1.7b", "1.7b-8bit", "0.6b", "0.6b-4bit"])

    def test_prepare_queues_one_job_per_model(self):
        submit = self.hold_worker()
        response = self.client.post("/api/models/0.6b-4bit/prepare")
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["id"]
        submit.assert_called_once_with(jobs._run_job, job_id)
        job = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(job["kind"], "prepare")
        self.assertEqual(job["model"], "0.6b-4bit")
        self.assertEqual(job["label"], "0.6B 4-bit")
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["filename"])
        # The catalog now says so, and a second request is refused.
        states = {item["id"]: item["state"] for item in self.client.get("/api/models").json()["models"]}
        self.assertEqual(states["0.6b-4bit"], "converting")
        again = self.client.post("/api/models/0.6b-4bit/prepare")
        self.assertEqual(again.status_code, 409)
        self.assertIn("already being prepared", again.json()["detail"])

    def test_prepare_refuses_what_needs_no_preparing(self):
        self.hold_worker()
        self.assertEqual(self.client.post("/api/models/1.7b/prepare").status_code, 409)
        self.assertEqual(self.client.post("/api/models/70b/prepare").status_code, 404)
        write_converted("1.7b-8bit")
        response = self.client.post("/api/models/1.7b-8bit/prepare")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already prepared", response.json()["detail"])
        self.assertEqual(jobs.jobs, {})

    def test_upload_refuses_an_unprepared_variant(self):
        self.hold_worker()
        response = self.client.post(
            "/api/jobs", data={"model": "0.6b-4bit", "language": "English"},
            files={"file": ("clip.wav", WAV)},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("not prepared", response.json()["detail"])
        self.assertEqual(sorted(config.UPLOAD_DIR.iterdir()), [])
        write_converted("0.6b-4bit")
        response = self.client.post(
            "/api/jobs", data={"model": "0.6b-4bit", "language": "English"},
            files={"file": ("clip.wav", WAV)},
        )
        self.assertEqual(response.status_code, 200)

    def test_remove_deletes_a_prepared_variant(self):
        directory = write_converted("0.6b-4bit")
        self.assertEqual(self.client.delete("/api/models/0.6b-4bit").status_code, 204)
        self.assertFalse(directory.exists())
        self.assertEqual(self.client.delete("/api/models/0.6b").status_code, 409)
        self.assertEqual(self.client.delete("/api/models/70b").status_code, 404)

    def test_remove_refuses_a_variant_a_job_still_needs(self):
        self.hold_worker()
        directory = write_converted("0.6b-4bit")
        self.client.post(
            "/api/jobs", data={"model": "0.6b-4bit", "language": "English"},
            files={"file": ("clip.wav", WAV)},
        )
        response = self.client.delete("/api/models/0.6b-4bit")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(directory.exists())

    def test_settings_accept_a_variant_and_skip_warming_an_unprepared_one(self):
        stored = mock.patch.object(settings, "SETTINGS_FILE", self.root / "settings.json")
        stored.start()
        self.addCleanup(stored.stop)
        with mock.patch.object(jobs, "warm_up") as warm_up:
            response = self.client.put("/api/settings", json={"dictation": {"model": "0.6b-4bit"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["dictation"]["model"], "0.6b-4bit")
            warm_up.assert_not_called()
            self.assertEqual(
                response.json()["options"]["models"], ["1.7b", "1.7b-8bit", "0.6b", "0.6b-4bit"]
            )
            write_converted("1.7b-8bit")
            self.client.put("/api/settings", json={"dictation": {"model": "1.7b-8bit"}})
            warm_up.assert_called_once_with("1.7b-8bit")
        self.client.put("/api/settings", json={"dictation": {"model": config.DEFAULT_MODEL}})


class PrepareWorkerTests(PrepareTestCase):
    def stage(self, model_id="0.6b-4bit") -> str:
        self.hold_worker()
        return jobs.prepare(model_id)

    def test_prepare_job_runs_the_conversion_and_reports_each_step(self):
        job_id = self.stage()
        seen: list[str] = []

        def fake_convert(model_id, *, report=None, cancelled=None):
            for step in ("Loading 0.6B", "Quantizing to 4-bit", "Saving"):
                report(step)
                seen.append(jobs.jobs[job_id]["detail"])
            return write_converted(model_id)

        with mock.patch.object(models, "convert", side_effect=fake_convert):
            jobs._run_job(job_id)
        job = jobs.jobs[job_id]
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["detail"], "Ready")
        self.assertEqual(job["result"]["model"], "0.6b-4bit")
        self.assertEqual(seen, ["Loading 0.6B", "Quantizing to 4-bit", "Saving"])
        self.prefetch.assert_called_once()
        self.assertEqual(self.prefetch.call_args.args, ("0.6b",))
        self.assertEqual(jobs.converting_models(), set())
        self.assertTrue(models.converted("0.6b-4bit"))

    def test_loaded_models_are_released_before_converting(self):
        job_id = self.stage("1.7b-8bit")
        with mock.patch.object(sessions, "loaded_models", return_value=["1.7b"]), \
             mock.patch.object(sessions, "drop_all") as drop_all, \
             mock.patch.object(models, "convert", side_effect=lambda m, **_k: write_converted(m)):
            jobs._run_job(job_id)
        drop_all.assert_called_once()
        self.assertEqual(jobs.jobs[job_id]["status"], "done")

    def test_a_failed_conversion_is_an_error_that_cannot_be_retried(self):
        job_id = self.stage()
        with mock.patch.object(models, "convert", side_effect=OSError("disk full")):
            jobs._run_job(job_id)
        job = jobs.jobs[job_id]
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["detail"], "OSError: disk full")
        with self.assertRaises(jobs.JobConflict):
            jobs.retry(job_id)
        response = self.client.post(f"/api/jobs/{job_id}/retry")
        self.assertEqual(response.status_code, 409)
        self.assertIn("model picker", response.json()["detail"])
        # The variant can be prepared again.
        self.assertEqual(self.client.post("/api/models/0.6b-4bit/prepare").status_code, 200)

    def test_cancel_while_queued_never_converts(self):
        job_id = self.stage()
        self.assertEqual(jobs.cancel(job_id), "cancelled")
        with mock.patch.object(models, "convert") as convert:
            jobs._run_job(job_id)
        convert.assert_not_called()
        self.assertEqual(jobs.jobs[job_id]["status"], "cancelled")
        self.prefetch.assert_not_called()

    def test_cancel_while_running_is_honoured_by_the_conversion(self):
        job_id = self.stage()

        def convert_then_notice_cancel(model_id, *, report=None, cancelled=None):
            self.assertEqual(jobs.cancel(job_id), "cancelling")
            self.assertTrue(cancelled.is_set())
            raise models.ConversionCancelled

        with mock.patch.object(models, "convert", side_effect=convert_then_notice_cancel):
            jobs._run_job(job_id)
        job = jobs.jobs[job_id]
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse(models.converted("0.6b-4bit"))

    def test_shutdown_settles_a_queued_prepare_job(self):
        job_id = self.stage()
        jobs.shutdown()
        self.addCleanup(jobs.stopping.clear)
        self.addCleanup(setattr, jobs, "executor", jobs.executor)
        self.assertEqual(jobs.jobs[job_id]["status"], "error")

    def test_warm_up_skips_an_unprepared_variant(self):
        with mock.patch.object(sessions, "get_session") as get_session:
            jobs._warm("0.6b-4bit")
            get_session.assert_not_called()
            self.prefetch.assert_not_called()
            write_converted("0.6b-4bit")
            jobs._warm("0.6b-4bit")
            get_session.assert_called_once_with("0.6b-4bit")

    def test_queue_listing_shows_the_prepare_job(self):
        job_id = self.stage("1.7b-8bit")
        listing = self.client.get("/api/jobs").json()["jobs"]
        self.assertEqual([job["id"] for job in listing], [job_id])
        self.assertEqual(listing[0]["kind"], "prepare")
        self.assertEqual(listing[0]["label"], "1.7B 8-bit")
        self.assertNotIn("path", listing[0])
        self.assertNotIn("cancelled", listing[0])


if __name__ == "__main__":
    unittest.main()
