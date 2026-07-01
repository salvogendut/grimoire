import io
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import call, patch

from daemon import grimoired
from daemon.grimoire.commands import (
    DictationInput,
    ParsedCommand,
    is_supported_command,
    normalize_dictation_input,
    normalize_dictation_text,
    parse_transcript,
    requires_confirmation,
)


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
        self.assertIsNone(parsed.handle)
        self.assertEqual(parsed.text, "Makefile target")

    def test_targeted_dictation(self):
        parsed = parse_transcript("type dove git status enter")

        self.assertEqual(parsed.intent, "dictate")
        self.assertEqual(parsed.handle, "dove")
        self.assertEqual(parsed.text, "git status enter")

    def test_targeted_dictation_with_preamble(self):
        parsed = parse_transcript("dictate to the crow window ls minus la")

        self.assertEqual(parsed.intent, "dictate")
        self.assertEqual(parsed.handle, "crow")
        self.assertEqual(parsed.text, "ls minus la")

    def test_short_dictation_handle_word_is_text(self):
        parsed = parse_transcript("type dove")

        self.assertEqual(parsed.intent, "dictate")
        self.assertIsNone(parsed.handle)
        self.assertEqual(parsed.text, "dove")

    def test_unknown(self):
        parsed = parse_transcript("please do something vague")

        self.assertEqual(parsed.intent, "unknown")
        self.assertFalse(is_supported_command(parsed))

    def test_close_requires_confirmation(self):
        parsed = parse_transcript("close sparrow")

        self.assertTrue(requires_confirmation(parsed))

    def test_focus_does_not_require_confirmation(self):
        parsed = parse_transcript("focus sparrow")

        self.assertFalse(requires_confirmation(parsed))
        self.assertTrue(is_supported_command(parsed))

    def test_unmaximized_asr_variant(self):
        parsed = parse_transcript("un-maximized dove")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "unmaximize")
        self.assertEqual(parsed.handle, "dove")

    def test_un_maximized_asr_variant(self):
        parsed = parse_transcript("un maximized dove")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "unmaximize")
        self.assertEqual(parsed.handle, "dove")

    def test_restore_alias(self):
        parsed = parse_transcript("restore dove")

        self.assertEqual(parsed.intent, "window")
        self.assertEqual(parsed.action, "unmaximize")
        self.assertEqual(parsed.handle, "dove")

    def test_open_app(self):
        parsed = parse_transcript("open calculator")

        self.assertEqual(parsed.intent, "app")
        self.assertEqual(parsed.action, "open")
        self.assertEqual(parsed.app, "calculator")
        self.assertTrue(is_supported_command(parsed))

    def test_launch_multiword_app(self):
        parsed = parse_transcript("launch system monitor")

        self.assertEqual(parsed.intent, "app")
        self.assertEqual(parsed.action, "open")
        self.assertEqual(parsed.app, "system monitor")

    def test_start_app_with_stopword(self):
        parsed = parse_transcript("start the calculator")

        self.assertEqual(parsed.intent, "app")
        self.assertEqual(parsed.action, "open")
        self.assertEqual(parsed.app, "calculator")


class NormalizeDictationTextTests(TestCase):
    def test_terminal_option_words(self):
        self.assertEqual(normalize_dictation_text("ls minus la"), "ls -la")

    def test_path_words(self):
        self.assertEqual(
            normalize_dictation_text("cd slash var slash home"),
            "cd /var/home",
        )

    def test_dot_words(self):
        self.assertEqual(normalize_dictation_text("open Makefile dot txt"), "open Makefile.txt")

    def test_enter_words(self):
        self.assertEqual(normalize_dictation_text("git status enter"), "git status\n")

    def test_quotes(self):
        self.assertEqual(
            normalize_dictation_text("git commit minus m quote first pass quote"),
            'git commit -m "first pass"',
        )

    def test_trailing_enter_becomes_key_press(self):
        self.assertEqual(
            normalize_dictation_input("what's next enter"),
            DictationInput(text="what's next", enter_presses=1),
        )

    def test_multiple_trailing_enters_become_key_presses(self):
        self.assertEqual(
            normalize_dictation_input("git status enter enter"),
            DictationInput(text="git status", enter_presses=2),
        )

    def test_enter_only_has_no_paste_text(self):
        self.assertEqual(
            normalize_dictation_input("enter"),
            DictationInput(text="", enter_presses=1),
        )


class DispatchTests(TestCase):
    def test_targeted_dictation_focuses_before_paste(self):
        parsed = ParsedCommand(intent="dictate", handle="dove", text="git status enter")

        with patch.object(grimoired, "call_shell", side_effect=[0, 0, 0]) as call_shell:
            with patch.object(grimoired.time, "sleep") as sleep:
                status = grimoired.dispatch(parsed, trace=False)

        self.assertEqual(status, 0)
        self.assertEqual(
            call_shell.mock_calls,
            [
                call("RunWindowCommand", "dove", "focus"),
                call("PasteText", "git status"),
                call("PressKey", "enter"),
            ],
        )
        self.assertEqual(sleep.mock_calls, [call(0.15), call(0.08)])

    def test_targeted_dictation_stops_when_focus_fails(self):
        parsed = ParsedCommand(intent="dictate", handle="dove", text="git status enter")

        with patch.object(grimoired, "call_shell", return_value=1) as call_shell:
            status = grimoired.dispatch(parsed, trace=False)

        self.assertEqual(status, 1)
        call_shell.assert_called_once_with("RunWindowCommand", "dove", "focus")

    def test_enter_only_dictation_skips_paste(self):
        parsed = ParsedCommand(intent="dictate", handle="dove", text="enter")

        with patch.object(grimoired, "call_shell", side_effect=[0, 0]) as call_shell:
            with patch.object(grimoired.time, "sleep"):
                status = grimoired.dispatch(parsed, trace=False)

        self.assertEqual(status, 0)
        self.assertEqual(
            call_shell.mock_calls,
            [
                call("RunWindowCommand", "dove", "focus"),
                call("PressKey", "enter"),
            ],
        )

    def test_targeted_dictation_trace(self):
        parsed = ParsedCommand(intent="dictate", handle="dove", text="git status enter")
        output = io.StringIO()

        with patch.object(grimoired, "call_shell", side_effect=[0, 0, 0]):
            with patch.object(grimoired.time, "sleep"):
                with redirect_stdout(output):
                    status = grimoired.dispatch(parsed)

        self.assertEqual(status, 0)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "action: focus dove -> ok",
                'action: paste "git status" -> ok',
                "action: press enter -> ok",
            ],
        )

    def test_failed_action_trace(self):
        parsed = ParsedCommand(intent="window", action="focus", handle="dove")
        output = io.StringIO()

        with patch.object(grimoired, "call_shell", return_value=1):
            with redirect_stdout(output):
                status = grimoired.dispatch(parsed)

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue().strip(), "action: focus dove -> failed")
