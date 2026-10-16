# skimsearch

Coding agents burn tokens when they grep a monorepo and paste the dump back into the prompt. A wall of unrelated files crowds out the few lines that matter. skimsearch indexes the tree once, then answers with a short deck of ranked snippets that fit a token budget.

## What skimsearch does

1. **Index.** `skimsearch index` folds code and docs into skim cards. A card is one function, class, heading, or window, with its path and line range. Cards live in a compact SQLite file at `.skimsearch/index.sqlite`. SQLite FTS5 (BM25) ranks them offline.
2. **Search.** `skimsearch search` returns the best cards, trimmed so the whole deck stays inside a token budget. The default mode is lexical and does not call the network.
3. **Ask.** `--mode semantic` asks an OpenAI-compatible model to turn a question into a few lexical probes, then searches locally. `--mode ask` does that and returns a short answer that cites only the cards it used. Optional `--embed` stores vectors when the provider implements `POST /embeddings`.

Agents spend tokens on the deck, not on a full-tree dump.

## Quickstart

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

skimsearch index examples/acme-billing
skimsearch search "where is the late fee calculated" --path examples/acme-billing
```

A lexical search stays on disk. On the sample project the deck looks like this:

```text
skimsearch  mode=lexical  cards=4  tokens=322/700

1. src/billing/invoices.py:17-28  function apply_late_fee  (python, score 1.71)
    def apply_late_fee(invoice: dict, as_of: date) -> dict:
        """The late fee is calculated from the subtotal after the grace window."""
        if invoice.get("settled"):
            return invoice
        overdue = (as_of - invoice["due"]).days
        if overdue <= GRACE_DAYS:
            return invoice
        fee = round(invoice["subtotal"] * LATE_FEE_RATE, 2)
        updated = dict(invoice)
        updated["late_fee"] = fee
        updated["total"] = round(invoice["subtotal"] + fee, 2)
        return updated

2. docs/billing.md:3-5  heading Late fees  (markdown, score 1.50)
    ## Late fees

    A fee of 1.5 percent is added after a three-day grace period. The late fee is calculated from the invoice subtotal.
```

Each card is a rank, a `path:start-end` locator, the symbol, and a trimmed body. The whole deck stops at the token budget.

LLM-assisted search uses the configured provider:

```bash
export ATMOROUTER_API_KEY=ar-your-key-here
skimsearch search "where is the late fee calculated" --path examples/acme-billing --mode ask
```

MCP, for Cursor or Claude Code:

```bash
skimsearch serve /absolute/path/to/repo
```

`serve` builds an index first when the project does not have one yet.

Smoke tests run offline:

```bash
pip install -e ".[dev]"
pytest -q
```

## Default LLM: AtmoRouter

Semantic and ask modes call an OpenAI-compatible chat API. The default base URL is [AtmoRouter](https://atmorouter.dev), a cheap AI API gateway and an OpenRouter alternative. One `ar-` key reaches the catalogue. Point `SKIMSEARCH_BASE_URL` at any other OpenAI-compatible endpoint when you want a different provider. Powered by AtmoRouter.

AtmoRouter also accepts the Anthropic Messages API. Set `SKIMSEARCH_API_STYLE=anthropic` and skimsearch posts to `{SKIMSEARCH_BASE_URL}/messages` (the default base already includes `/v1`). Chat completions stay on `{SKIMSEARCH_BASE_URL}/chat/completions`.

```bash
curl "$SKIMSEARCH_BASE_URL/models" -H "Authorization: Bearer $ATMOROUTER_API_KEY"
```

The default model is `atmo/deepseek-v4.1-flash`. Set `SKIMSEARCH_MODEL` to any id your key can call.

## Configuration

Copy `.env.example` to `.env` in the project you index, or export the variables in the shell. Values already set in the environment win over `.env`.

| Variable | Default | Role |
| --- | --- | --- |
| `ATMOROUTER_API_KEY` | unset | Preferred API key. AtmoRouter keys look like `ar-…`. |
| `SKIMSEARCH_API_KEY` | unset | Alias used when `ATMOROUTER_API_KEY` is unset. |
| `OPENAI_API_KEY` | unset | Alias used when the two keys above are unset. |
| `SKIMSEARCH_BASE_URL` | `https://atmorouter.dev/v1` | OpenAI-compatible base URL, including `/v1`. |
| `OPENAI_BASE_URL` | unset | Used when `SKIMSEARCH_BASE_URL` is unset. |
| `SKIMSEARCH_MODEL` | `atmo/deepseek-v4.1-flash` | Chat model id. |
| `SKIMSEARCH_API_STYLE` | `openai` | `openai` or `anthropic`. |
| `SKIMSEARCH_BUDGET` | `700` | Token budget for one deck. |
| `SKIMSEARCH_CARD_TOKENS` | `140` | Token cap for a single card before the budget cuts it. |
| `SKIMSEARCH_TIMEOUT` | `60` | HTTP timeout in seconds. |
| `SKIMSEARCH_EMBED_MODEL` | unset | Embedding model for `index --embed`. |

`index` skips dependency folders, build output, lockfiles, and secret files (`.env`, keys, `.pem`). It also reads `.gitignore`. A `.skimignore` file uses the same pattern syntax and is applied after `.gitignore`, so a negation such as `!dist/` puts a path back into the index. The sample project ignores `vendor/` with its own `.gitignore` and ships a fake `.env` that the built-in rules leave out of the index.

```bash
skimsearch index .                 # create or update .skimsearch/index.sqlite
skimsearch index . --force         # rebuild
skimsearch index . --embed         # also store /embeddings vectors
skimsearch search "session ttl" -C . --budget 500
skimsearch search "session ttl" --mode semantic
skimsearch search "session ttl" --mode ask --format json
skimsearch status .
skimsearch serve .
```

`search` from a subdirectory finds an index in a parent directory. `index` always writes the index into the directory you name.

## Cursor and Claude Code

Index the repo, then register the stdio server. Both clients use the same command.

Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "skimsearch": {
      "command": "skimsearch",
      "args": ["serve", "/absolute/path/to/repo"],
      "env": {
        "ATMOROUTER_API_KEY": "ar-your-key-here",
        "SKIMSEARCH_BASE_URL": "https://atmorouter.dev/v1"
      }
    }
  }
}
```

Claude Code (`.mcp.json` in the project, or the user MCP config):

```json
{
  "mcpServers": {
    "skimsearch": {
      "command": "skimsearch",
      "args": ["serve", "/absolute/path/to/repo"],
      "env": {
        "ATMOROUTER_API_KEY": "ar-your-key-here"
      }
    }
  }
}
```

Tools:

- `skim_search` — `query`, optional `mode` (`lexical`, `semantic`, `ask`), `budget`, `limit`, `format`
- `skim_status` — index stats and the configured provider

`lexical` is the right default for a tight loop. Use `ask` when the question is phrased in prose and you want a short grounded answer plus the cards that support it.

## License

MIT. See [LICENSE](LICENSE).
