"""Dictation cleanups: spoken commands, replacements, and normalisation."""

import unittest

from qwen_scribe import cleanup


class SpokenCommandTests(unittest.TestCase):
    def test_a_command_standing_on_its_own_becomes_a_break(self):
        self.assertEqual(
            cleanup.dictation_text("Dear Sam. New paragraph. Thanks for the notes. New line. Best, Al.", "English", {}),
            "Dear Sam.\n\nThanks for the notes.\nBest, Al.",
        )

    def test_a_command_inside_prose_is_left_alone(self):
        text = "We launched a new line of products and a new paragraph in the terms."
        self.assertEqual(cleanup.dictation_text(text, "English", {}), text)

    def test_commands_after_a_comma_or_at_the_start(self):
        self.assertEqual(cleanup.dictation_text("New line, first item, new line, second item.", "English", {}),
                         "first item,\nsecond item.")

    def test_commands_only_apply_to_the_language_they_belong_to(self):
        text = "Guten Tag. New line. Bis bald."
        self.assertEqual(cleanup.dictation_text(text, "German", {}), text)
        self.assertEqual(cleanup.dictation_text(text, None, {}), text)

    def test_commands_can_be_switched_off(self):
        text = "One. New line. Two."
        self.assertEqual(cleanup.dictation_text(text, "English", {"spoken_commands": False}), text)


class ReplacementTests(unittest.TestCase):
    def test_whole_words_case_insensitively(self):
        rules = [{"from": "qwen scribe", "to": "Qwen Scribe"}, {"from": "cat", "to": "Cat"}]
        self.assertEqual(
            cleanup.apply_replacements("qwen  scribe and QWEN SCRIBE saw a cat in the catalogue.", rules),
            "Qwen Scribe and Qwen Scribe saw a Cat in the catalogue.",
        )

    def test_longest_phrase_wins(self):
        rules = [{"from": "Qwen", "to": "QWEN"}, {"from": "Qwen Scribe", "to": "the app"}]
        self.assertEqual(cleanup.apply_replacements("Qwen Scribe by Qwen", rules), "the app by QWEN")

    def test_an_apostrophe_is_part_of_the_word(self):
        rules = [{"from": "Al", "to": "Alexandra"}]
        self.assertEqual(cleanup.apply_replacements("Al's plan, Al.", rules), "Al's plan, Alexandra.")

    def test_validator(self):
        self.assertTrue(cleanup.valid_replacements([]))
        self.assertTrue(cleanup.valid_replacements([{"from": "a", "to": ""}]))
        for bad in (
            "not a list",
            [{"from": "", "to": "x"}],
            [{"from": "   ", "to": "x"}],
            [{"from": "a"}],
            [{"from": "a", "to": "b", "extra": 1}],
            [{"from": 1, "to": "b"}],
            [{"from": "x" * 101, "to": "b"}],
            [{"from": "a", "to": "x" * 101}],
            [{"from": "a", "to": "b"}] * 101,
        ):
            with self.subTest(bad=bad):
                self.assertFalse(cleanup.valid_replacements(bad))


class NormalisationTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(cleanup.normalise("  Hello   world \n\n\n\nNext  line  \r\n"), "Hello world\n\nNext line")

    def test_a_break_before_punctuation_does_not_orphan_it(self):
        self.assertEqual(cleanup.normalise("End\n."), "End.")


if __name__ == "__main__":
    unittest.main()
