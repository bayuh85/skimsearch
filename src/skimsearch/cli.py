"""Command line interface for skimsearch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from skimsearch import __version__
from skimsearch.engine import index_project, search_project, status_project
from skimsearch.errors import IndexMissing, SkimError
from skimsearch.log import emit
from skimsearch.mcp_server import serve
from skimsearch.render import render_index, render_search, render_search_json, render_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skimsearch",
        description=(
            "Index a local project and return a short deck of ranked snippets. "
            "Default LLM: AtmoRouter (https://atmorouter.dev)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"skimsearch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    index_cmd = sub.add_parser("index", help="Fold a project into a local SQLite index")
    index_cmd.add_argument("path", nargs="?", default=".", help="Project root (default: .)")
    index_cmd.add_argument("--force", action="store_true", help="Delete the index and rebuild it")
    index_cmd.add_argument(
        "--embed",
        action="store_true",
        help="Store vectors from POST /embeddings (provider must support them)",
    )
    index_cmd.add_argument(
        "--max-file-kb",
        type=int,
        default=512,
        help="Skip files larger than this (default: 512)",
    )

    search_cmd = sub.add_parser("search", help="Return a token-budgeted deck of snippets")
    search_cmd.add_argument("query", nargs="+", help="Words, a symbol, or a question")
    search_cmd.add_argument(
        "-C",
        "--path",
        default=".",
        help="Project directory, or any subdirectory of an indexed project",
    )
    search_cmd.add_argument(
        "-m",
        "--mode",
        choices=("lexical", "semantic", "ask"),
        default="lexical",
        help="lexical is offline; semantic and ask call the configured LLM",
    )
    search_cmd.add_argument("-b", "--budget", type=int, default=None, help="Max tokens in the deck")
    search_cmd.add_argument("-n", "--limit", type=int, default=8, help="Max cards (default: 8)")
    search_cmd.add_argument(
        "--card-tokens",
        type=int,
        default=None,
        help="Max tokens for one card before the deck budget cuts it",
    )
    search_cmd.add_argument(
        "-f",
        "--format",
        choices=("text", "json"),
        default="text",
        help="text is a compact deck; json is the same cards",
    )

    serve_cmd = sub.add_parser("serve", help="MCP stdio server for Cursor or Claude Code")
    serve_cmd.add_argument("path", nargs="?", default=".", help="Project root (default: .)")

    status_cmd = sub.add_parser("status", help="Show index stats and the configured provider")
    status_cmd.add_argument("path", nargs="?", default=".", help="Project root (default: .)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            return _cmd_index(args)
        if args.command == "search":
            return _cmd_search(args)
        if args.command == "serve":
            return serve(Path(args.path))
        if args.command == "status":
            sys.stdout.write(render_status(status_project(Path(args.path))))
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except IndexMissing as exc:
        emit(str(exc))
        return 2
    except SkimError as exc:
        emit(str(exc))
        return 1
    except KeyboardInterrupt:
        emit("interrupted")
        return 130


def _cmd_index(args: argparse.Namespace) -> int:
    if args.max_file_kb < 1:
        raise SkimError("--max-file-kb must be positive")
    report = index_project(
        Path(args.path),
        force=args.force,
        max_file_bytes=args.max_file_kb * 1024,
        embed=args.embed,
    )
    sys.stdout.write(render_index(report))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    result = search_project(
        Path(args.path),
        " ".join(args.query),
        mode=args.mode,
        budget=args.budget,
        limit=args.limit,
        card_tokens=args.card_tokens,
    )
    if result.cost:
        emit(f"provider cost ${result.cost}")
    if args.format == "json":
        sys.stdout.write(render_search_json(result))
    else:
        sys.stdout.write(render_search(result))
    return 0
