# Pipeline Overview

The `codex-vault-pipeline` is the engine that operates on a
[Codex Vault](https://github.com/mnh-shop/codex-vault). It is
extracted from the vault's `.runtime/tools/` so it can be
versioned, tested, and released independently of the vault
itself.

## 1.0 Goals

- **Reusability.** The same engine should drive the public
  Codex Vault, an internal private vault, and any future
  Codex Vault deployment. The engine never assumes a specific
  vault location.
- **Reproducibility.** Given the same vault, the engine
  produces the same indexes, the same validator output, and
  the same benchmark numbers. No nondeterministic operations
  are introduced by the refactor.
- **Auditability.** Every state-mutating operation writes a
  machine-readable report. The strict validator's 20 rules
  are unchanged from the original tools.
- **Decoupling.** The engine never reaches into the vault's
  wiki, candidate notes, or migration reports. Those are
  owner-managed and never modified by the pipeline.

## 2.0 Architecture

```
   ┌─────────────────────────────────────────────────┐
   │  scripts/  ──  thin bash wrappers (4 commands)  │
   ├─────────────────────────────────────────────────┤
   │  src/codex_vault_pipeline/cli.py                │
   │  ── unified subcommand dispatcher                │
   │  ── @subcommand registry                         │
   │  ── argv synthesis for legacy scripts            │
   ├─────────────────────────────────────────────────┤
   │  src/codex_vault_pipeline/paths.py              │
   │  ── vault-root resolution                        │
   │  ── VaultPaths dataclass (subpath derivation)    │
   │  ── add_vault_root_arg, add_dry_run_arg,        │
   │     require_vault_root, is_dry_run               │
   ├─────────────────────────────────────────────────┤
   │  src/codex_vault_pipeline/legacy/                │
   │  ── 27 refactored phase 0-6 tools                │
   │  ── each accepts --vault-root and resolves       │
   │     every subpath via paths.resolve_paths         │
   ├─────────────────────────────────────────────────┤
   │  src/codex_vault_pipeline/extractors/            │
   │  ── tech_profile.py (deterministic tech-profile  │
   │     extractor; populates source_platform,        │
   │     repo_identity, repo_profile, interfaces)     │
   │  ── tech_profile_backfill_report.py              │
   │     (no-record-rewrite feasibility report)        │
   ├─────────────────────────────────────────────────┤
   │  src/codex_vault_pipeline/schemas/               │
   │  ── 10 schema YAMLs (Layer A-E)                  │
   │  ── 8 vocab YAMLs (controlled vocabularies)       │
   ├─────────────────────────────────────────────────┤
   │  tests/                                          │
   │  ── test_paths.py (unit, no FS)                  │
   │  ── test_smoke.py (integration vs. live vault)   │
   └─────────────────────────────────────────────────┘
```

## 3.0 Subcommand Lifecycle

Each subcommand follows the same lifecycle:

1. **Argparse.** The CLI's `build_parser()` collects
   `--vault-root`, `--dry-run`, and any subcommand-specific
   options.
2. **Path resolution.** `require_vault_root(args)` produces
   a `VaultPaths` dataclass with every standard subpath
   pre-derived.
3. **Dry-run short-circuit.** `is_dry_run(args)` returns
   True; the handler prints a plan and exits 0.
4. **Argv synthesis for legacy.** `_run_legacy()` exports
   `CODEX_VAULT_ROOT` so the legacy script's module-level
   `Path(os.environ.get("CODEX_VAULT_ROOT"))` resolves
   correctly. It then imports the legacy module via
   `importlib.import_module` (not `runpy.run_module`, which
   mishandles `@dataclass` forward references), sets
   `sys.argv`, and calls `main()`.
5. **Result handling.** The legacy script may return
   `None`, an `int` (success / error code), or a `dict`
   (manifest). The CLI coerces all three to an int exit code.
6. **State preservation.** The CLI never cleans up
   `${VAULT_ROOT}/.runtime/` or `${VAULT_ROOT}/raw/` on
   success. The caller is responsible for retention.

## 4.0 Refactor Guarantees

The refactor of the 27 legacy scripts is **mechanical and
deterministic**. The script `scripts/_refactor_tools.py` is
the canonical source of the transformation. It guarantees:

- Every `default="/Users/admin1/agent-brain/codex-vault[/...]"`
  literal is replaced with `default=os.environ.get("CODEX_VAULT_ROOT", "")`
  or `os.path.join(...)` of the same.
- Every `Path("/Users/admin1/agent-brain/codex-vault[/...]")`
  literal is replaced with `Path(os.environ.get("CODEX_VAULT_ROOT"))`
  or a join of the same.
- Every `Path(__file__).resolve().parents[2]` (which silently
  relies on file-system location) is replaced with
  `Path(os.environ.get("CODEX_VAULT_ROOT") or ".")`.
- Every `argparse.ArgumentParser` is augmented with
  `add_vault_root_arg(ap)` so `--vault-root` is always
  accepted (defaulted to the env var).

The refactor is **idempotent**: re-running it on a file that
already has the `from codex_vault_pipeline.paths import ...`
line is a no-op. A separate round-2 refactor
(`scripts/_refactor_round2.py`) adds the `--no-vector` flag
to `build_indexes.py` and the `--quick` flag to
`run_retrieval_benchmarks.py`.

## 5.0 Failure Modes

The pipeline is designed to **fail loud, never silently
skip**. Specifically:

- If `CODEX_VAULT_ROOT` and `--vault-root` are both unset, the
  CLI exits with a clear error message and no work is done.
- If the vault root does not exist, `resolve_paths()` raises
  `SystemExit` with a clear message.
- If a legacy script's dependencies are missing (e.g. `lancedb`
  for the vector index), the script logs the dependency
  blocker to `.runtime/reports/dependency-blocker-report.md`
  and continues with whatever it can do. The pipeline never
  fails the whole run because of one missing optional dep.
- If the strict validator finds a rejection, the script
  exits non-zero. The pipeline surfaces this as the CLI's
  exit code.

## 6.0 Versioning

The package version is in `src/codex_vault_pipeline/__init__.py`
as `__version__`. The CLI exposes it via `--version`. The
pipeline repo's git tags will follow `vMAJOR.MINOR.PATCH`.
Breaking changes to the public CLI (subcommand names, required
flags) are major-version bumps.
