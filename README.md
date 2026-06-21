# codex-vault-pipeline

A standalone, installable Python package that operates on a
[Codex Vault](https://github.com/mnh-shop/codex-vault) — the
ingestion, indexing, validation, and retrieval engine extracted
from the vault's `.runtime/tools/` so it can be developed,
tested, versioned, and released independently of the vault
itself.

## What it does

The pipeline exposes four subcommands:

| Subcommand | Purpose |
|---|---|
| `validate` | Run the strict 20-rule validator against the vault's `.runtime/` |
| `ingest` | Ingest a GitHub source into the vault (clones, indexes, writes records) |
| `build-indexes` | Build the SQLite metadata DB, FTS5 search index, and (optionally) LanceDB vector index |
| `benchmark` | Run the retrieval benchmark (FTS / vector / hybrid) and write reports |

It never holds the vault's data; it reads from and writes to a
vault root that is resolved at every invocation from
`--vault-root` or the `CODEX_VAULT_ROOT` environment variable.

## Installation

From the repo root (development install):

```bash
pip install -e .
```

Or just point `PYTHONPATH` at `src/`:

```bash
export PYTHONPATH="$(pwd)/src"
```

## Quick start

```bash
# Validate the vault's machine data
codex-vault-validate --vault-root /path/to/codex-vault

# Build indexes (skip the vector index for fast smoke)
codex-vault-build-indexes --vault-root /path/to/codex-vault --no-vector

# Run a 3-query smoke benchmark
codex-vault-benchmark --vault-root /path/to/codex-vault --quick

# Plan an ingest (no clone, no writes)
codex-vault-ingest --vault-root /path/to/codex-vault --github https://github.com/owner/repo --dry-run
```

Or via the bash wrappers in `scripts/`:

```bash
./scripts/codex-vault-validate --vault-root /path/to/codex-vault
./scripts/codex-vault-build-indexes --vault-root /path/to/codex-vault --no-vector
./scripts/codex-vault-benchmark --vault-root /path/to/codex-vault --quick
./scripts/codex-vault-ingest --vault-root /path/to/codex-vault --github URL --dry-run
```

## Repository layout

```
codex-vault-pipeline/
├── README.md                  this file
├── AGENTS.md                  developer guide
├── pyproject.toml             pip-installable package
├── requirements.txt           dependency list
├── .gitignore                 standard Python ignores + vault data ignores
├── src/
│   └── codex_vault_pipeline/
│       ├── __init__.py        package metadata
│       ├── paths.py           vault-root resolution + subpath derivation
│       ├── cli.py             subcommand dispatcher (validate, ingest, ...)
│       ├── legacy/            refactored phase 0-6 tools (27 modules)
│       └── schemas/           bundled schema YAMLs (10 schemas + 8 vocabs)
├── scripts/                   thin bash wrappers for each subcommand
├── schemas/                   top-level mirror of the bundled schemas
├── tests/                     smoke tests
└── docs/
    ├── pipeline-overview.md
    ├── data-model.md
    ├── security-policy.md
    ├── incremental-ingest.md
    └── indexing-and-benchmarks.md
```

## Path resolution

The pipeline resolves a single **vault root** and derives
every subpath from it:

```
${VAULT_ROOT}/
├── .runtime/                 machine data (sources, artifacts, ...)
├── raw/                      raw source captures
├── wiki/                     human-readable vault (Obsidian)
├── reports/
├── queries/
└── archive/
```

The vault root is resolved in this order:

1. `--vault-root <path>` on the command line
2. The `CODEX_VAULT_ROOT` environment variable
3. The current working directory's parent, if it has a `.runtime/`
   subdirectory (so the pipeline is convenient when it lives
   next to the vault)

## What is *not* in this repo

The pipeline repo is **code only**. It explicitly excludes:

- raw source snapshots (`.git/`, `*.zip`, downloaded repos)
- runtime data (`.runtime/artifacts`, `.runtime/occurrences`,
  `.runtime/units`, `.runtime/domain`, `.runtime/relations`,
  `.runtime/db`, `.runtime/indexes`, LanceDB files, embeddings)
- backups and caches
- secrets, env files, credentials

See `.gitignore` for the full list. The `vault/` is the data;
the `pipeline/` is the engine.

## License

This package is internal tooling for the Codex Vault project.
See the parent `codex-vault` repo for license terms.
