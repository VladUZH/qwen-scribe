"""Model sessions: caching, idle release, and the first-run download.

No model and no network: mlx_qwen3_asr and huggingface_hub are replaced with
doubles that record what they were asked for.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from qwen_scribe import config, sessions


def install_fake_modules(test, fakes):
    """Put fake modules in sys.modules and restore exactly those keys after.

    Deliberately not mock.patch.dict on the whole of sys.modules: that
    restores the dict wholesale on exit, which also evicts any real module
    first imported during the test. On a Mac with MLX installed that once
    evicted the mlx.core native extension, and re-importing a nanobind
    extension aborts the interpreter.
    """
    previous = {name: sys.modules.get(name) for name in fakes}

    def restore():
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    test.addCleanup(restore)
    sys.modules.update(fakes)


class FakeSession:
    instances = 0

    def __init__(self, model):
        FakeSession.instances += 1
        self.model_id = model


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        sessions.drop_all()
        self.addCleanup(sessions.drop_all)
        package = ModuleType("mlx_qwen3_asr")
        package.Session = FakeSession
        install_fake_modules(self, {"mlx_qwen3_asr": package})
        FakeSession.instances = 0

    def test_sessions_are_cached_and_reused(self):
        first = sessions.get_session("1.7b")
        self.assertIs(sessions.get_session("1.7b"), first)
        self.assertEqual(FakeSession.instances, 1)
        self.assertEqual(sessions.loaded_models(), ["1.7b"])

    def test_idle_sessions_are_released_and_recent_ones_kept(self):
        sessions.get_session("1.7b")
        sessions._last_used[config.MODELS["1.7b"]] = time.time() - 3600
        sessions.get_session("0.6b")

        dropped = sessions.drop_idle(20 * 60)

        self.assertEqual(dropped, [config.MODELS["1.7b"]])
        self.assertEqual(sessions.loaded_models(), ["0.6b"])
        # Asking again loads it afresh rather than resurrecting a dropped one.
        sessions.get_session("1.7b")
        self.assertEqual(FakeSession.instances, 3)

    def test_nothing_idle_means_nothing_dropped(self):
        sessions.get_session("0.6b")
        self.assertEqual(sessions.drop_idle(20 * 60), [])
        self.assertEqual(sessions.loaded_models(), ["0.6b"])


class DownloadTests(unittest.TestCase):
    """ensure_downloaded fetches only what the library would, with progress."""

    def setUp(self):
        sessions.drop_all()
        self.addCleanup(sessions.drop_all)

    def install_fake_hub(self, siblings, cached=(), fail=False):
        """A huggingface_hub double: model_info, cache lookup, snapshot_download."""
        calls = SimpleNamespace(snapshot=None, files_bar_updates=0)

        class Tqdm:
            def __init__(self, *args, **kwargs):
                self.n = 0
                self.kwargs = kwargs

            def update(self, n=1):
                self.n += n

            def close(self):
                pass

        class HfApi:
            def model_info(self, repo_id, files_metadata=False):
                if fail:
                    raise ConnectionError("offline")
                return SimpleNamespace(
                    siblings=[SimpleNamespace(rfilename=name, size=size) for name, size in siblings]
                )

        def try_to_load_from_cache(repo_id, filename):
            return f"/cache/{filename}" if filename in cached else None

        def snapshot_download(repo_id, allow_patterns=None, tqdm_class=None, **kwargs):
            calls.snapshot = {"repo_id": repo_id, "allow_patterns": allow_patterns}
            # One counter per file, as the real library does, plus the bar that
            # counts files rather than bytes and must be ignored.
            files_bar = tqdm_class(total=len(siblings), unit="it", desc="Fetching files")
            for name, size in siblings:
                if name in cached or not any(Path(name).match(p) for p in allow_patterns):
                    continue
                bar = tqdm_class(total=size, unit="B", unit_scale=True, desc=name)
                bar.update(size // 2)
                bar.update(size - size // 2)
                files_bar.update(1)
                calls.files_bar_updates += 1
            return "/cache/snapshot"

        hub = ModuleType("huggingface_hub")
        hub.HfApi = HfApi
        hub.try_to_load_from_cache = try_to_load_from_cache
        hub.snapshot_download = snapshot_download
        utils = ModuleType("huggingface_hub.utils")
        utils.tqdm = Tqdm
        hub.utils = utils
        install_fake_modules(self, {"huggingface_hub": hub, "huggingface_hub.utils": utils})
        return calls

    def test_progress_counts_the_bytes_the_library_would_fetch(self):
        calls = self.install_fake_hub(
            siblings=[("model.safetensors", 100), ("config.json", 20),
                      ("README.md", 5), ("tokenizer.model", 30)],
            cached=["tokenizer.model"],
        )
        reports = []

        sessions.ensure_downloaded("0.6b", progress=lambda done, total: reports.append((done, total)))

        self.assertEqual(calls.snapshot, {
            "repo_id": config.MODELS["0.6b"],
            "allow_patterns": sessions.WEIGHT_PATTERNS,
        })
        # README.md is outside the library's patterns and tokenizer.model is
        # cached: 120 bytes to fetch, reported from zero to all of them.
        self.assertEqual(reports[0], (0, 120))
        self.assertEqual(reports[-1], (120, 120))
        self.assertEqual(calls.files_bar_updates, 2)

    def test_a_fully_cached_model_never_touches_the_downloader(self):
        calls = self.install_fake_hub(
            siblings=[("model.safetensors", 100), ("config.json", 20)],
            cached=["model.safetensors", "config.json"],
        )
        reports = []
        sessions.ensure_downloaded("0.6b", progress=reports.append)
        self.assertIsNone(calls.snapshot)
        self.assertEqual(reports, [])

    def test_a_local_model_directory_needs_no_hub_at_all(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            config.MODELS, {"1.7b": directory}
        ):
            calls = self.install_fake_hub(siblings=[("model.safetensors", 100)], fail=True)
            sessions.ensure_downloaded("1.7b", progress=lambda *_: self.fail("no download expected"))
            self.assertIsNone(calls.snapshot)

    def test_an_unreachable_hub_leaves_loading_to_the_library(self):
        """Offline with a cached model must keep working exactly as before:
        the library's own fetch falls back to its cache."""
        calls = self.install_fake_hub(siblings=[("model.safetensors", 100)], fail=True)
        sessions.ensure_downloaded("0.6b", progress=lambda *_: self.fail("no download expected"))
        self.assertIsNone(calls.snapshot)


if __name__ == "__main__":
    unittest.main()
