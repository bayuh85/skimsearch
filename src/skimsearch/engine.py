"""Index a project and answer searches from the local deck."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skimsearch.config import DEFAULT_EMBED_MODEL, Settings, load_settings
from skimsearch.errors import IndexMissing, SkimError
from skimsearch.fold import fold_source, language_of
from skimsearch.ignore import IgnoreSet
from skimsearch.llm import LLMClient, LLMError, parse_json_object
from skimsearch.log import emit
from skimsearch.render import IndexReport, SearchResult, StatusReport
from skimsearch.retrieve import (
    DeckCard,
    pack_deck,
    query_terms,
    search_embeddings,
    search_lexical,
    search_probes,
)
from skimsearch.store import (
    connect,
    counts,
    delete_path,
    get_meta,
    has_index,
    index_path,
    insert_card,
    optimize,
    remove_index,
    resolve_index_root,
    set_meta,
)
from skimsearch.tokens import estimate_tokens
from skimsearch.vectors import pack_vector

_PROBE_SYSTEM = (
    "You rewrite a code-search question into lexical probes for a source index. "
    'Reply with JSON only: {"probes": [string, ...]}. '
    "Return 3 to 6 probes. Each probe is a short identifier, file name, or phrase "
    "that would appear in code or docs. No explanations."
)

_ASK_SYSTEM = (
    "You answer questions about a codebase using only the skim cards in the user message. "
    'Reply with JSON only: {"answer": string, "card_ids": [number, ...]}. '
    "The answer is at most 120 words. card_ids lists the cards that support the answer, "
    "most useful first. Use only ids that appear in the prompt. "
    "If the cards are not enough, say so and return an empty card_ids array."
)


def index_project(
    root: Path,
    *,
    force: bool = False,
    max_file_bytes: int = 512 * 1024,
    embed: bool = False,
    client: Any = None,
    settings: Settings | None = None,
) -> IndexReport:
    root = root.resolve()
    if not root.is_dir():
        raise SkimError(f"not a directory: {root}")
    if max_file_bytes < 1:
        raise SkimError("max file size must be positive")
    settings = settings or load_settings(dotenv_paths=[root / ".env", Path.cwd() / ".env"])
    if force:
        remove_index(root)
    ignore = IgnoreSet(root)
    updated = 0
    removed = 0
    skipped = 0
    seen: set[str] = set()
    con = connect(root)
    try:
        con.execute("BEGIN")
        try:
            for path in _iter_files(root, ignore):
                rel = path.relative_to(root).as_posix()
                seen.add(rel)
                try:
                    stat = path.stat()
                    payload = None
                    if stat.st_size > max_file_bytes:
                        delete_path(con, rel)
                        skipped += 1
                        continue
                    existing = con.execute(
                        "SELECT mtime, size, sha256 FROM files WHERE path = ?",
                        (rel,),
                    ).fetchone()
                    if (
                        existing
                        and int(existing["size"]) == stat.st_size
                        and abs(float(existing["mtime"]) - stat.st_mtime) < 1e-3
                    ):
                        continue
                    payload = path.read_bytes()
                except OSError:
                    delete_path(con, rel)
                    skipped += 1
                    continue
                if payload is None or b"\0" in payload[:8192]:
                    delete_path(con, rel)
                    skipped += 1
                    continue
                digest = hashlib.sha256(payload).hexdigest()
                if existing and str(existing["sha256"]) == digest:
                    con.execute(
                        "UPDATE files SET mtime = ?, size = ? WHERE path = ?",
                        (stat.st_mtime, stat.st_size, rel),
                    )
                    continue
                text = payload.decode("utf-8", errors="replace")
                folded = fold_source(rel, text)
                delete_path(con, rel)
                language = folded[0].language if folded else (language_of(rel) or "text")
                con.execute(
                    """
                    INSERT INTO files(path, mtime, size, sha256, language)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (rel, stat.st_mtime, stat.st_size, digest, language),
                )
                for card in folded:
                    insert_card(
                        con,
                        path=rel,
                        language=card.language,
                        kind=card.kind,
                        symbol=card.symbol,
                        start_line=card.start_line,
                        end_line=card.end_line,
                        part=card.part,
                        text=card.text,
                        preview=_preview(card.text),
                        token_estimate=estimate_tokens(card.text),
                    )
                updated += 1
                if updated % 50 == 0:
                    emit(f"updated {updated} files…")
            for row in con.execute("SELECT path FROM files").fetchall():
                rel = str(row["path"])
                if rel not in seen:
                    delete_path(con, rel)
                    removed += 1
            set_meta(con, "schema_version", "1")
            set_meta(con, "indexed_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            set_meta(con, "skimsearch", "0.1.0")
            con.commit()
        except Exception:
            con.rollback()
            raise
        optimize(con)
        con.commit()
        embedded = 0
        embed_model: str | None = None
        if embed:
            embedded, embed_model = _embed_index(con, settings, client)
        files, cards, _tokens = counts(con)
    finally:
        con.close()
    emit(f"indexed {root}")
    return IndexReport(
        root=str(root),
        index_path=str(index_path(root)),
        files=files,
        cards=cards,
        updated=updated,
        removed=removed,
        skipped=skipped,
        embedded=embedded,
        embed_model=embed_model,
    )


def search_project(
    root: Path,
    query: str,
    *,
    mode: str = "lexical",
    budget: int | None = None,
    limit: int = 8,
    card_tokens: int | None = None,
    client: Any = None,
    settings: Settings | None = None,
) -> SearchResult:
    if mode not in {"lexical", "semantic", "ask"}:
        raise SkimError("mode must be lexical, semantic, or ask")
    if limit < 1:
        raise SkimError("limit must be positive")
    query = query.strip()
    if not query:
        raise SkimError("query is empty")
    notice: str | None = None
    if len(query) > 500:
        query = query[:500]
        notice = "Query truncated to 500 characters."
    start = root.resolve()
    root = resolve_index_root(start)
    if not has_index(root):
        raise IndexMissing(f"no index at {start}. Run: skimsearch index {start}")
    settings = settings or load_settings(dotenv_paths=[root / ".env", Path.cwd() / ".env"])
    budget = settings.budget if budget is None else budget
    card_tokens = settings.card_tokens if card_tokens is None else card_tokens
    if budget < 1 or card_tokens < 1:
        raise SkimError("budget and card token cap must be positive")
    terms = query_terms(query)
    if not terms:
        return SearchResult(
            query=query,
            mode="lexical",
            requested_mode=mode,
            budget=budget,
            used_tokens=0,
            answer=None,
            probes=[],
            notice="Query has no searchable terms.",
            cost=None,
            cards=[],
            root=str(root),
        )
    pool = max(24, limit * 6)
    con = connect(root)
    try:
        model = _prepare_client(settings, client) if mode in {"semantic", "ask"} else None
        ranked, probes, cost, actual, extra = _retrieve(
            con,
            query=query,
            mode=mode,
            pool=pool,
            client=model,
            settings=settings,
        )
        if extra:
            notice = f"{notice} {extra}".strip() if notice else extra
        reserve = min(180, max(40, budget // 3)) if mode == "ask" and model is not None else 0
        deck, _packed_tokens = pack_deck(
            ranked,
            terms,
            budget=max(16, budget - reserve),
            limit=limit,
            card_tokens=card_tokens,
        )
        answer: str | None = None
        if mode == "ask" and model is not None:
            try:
                answer, cited, answer_cost = _answer(model, query, deck)
                cost = _add_cost(cost, answer_cost)
                actual = "ask"
                if cited:
                    chosen = [deck[number - 1] for number in cited if 1 <= number <= len(deck)]
                    # Preserve first-seen order from the model, drop duplicates.
                    deduped: list[DeckCard] = []
                    seen_ids: set[tuple[str, int, int, str]] = set()
                    for card in chosen:
                        key = (card.path, card.start_line, card.end_line, card.symbol)
                        if key in seen_ids:
                            continue
                        seen_ids.add(key)
                        deduped.append(card)
                    if deduped:
                        deck = deduped
                elif answer:
                    cite_note = "The model did not cite cards; showing the retrieved deck."
                    notice = f"{notice} {cite_note}".strip() if notice else cite_note
            except SkimError as exc:
                actual = "lexical-fallback"
                fail = f"Ask failed ({exc}). Showing the retrieved deck."
                notice = f"{notice} {fail}".strip() if notice else fail
                answer = None
        deck = _with_ranks(deck)
        answer_tokens = estimate_tokens(answer or "")
        while deck and answer_tokens + _deck_tokens(deck) > budget and len(deck) > 1:
            deck = _with_ranks(deck[:-1])
        if answer and answer_tokens + _deck_tokens(deck) > budget:
            room = max(1, budget - _deck_tokens(deck))
            answer = _trim_text(answer, room)
            answer_tokens = estimate_tokens(answer)
        used = answer_tokens + _deck_tokens(deck)
    finally:
        con.close()
    return SearchResult(
        query=query,
        mode=actual,
        requested_mode=mode,
        budget=budget,
        used_tokens=used,
        answer=answer,
        probes=probes,
        notice=notice,
        cost=cost,
        cards=deck,
        root=str(root),
    )


def status_project(root: Path, *, settings: Settings | None = None) -> StatusReport:
    start = root.resolve()
    root = resolve_index_root(start)
    if not has_index(root):
        raise IndexMissing(f"no index at {start}. Run: skimsearch index {start}")
    settings = settings or load_settings(dotenv_paths=[root / ".env", Path.cwd() / ".env"])
    con = connect(root)
    try:
        files, cards, tokens = counts(con)
        return StatusReport(
            root=str(root),
            index_path=str(index_path(root)),
            files=files,
            cards=cards,
            stored_tokens=tokens,
            indexed_at=get_meta(con, "indexed_at") or "",
            schema_version=get_meta(con, "schema_version") or "1",
            embed_model=get_meta(con, "embed_model") or "(none)",
            settings=settings,
        )
    finally:
        con.close()


def _retrieve(
    con: Any,
    *,
    query: str,
    mode: str,
    pool: int,
    client: Any,
    settings: Settings,
) -> tuple[list, list[str], str | None, str, str | None]:
    if mode == "lexical" or client is None:
        notice = None
        actual = "lexical"
        if mode != "lexical":
            actual = "lexical-fallback"
            notice = (
                "No API key set. Showing a lexical deck. "
                "Export ATMOROUTER_API_KEY to use semantic or ask mode."
            )
        return search_lexical(con, query, pool=pool), [], None, actual, notice

    cost: str | None = None
    notice: str | None = None
    ranked: list = []
    probes: list[str] = []
    if _has_embeddings(con):
        embed_model = settings.embed_model or get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
        try:
            vector = client.embed([query], embed_model)[0]
            ranked = search_embeddings(con, vector, pool=pool)
        except SkimError as exc:
            notice = f"Embedding search failed ({exc}). Using chat probes instead."
            ranked = []
        else:
            if ranked:
                return ranked, [], None, "semantic", notice
    try:
        probes, probe_cost = _expand_probes(client, query)
        cost = probe_cost
    except SkimError as exc:
        notice = f"Probe expansion failed ({exc}). Using the query as written."
        probes = [query]
    ranked = search_probes(con, probes, pool=pool)
    return ranked, probes, cost, "semantic", notice


def _expand_probes(client: Any, query: str) -> tuple[list[str], str | None]:
    reply = client.complete(
        [
            {"role": "system", "content": _PROBE_SYSTEM},
            {"role": "user", "content": query},
        ],
        max_tokens=200,
    )
    probes = [query]
    try:
        data = parse_json_object(reply.text)
    except ValueError:
        return _dedupe(probes), reply.cost_usd
    raw = data.get("probes", [])
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                probes.append(item.strip()[:80])
    return _dedupe(probes)[:7], reply.cost_usd


def _answer(client: Any, query: str, deck: list[DeckCard]) -> tuple[str, list[int], str | None]:
    blocks = [
        f"[{card.rank}] {card.path}:{card.start_line}-{card.end_line} {card.kind} {card.symbol}\n"
        f"{card.text}"
        for card in deck
    ]
    user = "Question: " + query + "\n\nCards:\n" + "\n\n".join(blocks)
    reply = client.complete(
        [
            {"role": "system", "content": _ASK_SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=500,
    )
    try:
        data = parse_json_object(reply.text)
    except ValueError:
        return reply.text.strip(), [], reply.cost_usd
    answer = data.get("answer", "")
    if not isinstance(answer, str):
        answer = str(answer)
    cited: list[int] = []
    raw_ids = data.get("card_ids", [])
    if isinstance(raw_ids, list):
        for item in raw_ids:
            try:
                cited.append(int(item))
            except (TypeError, ValueError):
                continue
    return answer.strip(), cited, reply.cost_usd


def _embed_index(con: Any, settings: Settings, client: Any) -> tuple[int, str]:
    active = client
    if active is None:
        if not settings.api_key:
            raise SkimError(
                "lexical index saved; embeddings need ATMOROUTER_API_KEY and a provider "
                "that implements POST /embeddings"
            )
        active = LLMClient(settings)
    model = settings.embed_model or DEFAULT_EMBED_MODEL
    previous = get_meta(con, "embed_model")
    if previous and previous != model:
        con.execute("UPDATE cards SET embedding = NULL")
        con.commit()
    rows = con.execute(
        "SELECT id, path, symbol, text FROM cards WHERE embedding IS NULL ORDER BY id"
    ).fetchall()
    stored = 0
    batch_size = 32
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        inputs = [_embed_input(row) for row in batch]
        try:
            vectors = active.embed(inputs, model)
        except SkimError as exc:
            raise SkimError(str(exc)) from exc
        for row, vector in zip(batch, vectors):
            con.execute(
                "UPDATE cards SET embedding = ? WHERE id = ?",
                (pack_vector(vector), int(row["id"])),
            )
        stored += len(batch)
        con.commit()
        emit(f"embedded {stored} cards…")
    set_meta(con, "embed_model", model)
    con.commit()
    return stored, model


def _embed_input(row: Any) -> str:
    text = str(row["text"])
    if len(text) > 6000:
        text = text[:6000]
    return f"{row['path']}\n{row['symbol']}\n{text}"


def _prepare_client(settings: Settings, client: Any) -> Any:
    if client is not None:
        return client
    if not settings.api_key:
        return None
    return LLMClient(settings)


def _has_embeddings(con: Any) -> bool:
    row = con.execute("SELECT 1 AS ok FROM cards WHERE embedding IS NOT NULL LIMIT 1").fetchone()
    return row is not None


def _iter_files(root: Path, ignore: IgnoreSet):
    def walk(directory: Path, rel: str):
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        files = []
        dirs = []
        for child in children:
            if child.is_symlink():
                continue
            child_rel = f"{rel}/{child.name}" if rel else child.name
            if child.is_dir():
                if child.name == ".git" or ignore.ignored(child_rel, True):
                    continue
                dirs.append(child)
            elif child.is_file():
                if ignore.ignored(child_rel, False):
                    continue
                if language_of(child_rel) is None:
                    continue
                files.append(child)
        yield from files
        for sub in dirs:
            sub_rel = f"{rel}/{sub.name}" if rel else sub.name
            ignore.push(sub, sub_rel)
            try:
                yield from walk(sub, sub_rel)
            finally:
                ignore.pop()

    yield from walk(root, "")


def _preview(text: str) -> str:
    for line in text.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:180]
    return ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _with_ranks(cards: list[DeckCard]) -> list[DeckCard]:
    return [replace(card, rank=index) for index, card in enumerate(cards, start=1)]


def _deck_tokens(cards: list[DeckCard]) -> int:
    return sum(card.tokens for card in cards)


def _trim_text(text: str, token_cap: int) -> str:
    if estimate_tokens(text) <= token_cap:
        return text
    chars = max(8, token_cap * 4)
    if len(text) <= chars:
        return text
    return text[: chars - 1].rstrip() + "…"


def _add_cost(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    try:
        return f"{float(left) + float(right):.6f}"
    except ValueError:
        return f"{left}+{right}"


# Re-export so callers can catch provider failures without importing llm directly.
__all__ = [
    "IndexReport",
    "LLMError",
    "SearchResult",
    "index_project",
    "search_project",
    "status_project",
]
