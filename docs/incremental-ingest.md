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
6. **Runs the deterministic tech-profile extractor** to populate
   `source_platform`, `repo_identity`, `repo_profile`,
   `interfaces`, and the data-side of `workflow_synthesis` (the
   `workflow_roles`/`provides`/`requires` arrays are operator-set,
   not auto-derived)
7. Produces an audit report

The subcommand is **additive** — it never modifies existing
records, never deletes files, and never overwrites the
vault's wiki, candidate notes, or migration reports.

The tech-profile extractor is the deterministic source of the
new Layer A fields. It is **safe by design**: it never reads
secret-bearing paths (`.env`, `*.pem`, `*credentials*`,
`*secret*`, `*token*`), never parses environment-variable
VALUES into semantic text, and never modifies any existing
file. See §8 for the full extractor contract.

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

## 8.0 Tech-Profile Extractor (deterministic, safe)

The technical-profile fields on Layer A (`source_platform`,
`repo_identity`, `repo_profile`, `interfaces`, and the
data-side of `workflow_synthesis`) are populated by
`src/codex_vault_pipeline/extractors/tech_profile.py`. The
extractor is a pure function: same raw/ tree → same profile.
It does not call any LLM.

**Inputs.** A `raw/<repo>/` directory tree (the clone of the
GitHub repo at the pinned commit) plus the `source_id` and
`resolved_commit` from the Layer A record.

**Outputs.** A JSON profile with the new Layer A fields, sorted
and deterministically ordered.

**What it inspects:**

- File extensions → `repo_profile.languages` and
  `repo_profile.language_breakdown` (a fractional 0.0–1.0 split
  based on file counts; binary files > 1 MB skipped).
- Recognized manifest files at the repo root or one level deep
  → `repo_profile.dependency_manifests[]` and
  `repo_profile.major_dependencies[]`. Recognized names:
  `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`,
  `requirements*.txt`, `Pipfile.lock`, `poetry.lock`, `uv.lock`,
  `package.json`, `tsconfig.json`, `pnpm-lock.yaml`, `yarn.lock`,
  `package-lock.json`, `bun.lockb`, `go.mod`, `go.sum`,
  `Cargo.toml`, `Cargo.lock`, `composer.json`, `Gemfile`,
  `Gemfile.lock`, `mix.exs`, `Dockerfile`, `Dockerfile.*`,
  `docker-compose.{yml,yaml}`, `compose.{yml,yaml}`,
  `Chart.yaml`, `kustomization.yaml`.
- Build systems → `repo_profile.build_systems[]` (e.g. `make`,
  `cmake`, `gradle`, `maven`, `sbt`, `rake`, `just`, `task`).
- Test systems → `repo_profile.test_systems[]` (e.g. `pytest`,
  `jest`, `go test`, `cargo test`, `rspec`, `minitest`).
- Entrypoints → `repo_profile.entrypoints[]` (recognized file
  names at the repo root: `main.py`, `app.py`, `server.py`,
  `wsgi.py`, `asgi.py`, `manage.py`, `cli.py`, `index.js`,
  `server.js`, `main.go`, `main.rs`, plus `cmd/*.go`).
- Docker compose services → `repo_profile.services[]`
  (service names only; no values).
- Data store names from dependency names and (recognizable)
  env-var keys → `repo_profile.data_stores[]` (e.g. `postgres`,
  `redis`, `milvus`, `kafka`, `vault`). Only the env-var NAME is
  matched; the value is never read or stored.
- Config files → `repo_profile.config_files[]` (recognized
  filenames at the repo root).
- Interfaces → `interfaces[]` (each interface is a single entry;
  `kind` is the controlled enum: `cli`, `rest-api`,
  `graphql-api`, `python-package`, `npm-package`, `docker-service`,
  `mcp-server`, `mcp-client`, `n8n-workflow`, `agent-skill`,
  `plugin`, `web-ui`, `library`, `dataset`, `unknown`).

**What it NEVER does:**

- Never reads `.env`, `*.pem`, `*credentials*`, `*secret*`,
  `*token*`, or any other secret-bearing path. The basename
  AND a regex pattern are checked before any read.
- Never reads an environment-variable VALUE. Env-var NAMES are
  scanned only to detect data-store categories; values are
  never parsed, never stored, never emitted.
- Never overwrites an existing `source.v1.yaml`. The extractor
  emits a profile; the ingest orchestrator decides whether to
  overlay it onto the source record.
- Never walks outside the given `raw_root`. Every file path is
  joined and re-rooted before being added to any output field.
- Never exceeds 50 000 files in the language-detection walk
  (defensive cap for very large repos).

**`workflow_synthesis.workflow_roles` / `provides` / `requires`
/ `composition_edges` / `composition_notes`** are operator-set,
not auto-derived. The extractor leaves these empty by default.
The reason: those fields express *intent* and *composition
intent*, which are not derivable from the raw tree.

## 9.0 Backfill Feasibility Report

A separate tool produces a feasibility report for the existing
34 source records:

```bash
PYTHONPATH=codex-vault-pipeline/src python3 \
  -m codex_vault_pipeline.extractors.tech_profile_backfill_report \
  --vault-root /path/to/codex-vault \
  --out .runtime/reports/tech-profile-backfill-report.json
```

The report walks every Layer A `source.v1.yaml` and runs the
extractor against the corresponding `raw/<repo>/` tree. It
summarizes:

- how many sources can infer `source_platform: github`
- how many sources can infer `repo_identity.owner`/`repo`
- how many sources have at least one dependency manifest
- how many sources have language signals
- how many sources have detectable interfaces

The report does **NOT** rewrite any source record. Backfill
decisions are deferred to a separate, explicit, opt-in tool.
