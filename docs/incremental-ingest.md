# Incremental Ingest

This document describes the `codex-vault-ingest` subcommand:
how it works, what it produces, and how to use it safely.

## 1.0 Purpose

Incremental ingest is the operation that adds a new source to
the vault. It:

1. Clones the upstream repository at a pinned commit
2. Walks every file in the clone (excluding `.git/`)
3. Detects content type, classifies the artifact role, and
   computes a per-file content hash
4. Scans every text-readable file with `detect-secrets`
5. Writes Layer A (source), Layer B (artifact), and Layer C
   (occurrence, unit, bundle) records
6. Produces an audit report

The subcommand is **additive** — it never modifies existing
records, never deletes files, and never overwrites the
vault's wiki, candidate notes, or migration reports.

## 2.0 CLI

```bash
codex-vault-ingest \
    --vault-root /path/to/codex-vault \
    --github https://github.com/<owner>/<repo> \
    [--dry-run] \
    [--run-id <id>] \
    [--skip-cloning]
```

| Flag | Purpose |
|---|---|
| `--vault-root` | Path to the Codex Vault root (required, or via `CODEX_VAULT_ROOT` env) |
| `--github` | URL of the source to ingest. `https://github.com/owner/repo` or `owner/repo` |
| `--dry-run` | Print the plan; do not clone, do not write, do not mutate |
| `--run-id` | Optional explicit run id; defaults to `incremental-ingest-<UTC-timestamp>` |
| `--skip-cloning` | (Future) reuse an existing clone under `${VAULT_ROOT}/raw/<repo>/` |

## 3.0 What It Writes

After a successful run, the following new files appear:

| Layer | Path | Count |
|---|---|---|
| A | `${VAULT_ROOT}/.runtime/sources/<encoded_source_id>/source.v1.yaml` | 1 |
| B | `${VAULT_ROOT}/.runtime/artifacts/<sha256:content>.json` | up to N (N = source file count) |
| C | `${VAULT_ROOT}/.runtime/occurrences/<encoded_source_id>/<sha256:occurrence_id>.json` | N |
| C | `${VAULT_ROOT}/.runtime/units/<kind>/<encoded_source_id>/<sha256>.json` | up to M (M = doc-sections + configs + deployments + scripts) |
| C | `${VAULT_ROOT}/.runtime/bundles/<bundle_id>/bundle.json` | depends on SKILL.md / SOUL.md detection |
| raw | `${VAULT_ROOT}/raw/<repo>/...` | N (excluded from the pipeline repo) |
| report | `${VAULT_ROOT}/.runtime/reports/incremental-ingest-<source>.md` | 1 |
| report | `${VAULT_ROOT}/.runtime/reports/incremental-ingest-<source>-security-audit.md` | 1 |
| report | `${VAULT_ROOT}/.runtime/reports/incremental-ingest-excluded-files.json` | 1 |
| report | `${VAULT_ROOT}/.runtime/reports/incremental-ingest-binary-security-classification.json` | 1 (if any binary files) |

`N` = number of non-`.git/` files in the source. `M` is a
subset that excludes binary files and pure media (images,
audio, video).

## 4.0 What It Does Not Write

The ingest subcommand explicitly does **not** modify:

- The vault's wiki (`${VAULT_ROOT}/wiki/`)
- The vault's candidate notes (`${VAULT_ROOT}/.runtime/knowledge-notes/`)
- The vault's migration reports (`${VAULT_ROOT}/.runtime/migration-reports/`)
- The vault's indexes (`${VAULT_ROOT}/.runtime/indexes/`)
- The vault's metadata DB (`${VAULT_ROOT}/.runtime/db/`)

The indexes and metadata DB are rebuilt by
`codex-vault-build-indexes`, which is a separate, explicit
subcommand.

## 5.0 Dry-Run

`--dry-run` causes the subcommand to print the planned
operations and exit 0 without writing anything. The output
includes:

- The resolved source URL and inferred `source_id`
- The clone target path under `${VAULT_ROOT}/raw/<repo>/`
- The expected Layer A / B / C record counts
- The expected security scan summary (clean / flagged / blocked / not_scanned)
- The expected excluded file count (`.git/` and friends)

The dry-run is the recommended way to verify scope before
running a real ingest.

## 6.0 Failure Modes

If any phase of the ingest fails, the run stops immediately
and the partial state is preserved. The audit report records
the error. The operator can:

- Re-run the ingest with the same `--github` URL — duplicate
  artifacts are deduplicated by `content_sha256`, so a re-run
  after a partial failure is safe
- Inspect `${VAULT_ROOT}/.runtime/reports/incremental-ingest-<source>-security-audit.md`
  for the security status of every scanned file

## 7.0 Example: Ingest a Source, Then Build Indexes

```bash
# 1. Plan
codex-vault-ingest \
    --vault-root /path/to/codex-vault \
    --github https://github.com/<owner>/<repo> \
    --dry-run

# 2. Real run
codex-vault-ingest \
    --vault-root /path/to/codex-vault \
    --github https://github.com/<owner>/<repo>

# 3. Build (or rebuild) indexes
codex-vault-build-indexes \
    --vault-root /path/to/codex-vault

# 4. Validate
codex-vault-validate --vault-root /path/to/codex-vault

# 5. Benchmark
codex-vault-benchmark --vault-root /path/to/codex-vault
```

These five steps are the standard incremental-ingest playbook.
The order matters: validate and benchmark both assume the
indexes are up to date.
