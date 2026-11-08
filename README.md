# dsh-lexicon

IME lexicon manager for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`): it bundles the `ime_lexicon.py` engine and exposes it to the model as tools for managing **Doubao（豆包）** and **WeType（微信输入法）** personal word libraries — detect, export, backup, industry-term search, build, optimize, and staging import.

The Python engine ships inside this package (`vendor/ime-lexicon/`) and stays the single source of truth for all file-layer operations; the plugin only shells out with validated args and bounded timeouts. **Import never applies to a live IME** — `apply` is deliberately not exposed as a tool and remains a manual, user-confirmed step.

## Install

```sh
dsh plugin add @taxueseek/dsh-lexicon
# or straight from GitHub
dsh plugin add taxueseek/dsh-lexicon
```

Requires `python3` on PATH (the engine is pure-stdlib Python; no pip installs).

## Tools

| Tool | Purpose |
|---|---|
| `ime_detect` | Report Doubao / WeType library status and used slots. |
| `ime_export` | Export word libraries to TSV (read-only snapshot). |
| `ime_backup` | Timestamped backup of the word libraries. |
| `ime_search` | Industry terminology search — prints a plan by default; `run: true` executes the actual search (argo first, generic fallback). |
| `ime_build` | Build an industry word-list TSV from a raw term list. |
| `ime_optimize` | Merge / dedupe / correct word lists across sources. |
| `ime_import` | Stage an optimized TSV into the staging area (set `replace` to rebuild from this TSV only, dropping corrected/removed words) — never touches the live library. |

## Discipline rules

- Copy before read, staging before apply; the input method must be exited before any apply.
- Doubao personal library has ~7500 slots (Doubao 0.9.7+ expansion; one word per slot) — always budget imports (`--budget`).
- Core words need pinyin; polyphones go to the extended list; pinyin-less terms go to the general list and are never applied.
- Error-fix replace: `ime_import` with `replace` rebuilds the staged library from only this TSV (not merged into live), dropping words optimize rewrote/removed; pinyin-less terms are still never applied.
- `ime_search --run` uses network (argo → generic fallback); it may take a couple of minutes.

## Config

| Key | Default | Meaning |
|---|---|---|
| `scriptPath` | bundled `vendor/ime-lexicon/ime_lexicon.py` | Path to the engine script (override to use your own copy) |
| `scriptTimeoutMs` | 120000 | Timeout for local commands |
| `searchTimeoutMs` | 300000 | Timeout for `search --run` (network) |

## Security notes

- Nothing leaves the machine except `ime_search --run` network calls; exports and backups stay local.
- The plugin never applies changes to a live IME — that stays a manual step.

## License

MIT
