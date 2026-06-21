# Indexing and Benchmarks

This document describes the two operator-facing subcommands
that consume Layer A/B/C records and produce the search and
retrieval infrastructure: `codex-vault-build-indexes` and
`codex-vault-benchmark`.

## 1.0 What `codex-vault-build-indexes` Produces

The subcommand builds three indexes in `${VAULT_ROOT}/.runtime/`:

| Index | Path | Type | Purpose |
|---|---|---|---|
| Metadata DB | `.runtime/db/codex-vault.db` | SQLite | Structured record storage |
| FTS5 | `.runtime/indexes/codex-vault-fts.db` | SQLite FTS5 | Full-text search |
| Vector | `.runtime/indexes/codex-vault-vectors/` | LanceDB | Semantic similarity search |

The metadata DB has one table per record type (`sources`,
`artifacts`, `occurrences`, `units`, `bundles`,
`domain_records`, `candidates`, `migration_reports`,
`evidence_links`, `security_status`, `source_coverage`). Every
`INSERT` uses explicit column names; no positional `VALUES`.

The FTS5 index has one virtual table per indexable layer:

| FTS table | Source rows | Notes |
|---|---|---|
| `candidate_fts` | one per knowledge note | title, summary, body, scope |
| `operational_fts` | one per operational artifact | semantic_text, title |
| `doc_section_fts` | one per unit | semantic_text, source_path |
| `skill_fts` | one per hermes-skill | semantic_text |
| `source_fts` | one per source | source_path, primary_domain |
| `n8n_workflow_fts` | one per n8n workflow | workflow_name, semantic_text |

The vector index is a LanceDB table per indexable layer
(`candidates`, `n8n_workflows`, `operational`). The embedding
model is `all-MiniLM-L6-v2` (384-dim) by default.

## 2.0 CLI

```bash
codex-vault-build-indexes \
    --vault-root /path/to/codex-vault \
    [--no-vector]
```

| Flag | Purpose |
|---|---|
| `--vault-root` | Path to the Codex Vault root (required) |
| `--no-vector` | Skip LanceDB vector index construction (smoke test, or no lancedb installed) |

`--no-vector` is the recommended flag for CI and smoke tests.
The vector index is the slowest step and the only one that
requires an optional dependency (`lancedb`, `numpy`,
`sentence-transformers`).

## 3.0 Build Process

The build is a single transaction per table, wrapped in a
`BEGIN IMMEDIATE` and `COMMIT` pair. If the build fails
mid-table, the table is rolled back to its pre-build state.
The build manifest at
`.runtime/indexes/index-build-manifest.json` records:

- Build timestamp
- Per-table row counts (before / after)
- Dependency availability (`lancedb`, `numpy`,
  `sentence-transformers`)
- Any blockers (logged to
  `.runtime/reports/dependency-blocker-report.md`)

The build is **idempotent**: re-running it against the same
vault produces the same indexes (same row counts, same
embeddings for the same model).

## 4.0 What `codex-vault-benchmark` Measures

The benchmark subcommand runs a fixed set of queries against
the indexes and measures four retrievers:

- **metadata** — direct SQL query against the metadata DB
- **fts5** — full-text search via the FTS5 index
- **vector** — semantic similarity via the LanceDB index
  (skipped if `--no-vector` was used at build time)
- **hybrid** — reciprocal-rank fusion of fts5 + vector

For each (query, retriever) pair, the benchmark records:

- `hit_at_k` — was the expected top-1 result in the top-K?
- `mrr` — mean reciprocal rank of the expected top-1 result
- `ndcg_at_k` — normalized discounted cumulative gain
- `has_blocked` — any blocked content in the top-K?
- `has_unredacted_flagged` — any flagged-but-unredacted
  content in the top-K?

A determinism check re-runs every query twice and compares
FTS+hybrid row hashes; the expected rate is 1.000.

## 5.0 CLI

```bash
codex-vault-benchmark \
    --vault-root /path/to/codex-vault \
    [--quick]
```

| Flag | Purpose |
|---|---|
| `--vault-root` | Path to the Codex Vault root (required) |
| `--quick` | Truncate the QUERIES list to 3 items for smoke testing |

## 6.0 Output Reports

The benchmark writes two reports to
`${VAULT_ROOT}/.runtime/reports/`:

| File | Format | Purpose |
|---|---|---|
| `retrieval-benchmark-results.md` | Markdown | Human-readable summary |
| `retrieval-benchmark-results.json` | JSON | Machine-readable per-query results |
| `index-security-audit.md` | Markdown | Per-source security summary (regenerated) |

The JSON report is the canonical source of truth for any
retrieval-quality regression test. Downstream tooling (CI
dashboards, vault status pages) should consume it.

## 7.0 Performance Notes

- The metadata DB build is I/O-bound; on the existing Codex
  Vault it takes ~15-20 seconds.
- The FTS5 build is also I/O-bound; ~5-10 seconds.
- The vector build is GPU-bound (or CPU-bound if no GPU).
  On the existing vault with `--no-vector`, the entire
  build completes in ~25 seconds.
- The benchmark with the full QUERIES list (40+ queries)
  takes ~5-15 seconds. The `--quick` flag reduces this to
  ~2-5 seconds for smoke testing.

## 8.0 CI Integration

The standard CI pipeline runs:

```bash
codex-vault-validate --vault-root /path/to/codex-vault
codex-vault-build-indexes --vault-root /path/to/codex-vault --no-vector
codex-vault-benchmark --vault-root /path/to/codex-vault --quick
```

All three exit zero on success. CI should fail the build if
any of them returns non-zero. The strict validator's `R01`
rule (malformed YAML/JSON) is the most common failure mode in
CI; it indicates a schema or data corruption.
