"""Choosing a decoder, and reading what it produces.

Which of the three ways a file takes decides both how fast it is and which
error it gets when it cannot be read, so the choice is pinned here. No real
media and no ffmpeg: the WAV path is exercised with real WAV files, and the
helper with a script that behaves like one.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import ModuleType
from unittest import mock

import numpy as np

from qwen_scribe import config, decode
from support import install_fake_modules


def write_wave(path: Path, samples, rate=decode.SAMPLE_RATE, channels=1, width=2) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return path


class ReadWaveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_a_16k_mono_wav_is_read_here_and_scaled_like_the_library(self):
        waveform = decode.read_wave(write_wave(self.root / "a.wav", [0, 16384, -32768, 32767]))
        self.assertEqual(waveform.dtype, np.float32)
        self.assertEqual(len(waveform), 4)
        self.assertAlmostEqual(float(waveform[0]), 0.0)
        self.assertAlmostEqual(float(waveform[1]), 0.5)
        self.assertAlmostEqual(float(waveform[2]), -1.0)

    def test_another_sample_rate_is_left_to_a_decoder(self):
        self.assertIsNone(decode.read_wave(write_wave(self.root / "b.wav", [1, 2], rate=44100)))

    def test_two_channels_are_left_to_a_decoder(self):
        self.assertIsNone(decode.read_wave(write_wave(self.root / "c.wav", [1, 2], channels=2)))

    def test_something_that_is_not_a_wav_at_all(self):
        (self.root / "d.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        self.assertIsNone(decode.read_wave(self.root / "d.wav"))
        self.assertIsNone(decode.read_wave(self.root / "missing.wav"))


class PlanTests(unittest.TestCase):
    """Which way a suffix takes, and so which error it can get."""

    def with_helper(self, present=True):
        tool = Path(tempfile.mkdtemp()) / "QwenScribe"
        if present:
            tool.write_text("#!/bin/sh\nexit 0\n")
            tool.chmod(0o755)
        patched = mock.patch.object(config, "DECODER", str(tool) if present else "")
        patched.start()
        self.addCleanup(patched.stop)

    def test_a_wav_is_read_without_any_decoder(self):
        self.with_helper(False)
        self.assertEqual(decode.plan(".wav"), "wave")
        self.assertFalse(decode.needs_ffmpeg(".wav"))

    def test_the_app_decodes_what_avfoundation_reads(self):
        self.with_helper()
        for suffix in (".m4a", ".mp3", ".mp4", ".mov", ".flac", ".qta"):
            self.assertEqual(decode.plan(suffix), "helper", suffix)
            self.assertFalse(decode.needs_ffmpeg(suffix), suffix)

    def test_without_the_app_those_formats_need_ffmpeg(self):
        self.with_helper(False)
        self.assertEqual(decode.plan(".m4a"), "ffmpeg")
        self.assertTrue(decode.needs_ffmpeg(".m4a"))
        self.assertIn("decoder", decode.ffmpeg_required_message(".m4a"))

    def test_five_formats_need_ffmpeg_either_way(self):
        self.with_helper()
        for suffix in decode.FFMPEG_SUFFIXES:
            self.assertEqual(decode.plan(suffix), "ffmpeg", suffix)
            self.assertTrue(decode.needs_ffmpeg(suffix), suffix)
        message = decode.ffmpeg_required_message(".mkv")
        for named in ("mkv", "webm", "ogg", "opus", "wma"):
            self.assertIn(named, message)

    def test_the_catalogue_of_suffixes_matches_what_the_app_accepts(self):
        self.assertEqual(decode.HELPER_SUFFIXES | decode.FFMPEG_SUFFIXES,
                         set(config.ALLOWED_SUFFIXES))


class ToWaveformTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        uploads = mock.patch.object(config, "UPLOAD_DIR", self.root / "uploads")
        uploads.start()
        self.addCleanup(uploads.stop)
        self.ffmpeg_calls = []
        # Whether ffmpeg exists decides which error a file gets, so it is
        # pinned rather than inherited from whatever machine this runs on.
        # Absent by default; the tests that need it say so.
        self.which = mock.patch.object(shutil, "which", return_value=None)
        self.which.start()
        self.addCleanup(self.which.stop)

    def with_ffmpeg(self):
        self.which.stop()
        patched = mock.patch.object(shutil, "which", return_value="/usr/bin/ffmpeg")
        patched.start()
        self.addCleanup(patched.stop)
        self.which = patched

    def fake_library(self, waveform=None, error=None):
        """Stand in for mlx_qwen3_asr.audio.load_audio_np."""
        def load_audio_np(source, sr=decode.SAMPLE_RATE):
            self.ffmpeg_calls.append(source)
            if error is not None:
                raise error
            return waveform
        package = ModuleType("mlx_qwen3_asr")
        audio = ModuleType("mlx_qwen3_asr.audio")
        audio.load_audio_np = load_audio_np
        package.audio = audio
        install_fake_modules(self, {"mlx_qwen3_asr": package, "mlx_qwen3_asr.audio": audio})

    def install_helper(self, body: str) -> Path:
        tool = self.root / "QwenScribe"
        tool.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
        tool.chmod(0o755)
        patched = mock.patch.object(config, "DECODER", str(tool))
        patched.start()
        self.addCleanup(patched.stop)
        return tool

    def test_a_16k_wav_never_reaches_a_decoder(self):
        self.fake_library(error=AssertionError("should not be called"))
        source = write_wave(self.root / "clip.wav", [0, 1000, -1000])
        self.assertEqual(len(decode.to_waveform(source)), 3)
        self.assertEqual(self.ffmpeg_calls, [])

    def test_the_app_decodes_an_m4a_and_the_result_is_read_back(self):
        self.install_helper(
            "import wave, numpy as np\n"
            "with wave.open(sys.argv[3], 'wb') as h:\n"
            "    h.setnchannels(1); h.setsampwidth(2); h.setframerate(16000)\n"
            "    h.writeframes(np.asarray([0, 8192, -8192], dtype='<i2').tobytes())\n")
        self.fake_library(error=AssertionError("ffmpeg should not be needed"))
        source = self.root / "voice.m4a"
        source.write_bytes(b"not really an m4a")
        waveform = decode.to_waveform(source)
        self.assertEqual(len(waveform), 3)
        self.assertAlmostEqual(float(waveform[1]), 0.25)
        # Nothing is left behind in the staging directory.
        self.assertEqual(list(config.UPLOAD_DIR.glob("decoded-*")), [])

    def test_a_wav_at_another_rate_goes_through_the_app_too(self):
        self.install_helper(
            "import wave, numpy as np\n"
            "with wave.open(sys.argv[3], 'wb') as h:\n"
            "    h.setnchannels(1); h.setsampwidth(2); h.setframerate(16000)\n"
            "    h.writeframes(np.asarray([16384], dtype='<i2').tobytes())\n")
        self.fake_library(error=AssertionError("ffmpeg should not be needed"))
        source = write_wave(self.root / "cd.wav", [1, 2, 3], rate=44100)
        self.assertEqual(len(decode.to_waveform(source)), 1)

    def test_a_file_the_app_cannot_read_falls_back_to_ffmpeg(self):
        self.with_ffmpeg()
        self.install_helper("sys.stderr.write('AVFoundation cannot decode this\\n'); sys.exit(5)")
        self.fake_library(waveform=np.zeros(7, dtype=np.float32))
        source = self.root / "odd.mp4"
        source.write_bytes(b"x")
        self.assertEqual(len(decode.to_waveform(source)), 7)
        self.assertEqual(self.ffmpeg_calls, [str(source)])

    def test_when_neither_can_read_it_the_error_names_both(self):
        self.install_helper("sys.stderr.write('AVFoundation cannot decode this\\n'); sys.exit(5)")
        self.fake_library(error=RuntimeError("ffmpeg not found on PATH"))
        source = self.root / "odd.mp4"
        source.write_bytes(b"x")
        with self.assertRaises(decode.DecodeError) as caught:
            decode.to_waveform(source)
        self.assertIn("AVFoundation cannot decode this", str(caught.exception))
        self.assertIn("ffmpeg is not installed", str(caught.exception))

    def test_a_format_only_ffmpeg_reads_says_so_when_it_is_missing(self):
        self.fake_library(error=RuntimeError("ffmpeg not found on PATH"))
        source = self.root / "clip.mkv"
        source.write_bytes(b"x")
        with self.assertRaises(decode.DecodeError) as caught:
            decode.to_waveform(source)
        self.assertIn("mkv", str(caught.exception))
        self.assertIn("brew install ffmpeg", str(caught.exception))

    def test_a_decoder_that_hangs_is_stopped(self):
        self.install_helper("import time\ntime.sleep(30)")
        self.fake_library(error=AssertionError("ffmpeg should not be needed"))
        timeout = mock.patch.object(config, "DECODE_TIMEOUT_SECONDS", 0.4)
        timeout.start()
        self.addCleanup(timeout.stop)
        source = self.root / "slow.m4a"
        source.write_bytes(b"x")
        with self.assertRaises(decode.DecodeError) as caught:
            decode.to_waveform(source)
        self.assertIn("too long", str(caught.exception))
        self.assertEqual(list(config.UPLOAD_DIR.glob("decoded-*")), [])

    def test_a_decoder_that_writes_nonsense_is_not_trusted(self):
        self.with_ffmpeg()
        self.install_helper("open(sys.argv[3], 'wb').write(b'not a wav')")
        self.fake_library(waveform=np.zeros(3, dtype=np.float32))
        source = self.root / "voice.m4a"
        source.write_bytes(b"x")
        # Falls back rather than failing: ffmpeg is still there to try.
        self.assertEqual(len(decode.to_waveform(source)), 3)


if __name__ == "__main__":
    unittest.main()
