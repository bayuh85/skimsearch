"""Minimal MCP stdio server.

Cursor and Claude Code can launch `skimsearch serve <project>`. The process
speaks newline-delimited JSON-RPC and also accepts Content-Length framing.
Stdout is reserved for protocol messages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

from skimsearch import __version__
from skimsearch.engine import index_project, search_project, status_project
from skimsearch.errors import SkimError
from skimsearch.log import emit
from skimsearch.render import render_search, render_search_json, render_status
from skimsearch.store import has_index, resolve_index_root

_PROTOCOL = "2024-11-05"


class McpServer:
    def __init__(self, root: Path):
        self.root = root
        self.framing = "ndjson"

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        has_id = "id" in message
        msg_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if not isinstance(method, str) or not method:
            if has_id:
                return _error(msg_id, -32600, "invalid request")
            return None
        if method.startswith("notifications/") or method == "initialized":
            return None
        if method == "initialize":
            version = params.get("protocolVersion")
            if not isinstance(version, str) or not version:
                version = _PROTOCOL
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "skimsearch", "version": __version__},
                    "instructions": (
                        "Search this project with skim_search. Results are short ranked "
                        "snippets inside a token budget. Prefer this over pasting grep output."
                    ),
                },
            }
        if not has_id:
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tools()}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
        if method == "tools/call":
            return self._call(msg_id, params)
        return _error(msg_id, -32601, f"method not found: {method}")

    def _call(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            if name == "skim_search":
                text = self._search(arguments)
            elif name == "skim_status":
                text = render_status(status_project(self.root))
            else:
                return _tool_result(msg_id, f"unknown tool: {name}", is_error=True)
        except SkimError as exc:
            return _tool_result(msg_id, str(exc), is_error=True)
        return _tool_result(msg_id, text, is_error=False)

    def _search(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not isinstance(query, str) or not query.strip():
            raise SkimError("query is required")
        mode = arguments.get("mode", "lexical")
        if mode not in {"lexical", "semantic", "ask"}:
            raise SkimError("mode must be lexical, semantic, or ask")
        budget = arguments.get("budget")
        limit = arguments.get("limit", 8)
        fmt = arguments.get("format", "text")
        if budget is not None:
            budget = int(budget)
        limit = int(limit)
        result = search_project(
            self.root,
            query,
            mode=str(mode),
            budget=budget,
            limit=limit,
        )
        if fmt == "json":
            return render_search_json(result)
        return render_search(result)


def serve(root: Path) -> int:
    root = root.resolve()
    found = resolve_index_root(root)
    if has_index(found):
        root = found
    else:
        emit(f"no index found, indexing {root}")
        index_project(root)
    emit(f"serving {root}")
    server = McpServer(root)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        message, framing = read_message(stdin)
        if message is None:
            break
        if framing:
            server.framing = framing
        if "method" not in message and "error" in message:
            write_message(stdout, message, server.framing)
            continue
        reply = server.handle(message)
        if reply is not None:
            write_message(stdout, reply, server.framing)
    return 0


def read_message(stream: BinaryIO) -> tuple[dict | None, str | None]:
    line = stream.readline()
    if not line:
        return None, None
    if line.lower().startswith(b"content-length:"):
        return _read_lsp(stream, line)
    if not line.strip():
        return read_message(stream)
    return _decode_json(line.decode("utf-8"), "ndjson")


def write_message(stream: BinaryIO, message: dict, framing: str) -> None:
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if framing == "lsp":
        stream.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
        stream.write(raw)
    else:
        stream.write(raw + b"\n")
    stream.flush()


def _read_lsp(stream: BinaryIO, first: bytes) -> tuple[dict | None, str | None]:
    try:
        length = int(first.split(b":", 1)[1].strip())
    except ValueError:
        return _parse_error("bad Content-Length"), "lsp"
    while True:
        header = stream.readline()
        if header in (b"\r\n", b"\n", b""):
            break
    body = stream.read(length)
    if len(body) < length:
        return None, None
    return _decode_json(body.decode("utf-8"), "lsp")


def _decode_json(text: str, framing: str) -> tuple[dict, str]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _parse_error("parse error"), framing
    if not isinstance(parsed, dict):
        return _error(None, -32600, "invalid request"), framing
    return parsed, framing


def _parse_error(message: str) -> dict:
    return _error(None, -32700, message)


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_result(msg_id: Any, text: str, *, is_error: bool) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    }


def _tools() -> list[dict]:
    return [
        {
            "name": "skim_search",
            "description": (
                "Search the local skimsearch index and return a short deck of ranked "
                "snippets. lexical is offline. semantic and ask call the configured "
                "OpenAI-compatible API (AtmoRouter by default) and stay inside a token budget."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Symbol, phrase, or question."},
                    "mode": {
                        "type": "string",
                        "enum": ["lexical", "semantic", "ask"],
                        "description": "lexical, semantic, or ask. Default lexical.",
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Maximum tokens in the returned deck.",
                    },
                    "limit": {"type": "integer", "description": "Maximum cards. Default 8."},
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "text is the compact deck. json is the same cards.",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "skim_status",
            "description": "Show index stats and which LLM provider is configured.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
