#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys

from grimoire.commands import ParsedCommand, parse_transcript


BUS_NAME = "org.grimoire.Shell"
OBJECT_PATH = "/org/grimoire/Shell"
INTERFACE = "org.grimoire.Shell"


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
    args = parser.parse_args(argv)

    if args.list_windows:
        return call_shell("ListWindows")

    if not args.command:
        parser.error("--command is required unless --list-windows is used")

    parsed = parse_transcript(args.command)
    if args.dry_run:
        print(parsed)
        return 0

    return dispatch(parsed)


def dispatch(parsed: ParsedCommand) -> int:
    if parsed.is_window_command:
        assert parsed.handle is not None
        assert parsed.action is not None
        return call_shell("RunWindowCommand", parsed.handle, parsed.action)

    if parsed.intent == "dictate":
        print("Dictation parsing works, but input execution is not implemented yet.", file=sys.stderr)
        return 2

    print(f"Unsupported command: {parsed}", file=sys.stderr)
    return 2


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

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
