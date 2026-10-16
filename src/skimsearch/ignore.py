"""Decide which paths belong in the index.

Built-in rules skip dependency folders, build output, and secret files.
A repo's `.gitignore` and `.skimignore` are applied after those rules, so a
negation such as `!dist/` in `.skimignore` can put a path back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Dependency trees, build output, secrets, and bulky binaries.
DEFAULT_IGNORE = """
.git/
.hg/
.svn/
.skimsearch/
.venv/
venv/
node_modules/
bower_components/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.tox/
.eggs/
*.egg-info/
.idea/
.vscode/
dist/
build/
target/
out/
htmlcov/
.next/
.nuxt/
coverage/
.DS_Store
*.pyc
*.pyo
*.so
*.dll
*.dylib
*.a
*.o
*.obj
*.exe
*.bin
*.png
*.jpg
*.jpeg
*.gif
*.webp
*.ico
*.pdf
*.zip
*.tar
*.gz
*.tgz
*.bz2
*.7z
*.woff
*.woff2
*.ttf
*.eot
*.mp4
*.mp3
*.wav
*.wasm
*.min.js
*.min.css
*.map
*.lock
*.sqlite
*.db
package-lock.json
yarn.lock
pnpm-lock.yaml
Cargo.lock
poetry.lock
Pipfile.lock
go.sum
.env
.env.*
*.pem
*.key
id_rsa
id_dsa
secrets.*
"""


@dataclass(frozen=True)
class Rule:
    negation: bool
    directory_only: bool
    regex: re.Pattern[str]
    base: str

    def matches(self, rel: str, is_dir: bool) -> bool:
        path = rel
        if self.base:
            prefix = self.base + "/"
            if not rel.startswith(prefix):
                return False
            path = rel[len(prefix) :]
        if self.regex.match(path):
            if self.directory_only and not is_dir:
                return False
            return True
        if not self.directory_only:
            return False
        parts = path.split("/")
        # A directory rule ignores everything under a matching directory.
        acc: list[str] = []
        limit = len(parts) - 1 if not is_dir else len(parts)
        for part in parts[:limit]:
            acc.append(part)
            if self.regex.match("/".join(acc)):
                return True
        return False


def parse_ignore_text(text: str, *, base: str = "") -> list[Rule]:
    rules: list[Rule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negation = False
        if line.startswith("!"):
            negation = True
            line = line[1:].strip()
        if not line or line.startswith("#"):
            continue
        directory_only = line.endswith("/")
        if directory_only:
            line = line[:-1]
        if not line:
            continue
        anchored = line.startswith("/") or "/" in line
        if line.startswith("/"):
            line = line[1:]
        body = _translate(line)
        # Unanchored rules match in any directory. re.match is start-anchored,
        # so the prefix has to be part of the pattern.
        expr = f"^{body}$" if anchored else f"(?:^|.*/){body}$"
        rules.append(
            Rule(
                negation=negation,
                directory_only=directory_only,
                regex=re.compile(expr),
                base=base.strip("/"),
            )
        )
    return rules


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    size = len(pattern)
    while i < size:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        char = pattern[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(pattern[i : end + 1])
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


class IgnoreSet:
    """Stack of ignore layers. Push a directory when the walk enters it."""

    def __init__(self, root: Path):
        self.root = root
        self._layers: list[list[Rule]] = [parse_ignore_text(DEFAULT_IGNORE, base="")]
        self._load(root, "")

    def _load(self, directory: Path, rel: str) -> None:
        found: list[Rule] = []
        for name in (".gitignore", ".skimignore"):
            path = directory / name
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                found.extend(parse_ignore_text(text, base=rel))
        self._layers.append(found)

    def push(self, directory: Path, rel: str) -> None:
        self._load(directory, rel)

    def pop(self) -> None:
        if len(self._layers) > 1:
            self._layers.pop()

    def ignored(self, rel: str, is_dir: bool) -> bool:
        rel = rel.strip("/")
        state = False
        for layer in self._layers:
            for rule in layer:
                if rule.matches(rel, is_dir):
                    state = not rule.negation
        return state
