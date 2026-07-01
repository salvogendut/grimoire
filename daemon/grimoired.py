#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
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
RECORD_RATE = 16000


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

    if args.listen_loop:
        return listen_loop(args)

    if args.listen or args.audio_file:
        return listen_once(args)

    if not args.command:
        parser.error(
            "--command is required unless --list-windows, --list-apps, --listen, "
            "--listen-loop, or --audio-file is used"
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

        parsed = listen_and_parse(args)
        if not is_supported_command(parsed):
            print("No supported command recognized.", file=sys.stderr)
            continue

        if args.dry_run:
            continue

        if requires_confirmation(parsed) and not confirm_command(parsed):
            print("skipped")
            continue

        dispatch(parsed, trace=not args.quiet)


def listen_once(args: argparse.Namespace) -> int:
    parsed = listen_and_parse(args)

    if args.execute_listen:
        if not is_supported_command(parsed):
            print("No supported command recognized.", file=sys.stderr)
            return 2
        return dispatch(parsed, trace=not args.quiet)

    return 0


def listen_and_parse(args: argparse.Namespace) -> ParsedCommand:
    with tempfile.TemporaryDirectory(prefix="grimoire-listen-") as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        audio_path = Path(args.audio_file) if args.audio_file else tmpdir / "utterance.wav"

        if not args.audio_file:
            record_audio(audio_path, args.record_seconds)

        transcript = transcribe_audio(audio_path, tmpdir, args.asr_command)
        parsed = parse_transcript(transcript)

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

    whisper_cli = Path(os.environ.get("GRIMOIRE_WHISPER_CLI", DEFAULT_WHISPER_CPP))
    whisper_model = Path(os.environ.get("GRIMOIRE_WHISPER_MODEL", DEFAULT_WHISPER_MODEL))

    if not whisper_cli.exists():
        raise SystemExit(
            f"whisper.cpp binary not found: {whisper_cli}\n"
            "Set GRIMOIRE_WHISPER_CLI or pass --asr-command."
        )
    if not whisper_model.exists():
        raise SystemExit(
            f"whisper.cpp model not found: {whisper_model}\n"
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
