from unittest import TestCase

from daemon.grimoire.commands import parse_transcript


class ParseTranscriptTests(TestCase):
    def test_focus_color(self):
        parsed = parse_transcript("focus yellow")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "focus")
        self.assertEqual(parsed.color, "yellow")
        self.assertEqual(parsed.handle, "yellow")

    def test_ignores_window_words(self):
        parsed = parse_transcript("focus the blue window")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "focus")
        self.assertEqual(parsed.color, "blue")

    def test_color_first_order(self):
        parsed = parse_transcript("green maximize")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "maximize")
        self.assertEqual(parsed.color, "green")

    def test_focus_bird_handle(self):
        parsed = parse_transcript("focus sparrow")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "focus")
        self.assertEqual(parsed.handle, "sparrow")

    def test_bird_first_order(self):
        parsed = parse_transcript("owl close")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "close")
        self.assertEqual(parsed.handle, "owl")

    def test_dictation_preserves_text_case(self):
        parsed = parse_transcript("type Makefile target")

        self.assertEqual(parsed.intent, "dictate")
        self.assertEqual(parsed.text, "Makefile target")

    def test_unknown(self):
        parsed = parse_transcript("please do something vague")

        self.assertEqual(parsed.intent, "unknown")
