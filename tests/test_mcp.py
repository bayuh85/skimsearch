"""MCP stdio server, in-process and as a subprocess."""

import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from skimsearch.engine import index_project
from skimsearch.mcp_server import McpServer, read_message, write_message

ROOT = Path(__file__).resolve().parents[1]


def test_initialize_and_search_round_trip(tmp_path: Path):
    (tmp_path / "fee.py").write_text(
        "def apply_late_fee():\n    return 1\n",
        encoding="utf-8",
    )
    index_project(tmp_path)
    server = McpServer(tmp_path)
    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "skimsearch"
    assert init["result"]["protocolVersion"] == "2024-11-05"
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == ["skim_search", "skim_status"]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    called = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "skim_search", "arguments": {"query": "apply_late_fee"}},
        }
    )
    text = called["result"]["content"][0]["text"]
    assert called["result"]["isError"] is False
    assert "apply_late_fee" in text
    missing = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "skim_search", "arguments": {}},
        }
    )
    assert missing["result"]["isError"] is True


def test_content_length_framing_round_trip():
    payload = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
    buffer = BytesIO()
    write_message(buffer, payload, "lsp")
    buffer.seek(0)
    message, framing = read_message(buffer)
    assert framing == "lsp"
    assert message == payload
    bare = BytesIO(json.dumps(payload).encode("utf-8") + b"\n")
    message, framing = read_message(bare)
    assert framing == "ndjson"
    assert message == payload


def test_serve_subprocess_lists_tools(tmp_path: Path):
    (tmp_path / "fee.py").write_text("def apply_late_fee():\n    return 1\n", encoding="utf-8")
    index_project(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("ATMOROUTER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("SKIMSEARCH_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "skimsearch", "serve", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    payload = json.dumps(request).encode("utf-8") + b"\n"
    stdout, stderr = proc.communicate(payload, timeout=20)
    assert proc.returncode == 0, stderr.decode()
    line = stdout.decode("utf-8").splitlines()[0]
    payload = json.loads(line)
    names = [tool["name"] for tool in payload["result"]["tools"]]
    assert "skim_search" in names
    assert b"skim_search" not in stderr
