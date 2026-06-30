#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    if args.dry_run:
        print(parsed)
        return 0

    return dispatch(parsed)


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

        dispatch(parsed)


def listen_once(args: argparse.Namespace) -> int:
    parsed = listen_and_parse(args)

    if args.execute_listen:
        if not is_supported_command(parsed):
            print("No supported command recognized.", file=sys.stderr)
            return 2
        return dispatch(parsed)

    return 0


def listen_and_parse(args: argparse.Namespace) -> ParsedCommand:
    with tempfile.TemporaryDirectory(prefix="grimoire-listen-") as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        audio_path = Path(args.audio_file) if args.audio_file else tmpdir / "utterance.wav"

        if not args.audio_file:
            record_audio(audio_path, args.record_seconds)

        transcript = transcribe_audio(audio_path, tmpdir, args.asr_command)
        parsed = parse_transcript(transcript)

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


def dispatch(parsed: ParsedCommand) -> int:
    if parsed.is_window_command:
        assert parsed.handle is not None
        assert parsed.action is not None
        return call_shell("RunWindowCommand", parsed.handle, parsed.action)

    if parsed.is_app_command:
        assert parsed.app is not None
        return call_shell("LaunchApp", parsed.app)

    if parsed.intent == "dictate":
        assert parsed.text is not None
        if parsed.handle is not None:
            focus_status = call_shell("RunWindowCommand", parsed.handle, "focus")
            if focus_status != 0:
                return focus_status
            time.sleep(0.15)

        dictation = normalize_dictation_input(parsed.text)
        if dictation.text:
            paste_status = call_shell("PasteText", dictation.text)
            if paste_status != 0:
                return paste_status
            if dictation.enter_presses:
                time.sleep(0.08)

        for _ in range(dictation.enter_presses):
            key_status = call_shell("PressKey", "enter")
            if key_status != 0:
                return key_status

        return 0

    print(f"Unsupported command: {parsed}", file=sys.stderr)
    return 2


def confirm_command(parsed: ParsedCommand) -> bool:
    answer = input(f"Confirm {parsed.action} {parsed.handle}? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def call_shell(method: str, *args: str) -> int:
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

    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode == 0 and result.stdout.strip().startswith("(false"):
        return 1

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
