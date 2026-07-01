#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

if __package__:
    from .grimoire.commands import (
        ParsedCommand,
        is_supported_command,
        normalize_dictation_input,
        parse_transcript,
        requires_confirmation,
    )
else:
    from grimoire.commands import (
        ParsedCommand,
        is_supported_command,
        normalize_dictation_input,
        parse_transcript,
        requires_confirmation,
    )


BUS_NAME = "org.grimoire.Shell"
OBJECT_PATH = "/org/grimoire/Shell"
INTERFACE = "org.grimoire.Shell"
DEFAULT_WHISPER_CPP = Path("/var/home/salvogendut/Dev/whisper.cpp/build/bin/whisper-cli")
DEFAULT_WHISPER_MODEL = Path("/var/home/salvogendut/Dev/whisper.cpp/models/ggml-base.en.bin")
WHISPER_CPP_CANDIDATES = (
    Path("/usr/bin/whisper-cli"),
    Path("/usr/local/bin/whisper-cli"),
    DEFAULT_WHISPER_CPP,
)
WHISPER_MODEL_CANDIDATES = (
    Path.home() / ".local/share/grimoire/models/ggml-base.en.bin",
    Path("/usr/share/grimoire/models/ggml-base.en.bin"),
    DEFAULT_WHISPER_MODEL,
)
RECORD_RATE = 16000
DAEMON_STATUS_INTERVAL_SECONDS = 2.0
DAEMON_STATE_INACTIVE = "inactive"
DAEMON_STATE_IDLE = "idle"
DAEMON_STATE_RECORDING = "recording"
DAEMON_STATE_TRANSCRIBING = "transcribing"
DAEMON_STATE_PARSING = "parsing"
DAEMON_STATE_PARSED = "parsed"
DAEMON_STATE_EXECUTING = "executing"
DAEMON_STATE_BLOCKED = "blocked"
DAEMON_STATE_ERROR = "error"
DAEMON_STATES = {
    DAEMON_STATE_INACTIVE,
    DAEMON_STATE_IDLE,
    DAEMON_STATE_RECORDING,
    DAEMON_STATE_TRANSCRIBING,
    DAEMON_STATE_PARSING,
    DAEMON_STATE_PARSED,
    DAEMON_STATE_EXECUTING,
    DAEMON_STATE_BLOCKED,
    DAEMON_STATE_ERROR,
}
MAX_DAEMON_STATUS_DETAIL_LENGTH = 120
_current_daemon_status: DaemonStatusHeartbeat | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse and dispatch Grimoire voice commands.",
    )
    parser.add_argument(
        "--command",
        help="Transcript to parse and dispatch, for example: 'focus yellow'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the command but do not call the shell extension.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress command trace output.",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="Ask the shell extension for its current color registry.",
    )
    parser.add_argument(
        "--list-apps",
        action="store_true",
        help="Ask the shell extension for launchable applications.",
    )
    parser.add_argument(
        "--execution-mode",
        action="store_true",
        help="Print whether listened command execution is currently armed.",
    )
    parser.add_argument(
        "--arm-execution",
        action="store_true",
        help="Allow listened commands to execute until disarmed or the daemon stops.",
    )
    parser.add_argument(
        "--disarm-execution",
        action="store_true",
        help="Prevent listened commands from executing.",
    )
    parser.add_argument(
        "--check-asr",
        action="store_true",
        help="Check whether the configured speech recognizer and model are available.",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="Record one short utterance, transcribe it, and parse it.",
    )
    parser.add_argument(
        "--listen-loop",
        action="store_true",
        help="Run an Enter-to-record command loop. Press q then Enter to quit.",
    )
    parser.add_argument(
        "--listen-service",
        action="store_true",
        help="Run a non-interactive continuous listen loop for a user service.",
    )
    parser.add_argument(
        "--execute-listen",
        action="store_true",
        help="Execute a listened command. Without this, listen mode is parse-only.",
    )
    parser.add_argument(
        "--record-seconds",
        type=float,
        default=3.0,
        help="Seconds to record in --listen mode. Default: 3.0.",
    )
    parser.add_argument(
        "--listen-delay",
        type=float,
        default=0.5,
        help="Seconds to wait between --listen-service recordings. Default: 0.5.",
    )
    parser.add_argument(
        "--audio-file",
        help="Transcribe this audio file instead of recording from the microphone.",
    )
    parser.add_argument(
        "--asr-command",
        help=(
            "ASR command template. Use {audio} for the audio path. "
            "Defaults to the local whisper.cpp installation when present."
        ),
    )
    args = parser.parse_args(argv)

    if args.list_windows:
        return call_shell("ListWindows")

    if args.list_apps:
        return call_shell("ListApps")

    if args.execution_mode:
        return print_execution_mode()

    if args.arm_execution:
        return set_execution_mode(True)

    if args.disarm_execution:
        return set_execution_mode(False)

    if args.check_asr:
        return check_asr(args.asr_command)

    if args.listen_loop:
        return listen_loop(args)

    if args.listen_service:
        return listen_service(args)

    if args.listen or args.audio_file:
        return listen_once(args)

    if not args.command:
        parser.error(
            "--command is required unless --list-windows, --list-apps, --listen, "
            "--execution-mode, --arm-execution, --disarm-execution, --check-asr, "
            "--listen-loop, --listen-service, or --audio-file is used"
        )

    parsed = parse_transcript(args.command)
    trace = not args.quiet
    if trace:
        trace_recognition(args.command, parsed)

    if args.dry_run:
        if not trace:
            print(parsed)
        return 0

    return dispatch(parsed, trace=trace)


def listen_loop(args: argparse.Namespace) -> int:
    print("Press Enter to record a command. Type q then Enter to quit.")
    print("Use Ctrl+C to stop immediately.")

    with DaemonStatusHeartbeat():
        while True:
            try:
                prompt = input("grimoire> ")
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                return 130

            if prompt.strip().lower() in {"q", "quit", "exit"}:
                return 0

            try:
                parsed = listen_and_parse(args)
            except (Exception, SystemExit) as error:
                set_daemon_state(DAEMON_STATE_ERROR, error)
                raise

            if not is_supported_command(parsed):
                set_daemon_state(DAEMON_STATE_BLOCKED, "no supported command")
                print("No supported command recognized.", file=sys.stderr)
                continue

            if args.dry_run:
                set_daemon_state(DAEMON_STATE_IDLE)
                continue

            if not should_execute_listened_command(args, trace=not args.quiet):
                continue

            if requires_confirmation(parsed) and not confirm_command(parsed):
                set_daemon_state(DAEMON_STATE_BLOCKED, "confirmation declined")
                print("skipped")
                continue

            set_daemon_state(DAEMON_STATE_EXECUTING, describe_parsed_command(parsed))
            status = dispatch(parsed, trace=not args.quiet)
            if status == 0:
                set_daemon_state(DAEMON_STATE_IDLE)
            else:
                set_daemon_state(DAEMON_STATE_ERROR, f"dispatch failed: {status}")


def listen_service(args: argparse.Namespace) -> int:
    if args.listen_delay < 0:
        raise SystemExit("--listen-delay must be zero or greater")

    print("Starting Grimoire listen service.", flush=True)
    if not args.execute_listen:
        print("Service is parse-only without --execute-listen.", file=sys.stderr)

    with DaemonStatusHeartbeat():
        try:
            while True:
                try:
                    parsed = listen_and_parse(args)
                except (Exception, SystemExit) as error:
                    set_daemon_state(DAEMON_STATE_ERROR, error)
                    raise

                if not is_supported_command(parsed):
                    set_daemon_state(DAEMON_STATE_BLOCKED, "no supported command")
                    print("No supported command recognized.", file=sys.stderr)
                elif not should_execute_listened_command(args, trace=not args.quiet):
                    continue
                elif requires_confirmation(parsed):
                    set_daemon_state(DAEMON_STATE_BLOCKED, "destructive command skipped")
                    print(
                        f"Skipped destructive command in service mode: "
                        f"{parsed.action} {parsed.handle}",
                        file=sys.stderr,
                    )
                else:
                    set_daemon_state(DAEMON_STATE_EXECUTING, describe_parsed_command(parsed))
                    status = dispatch(parsed, trace=not args.quiet)
                    if status == 0:
                        set_daemon_state(DAEMON_STATE_IDLE)
                    else:
                        set_daemon_state(DAEMON_STATE_ERROR, f"dispatch failed: {status}")

                if args.listen_delay:
                    time.sleep(args.listen_delay)
        except KeyboardInterrupt:
            return 130


class DaemonStatusHeartbeat:
    def __init__(self, interval: float = DAEMON_STATUS_INTERVAL_SECONDS) -> None:
        self.interval = interval
        self.state = DAEMON_STATE_IDLE
        self.detail = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> DaemonStatusHeartbeat:
        global _current_daemon_status

        _current_daemon_status = self
        self.send()
        self._thread = threading.Thread(
            target=self._run,
            name="grimoire-daemon-status",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        global _current_daemon_status

        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if exc is not None and exc_type not in {KeyboardInterrupt, EOFError}:
            self.set_state(DAEMON_STATE_ERROR, format_status_detail(exc))
        update_daemon_state(DAEMON_STATE_INACTIVE)
        if _current_daemon_status is self:
            _current_daemon_status = None

    def set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            self.state = normalize_daemon_state(state)
            self.detail = format_status_detail(detail)

        self.send()

    def send(self) -> int:
        with self._lock:
            state = self.state
            detail = self.detail

        return update_daemon_state(state, detail)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.send()


def update_daemon_status(running: bool) -> int:
    return update_daemon_state(
        DAEMON_STATE_IDLE if running else DAEMON_STATE_INACTIVE,
    )


def update_daemon_state(state: str, detail: str = "") -> int:
    state = normalize_daemon_state(state)
    detail = format_status_detail(detail)

    status = call_shell_quiet("SetDaemonState", state, detail)
    if status == 0:
        return status

    return call_shell_quiet(
        "SetDaemonStatus",
        "false" if state == DAEMON_STATE_INACTIVE else "true",
    )


def set_daemon_state(state: str, detail: str = "") -> None:
    reporter = _current_daemon_status
    if reporter is None:
        update_daemon_state(state, detail)
        return

    reporter.set_state(state, detail)


def normalize_daemon_state(state: str) -> str:
    normalized = state.strip().lower()
    if normalized not in DAEMON_STATES:
        return DAEMON_STATE_ERROR

    return normalized


def format_status_detail(detail: object) -> str:
    text = " ".join(str(detail).strip().split())
    if len(text) <= MAX_DAEMON_STATUS_DETAIL_LENGTH:
        return text

    return f"{text[:MAX_DAEMON_STATUS_DETAIL_LENGTH - 3]}..."


def should_execute_listened_command(args: argparse.Namespace, trace: bool = True) -> bool:
    if args.dry_run or not args.execute_listen:
        return False

    enabled = execution_mode_enabled()
    if trace and not enabled:
        print("execution disabled; click the Grimoire top-bar icon or press Ctrl+Alt+Space to arm")
    if not enabled:
        set_daemon_state(DAEMON_STATE_BLOCKED, "execution disabled")

    return enabled


def execution_mode_enabled() -> bool:
    status, enabled = call_shell_boolean("GetExecutionMode")
    return status == 0 and enabled


def print_execution_mode() -> int:
    status, enabled = call_shell_boolean("GetExecutionMode")
    if status != 0:
        print("execution: unknown")
        return status

    print(f"execution: {'armed' if enabled else 'disarmed'}")
    return 0


def set_execution_mode(enabled: bool) -> int:
    status = call_shell("SetExecutionMode", "true" if enabled else "false")
    if status == 0:
        print(f"execution: {'armed' if enabled else 'disarmed'}")

    return status


def listen_once(args: argparse.Namespace) -> int:
    with DaemonStatusHeartbeat():
        try:
            parsed = listen_and_parse(args)
        except (Exception, SystemExit) as error:
            set_daemon_state(DAEMON_STATE_ERROR, error)
            raise

        if args.execute_listen:
            if not is_supported_command(parsed):
                set_daemon_state(DAEMON_STATE_BLOCKED, "no supported command")
                print("No supported command recognized.", file=sys.stderr)
                return 2
            if not should_execute_listened_command(args, trace=not args.quiet):
                return 0

            set_daemon_state(DAEMON_STATE_EXECUTING, describe_parsed_command(parsed))
            status = dispatch(parsed, trace=not args.quiet)
            if status == 0:
                set_daemon_state(DAEMON_STATE_IDLE)
            else:
                set_daemon_state(DAEMON_STATE_ERROR, f"dispatch failed: {status}")
            return status

        set_daemon_state(DAEMON_STATE_IDLE)
        return 0


def listen_and_parse(args: argparse.Namespace) -> ParsedCommand:
    with tempfile.TemporaryDirectory(prefix="grimoire-listen-") as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        audio_path = Path(args.audio_file) if args.audio_file else tmpdir / "utterance.wav"

        if not args.audio_file:
            set_daemon_state(DAEMON_STATE_RECORDING, f"{args.record_seconds:.1f}s")
            record_audio(audio_path, args.record_seconds)

        set_daemon_state(DAEMON_STATE_TRANSCRIBING, audio_path.name)
        transcript = transcribe_audio(audio_path, tmpdir, args.asr_command)
        set_daemon_state(DAEMON_STATE_PARSING, transcript)
        parsed = parse_transcript(transcript)
        set_daemon_state(DAEMON_STATE_PARSED, describe_parsed_command(parsed))

        if not args.quiet:
            trace_recognition(transcript, parsed)
        else:
            print(f"transcript: {transcript}")
            print(f"parsed: {parsed}")

        return parsed


def record_audio(audio_path: Path, seconds: float) -> None:
    if seconds <= 0:
        raise SystemExit("--record-seconds must be greater than zero")

    sample_count = max(1, int(seconds * RECORD_RATE))
    command = [
        "pw-record",
        "--rate",
        str(RECORD_RATE),
        "--channels",
        "1",
        "--format",
        "s16",
        "--container",
        "wav",
        "--sample-count",
        str(sample_count),
        str(audio_path),
    ]

    print(f"recording {seconds:.1f}s...", file=sys.stderr)
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return

    # Some pw-record builds return 1 after --sample-count even though they wrote
    # a valid WAV. Treat that as success, but only if audio data exists.
    if audio_path.exists() and audio_path.stat().st_size > 44:
        print(
            f"pw-record exited with {result.returncode}, continuing with recorded WAV",
            file=sys.stderr,
        )
        return

    raise SystemExit(format_failure(command, result, "record audio"))


def transcribe_audio(audio_path: Path, tmpdir: Path, asr_command: str | None) -> str:
    if asr_command:
        command = shlex.split(asr_command.format(audio=str(audio_path)))
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise SystemExit(format_failure(command, result, "run ASR command"))
        return normalize_transcript(result.stdout)

    whisper_cli = configured_path("GRIMOIRE_WHISPER_CLI", WHISPER_CPP_CANDIDATES)
    whisper_model = configured_path("GRIMOIRE_WHISPER_MODEL", WHISPER_MODEL_CANDIDATES)

    if not whisper_cli.exists():
        raise SystemExit(
            f"whisper.cpp binary not found: {whisper_cli}\n"
            f"Checked: {format_path_candidates(WHISPER_CPP_CANDIDATES)}\n"
            "Set GRIMOIRE_WHISPER_CLI or pass --asr-command."
        )
    if not whisper_model.exists():
        raise SystemExit(
            f"whisper.cpp model not found: {whisper_model}\n"
            f"Checked: {format_path_candidates(WHISPER_MODEL_CANDIDATES)}\n"
            "Set GRIMOIRE_WHISPER_MODEL or pass --asr-command."
        )

    output_base = tmpdir / "transcript"
    command = [
        str(whisper_cli),
        "-m",
        str(whisper_model),
        "-f",
        str(audio_path),
        "-np",
        "-nt",
        "-otxt",
        "-of",
        str(output_base),
    ]
    run_checked(command, "transcribe audio")

    transcript_path = output_base.with_suffix(".txt")
    if not transcript_path.exists():
        raise SystemExit(f"whisper.cpp did not write transcript: {transcript_path}")

    return normalize_transcript(transcript_path.read_text(encoding="utf-8"))


def check_asr(asr_command: str | None = None) -> int:
    if asr_command:
        return check_asr_command(asr_command)

    whisper_cli = configured_path("GRIMOIRE_WHISPER_CLI", WHISPER_CPP_CANDIDATES)
    whisper_model = configured_path("GRIMOIRE_WHISPER_MODEL", WHISPER_MODEL_CANDIDATES)

    cli_ok = whisper_cli.exists()
    model_ok = whisper_model.exists()

    print_path_status("whisper-cli", whisper_cli, cli_ok)
    if not cli_ok:
        print(f"checked whisper-cli paths: {format_path_candidates(WHISPER_CPP_CANDIDATES)}")
        print("hint: install whisper.cpp or set GRIMOIRE_WHISPER_CLI")

    print_path_status("model", whisper_model, model_ok)
    if not model_ok:
        print(f"checked model paths: {format_path_candidates(WHISPER_MODEL_CANDIDATES)}")
        print("hint: install ggml-base.en.bin or set GRIMOIRE_WHISPER_MODEL")

    return 0 if cli_ok and model_ok else 1


def check_asr_command(asr_command: str) -> int:
    try:
        command = shlex.split(asr_command.format(audio="/tmp/grimoire-check.wav"))
    except (KeyError, ValueError) as error:
        print(f"asr-command: invalid template: {error}")
        return 1

    if not command:
        print("asr-command: empty")
        return 1

    executable = resolve_executable(command[0])
    executable_ok = executable is not None
    print(f"asr-command: {asr_command}")
    print_path_status("asr-command executable", executable or Path(command[0]), executable_ok)

    if "{audio}" not in asr_command:
        print("warning: asr-command does not include {audio}")

    if not executable_ok:
        print("hint: install the ASR command or use an absolute executable path")
        return 1

    return 0


def resolve_executable(command_name: str) -> Path | None:
    command_path = Path(command_name)
    if command_path.parent != Path("."):
        return command_path if command_path.exists() else None

    resolved = shutil.which(command_name)
    return Path(resolved) if resolved else None


def print_path_status(label: str, path: Path, ok: bool) -> None:
    status = "found" if ok else "missing"
    print(f"{label}: {status} {path}")


def configured_path(env_name: str, candidates: tuple[Path, ...]) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def format_path_candidates(candidates: tuple[Path, ...]) -> str:
    return ", ".join(str(candidate) for candidate in candidates)


def normalize_transcript(transcript: str) -> str:
    return " ".join(transcript.strip().split())


def run_checked(command: list[str], action: str) -> None:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SystemExit(format_failure(command, result, action))


def format_failure(command: list[str], result: subprocess.CompletedProcess[str], action: str) -> str:
    lines = [
        f"Failed to {action}: {' '.join(shlex.quote(part) for part in command)}",
        f"exit code: {result.returncode}",
    ]
    if result.stdout:
        lines.append("stdout:")
        lines.append(result.stdout.rstrip())
    if result.stderr:
        lines.append("stderr:")
        lines.append(result.stderr.rstrip())
    return "\n".join(lines)


def dispatch(parsed: ParsedCommand, trace: bool = True) -> int:
    if parsed.is_window_command:
        assert parsed.handle is not None
        assert parsed.action is not None
        return run_shell_action(
            f"{parsed.action} {parsed.handle}",
            "RunWindowCommand",
            parsed.handle,
            parsed.action,
            trace=trace,
        )

    if parsed.is_app_command:
        assert parsed.app is not None
        return run_shell_action(
            f"open app {quote_text(parsed.app)}",
            "LaunchApp",
            parsed.app,
            trace=trace,
        )

    if parsed.is_inventory_command:
        assert parsed.action is not None
        return dispatch_inventory(parsed.action, trace=trace)

    if parsed.is_handle_command:
        assert parsed.action is not None
        return dispatch_handle_action(parsed.action, trace=trace)

    if parsed.intent == "dictate":
        assert parsed.text is not None
        if parsed.handle is not None:
            focus_status = run_shell_action(
                f"focus {parsed.handle}",
                "RunWindowCommand",
                parsed.handle,
                "focus",
                trace=trace,
            )
            if focus_status != 0:
                return focus_status
            time.sleep(0.15)

        dictation = normalize_dictation_input(parsed.text)
        if dictation.text:
            paste_status = run_shell_action(
                f"paste {quote_text(dictation.text)}",
                "PasteText",
                dictation.text,
                trace=trace,
            )
            if paste_status != 0:
                return paste_status
            if dictation.enter_presses:
                time.sleep(0.08)

        for _ in range(dictation.enter_presses):
            key_status = run_shell_action("press enter", "PressKey", "enter", trace=trace)
            if key_status != 0:
                return key_status

        return 0

    print(f"Unsupported command: {parsed}", file=sys.stderr)
    return 2


def confirm_command(parsed: ParsedCommand) -> bool:
    answer = input(f"Confirm {parsed.action} {parsed.handle}? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def dispatch_inventory(action: str, trace: bool = True) -> int:
    if action == "windows":
        status, windows = call_shell_json("ListWindows")
        if trace:
            print(f"action: list windows -> {'ok' if status == 0 else 'failed'}")
        if status == 0:
            print(format_windows(windows))
        return status

    if action == "apps":
        status, apps = call_shell_json("ListApps")
        if trace:
            print(f"action: list apps -> {'ok' if status == 0 else 'failed'}")
        if status == 0:
            print(format_apps(apps))
        return status

    print(f"Unsupported inventory action: {action}", file=sys.stderr)
    return 2


def dispatch_handle_action(action: str, trace: bool = True) -> int:
    if action == "refresh":
        return run_shell_action("refresh handles", "RefreshHandles", trace=trace)

    print(f"Unsupported handle action: {action}", file=sys.stderr)
    return 2


def call_shell_json(method: str, *args: str) -> tuple[int, list[dict[str, object]]]:
    result = run_gdbus(method, *args)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    status = shell_status(result)
    if status != 0:
        if result.stdout:
            print(result.stdout.rstrip())
        return status, []

    try:
        payload = parse_gdbus_string(result.stdout)
        parsed = json.loads(payload)
    except (SyntaxError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"Failed to parse {method} response: {error}", file=sys.stderr)
        if result.stdout:
            print(result.stdout.rstrip(), file=sys.stderr)
        return 1, []

    if not isinstance(parsed, list):
        print(f"Expected {method} to return a JSON list", file=sys.stderr)
        return 1, []

    return 0, [entry for entry in parsed if isinstance(entry, dict)]


def parse_gdbus_string(output: str) -> str:
    parsed = ast.literal_eval(output.strip())
    if not isinstance(parsed, tuple) or not parsed or not isinstance(parsed[0], str):
        raise ValueError("expected a one-string DBus tuple")

    return parsed[0]


def format_windows(windows: list[dict[str, object]]) -> str:
    if not windows:
        return "windows: none"

    lines = ["windows:"]
    for window in windows:
        bird = clean_field(window.get("bird"), "?")
        color = clean_field(window.get("color"), "?")
        title = clean_field(window.get("title"), "untitled")
        wm_class = clean_field(window.get("wm_class"), "")
        focused = " focused" if window.get("focused") else ""
        source = clean_field(window.get("handle_source"), "")
        source_text = f" {source}" if source else ""
        suffix = f" [{wm_class}]" if wm_class else ""
        lines.append(f"- {bird}/{color}{focused}{source_text}: {title}{suffix}")

    return "\n".join(lines)


def format_apps(apps: list[dict[str, object]], limit: int = 30) -> str:
    if not apps:
        return "apps: none"

    names = [
        clean_field(app.get("name"), clean_field(app.get("id"), "unnamed"))
        for app in apps
    ]
    shown = names[:limit]
    lines = [f"apps: {len(apps)} available"]
    lines.extend(f"- {name}" for name in shown)
    if len(apps) > limit:
        lines.append(f"... {len(apps) - limit} more")

    return "\n".join(lines)


def clean_field(value: object, fallback: str) -> str:
    if value is None:
        return fallback

    text = str(value).strip()
    return text if text else fallback


def trace_recognition(transcript: str, parsed: ParsedCommand) -> None:
    print(f"heard: {quote_text(transcript)}")
    print(f"parsed: {describe_parsed_command(parsed)}")


def describe_parsed_command(parsed: ParsedCommand) -> str:
    if parsed.is_window_command:
        return f"window action={parsed.action} target={parsed.handle}"

    if parsed.is_app_command:
        return f"app action=open name={quote_text(parsed.app or '')}"

    if parsed.is_inventory_command:
        return f"inventory action={parsed.action}"

    if parsed.is_handle_command:
        return f"handles action={parsed.action}"

    if parsed.intent == "dictate":
        target = parsed.handle if parsed.handle is not None else "focused"
        return f"dictate target={target} text={quote_text(parsed.text or '')}"

    if parsed.text:
        return f"{parsed.intent} text={quote_text(parsed.text)}"

    return parsed.intent


def run_shell_action(label: str, method: str, *args: str, trace: bool = True) -> int:
    status = call_shell(method, *args)
    if trace:
        result = "ok" if status == 0 else "failed"
        print(f"action: {label} -> {result}")

    return status


def quote_text(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def call_shell(method: str, *args: str) -> int:
    result = run_gdbus(method, *args)

    stdout = result.stdout.strip()
    if result.stdout and not is_dbus_boolean(stdout):
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    return shell_status(result)


def call_shell_quiet(method: str, *args: str) -> int:
    return shell_status(run_gdbus(method, *args))


def call_shell_boolean(method: str, *args: str) -> tuple[int, bool]:
    result = run_gdbus(method, *args)
    status = result.returncode
    if status != 0:
        return status, False

    try:
        return 0, parse_gdbus_boolean(result.stdout)
    except (SyntaxError, ValueError, TypeError) as error:
        print(f"Failed to parse {method} response: {error}", file=sys.stderr)
        return 1, False


def parse_gdbus_boolean(output: str) -> bool:
    stripped = output.strip()
    if stripped.startswith("(true"):
        return True
    if stripped.startswith("(false"):
        return False

    raise ValueError("expected a one-boolean DBus tuple")


def run_gdbus(method: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        "gdbus",
        "call",
        "--session",
        "--dest",
        BUS_NAME,
        "--object-path",
        OBJECT_PATH,
        "--method",
        f"{INTERFACE}.{method}",
        *args,
    ]

    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def shell_status(result: subprocess.CompletedProcess[str]) -> int:
    stdout = result.stdout.strip()
    if result.returncode == 0 and stdout.startswith("(false"):
        return 1

    return result.returncode


def is_dbus_boolean(output: str) -> bool:
    return output.startswith("(true") or output.startswith("(false")


if __name__ == "__main__":
    raise SystemExit(main())
