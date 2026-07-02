import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from daemon import grimoired
from daemon.grimoire.commands import ParsedCommand


class AIProviderTests(TestCase):
    def test_defaults_to_openai(self):
        with patch.dict(grimoired.os.environ, {}, clear=True):
            self.assertEqual(grimoired.ai_provider(), "openai")

    def test_accepts_anthropic(self):
        with patch.dict(grimoired.os.environ, {"GRIMOIRE_AI_PROVIDER": "anthropic"}, clear=True):
            self.assertEqual(grimoired.ai_provider(), "anthropic")

    def test_claude_alias_maps_to_claude_code(self):
        with patch.dict(grimoired.os.environ, {"GRIMOIRE_AI_PROVIDER": "claude"}, clear=True):
            self.assertEqual(grimoired.ai_provider(), "claude-code")

    def test_rejects_unknown_provider(self):
        with patch.dict(grimoired.os.environ, {"GRIMOIRE_AI_PROVIDER": "gemini"}, clear=True):
            with self.assertRaises(grimoired.AIInterpreterError):
                grimoired.ai_provider()

    def test_parse_with_ai_survives_interpreter_failure(self):
        args = grimoired.argparse.Namespace(ai=True, ai_dry_run=False, ai_mode=None)

        with patch.object(
            grimoired,
            "interpret_command_with_ai",
            side_effect=grimoired.AIInterpreterError("boom"),
        ):
            parsed = grimoired.parse_with_optional_ai("put dove away", args)

        self.assertEqual(parsed.intent, "unknown")

    def test_missing_openai_key_does_not_exit(self):
        args = grimoired.argparse.Namespace(ai=True, ai_dry_run=False, ai_mode=None)

        with patch.dict(grimoired.os.environ, {}, clear=True):
            parsed = grimoired.parse_with_optional_ai("put dove away", args)

        self.assertEqual(parsed.intent, "unknown")


class AnthropicPayloadTests(TestCase):
    def test_extracts_tool_input(self):
        payload = grimoired.extract_anthropic_tool_input({
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "name": "grimoire_command", "input": {"intent": "window"}},
            ],
        })

        self.assertEqual(payload, {"intent": "window"})

    def test_rejects_response_without_tool_use(self):
        with self.assertRaises(grimoired.AIInterpreterError):
            grimoired.extract_anthropic_tool_input({"content": [{"type": "text", "text": "hi"}]})

    def test_rejects_non_object_response(self):
        with self.assertRaises(grimoired.AIInterpreterError):
            grimoired.extract_anthropic_tool_input(["not", "a", "dict"])


class ClaudeCliPayloadTests(TestCase):
    def test_parses_plain_json_result(self):
        stdout = json.dumps({"result": '{"intent": "window", "action": "focus"}'})

        payload = grimoired.parse_claude_cli_payload(stdout)

        self.assertEqual(payload, {"intent": "window", "action": "focus"})

    def test_parses_fenced_json_result(self):
        stdout = json.dumps({"result": 'Here it is:\n```json\n{"intent": "unknown"}\n```'})

        payload = grimoired.parse_claude_cli_payload(stdout)

        self.assertEqual(payload, {"intent": "unknown"})

    def test_rejects_cli_error(self):
        stdout = json.dumps({"is_error": True, "result": "not logged in"})

        with self.assertRaises(grimoired.AIInterpreterError):
            grimoired.parse_claude_cli_payload(stdout)

    def test_rejects_result_without_json(self):
        stdout = json.dumps({"result": "sorry, no can do"})

        with self.assertRaises(grimoired.AIInterpreterError):
            grimoired.parse_claude_cli_payload(stdout)

    def test_interpreter_requires_cli(self):
        deterministic = ParsedCommand(intent="unknown", text="put dove away")

        with patch.object(grimoired, "claude_cli_path", return_value=None):
            with self.assertRaises(grimoired.AIInterpreterError):
                grimoired.call_claude_cli_interpreter("put dove away", deterministic, "fallback")


class CheckAITests(TestCase):
    def test_reports_anthropic_missing_key(self):
        output = io.StringIO()

        environment = {"GRIMOIRE_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}
        with patch.dict(grimoired.os.environ, environment, clear=True):
            with redirect_stdout(output):
                status = grimoired.check_ai()

        self.assertEqual(status, 1)
        self.assertIn("anthropic-api-key: missing", output.getvalue())

    def test_reports_missing_claude_cli(self):
        output = io.StringIO()

        with patch.dict(grimoired.os.environ, {"GRIMOIRE_AI_PROVIDER": "claude-code"}, clear=True):
            with patch.object(grimoired, "claude_cli_path", return_value=None):
                with redirect_stdout(output):
                    status = grimoired.check_ai()

        self.assertEqual(status, 1)
        self.assertIn("claude-cli: missing", output.getvalue())

    def test_accepts_found_claude_cli(self):
        output = io.StringIO()

        with patch.dict(grimoired.os.environ, {"GRIMOIRE_AI_PROVIDER": "claude"}, clear=True):
            with patch.object(grimoired, "claude_cli_path", return_value=Path("/usr/bin/claude")):
                with redirect_stdout(output):
                    status = grimoired.check_ai()

        self.assertEqual(status, 0)
        self.assertIn("claude-cli: found /usr/bin/claude", output.getvalue())


class UpdateEnvFileTests(TestCase):
    def test_creates_file_with_secure_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config" / "grimoired.env"

            grimoired.update_env_file(path, {"GRIMOIRE_AI_PROVIDER": "anthropic"})

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('GRIMOIRE_AI_PROVIDER="anthropic"', path.read_text())

    def test_replaces_commented_placeholder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "grimoired.env"
            path.write_text(
                "# Optional AI interpreter.\n"
                "# GRIMOIRE_AI_PROVIDER=openai\n"
                "GRIMOIRE_DAEMON_ARGS=\"--listen-service\"\n"
            )

            grimoired.update_env_file(path, {"GRIMOIRE_AI_PROVIDER": "claude-code"})

            content = path.read_text()
            self.assertIn('GRIMOIRE_AI_PROVIDER="claude-code"', content)
            self.assertNotIn("# GRIMOIRE_AI_PROVIDER=openai", content)
            self.assertIn('GRIMOIRE_DAEMON_ARGS="--listen-service"', content)
            self.assertIn("# Optional AI interpreter.", content)

    def test_replaces_existing_value_once_and_appends_new_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "grimoired.env"
            path.write_text('OPENAI_API_KEY="old"\n')

            grimoired.update_env_file(path, {
                "OPENAI_API_KEY": "new",
                "GRIMOIRE_AI_MODE": "fallback",
            })

            content = path.read_text()
            self.assertEqual(content.count("OPENAI_API_KEY"), 1)
            self.assertIn('OPENAI_API_KEY="new"', content)
            self.assertIn('GRIMOIRE_AI_MODE="fallback"', content)

    def test_escapes_quotes_in_values(self):
        self.assertEqual(
            grimoired.format_env_line("KEY", 'a"b\\c'),
            'KEY="a\\"b\\\\c"',
        )
