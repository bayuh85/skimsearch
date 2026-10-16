"""Fold source files into skim cards.

A card is one symbol, heading, or window: small enough to quote, large enough
to answer a question. Line numbers stay attached so an agent can open the file
instead of pasting the whole tree into the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 80
WINDOW_LINES = 70
WINDOW_OVERLAP = 12

_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".rb": "ruby",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".adoc": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "text",
    ".cfg": "text",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "vue",
    ".svelte": "svelte",
    ".csv": "text",
}

_SPECIAL_NAMES: dict[str, str] = {
    "makefile": "make",
    "dockerfile": "docker",
    "jenkinsfile": "text",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "procfile": "text",
    "license": "text",
    "licence": "text",
    "copying": "text",
    "readme": "text",
}

_BRACE_LANGS = {
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "php",
    "swift",
    "scala",
    "vue",
    "svelte",
}

_SLASH_COMMENT = {
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "php",
    "swift",
    "scala",
    "vue",
    "svelte",
}

_PY_DECL = re.compile(r"^([ \t]*)(async[ \t]+def|def|class)[ \t]+(\w+)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_JS_FUNC = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(\w+)",
    re.IGNORECASE,
)
_JS_CLASS = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)",
)
_JS_CONST = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?"
    r"(?:function\b|\([^;]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
)
_GO_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)")
_GO_TYPE = re.compile(r"^type\s+(\w+)\b")
_RS_ITEM = re.compile(
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:fn|struct|enum|trait|type|mod)\s+(\w+)"
)
_RS_IMPL = re.compile(r"^impl\b\s*(.+)$")
_TYPE_DECL = re.compile(
    r"^(?:(?:public|private|protected|internal|static|final|abstract|sealed|data|open|partial)\s+)*"
    r"(?:class|interface|enum|struct|object|trait)\s+(\w+)"
)
_FUN_DECL = re.compile(
    r"^(?:(?:public|private|protected|internal|static|final|abstract|async|open|override)\s+)*"
    r"(?:fun|func)\s+(\w+)\s*\("
)
_JAVA_METHOD = re.compile(
    r"^(?:(?:public|private|protected|static|final|synchronized|native|abstract)\s+)+"
    r"(?:[\w.<>,\[\]?]+\s+)+(\w+)\s*\("
)
_PHP_FUNC = re.compile(
    r"^(?:(?:public|private|protected|static|final)\s+)*function\s+(\w+)",
    re.IGNORECASE,
)
_PHP_CLASS = re.compile(
    r"^(?:(?:final|abstract)\s+)?class\s+(\w+)",
    re.IGNORECASE,
)
_SWIFT = re.compile(
    r"^(?:(?:public|private|internal|open|fileprivate|static|final)\s+)*"
    r"(?:func|class|struct|enum|protocol)\s+(\w+)"
)
_SQL = re.compile(
    r"^create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?"
    r"(?:table|view|function|index)\s+(?:if\s+not\s+exists\s+)?([^\s(]+)",
    re.IGNORECASE,
)
_SHELL_FUNC = re.compile(r"^(?:function\s+)?([A-Za-z_][\w]*)\s*\(\s*\)\s*\{?\s*$")


@dataclass(frozen=True)
class FoldedCard:
    language: str
    kind: str
    symbol: str
    start_line: int
    end_line: int
    part: int
    text: str


def language_of(rel_path: str) -> str | None:
    path = Path(rel_path)
    name = path.name.lower()
    if name in _SPECIAL_NAMES:
        return _SPECIAL_NAMES[name]
    return _EXT_LANG.get(path.suffix.lower())


def fold_source(rel_path: str, text: str) -> list[FoldedCard]:
    language = language_of(rel_path)
    if language is None:
        return []
    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        return []
    if language == "python":
        spans = _fold_python(lines)
    elif language == "markdown":
        spans = _fold_markdown(lines)
    elif language in _BRACE_LANGS:
        spans = _fold_brace(lines, language)
    elif language == "sql":
        spans = _fold_anchored(lines, [(_SQL, "sql")])
    elif language == "shell":
        spans = _fold_anchored(lines, [(_SHELL_FUNC, "function")])
    else:
        spans = []
    if not spans:
        stem = path_symbol(rel_path)
        spans = [(0, len(lines) - 1, "file", stem)]
    cards: list[FoldedCard] = []
    for start, end, kind, symbol in spans:
        cards.extend(_split_span(lines, start, end, kind, symbol, language))
    return [card for card in cards if card.text.strip()]


def path_symbol(rel_path: str) -> str:
    stem = Path(rel_path).stem
    return stem or Path(rel_path).name or rel_path


def _fold_python(lines: list[str]) -> list[tuple[int, int, str, str]]:
    decls: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = _PY_DECL.match(line)
        if not match:
            continue
        indent = _indent_width(match.group(1))
        keyword = match.group(2)
        name = match.group(3)
        is_class = keyword == "class"
        if indent == 0 or (indent == 4 and not is_class):
            kind = "class" if is_class else "function"
            decls.append((index, indent, kind, name))
    if not decls:
        return []

    starts: list[int] = []
    for line_no, indent, _kind, _name in decls:
        start = line_no
        cursor = line_no - 1
        while cursor >= 0:
            raw = lines[cursor]
            if raw.strip() == "":
                prev = lines[cursor - 1] if cursor > 0 else ""
                prev_lead = prev[: len(prev) - len(prev.lstrip(" \t"))]
                if prev.lstrip().startswith("@") and _indent_width(prev_lead) == indent:
                    start = cursor
                    cursor -= 1
                    continue
                break
            lead = raw[: len(raw) - len(raw.lstrip(" \t"))]
            if _indent_width(lead) != indent:
                break
            stripped = raw.strip()
            if stripped.startswith("@") or stripped.startswith("#"):
                start = cursor
                cursor -= 1
                continue
            break
        starts.append(start)

    for index in range(1, len(starts)):
        if starts[index] <= starts[index - 1]:
            starts[index] = decls[index][0]

    spans: list[tuple[int, int, str, str]] = []
    if starts[0] > 0 and any(lines[i].strip() for i in range(starts[0])):
        intro_end = starts[0] - 1
        while intro_end > 0 and not lines[intro_end].strip():
            intro_end -= 1
        spans.append((0, intro_end, "module", "(intro)"))

    current_class: str | None = None
    for index, (line_no, indent, kind, name) in enumerate(decls):
        if indent == 0 and kind == "class":
            current_class = name
        elif indent == 0:
            current_class = None
        symbol = name
        if indent == 4 and kind == "function" and current_class:
            symbol = f"{current_class}.{name}"
        limit = starts[index + 1] if index + 1 < len(starts) else len(lines)
        end = _end_of_indent_block(lines, line_no, indent, limit)
        start = starts[index]
        if end < start:
            end = line_no
        spans.append((start, end, kind, symbol))
        gap_start = end + 1
        gap_end = limit - 1
        if gap_start <= gap_end and any(lines[i].strip() for i in range(gap_start, gap_end + 1)):
            while gap_end > gap_start and not lines[gap_end].strip():
                gap_end -= 1
            spans.append((gap_start, gap_end, "module", "(module)"))
    return _merge_touching(spans)


def _end_of_indent_block(lines: list[str], decl: int, indent: int, limit: int) -> int:
    """Last line that still belongs to a decl.

    Top-level units stop at the next column-0 statement. Indented units stop
    when a later line returns to that indent or less. The ``limit`` line is
    the next card, so this never consumes it.
    """
    end = decl
    cursor = decl + 1
    while cursor < limit:
        line = lines[cursor]
        if line.strip():
            lead = line[: len(line) - len(line.lstrip(" \t"))]
            if _indent_width(lead) <= indent:
                break
            end = cursor
        cursor += 1
    return end


def _merge_touching(
    spans: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    """Drop inverted spans produced when a gap sits inside the next start."""
    cleaned: list[tuple[int, int, str, str]] = []
    for start, end, kind, symbol in spans:
        if end < start:
            continue
        cleaned.append((start, end, kind, symbol))
    return cleaned


def _fold_markdown(lines: list[str]) -> list[tuple[int, int, str, str]]:
    markers: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            title = match.group(2).strip()[:120] or "(heading)"
            markers.append((index, "heading", title))
    return _spans_from_markers(lines, markers)


def _fold_anchored(
    lines: list[str],
    patterns: list[tuple[re.Pattern[str], str]],
) -> list[tuple[int, int, str, str]]:
    markers: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t")):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, kind in patterns:
            match = pattern.match(stripped)
            if match:
                symbol = next((group for group in match.groups() if group), kind)
                markers.append((index, kind, symbol.strip()[:120]))
                break
    return _spans_from_markers(lines, markers)


def _fold_brace(lines: list[str], language: str) -> list[tuple[int, int, str, str]]:
    patterns = _brace_patterns(language)
    markers: list[tuple[int, str, str]] = []
    depth = 0
    state = _ScanState()
    for index, line in enumerate(lines):
        if depth <= 0:
            stripped = _code_prefix(line, language).strip()
            for pattern, kind in patterns:
                match = pattern.match(stripped)
                if match:
                    groups = [group for group in match.groups() if group]
                    symbol = groups[-1].strip() if groups else kind
                    symbol = re.sub(r"\s+", " ", symbol)[:120]
                    markers.append((index, kind, symbol))
                    break
        depth = _update_depth(line, depth, state, language)
        if depth < 0:
            depth = 0
    return _spans_from_markers(lines, markers)


def _brace_patterns(language: str) -> list[tuple[re.Pattern[str], str]]:
    if language in {"javascript", "typescript", "vue", "svelte"}:
        return [(_JS_FUNC, "function"), (_JS_CLASS, "class"), (_JS_CONST, "function")]
    if language == "go":
        return [(_GO_FUNC, "function"), (_GO_TYPE, "type")]
    if language == "rust":
        return [(_RS_ITEM, "item"), (_RS_IMPL, "impl")]
    if language == "php":
        return [(_PHP_CLASS, "class"), (_PHP_FUNC, "function")]
    if language == "swift":
        return [(_SWIFT, "decl")]
    if language in {"java", "kotlin", "csharp", "scala", "c", "cpp"}:
        return [
            (_TYPE_DECL, "class"),
            (_FUN_DECL, "function"),
            (_JAVA_METHOD, "function"),
        ]
    return [(_TYPE_DECL, "class"), (_FUN_DECL, "function")]


def _spans_from_markers(
    lines: list[str],
    markers: list[tuple[int, str, str]],
) -> list[tuple[int, int, str, str]]:
    if not markers:
        return []
    spans: list[tuple[int, int, str, str]] = []
    first = markers[0][0]
    if first > 0 and any(line.strip() for line in lines[:first]):
        intro_end = first - 1
        while intro_end > 0 and not lines[intro_end].strip():
            intro_end -= 1
        spans.append((0, intro_end, "module", "(intro)"))
    for index, (start, kind, symbol) in enumerate(markers):
        end = markers[index + 1][0] - 1 if index + 1 < len(markers) else len(lines) - 1
        while end > start and not lines[end].strip():
            end -= 1
        if end < start:
            end = start
        spans.append((start, end, kind, symbol))
    return spans


def _split_span(
    lines: list[str],
    start: int,
    end: int,
    kind: str,
    symbol: str,
    language: str,
) -> list[FoldedCard]:
    if end < start:
        return []
    length = end - start + 1
    if length <= MAX_LINES:
        return [_make_card(lines, start, end, kind, symbol, language, 1)]
    cards: list[FoldedCard] = []
    part = 1
    pos = start
    window_kind = "window" if kind == "file" else kind
    while pos <= end:
        chunk_end = min(end, pos + WINDOW_LINES - 1)
        cards.append(_make_card(lines, pos, chunk_end, window_kind, symbol, language, part))
        if chunk_end >= end:
            break
        nxt = chunk_end + 1 - WINDOW_OVERLAP
        if nxt <= pos:
            nxt = pos + 1
        pos = nxt
        part += 1
    return cards


def _make_card(
    lines: list[str],
    start: int,
    end: int,
    kind: str,
    symbol: str,
    language: str,
    part: int,
) -> FoldedCard:
    text = "\n".join(lines[start : end + 1]).strip("\n")
    return FoldedCard(
        language=language,
        kind=kind,
        symbol=symbol[:120],
        start_line=start + 1,
        end_line=end + 1,
        part=part,
        text=text,
    )


def _indent_width(whitespace: str) -> int:
    return len(whitespace.expandtabs(4))


class _ScanState:
    def __init__(self) -> None:
        self.in_block = False
        self.in_string: str | None = None


def _code_prefix(line: str, language: str) -> str:
    """Drop a trailing line comment so declaration regexes see the code."""
    state = _ScanState()
    out: list[str] = []
    i = 0
    while i < len(line):
        if state.in_string:
            out.append(line[i])
            if line[i] == "\\" and state.in_string != "`":
                if i + 1 < len(line):
                    out.append(line[i + 1])
                i += 2
                continue
            if line[i] == state.in_string:
                state.in_string = None
            i += 1
            continue
        if language in _SLASH_COMMENT and line.startswith("//", i):
            break
        if language not in _SLASH_COMMENT and line[i] == "#":
            break
        if line.startswith("/*", i):
            break
        char = line[i]
        if char in {'"', "'", "`"}:
            state.in_string = char
        out.append(char)
        i += 1
    return "".join(out)


def _update_depth(line: str, depth: int, state: _ScanState, language: str) -> int:
    i = 0
    while i < len(line):
        if state.in_block:
            end = line.find("*/", i)
            if end == -1:
                return depth
            state.in_block = False
            i = end + 2
            continue
        if state.in_string:
            if line[i] == "\\" and state.in_string != "`":
                i += 2
                continue
            if line[i] == state.in_string:
                state.in_string = None
            i += 1
            continue
        if language in _SLASH_COMMENT and line.startswith("//", i):
            break
        if line.startswith("/*", i):
            state.in_block = True
            i += 2
            continue
        char = line[i]
        if char in {'"', "'", "`"}:
            state.in_string = char
            i += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        i += 1
    if state.in_string in {'"', "'"}:
        state.in_string = None
    return depth
