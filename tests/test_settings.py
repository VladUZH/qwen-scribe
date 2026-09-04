"""Persisted dictation settings — the contract the native helper relies on."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from qwen_scribe import api, config, jobs, settings

BASE_URL = "http://127.0.0.1:8990"


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app, base_url=BASE_URL)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        patcher = mock.patch.object(
            settings, "SETTINGS_FILE", Path(self.temporary_directory.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # The settings dict is module state; restore it whatever the test did.
        original = copy.deepcopy(settings._settings)
        self.addCleanup(lambda: settings._settings.update(copy.deepcopy(original)))
        for section, values in settings.DEFAULT_SETTINGS.items():
            settings._settings[section] = dict(values)

    def test_languages_cover_every_model_language(self):
        """The picker must not hide a language the model can actually do.

        Spelled out rather than imported because CI runs without MLX. That
        makes this half of the check a guard against edits to the list, not
        against the model changing under it — which is what the companion
        test below is for, on machines that have the model installed.
        """
        self.assertEqual(config.LANGUAGES[0], "auto")
        self.assertEqual(
            sorted(config.LANGUAGES[1:]),
            [
                "Arabic", "Chinese", "Dutch", "English", "French", "German",
                "Hindi", "Italian", "Japanese", "Korean", "Portuguese",
                "Russian", "Spanish", "Turkish",
            ],
        )

    def test_languages_match_the_installed_model(self):
        """The one check that can actually notice a model upgrade.

        Skipped where MLX is absent (CI, and any machine that has not run the
        app yet), so the hardcoded list above stays the everyday guard.
        """
        try:
            from mlx_qwen3_asr.tokenizer import known_language_names
        except Exception as exc:   # ImportError, or MLX failing to initialize
            self.skipTest(f"mlx_qwen3_asr is not installed here ({exc})")

        self.assertEqual(sorted(config.LANGUAGES[1:]), sorted(known_language_names()))

    def test_defaults_and_options(self):
        body = self.client.get("/api/settings").json()
        self.assertEqual(body["dictation"], settings.DEFAULT_SETTINGS["dictation"])
        self.assertEqual(
            [h["id"] for h in body["options"]["hotkeys"]],
            list(config.DICTATION_HOTKEYS.keys()),
        )
        self.assertEqual(body["options"]["models"], list(config.MODELS.keys()))
        self.assertEqual(body["options"]["languages"], config.LANGUAGES)

    def test_transcription_defaults(self):
        body = self.client.get("/api/settings").json()
        self.assertEqual(
            body["transcription"],
            {
                "model": config.DEFAULT_MODEL,
                "language": "auto",
                "timestamps": False,
                "turbo": False,
                "context": "",
                "sentence_per_line": False,
            },
        )

    def test_transcription_settings_round_trip(self):
        body = self.client.put(
            "/api/settings",
            json={"transcription": {"language": "Korean", "timestamps": True}},
        ).json()
        self.assertEqual(body["transcription"]["language"], "Korean")
        self.assertTrue(body["transcription"]["timestamps"])
        # Untouched fields survive a partial update.
        self.assertEqual(body["transcription"]["model"], config.DEFAULT_MODEL)
        # And what a restarted server would load matches.
        reloaded = settings._load_settings()["transcription"]
        self.assertEqual(reloaded["language"], "Korean")
        self.assertTrue(reloaded["timestamps"])

    def test_updating_one_section_leaves_the_other_alone(self):
        self.client.put("/api/settings", json={"transcription": {"language": "Korean"}})
        body = self.client.put(
            "/api/settings", json={"dictation": {"hotkey": "right_option"}}
        ).json()
        self.assertEqual(body["dictation"]["hotkey"], "right_option")
        self.assertEqual(body["transcription"]["language"], "Korean")

    def test_transcription_rejects_bad_values(self):
        for payload in (
            {"transcription": {"language": "Klingon"}},
            {"transcription": {"model": "70b"}},
            {"transcription": {"timestamps": "yes"}},
            {"transcription": {"turbo": 1}},
            {"transcription": {"nope": 1}},
            {"transcription": {"context": "x" * (config.MAX_CONTEXT_CHARS + 1)}},
            {"transcription": "fast"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.put("/api/settings", json=payload).status_code, 400
                )
        self.assertFalse(settings.SETTINGS_FILE.exists())

    def test_unknown_section_is_rejected(self):
        self.assertEqual(
            self.client.put("/api/settings", json={"telemetry": {"on": True}}).status_code,
            400,
        )

    def test_bad_stored_transcription_field_does_not_lose_the_others(self):
        settings.SETTINGS_FILE.write_text(
            '{"transcription": {"language": "Korean", "model": "does-not-exist"}}',
            encoding="utf-8",
        )
        loaded = settings._load_settings()["transcription"]
        self.assertEqual(loaded["language"], "Korean")          # good -> kept
        self.assertEqual(loaded["model"], config.DEFAULT_MODEL)  # bad -> default

    def test_partial_update_persists_and_survives_reload(self):
        response = self.client.put(
            "/api/settings", json={"dictation": {"hotkey": "right_control", "language": "German"}}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["dictation"],
            {**settings.DEFAULT_SETTINGS["dictation"], "hotkey": "right_control", "language": "German"},
        )
        # What lands on disk is what a restarted server would load.
        self.assertEqual(settings._load_settings()["dictation"]["hotkey"], "right_control")

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
            settings.DEFAULT_SETTINGS["dictation"],
        )
        self.assertFalse(settings.SETTINGS_FILE.exists())

    def test_damaged_settings_file_falls_back_to_defaults(self):
        settings.SETTINGS_FILE.write_text("not json", encoding="utf-8")
        self.assertEqual(settings._load_settings(), settings.DEFAULT_SETTINGS)
        settings.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": "left_pinky"}}', encoding="utf-8"
        )
        self.assertEqual(settings._load_settings(), settings.DEFAULT_SETTINGS)

    def test_unhashable_values_are_rejected_not_crashes(self):
        """A list value raises TypeError from `in dict` unless guarded —
        which took the whole server down at import when hand-edited."""
        settings.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": ["right_command"], "model": {"a": 1}}}',
            encoding="utf-8",
        )
        self.assertEqual(settings._load_settings(), settings.DEFAULT_SETTINGS)
        response = self.client.put(
            "/api/settings", json={"dictation": {"hotkey": ["right_command"]}}
        )
        self.assertEqual(response.status_code, 400)

    def test_one_bad_field_does_not_discard_the_good_ones(self):
        settings.SETTINGS_FILE.write_text(
            '{"dictation": {"hotkey": "left_pinky", "language": "German"}}',
            encoding="utf-8",
        )
        loaded = settings._load_settings()["dictation"]
        self.assertEqual(loaded["hotkey"], "right_command")   # bad -> default
        self.assertEqual(loaded["language"], "German")        # good -> kept

    def test_failed_save_does_not_leave_the_new_value_live(self):
        with mock.patch.object(settings, "_save_settings", side_effect=OSError("disk full")):
            response = self.client.put(
                "/api/settings", json={"dictation": {"hotkey": "right_option"}}
            )
        self.assertEqual(response.status_code, 500)
        # The in-memory settings the helper polls must still be the old value.
        self.assertEqual(
            self.client.get("/api/settings").json()["dictation"]["hotkey"],
            "right_command",
        )

    def test_dictionary_and_history_choice_round_trip(self):
        body = self.client.put(
            "/api/settings",
            json={"dictation": {"dictionary": "EBITDA Zürich", "save_history": False}},
        ).json()
        self.assertEqual(body["dictation"]["dictionary"], "EBITDA Zürich")
        self.assertFalse(body["dictation"]["save_history"])
        reloaded = settings._load_settings()["dictation"]
        self.assertEqual(reloaded["dictionary"], "EBITDA Zürich")
        self.assertFalse(reloaded["save_history"])

    def test_dictionary_shares_the_vocabulary_limit(self):
        """It is sent as the vocabulary hint, so it is bounded like one."""
        full = self.client.put(
            "/api/settings", json={"dictation": {"dictionary": "x" * config.MAX_CONTEXT_CHARS}}
        )
        self.assertEqual(full.status_code, 200)
        for payload in (
            {"dictation": {"dictionary": "x" * (config.MAX_CONTEXT_CHARS + 1)}},
            {"dictation": {"dictionary": ["EBITDA"]}},
            {"dictation": {"save_history": "no"}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.put("/api/settings", json=payload).status_code, 400)

    def test_mode_and_recording_limit_round_trip_within_bounds(self):
        body = self.client.put(
            "/api/settings", json={"dictation": {"mode": "toggle", "max_seconds": 300}}
        ).json()
        self.assertEqual(body["dictation"]["mode"], "toggle")
        self.assertEqual(body["dictation"]["max_seconds"], 300)
        self.assertEqual([m["id"] for m in body["options"]["modes"]], ["hold", "toggle"])
        status = self.client.get("/api/dictation/status").json()
        self.assertEqual((status["mode"], status["max_seconds"]), ("toggle", 300))
        for payload in (
            {"dictation": {"mode": "sometimes"}},
            {"dictation": {"max_seconds": config.DICTATION_MIN_SECONDS - 1}},
            {"dictation": {"max_seconds": config.DICTATION_MAX_SECONDS + 1}},
            {"dictation": {"max_seconds": "120"}},
            {"dictation": {"max_seconds": True}},
            {"dictation": {"max_seconds": 120.0}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.put("/api/settings", json=payload).status_code, 400)

    def test_performance_section_defaults_and_bounds(self):
        body = self.client.get("/api/settings").json()
        self.assertEqual(body["performance"], {"unload_after_minutes": 20, "preload_dictation_model": True})
        body = self.client.put(
            "/api/settings", json={"performance": {"unload_after_minutes": 0, "preload_dictation_model": False}}
        ).json()
        self.assertEqual(body["performance"], {"unload_after_minutes": 0, "preload_dictation_model": False})
        for payload in (
            {"performance": {"unload_after_minutes": -1}},
            {"performance": {"unload_after_minutes": 24 * 60 + 1}},
            {"performance": {"unload_after_minutes": "20"}},
            {"performance": {"preload_dictation_model": 1}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.put("/api/settings", json=payload).status_code, 400)

    def test_changing_the_dictation_model_loads_it_in_the_background(self):
        with mock.patch.object(jobs, "warm_up") as warm_up:
            self.client.put("/api/settings", json={"dictation": {"language": "German"}})
            warm_up.assert_not_called()
            self.client.put("/api/settings", json={"dictation": {"model": "0.6b"}})
            warm_up.assert_called_once_with("0.6b")

    def test_replacements_and_commands_round_trip(self):
        rules = [{"from": "my email", "to": "sam@example.com"}]
        body = self.client.put(
            "/api/settings", json={"dictation": {"replacements": rules, "spoken_commands": False}}
        ).json()
        self.assertEqual(body["dictation"]["replacements"], rules)
        self.assertFalse(body["dictation"]["spoken_commands"])
        self.assertEqual(settings._load_settings()["dictation"]["replacements"], rules)
        for payload in (
            {"dictation": {"replacements": [{"from": "", "to": "x"}]}},
            {"dictation": {"replacements": [{"say": "a", "paste": "b"}]}},
            {"dictation": {"replacements": "a => b"}},
            {"dictation": {"spoken_commands": "yes"}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.put("/api/settings", json=payload).status_code, 400)

    def test_dictation_status_reports_the_configured_hotkey(self):
        self.client.put("/api/settings", json={"dictation": {"hotkey": "right_option"}})
        status = self.client.get("/api/dictation/status").json()
        self.assertEqual(status["hotkey"], "right_option")
        self.assertEqual(status["shortcut"], "Right ⌥")

    def test_the_fn_key_is_offered_and_accepted(self):
        """The helper's table and this one must agree; see DictationHelper.m."""
        body = self.client.get("/api/settings").json()
        self.assertIn({"id": "fn", "label": "Fn"}, body["options"]["hotkeys"])
        self.assertEqual(
            self.client.put("/api/settings", json={"dictation": {"hotkey": "fn"}}).status_code, 200
        )
        self.assertEqual(self.client.get("/api/dictation/status").json()["shortcut"], "Fn")


if __name__ == "__main__":
    unittest.main()
