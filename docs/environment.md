# Codex Vault Pipeline — Environment Setup

## Prerequisites

- **uv** (required) — Python package manager
  - Install: `brew install uv`
  - Verify: `uv --version`

- **Python 3.11+** (required)
  - Managed by uv via `.python-version`
  - Do NOT use system Python for project commands

## Quick Start

```bash
# Clone the pipeline repo
git clone git@github.com:mnh-shop/codex-vault-pipeline.git
cd codex-vault-pipeline

# Run bootstrap script
./scripts/bootstrap_env.sh

# Or manually:
uv venv
uv sync --all-extras --dev
```

## Environment Layout

```
codex-vault-pipeline/
├── .python-version      # Python version (3.11)
├── .venv/               # Project-local virtual environment
├── pyproject.toml       # Project config and dependencies
├── src/                 # Source code
├── tests/               # Test suite
├── scripts/             # Utility scripts
└── docs/                # Documentation
```

## Running Commands

**Always use `uv run`** after bootstrap:

```bash
# CLI entrypoint
uv run codex-vault --help

# Python module
uv run python -m codex_vault_pipeline --help

# Environment diagnostics
uv run codex-vault env doctor
uv run codex-vault env info

# Path validation
uv run codex-vault --vault-root /path/to/vault paths doctor
uv run codex-vault --vault-root /path/to/vault paths show

# Tests
uv run pytest

# Linting
uv run ruff check src/

# Type checking
uv run mypy src/
```

## Environment Doctor

The `env doctor` subcommand checks:

- Python version (>=3.9 required, 3.11+ recommended)
- Virtual environment active
- `uv` available in PATH
- `CODEX_VAULT_ROOT` environment variable
- `__main__.py` exists
- `codex-vault` entrypoint in pyproject.toml

```bash
uv run codex-vault env doctor
```

## Paths Doctor

The `paths doctor` subcommand validates:

- Vault root exists and is a directory
- Runtime root exists
- Indexes root exists
- Metadata DB exists
- LanceDB index exists
- Quarantine contents
- Raw root is readable

```bash
uv run codex-vault --vault-root /path/to/vault paths doctor
```

## Troubleshooting

### "uv not found"

Install uv:
```bash
brew install uv
```

### "Not running in a virtual environment"

Activate the venv or use `uv run`:
```bash
source .venv/bin/activate
# or
uv run codex-vault --help
```

### "CODEX_VAULT_ROOT not set"

Set the environment variable or pass `--vault-root`:
```bash
export CODEX_VAULT_ROOT=/path/to/codex-vault
# or
uv run codex-vault --vault-root /path/to/codex-vault paths doctor
```

### "Python version too old"

Upgrade Python:
```bash
uv python install 3.11
uv venv
uv sync --all-extras --dev
```

## Dependency Groups

| Group | Purpose | Install |
|-------|---------|---------|
| (default) | Core pipeline | `uv sync` |
| `vector` | LanceDB + embeddings | `uv sync --extra vector` |
| `test` | pytest + coverage | `uv sync --extra test` |
| `dev` | ruff + mypy | `uv sync --extra dev` |
| `all` | Everything | `uv sync --all-extras --dev` |

## Adding Dependencies

**Always use `uv add`** to make dependency contracts explicit:

```bash
# Add a runtime dependency
uv add pyyaml

# Add a test dependency
uv add --dev pytest pytest-cov

# Add a dev dependency
uv add --dev ruff mypy

# Add an optional dependency
uv add --optional vector lancedb
```

**Do NOT use** `uv pip install ...` for project dependencies. This installs packages without updating `pyproject.toml` or `uv.lock`, making the dependency contract invisible.

The only exception is emergency debugging where you need a quick one-off install.
