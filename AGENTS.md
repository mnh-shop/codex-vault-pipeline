# Codex Vault Pipeline — Agent Guide

This file documents the conventions and boundaries for working
on the `codex-vault-pipeline` repository. It is the
`AGENTS.md` for agents and humans editing this codebase.

## Repository Boundary / Git Safety

The Pipeline Git root is **`/Users/admin1/agent-brain/codex-vault-pipeline`**.

**Hard rules:**

- The only remote is **`git@github.com:mnh-shop/codex-vault-pipeline.git`**.
- The pipeline repo contains **code, schemas, docs, tests, and
  CLI wrappers only**.
- It must **not** contain raw source snapshots, `.runtime/`,
  databases, indexes, LanceDB files, embeddings, backups, or
  vault content.
- Pipeline commands must operate on the vault only through
  `--vault-root` or the `CODEX_VAULT_ROOT` environment variable.
- The pipeline must **not** modify the vault's wiki, candidate
  notes, or migration reports. Those are owner-managed.
- The pipeline must **not** promote candidates. Promotion is a
  separate, explicit action with its own audit trail.

**Pre-commit safety check (mandatory before every commit):**

```bash
git rev-parse --show-toplevel
```

must equal:

```text
/Users/admin1/agent-brain/codex-vault-pipeline
```

If the toplevel is anything else (especially `/Users/admin1/agent-brain/`
or any parent), **abort the commit** — the working directory is
the wrong scope.

The vault (a separate, sibling repo) holds the data this
pipeline operates on. The pipeline never ships or holds the
vault's data, runtime data, raw captures, indexes, or
embeddings.

## 1. Non-Negotiables

- The pipeline repo contains **code, schemas, docs, and tests only**.
- The vault's data, raw captures, databases, indexes, and
  embeddings **must not** be committed. They live in
  `${VAULT_ROOT}/.runtime/` and `${VAULT_ROOT}/raw/`, which are
  explicitly ignored by `.gitignore`.
- The pipeline does **not** modify the vault's wiki, candidate
  notes, or migration reports. Those are owner-managed.
- The pipeline does **not** promote candidates. Promotion is a
  separate, explicit action with its own audit trail.
- The pipeline does **not** delete project files anywhere. Every
  destructive operation requires a separate, explicit user
  action.

## 2. Path Resolution

Every script that touches a vault resolves a single **vault
root** via `codex_vault_pipeline.paths.resolve_paths()`. The
helper accepts:

- `cli_value` — the `--vault-root` argument value
- `env` — the process environment (defaults to `os.environ`)
- `require_exists` — whether the vault root must exist (default True)

Order of precedence is documented in `paths.py`. The vault root
is expanded and resolved to an absolute path. The standard
subpaths (`.runtime`, `raw`, `wiki`, `db`, `indexes`, etc.) are
derived as `Path` attributes on the returned `VaultPaths` dataclass.

**Never hardcode an absolute vault path in any module of this
repo.** A grep for `/Users/` or for `Path(__file__).resolve().parents[`
should return only the example inside `paths.py` and the test
data in `tests/`.

## 3. Schema Layer

This package bundles the schema YAMLs that the validator and
the legacy scripts read. Two locations are kept in sync:

- `schemas/` at the repo root — for human inspection, diffing,
  and external tooling
- `src/codex_vault_pipeline/schemas/` — for runtime import
  (`importlib.resources`)

When updating a schema, edit the file under `src/.../schemas/`
and then mirror it to `schemas/`. (A future pass could add a
CI check that they stay in sync; for now we copy at build time.)

## 4. Legacy Module Refactor Rules

The `src/codex_vault_pipeline/legacy/` subpackage contains
refactored copies of the vault's phase 0–6 tools. The
refactor rules are:

1. The `Path(__file__).resolve().parents[2]` idiom and any
   other hardcoded vault absolute path must be replaced with
   `Path(os.environ.get("CODEX_VAULT_ROOT") or ".")`.
2. `argparse.ArgumentParser` must be augmented with
   `add_vault_root_arg(ap)` so every script accepts
   `--vault-root` (with the `CODEX_VAULT_ROOT` env var as
   default).
3. The script's `main()` must return an int (or a dict, for
   `build_indexes.py` which returns a manifest) and must not
   raise on common user errors.
4. Indented imports inside `try:` blocks must be left alone;
   the refactor only touches the top-level import block.

The `scripts/_refactor_tools.py` script is the canonical
refactor. Re-run it after editing the vault to re-import any
new tools.

## 5. CLI Conventions

The unified CLI is in `src/codex_vault_pipeline/cli.py`. New
subcommands should:

1. Be registered with the `@subcommand("name", help="...")`
   decorator.
2. Accept `--vault-root` (via `add_vault_root_arg`) and
   `--dry-run` (via `add_dry_run_arg`).
3. Resolve the vault root with `require_vault_root(args)`.
4. Respect dry-run by exiting early with a printed plan, not
   by no-op-ing silently.
5. Return an int exit code (0 = success, non-zero = error).

Bash wrappers in `scripts/` are thin shims that set
`PYTHONPATH` to `src/` (for development installs) and exec
`python -m codex_vault_pipeline.cli <subcommand> "$@"`. They
do not contain any logic of their own.

## 6. Testing

The smoke tests in `tests/` are designed to run against the
existing Codex Vault on the developer's machine. They are
intentionally minimal:

- `tests/test_paths.py` — pure unit tests for the path
  resolver (no filesystem side effects)
- `tests/test_smoke.py` — integration smoke that runs the
  full pipeline (validate, build-indexes --no-vector,
  benchmark --quick) against the live vault

For CI, the smoke tests should be skipped if the
`CODEX_VAULT_ROOT` env var is unset. The vault is a sibling
repo, not a dependency of this one.

## 7. Style

- Python 3.9+ (matches the vault's runtime)
- PEP 8 spacing; ruff-style import ordering
- Type hints on every public function
- `argparse` for every CLI; no `click` or `typer`
- No external HTTP, no auth tokens, no `requests` calls

## 8. Common Tasks

### Add a new subcommand

1. Implement the function in `src/codex_vault_pipeline/cli.py`,
   decorated with `@subcommand("name", help="...")`.
2. Add any subcommand-specific args in `build_parser()`.
3. Add a thin bash wrapper in `scripts/`.
4. Update the README and `docs/cli-reference.md` (if present).

### Re-import a refactored tool from the vault

```bash
python3 scripts/_refactor_tools.py
```

This re-copies the 27 listed tools from the vault, applies
the canonical refactor, and writes them to
`src/codex_vault_pipeline/legacy/`. Always run it after the
vault's phase scripts change.

### Update bundled schemas

1. Edit the YAML under `src/codex_vault_pipeline/schemas/`.
2. Copy it to `schemas/` at the repo root:
   `cp src/.../schemas/<name>.yaml schemas/`.
3. Verify the validator still passes against the existing
   vault: `./scripts/codex-vault-validate --vault-root ...`.
