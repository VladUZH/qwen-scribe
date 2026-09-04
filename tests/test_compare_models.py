"""The A/B metric in compare_models.py, on transcripts a real model produced.

The strings below came out of the 0.6B model and its 4-bit conversion on a
macOS runner, which is what the metric exists to compare.
"""

import unittest

from compare_models import word_diff_rate, words


class TokenTests(unittest.TestCase):
    def test_latin_text_splits_into_words(self):
        self.assertEqual(words("The meeting starts at three."),
                         ["the", "meeting", "starts", "at", "three"])
        self.assertEqual(words("don't stop"), ["don't", "stop"])

    def test_cjk_splits_into_characters(self):
        # Written without spaces: one whitespace "word" would be the whole
        # sentence, and a single wrong character would hide inside it.
        self.assertEqual(words("会議は3時"), ["会", "議", "は", "3", "時"])
        self.assertEqual(words("안녕하세요 오늘"),
                         ["안", "녕", "하", "세", "요", "오", "늘"])

    def test_a_latin_word_next_to_cjk_stays_whole(self):
        self.assertEqual(words("Mac今日"), ["mac", "今", "日"])


class DiffRateTests(unittest.TestCase):
    def test_identical_transcripts_differ_by_nothing(self):
        rate, spans = word_diff_rate("hello there", "hello there")
        self.assertEqual(rate, 0.0)
        self.assertEqual(spans, [])

    def test_punctuation_and_korean_spacing_are_not_differences(self):
        # The same Korean sentence from both models: the 4-bit output added
        # commas and wrote "세시에" for "세 시에". Nothing was transcribed
        # differently, and the metric should say so.
        rate, _spans = word_diff_rate(
            "안녕하세요 오늘 날씨가 정말 좋네요 회의는 세 시에 시작합니다.",
            "안녕하세요, 오늘 날씨가 정말 좋네요. 회의는 세시에 시작합니다.")
        self.assertEqual(rate, 0.0)

    def test_a_wrong_japanese_word_is_reported(self):
        # The 4-bit model heard "今日は" (today) for "こんにちは" (hello).
        rate, spans = word_diff_rate(
            "こんにちは今日はいい天気ですね。会議は3時に始まります。",
            "今日は今日はいい天気ですね。会議は3時に始まります。")
        self.assertGreater(rate, 0.1)
        self.assertLess(rate, 0.3)
        self.assertTrue(spans)

    def test_a_run_together_name_is_reported(self):
        rate, spans = word_diff_rate("Quint scribe transcribes speech",
                                     "Quinscribe transcribes speech")
        self.assertAlmostEqual(rate, 0.5, places=2)
        self.assertIn("quint scribe", spans[0])

    def test_nothing_in_common_approaches_everything(self):
        rate, _spans = word_diff_rate("the meeting starts at three", "ハローワールド")
        self.assertGreater(rate, 0.9)


if __name__ == "__main__":
    unittest.main()
