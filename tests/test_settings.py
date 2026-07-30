"""Persisted dictation settings — the contract the native helper relies on."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server

BASE_URL = "http://127.0.0.1:8990"


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url=BASE_URL)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        patcher = mock.patch.object(
            server, "SETTINGS_FILE", Path(self.temporary_directory.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # The settings dict is module state; restore it whatever the test did.
        original = copy.deepcopy(server._settings)
        self.addCleanup(lambda: server._settings.update(copy.deepcopy(original)))
        server._settings["dictation"] = dict(server.DEFAULT_SETTINGS["dictation"])

    def test_defaults_and_options(self):
        body = self.client.get("/api/settings").json()
        self.assertEqual(body["dictation"], server.DEFAULT_SETTINGS["dictation"])
        self.assertEqual(
            [h["id"] for h in body["options"]["hotkeys"]],
            list(server.DICTATION_HOTKEYS.keys()),
        )
        self.assertEqual(body["options"]["models"], list(server.MODELS.keys()))
        self.assertEqual(body["options"]["languages"], server.LANGUAGES)

    def test_partial_update_persists_and_survives_reload(self):
        response = self.client.put(
            "/api/settings", json={"dictation": {"hotkey": "right_control", "language": "German"}}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["dictation"],
            {"hotkey": "right_control", "model": server.DEFAULT_MODEL, "language": "German"},
        )
        # What lands on disk is what a restarted server would load.
        self.assertEqual(server._load_settings()["dictation"]["hotkey"], "right_control")

    def test_rejects_invalid_values_without_side_effects(self):
        for payload in (
            {"dictation": {"hotkey": "left_pinky"}},
            {"dictation": {"model": "70b"}},
            {"dictation": {"language": "Klingon"}},
            {"dictation": {"volume": 11}},
            {"dictation": "loud"},
        ):
            response = self.client.put("/api/settings", json=payload)
            self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(
            self.client.get("/api/settings").json()["dictation"],
            server.DEFAULT_SETTINGS["dictation"],
        )
        self.assertFalse(server.SETTINGS_FILE.exists())

    def test_damaged_settings_file_falls_back_to_defaults(self):
        server.SETTINGS_FILE.write_text("not json", encoding="utf-8")
        self.assertEqual(server._load_settings(), server.DEFAULT_SETTINGS)
        server.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": "left_pinky"}}', encoding="utf-8"
        )
        self.assertEqual(server._load_settings(), server.DEFAULT_SETTINGS)

    def test_unhashable_values_are_rejected_not_crashes(self):
        """A list value raises TypeError from `in dict` unless guarded —
        which took the whole server down at import when hand-edited."""
        server.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": ["right_command"], "model": {"a": 1}}}',
            encoding="utf-8",
        )
        self.assertEqual(server._load_settings(), server.DEFAULT_SETTINGS)
        response = self.client.put(
            "/api/settings", json={"dictation": {"hotkey": ["right_command"]}}
        )
        self.assertEqual(response.status_code, 400)

    def test_one_bad_field_does_not_discard_the_good_ones(self):
        server.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": "left_pinky", "language": "German"}}',
            encoding="utf-8",
        )
        loaded = server._load_settings()["dictation"]
        self.assertEqual(loaded["hotkey"], "right_command")   # bad -> default
        self.assertEqual(loaded["language"], "German")        # good -> kept

    def test_failed_save_does_not_leave_the_new_value_live(self):
        with mock.patch.object(server, "_save_settings", side_effect=OSError("disk full")):
            response = self.client.put(
                "/api/settings", json={"dictation": {"hotkey": "right_option"}}
            )
        self.assertEqual(response.status_code, 500)
        # The in-memory settings the helper polls must still be the old value.
        self.assertEqual(
            self.client.get("/api/settings").json()["dictation"]["hotkey"],
            "right_command",
        )

    def test_dictation_status_reports_the_configured_hotkey(self):
        self.client.put("/api/settings", json={"dictation": {"hotkey": "right_option"}})
        status = self.client.get("/api/dictation/status").json()
        self.assertEqual(status["hotkey"], "right_option")
        self.assertEqual(status["shortcut"], "Right ⌥")


if __name__ == "__main__":
    unittest.main()
