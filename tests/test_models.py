"""The model catalog: states, the on-device conversion, and legacy adoption.

No model, no MLX and no network: the Hub cache lookup, MLX and the upstream
loader are replaced with doubles that write real files into a temporary
store, so the state logic is exercised against the disk it reads.
"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from qwen_scribe import config, models, sessions
from test_sessions import install_fake_modules


def use_temporary_store(test) -> Path:
    store = tempfile.TemporaryDirectory()
    test.addCleanup(store.cleanup)
    patched = mock.patch.object(config, "MODEL_DIR", Path(store.name) / "models")
    patched.start()
    test.addCleanup(patched.stop)
    return Path(store.name)


def write_converted(model_id: str) -> Path:
    directory = models.variant_dir(model_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.safetensors").write_bytes(b"\0" * 2048)
    (directory / "config.json").write_text("{}")
    (directory / "quantization_config.json").write_text(json.dumps({"bits": 8, "group_size": 64}))
    return directory


def fake_hub(cached: dict[str, Path]):
    """A huggingface_hub whose cache holds the given repos at the given dirs."""
    hub = ModuleType("huggingface_hub")

    def try_to_load_from_cache(repo_id, filename, **_kwargs):
        directory = cached.get(repo_id)
        if directory is None:
            return None
        return str(directory / filename)

    def snapshot_download(repo_id, **_kwargs):
        return str(cached[repo_id])

    hub.try_to_load_from_cache = try_to_load_from_cache
    hub.snapshot_download = snapshot_download
    return hub


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.root = use_temporary_store(self)
        sessions.drop_all()
        self.addCleanup(sessions.drop_all)

    def test_catalog_lists_every_model_with_its_state(self):
        install_fake_modules(self, {"huggingface_hub": fake_hub({})})
        listing = {item["id"]: item for item in models.listing()}
        self.assertEqual(list(listing), ["1.7b", "1.7b-8bit", "0.6b", "0.6b-4bit"])
        self.assertEqual(listing["1.7b"]["state"], "downloadable")
        self.assertEqual(listing["1.7b-8bit"]["state"], "needs_conversion")
        self.assertEqual(listing["1.7b-8bit"]["base"], "1.7b")
        self.assertEqual(listing["1.7b-8bit"]["bits"], 8)
        self.assertEqual(listing["0.6b-4bit"]["bits"], 4)
        self.assertIsNone(listing["1.7b"]["bits"])
        for item in listing.values():
            self.assertIn(item["state"], models.STATES)
            self.assertGreater(item["memory_gb"], 0)
            self.assertFalse(item["loaded"])

    def test_upstream_model_is_ready_once_its_weights_are_in_the_hub_cache(self):
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        (snapshot / "config.json").write_text("{}")
        (snapshot / "model.safetensors").write_bytes(b"\0")
        install_fake_modules(self, {"huggingface_hub": fake_hub({"Qwen/Qwen3-ASR-0.6B": snapshot})})
        self.assertEqual(models.state("0.6b"), "ready")
        self.assertEqual(models.state("1.7b"), "downloadable")

    def test_hub_cache_without_weights_does_not_count(self):
        # A config.json alone, with the weights never fetched or half-fetched
        # (a .incomplete blob is never linked into the snapshot).
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        (snapshot / "config.json").write_text("{}")
        install_fake_modules(self, {"huggingface_hub": fake_hub({"Qwen/Qwen3-ASR-0.6B": snapshot})})
        self.assertEqual(models.state("0.6b"), "downloadable")

    def test_converted_variant_is_ready_and_measured(self):
        write_converted("1.7b-8bit")
        install_fake_modules(self, {"huggingface_hub": fake_hub({})})
        item = models.describe("1.7b-8bit")
        self.assertEqual(item["state"], "ready")
        self.assertAlmostEqual(item["disk_gb"], 0.0, places=2)
        self.assertTrue(models.usable("1.7b-8bit"))
        self.assertFalse(models.usable("0.6b-4bit"))
        # An upstream model is always usable: it downloads on first use.
        self.assertTrue(models.usable("1.7b"))

    def test_a_variant_being_prepared_reports_converting(self):
        install_fake_modules(self, {"huggingface_hub": fake_hub({})})
        self.assertEqual(models.state("0.6b-4bit", converting={"0.6b-4bit"}), "converting")
        self.assertEqual(models.state("1.7b-8bit", converting={"0.6b-4bit"}), "needs_conversion")

    def test_incomplete_directory_is_not_ready(self):
        directory = models.variant_dir("0.6b-4bit")
        directory.mkdir(parents=True)
        (directory / "quantization_config.json").write_text("{}")
        self.assertFalse(models.converted("0.6b-4bit"))

    def test_remove_deletes_the_variant_and_its_leftovers(self):
        directory = write_converted("0.6b-4bit")
        partial = directory.with_name(directory.name + ".partial")
        partial.mkdir()
        self.assertTrue(models.remove("0.6b-4bit"))
        self.assertFalse(directory.exists())
        self.assertFalse(partial.exists())
        self.assertFalse(models.remove("0.6b-4bit"))
        with self.assertRaises(ValueError):
            models.remove("0.6b")

    def test_remove_unloads_a_loaded_variant(self):
        write_converted("1.7b-8bit")
        package = ModuleType("mlx_qwen3_asr")
        package.Session = lambda model: SimpleNamespace(model_id=model)
        install_fake_modules(self, {"mlx_qwen3_asr": package})
        sessions.get_session("1.7b-8bit")
        self.assertEqual(sessions.loaded_models(), ["1.7b-8bit"])
        models.remove("1.7b-8bit")
        self.assertEqual(sessions.loaded_models(), [])


class PartialSweepTests(unittest.TestCase):
    def setUp(self):
        use_temporary_store(self)

    def test_half_written_conversions_are_removed_and_finished_ones_kept(self):
        finished = write_converted("1.7b-8bit")
        partial = models.variant_dir("0.6b-4bit").with_name("qwen3-asr-0.6b-4bit.partial")
        partial.mkdir(parents=True)
        (partial / "model.safetensors").write_bytes(b"\0")
        self.assertEqual(models.sweep_partials(), [partial])
        self.assertFalse(partial.exists())
        self.assertTrue(finished.exists())
        self.assertEqual(models.sweep_partials(), [])

    def test_a_missing_store_is_not_an_error(self):
        self.assertEqual(models.sweep_partials(), [])


class LegacyAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.root = use_temporary_store(self)
        legacy = mock.patch.object(config, "LEGACY_MODEL_DIR", self.root / "legacy-models")
        legacy.start()
        self.addCleanup(legacy.stop)

    def test_a_conversion_next_to_the_source_moves_into_the_store(self):
        old = config.LEGACY_MODEL_DIR / "qwen3-asr-1.7b-8bit"
        old.mkdir(parents=True)
        (old / "quantization_config.json").write_text("{}")
        (old / "model.safetensors").write_bytes(b"\0")
        moved = models.adopt_legacy()
        self.assertEqual(moved, models.variant_dir("1.7b-8bit"))
        self.assertFalse(old.exists())
        self.assertTrue(models.converted("1.7b-8bit"))
        # Idempotent: nothing left to adopt.
        self.assertIsNone(models.adopt_legacy())

    def test_nothing_to_adopt(self):
        self.assertIsNone(models.adopt_legacy())

    def test_an_existing_store_entry_wins(self):
        old = config.LEGACY_MODEL_DIR / "qwen3-asr-1.7b-8bit"
        old.mkdir(parents=True)
        (old / "quantization_config.json").write_text("{}")
        write_converted("1.7b-8bit")
        self.assertIsNone(models.adopt_legacy())
        self.assertTrue(old.exists())


class ConversionTests(unittest.TestCase):
    """models.convert against fake MLX modules that write real files."""

    def setUp(self):
        self.root = use_temporary_store(self)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        (self.snapshot / "config.json").write_text('{"a": 1}')
        (self.snapshot / "tokenizer.json").write_text("{}")
        (self.snapshot / "model.safetensors").write_bytes(b"\0" * 64)
        (self.snapshot / "model.safetensors.index.json").write_text("{}")
        self.calls: list[str] = []

        mx = ModuleType("mlx.core")
        mx.float16 = "float16"

        def save_safetensors(path, weights):
            self.calls.append("save")
            Path(path).write_bytes(b"\1" * 32)

        mx.save_safetensors = save_safetensors
        mx.clear_cache = lambda: None
        nn = ModuleType("mlx.nn")

        def quantize(model, bits, group_size):
            self.calls.append(f"quantize {bits} {group_size}")

        nn.quantize = quantize
        utils = ModuleType("mlx.utils")
        utils.tree_flatten = lambda params: [("w", "weights")]
        mlx = ModuleType("mlx")
        loader = ModuleType("mlx_qwen3_asr.load_models")

        def load_model(source, dtype=None):
            self.calls.append(f"load {source} {dtype}")
            return SimpleNamespace(parameters=lambda: {}), {}

        loader.load_model = load_model
        package = ModuleType("mlx_qwen3_asr")
        package.load_models = loader
        install_fake_modules(self, {
            "mlx": mlx, "mlx.core": mx, "mlx.nn": nn, "mlx.utils": utils,
            "mlx_qwen3_asr": package, "mlx_qwen3_asr.load_models": loader,
            "huggingface_hub": fake_hub({
                "Qwen/Qwen3-ASR-1.7B": self.snapshot, "Qwen/Qwen3-ASR-0.6B": self.snapshot,
            }),
        })

    def test_convert_writes_a_complete_variant(self):
        reports: list[str] = []
        target = models.convert("0.6b-4bit", report=reports.append)
        self.assertEqual(target, models.variant_dir("0.6b-4bit"))
        self.assertTrue(models.converted("0.6b-4bit"))
        self.assertEqual(self.calls, ["load Qwen/Qwen3-ASR-0.6B float16", "quantize 4 64", "save"])
        self.assertEqual(reports, ["Loading 0.6B", "Quantizing to 4-bit", "Saving"])
        self.assertEqual(json.loads((target / "quantization_config.json").read_text()),
                         {"bits": 4, "group_size": 64})
        # Configuration and tokenizer copied; upstream weights and index not.
        self.assertEqual(json.loads((target / "config.json").read_text()), {"a": 1})
        self.assertTrue((target / "tokenizer.json").exists())
        self.assertEqual((target / "model.safetensors").read_bytes(), b"\1" * 32)
        self.assertFalse((target / "model.safetensors.index.json").exists())
        self.assertFalse(target.with_name(target.name + ".partial").exists())

    def test_convert_refuses_an_upstream_model(self):
        with self.assertRaises(ValueError):
            models.convert("1.7b")

    def test_cancel_between_steps_leaves_nothing_behind(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(models.ConversionCancelled):
            models.convert("1.7b-8bit", cancelled=cancelled)
        self.assertEqual(self.calls, [])
        self.assertFalse(models.variant_dir("1.7b-8bit").exists())

    def test_cancel_during_the_save_discards_the_result(self):
        cancelled = threading.Event()
        # Set while saving, i.e. after the last check before the write.
        original = sys.modules["mlx.core"].save_safetensors

        def save_then_cancel(path, weights):
            original(path, weights)
            cancelled.set()

        sys.modules["mlx.core"].save_safetensors = save_then_cancel
        with self.assertRaises(models.ConversionCancelled):
            models.convert("1.7b-8bit", cancelled=cancelled)
        target = models.variant_dir("1.7b-8bit")
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name(target.name + ".partial").exists())

    def test_a_failed_save_leaves_no_partial_directory(self):
        def broken(path, weights):
            raise OSError("disk full")

        sys.modules["mlx.core"].save_safetensors = broken
        with self.assertRaises(OSError):
            models.convert("0.6b-4bit")
        target = models.variant_dir("0.6b-4bit")
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name(target.name + ".partial").exists())

    def test_convert_replaces_an_older_variant(self):
        old = write_converted("1.7b-8bit")
        (old / "stale.txt").write_text("old")
        models.convert("1.7b-8bit")
        self.assertFalse((old / "stale.txt").exists())
        self.assertTrue(models.converted("1.7b-8bit"))


if __name__ == "__main__":
    unittest.main()
