from __future__ import annotations

from dataclasses import dataclass
import re


COLOR_NAMES = (
    "yellow",
    "blue",
    "green",
    "red",
    "purple",
    "orange",
    "cyan",
    "pink",
    "white",
    "black",
)

BIRD_NAMES = (
    "sparrow",
    "crow",
    "dove",
    "owl",
    "robin",
    "raven",
    "finch",
    "hawk",
    "wren",
    "swan",
)

HANDLE_NAMES = COLOR_NAMES + BIRD_NAMES

WINDOW_ACTIONS = (
    "focus",
    "close",
    "minimize",
    "unminimize",
    "maximize",
    "unmaximize",
    "fullscreen",
    "unfullscreen",
)

DESTRUCTIVE_ACTIONS = {"close"}

_STOPWORDS = {"the", "a", "an", "window", "pane", "one"}
_DICTATION_PREFIXES = ("type", "dictate", "write")


@dataclass(frozen=True)
class ParsedCommand:
    intent: str
    action: str | None = None
    handle: str | None = None
    text: str | None = None

    @property
    def is_window_command(self) -> bool:
        return self.intent == "window"

    @property
    def color(self) -> str | None:
        return self.handle


def parse_transcript(transcript: str) -> ParsedCommand:
    raw = transcript.strip()
    if not raw:
        return ParsedCommand(intent="empty")

    dictation = _parse_dictation(raw)
    if dictation is not None:
        return ParsedCommand(intent="dictate", text=dictation)

    tokens = _tokens(raw)
    if len(tokens) < 2:
        return ParsedCommand(intent="unknown", text=raw)

    parsed = _parse_action_handle(tokens)
    if parsed is not None:
        return parsed

    return ParsedCommand(intent="unknown", text=raw)


def is_supported_command(parsed: ParsedCommand) -> bool:
    return parsed.is_window_command or parsed.intent == "dictate"


def requires_confirmation(parsed: ParsedCommand) -> bool:
    return parsed.is_window_command and parsed.action in DESTRUCTIVE_ACTIONS


def _parse_dictation(raw: str) -> str | None:
    lowered = raw.lower().strip()
    for prefix in _DICTATION_PREFIXES:
        marker = f"{prefix} "
        if lowered.startswith(marker):
            return raw[len(marker):].strip()

    return None


def _parse_action_handle(tokens: list[str]) -> ParsedCommand | None:
    first, second = tokens[0], tokens[1]

    if first in WINDOW_ACTIONS and second in HANDLE_NAMES:
        return ParsedCommand(intent="window", action=first, handle=second)

    if first in HANDLE_NAMES and second in WINDOW_ACTIONS:
        return ParsedCommand(intent="window", action=second, handle=first)

    return None


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return [
        token
        for token in normalized.split()
        if token and token not in _STOPWORDS
    ]
