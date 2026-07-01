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

APP_ACTIONS = ("open", "launch", "start")

INVENTORY_WINDOW_WORDS = {"window", "windows", "handle", "handles"}
INVENTORY_APP_WORDS = {"app", "apps", "application", "applications", "program", "programs"}
INVENTORY_ACTION_WORDS = {"list", "show", "what", "which"}

ACTION_ALIASES = {
    "maximized": "maximize",
    "unmaximized": "unmaximize",
    "unmaximize": "unmaximize",
    "restore": "unmaximize",
    "restored": "unmaximize",
    "minimized": "minimize",
    "unminimized": "unminimize",
    "fullscreened": "fullscreen",
    "unfullscreened": "unfullscreen",
}

DESTRUCTIVE_ACTIONS = {"close"}

_STOPWORDS = {"the", "a", "an", "window", "pane", "one"}
_DICTATION_PREFIXES = ("type", "dictate", "write")

_DICTATION_SYMBOLS = {
    "minus": "-",
    "dash": "-",
    "hyphen": "-",
    "slash": "/",
    "forward slash": "/",
    "backslash": "\\",
    "back slash": "\\",
    "dot": ".",
    "period": ".",
    "point": ".",
    "underscore": "_",
    "under score": "_",
    "equals": "=",
    "equal": "=",
    "colon": ":",
    "semicolon": ";",
    "comma": ",",
    "pipe": "|",
    "bar": "|",
    "ampersand": "&",
    "quote": '"',
    "quotes": '"',
    "double quote": '"',
    "single quote": "'",
    "apostrophe": "'",
    "open parenthesis": "(",
    "close parenthesis": ")",
    "left parenthesis": "(",
    "right parenthesis": ")",
    "enter": "\n",
    "return": "\n",
    "new line": "\n",
    "newline": "\n",
}

_ATTACH_TO_NEXT = {"-", "/", "\\", "_", ".", "=", "|", "&", "(", '"', "'"}
_ATTACH_TO_PREVIOUS = {"/", "\\", "_", ".", ",", ":", ";", ")", '"', "'"}
_OPEN_QUOTES = {'"', "'"}
_PATH_COMMANDS = {
    "cd",
    "cat",
    "code",
    "cp",
    "ls",
    "mkdir",
    "mv",
    "nano",
    "open",
    "rm",
    "touch",
    "vim",
}


@dataclass(frozen=True)
class ParsedCommand:
    intent: str
    action: str | None = None
    handle: str | None = None
    app: str | None = None
    text: str | None = None

    @property
    def is_window_command(self) -> bool:
        return self.intent == "window"

    @property
    def is_app_command(self) -> bool:
        return self.intent == "app"

    @property
    def is_inventory_command(self) -> bool:
        return self.intent == "inventory"

    @property
    def color(self) -> str | None:
        return self.handle


@dataclass(frozen=True)
class DictationInput:
    text: str
    enter_presses: int = 0


def parse_transcript(transcript: str) -> ParsedCommand:
    raw = transcript.strip()
    if not raw:
        return ParsedCommand(intent="empty")

    parsed = _parse_inventory(raw)
    if parsed is not None:
        return parsed

    dictation = _parse_dictation(raw)
    if dictation is not None:
        handle, text = _parse_dictation_target(dictation)
        return ParsedCommand(intent="dictate", handle=handle, text=text)

    tokens = _tokens(raw)
    if len(tokens) < 2:
        return ParsedCommand(intent="unknown", text=raw)

    parsed = _parse_action_handle(tokens)
    if parsed is not None:
        return parsed

    parsed = _parse_app_action(tokens)
    if parsed is not None:
        return parsed

    return ParsedCommand(intent="unknown", text=raw)


def is_supported_command(parsed: ParsedCommand) -> bool:
    return (
        parsed.is_window_command or
        parsed.is_app_command or
        parsed.is_inventory_command or
        parsed.intent == "dictate"
    )


def requires_confirmation(parsed: ParsedCommand) -> bool:
    return parsed.is_window_command and parsed.action in DESTRUCTIVE_ACTIONS


def normalize_dictation_text(text: str) -> str:
    pieces = _dictation_pieces(text)
    output = ""
    attach_next = False
    open_quotes: set[str] = set()

    for piece in pieces:
        if piece == "\n":
            if not output.endswith("\n"):
                output = output.rstrip()
            output += "\n"
            attach_next = False
            continue

        if piece in _OPEN_QUOTES:
            if piece in open_quotes:
                output = output.rstrip()
                output += piece
                open_quotes.remove(piece)
                attach_next = False
            else:
                if output and not output[-1].isspace():
                    output += " "
                output += piece
                open_quotes.add(piece)
                attach_next = True
            continue

        if _needs_space(output, piece, attach_next):
            output += " "

        output += piece
        attach_next = piece in _ATTACH_TO_NEXT

    return output.rstrip(" ")


def normalize_dictation_input(text: str) -> DictationInput:
    normalized = normalize_dictation_text(text)
    enter_presses = 0

    while normalized.endswith("\n"):
        enter_presses += 1
        normalized = normalized[:-1].rstrip(" ")

    return DictationInput(text=normalized, enter_presses=enter_presses)


def _parse_dictation(raw: str) -> str | None:
    lowered = raw.lower().strip()
    for prefix in _DICTATION_PREFIXES:
        marker = f"{prefix} "
        if lowered.startswith(marker):
            return raw[len(marker):].strip()

    return None


def _parse_dictation_target(text: str) -> tuple[str | None, str]:
    match = re.match(
        r"^\s*(?:(?:to|into|in)\s+)?(?:(?:the|a|an)\s+)?"
        r"(?P<handle>[a-z0-9]+)(?:\s+(?:window|pane))?\s+(?P<text>.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, text

    handle = match.group("handle").lower()
    if handle not in HANDLE_NAMES:
        return None, text

    return handle, match.group("text").strip()


def _parse_inventory(raw: str) -> ParsedCommand | None:
    tokens = _raw_tokens(raw)
    token_set = set(tokens)

    if not token_set.intersection(INVENTORY_ACTION_WORDS):
        return None

    if token_set.intersection(INVENTORY_WINDOW_WORDS):
        return ParsedCommand(intent="inventory", action="windows")

    if token_set.intersection(INVENTORY_APP_WORDS):
        return ParsedCommand(intent="inventory", action="apps")

    return None


def _parse_action_handle(tokens: list[str]) -> ParsedCommand | None:
    tokens = _normalize_action_tokens(tokens)
    first, second = tokens[0], tokens[1]

    if first in WINDOW_ACTIONS and second in HANDLE_NAMES:
        return ParsedCommand(intent="window", action=first, handle=second)

    if first in HANDLE_NAMES and second in WINDOW_ACTIONS:
        return ParsedCommand(intent="window", action=second, handle=first)

    return None


def _parse_app_action(tokens: list[str]) -> ParsedCommand | None:
    if len(tokens) < 2:
        return None

    action = ACTION_ALIASES.get(tokens[0], tokens[0])
    if action not in APP_ACTIONS:
        return None

    return ParsedCommand(intent="app", action="open", app=" ".join(tokens[1:]))


def _normalize_action_tokens(tokens: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token == "un" and index + 1 < len(tokens):
            combined = f"un{tokens[index + 1]}"
            normalized.append(ACTION_ALIASES.get(combined, combined))
            index += 2
            continue

        normalized.append(ACTION_ALIASES.get(token, token))
        index += 1

    return normalized


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in _raw_tokens(text)
        if token and token not in _STOPWORDS
    ]


def _raw_tokens(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return normalized.split()


def _dictation_pieces(text: str) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    index = 0

    while index < len(words):
        two_word = " ".join(words[index:index + 2]).lower()
        if two_word in _DICTATION_SYMBOLS:
            pieces.append(_DICTATION_SYMBOLS[two_word])
            index += 2
            continue

        word = words[index]
        pieces.append(_DICTATION_SYMBOLS.get(word.lower(), word))
        index += 1

    return pieces


def _needs_space(output: str, piece: str, attach_next: bool) -> bool:
    if not output or output[-1].isspace() or attach_next:
        return False

    if piece in {"/", "\\"} and output.split()[-1].lower() in _PATH_COMMANDS:
        return True

    if piece in _ATTACH_TO_PREVIOUS:
        return False

    return True
