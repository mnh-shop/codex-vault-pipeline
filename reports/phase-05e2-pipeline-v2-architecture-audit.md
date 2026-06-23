# Phase 05E2 Pipeline v2 Architecture Audit

**Date:** 2026-06-23
**Auditor:** Orchestrator + @oracle
**Scope:** codex-vault-pipeline v2, n8n workflow catalog scanner, reingest safety
**Status:** UPDATED — credential semantics bug fixed, corrected counts confirmed
**Previous status:** PRE-COMMIT AUDIT — DO NOT COMMIT

---

## Executive Summary

The codex-vault-pipeline v2 is a clean, well-structured deterministic processing layer. The v2 context packer, pack index, and retrieval policy are production-quality. The n8n workflow catalog scanner (Phase 05E2) originally had a **critical semantic bug** — `security_flagged` conflated credential references with actual secrets — but this has been **fixed and verified**.

**Updated Go/No-Go:** 05E2 is now safe to commit after the credential semantics correction. The scanner correctly distinguishes between credential references (normal n8n behavior) and actual secret values. 557 tests pass (36 new scanner tests), dry-run verified on real vault (3,774 files, 3,162 workflows).

### Corrected Dry-Run Counts (post-fix)
| Metric | Value |
|--------|-------|
| Files scanned | 3,774 |
| Workflows | 3,162 |
| Metadata files | 605 |
| Invalid JSON | 7 |
| Duplicate hashes | 5 |
| With credentials | 2,339 |
| Credential refs only | 2,238 |
| Potential secret leak | 789 |
| Security clean | 135 |
| With AI components | 1,575 |

### What Changed (fix)
- `credential_security_flag` (bool) deprecated as derived property
- New fields: `credential_type_present`, `credential_reference_present`, `credential_value_present`, `secret_value_detected`
- `security_state` enum: `clean` | `credential_refs_only` | `potential_secret_leak` | `blocked` | `not_scanned`
- Placeholder detection added (e.g., `YOUR_API_KEY_HERE` does not trigger leak)
- False positive patterns added (short tokens, common words, domain names, HTML entities)
- CLI print updated to show corrected counts

---

## 1. Repository Layout

### Package Structure
```
src/codex_vault_pipeline/
├── __init__.py
├── __main__.py
├── cli.py                  # Main CLI entrypoint (1580 lines)
├── cli/                    # CLI subpackages
├── extractors/             # Content extractors
├── graph/                  # Obsidian graph projection
├── index/                  # Legacy index builders
├── ingest/                 # Ingest pipeline (batch, checkpoints)
├── legacy/                 # 32 legacy scripts (v1)
├── paths.py                # VaultPaths resolution (288 lines)
├── query/                  # Query interfaces
├── schemas/                # Schema definitions
├── utils/                  # Shared utilities
└── v2/                     # v2 repo-context lane (12 modules)
```

### v1/v2 Boundaries
- **v1 (legacy/):** 32 scripts for ingestion, validation, indexing, benchmarking, candidate generation. These are the original pipeline scripts.
- **v2 (v2/):** 12 modules for the repo-context lane: Repomix adapter, pack index (SQLite/FTS5), context packer, retrieval policy, deepwiki sanity, n8n coverage, n8n workflow catalog, config, manifest, context pack schema.
- **Boundary:** v2 is additive, not a replacement. v1 scripts remain for reproducibility. v2 writes to `.runtime/indexes/v2/` (separate from v1's `.runtime/indexes/`).

### CLI Entrypoints
- `codex-vault` → `cli.py:main()` (registered in pyproject.toml)
- Subcommands: validate, ingest, build-indexes, benchmark, ingest-batch, ingest-status, query-units, env, paths, vector, **v2**
- v2 sub-actions: doctor, repomix, deepwiki, n8n (coverage, catalog), retrieval, context, packs

### Test Layout
- `tests/` — 550 tests (521 existing + 29 new for n8n scanner)
- pytest with cov, anyio plugins
- Tests cover: CLI, v2 modules, path resolution, pack index, context packer, retrieval policy, n8n catalog

### Runtime/Output Conventions
- All machine data under `.runtime/` (gitignored)
- v2 index: `.runtime/indexes/v2/repo-packs.sqlite`
- v2 reports: `.runtime/reports/v2/`
- n8n catalog: `.runtime/domain/n8n-workflows/` (new from 05E2)
- Legacy index: `.runtime/db/codex-vault.db`, `.runtime/indexes/codex-vault-fts.db`, `.runtime/indexes/codex-vault-vectors/`

---

## 2. Data Flow

### Inputs
- **raw/** — Acquired source repositories (42 repos, frozen)
- **.runtime/sources/** — Source records (Layer A)
- **.runtime/artifacts/** — Artifact records (Layer B)
- **.runtime/occurrences/** — Occurrence records
- **.runtime/units/** — Extracted units (Layer C)
- **.runtime/domain/** — Domain records (Layer D)
- **.runtime/bundles/** — Bundle records

### Pipeline Stages
```
raw/ → Repomix → repo-packs/ → pack_index (SQLite/FTS5) → context_packer → context packs
                                    ↓
                              n8n_workflow_catalog → catalog.jsonl
                                    ↓
                              n8n_coverage → coverage report
```

### v2 Data Flow (Detailed)
1. **Repomix** scans raw repo directories, produces `output.md` packs (markdown format)
2. **Pack index** parses Repomix output into `packs`, `pack_files`, `pack_chunks` tables + FTS5 index
3. **Context packer** queries FTS5, applies source routing rules, ranks results, selects items within token budget
4. **Context pack** output: JSON or markdown with provenance, source coverage, artifact roles, warnings

### Runtime Outputs
- `.runtime/indexes/v2/repo-packs.sqlite` — 42 packs, 22,931 files, 133,880 chunks
- `.runtime/domain/n8n-workflows/catalog.jsonl` — 3,767 workflow entries (05E2, uncommitted)
- `.runtime/domain/n8n-workflows/summary.json` — Aggregate stats
- `.runtime/domain/n8n-workflows/validation_report.json` — Per-file errors

### Tracked Vault Outputs
- `wiki/` — Human-readable knowledge notes (Obsidian)
- `60-sources/` — Source catalogs and maps (post-restructure)
- `20-domains/`, `30-concepts/`, `40-use-cases/`, `50-guides/`, `10-mocs/` — JD-structured vault

---

## 3. Deterministic Stages

### Acquisition
- **Assumption:** raw/ directories contain acquired source repos
- **Status:** 42 repos acquired, 2 excluded (Alibaba-NLP/DeepResearch 100+MB, nusquama/n8nworkflows.xyz 57+MB)
- **Policy:** raw/ is frozen. No reacquisition.

### Validation
- **v1:** `legacy/validate.py` — schema validation, hash checks, coverage arithmetic
- **v2:** `v2/deepwiki_sanity.py` — DeepWiki output sanity checks
- **Pack index:** Schema validation on insert (FOREIGN KEY constraints, explicit column names)

### Source Routing
- **v2 context packer** has 5 routing rules (AgentField, n8n-docs, OSINT, memory systems, coding agents)
- Each rule: regex pattern → preferred source_ids + boost value
- Boost applied during scoring: `_compute_source_routing_bonus()`

### Chunking
- **Repomix** produces markdown packs with `## File:` headers
- **Pack index** parses into chunks: split by markdown headings, then by size (MAX_CHUNK_CHARS = 8000)
- Token estimate: chars / 4 (rough)

### Metadata Extraction
- **Pack index** classifies artifact roles: readme, generated_catalog, n8n_workflow, skill, soul, code, docs, config, other
- **Priority mapping:** high (n8n_workflow, skill, soul), low (readme, generated_catalog), normal (rest)
- **File flags:** is_readme, is_generated_catalog, is_workflow_json, is_skill_file, is_soul_file, is_code_file

### Catalog Generation (05E2)
- **Scanner** walks 4 raw n8n directories, classifies JSON files, extracts 22 fields
- **Classification:** workflow | metadata | invalid | unknown
- **Duplicate detection:** SHA-256 content hash
- **Output:** catalog.jsonl, summary.json, validation_report.json

### Context Pack Generation
- **Input:** FTS5 query results from pack index
- **Scoring:** base_score (FTS rank) + role_bonus + priority_bonus - demotion_penalty + source_routing_bonus
- **Selection:** greedy within token budget, per-source limit (15), per-file limit (5), max items (50)
- **Output:** ContextPack with items, source coverage, artifact role summary, warnings

### Benchmarks/Tests
- **v2 tests:** 550 passed, 3 skipped
- **Source routing benchmark:** 14/16 (87.5%) — still current
- **v2 pack index:** 42 packs, 133,880 chunks — still current

---

## 4. Retrieval/Search Logic

### Exact Lookup
- **SQLite** queries on `packs`, `pack_files`, `pack_chunks` tables
- **Source ID** filtering: `WHERE source_id = ?`
- **Path** filtering: `WHERE path = ?`
- **Artifact role** filtering: `WHERE artifact_role = ?`

### SQLite/FTS/BM25
- **FTS5 virtual table:** `pack_chunks_fts` over chunk text
- **Query processing:** Split query into words, escape FTS5 special chars, join with OR
- **Ranking:** FTS5 built-in BM25 rank (lower = better)
- **Snippet generation:** `snippet(pack_chunks_fts, 4, '<b>', '</b>', '...', 64)`

### Semantic/Vector Search
- **Legacy:** LanceDB with `all-MiniLM-L6-v2` embeddings (384 dims)
- **v2:** NOT YET IMPLEMENTED — retrieval policy declares `vector_for_semantic_search: true` but no v2 vector index exists
- **Legacy vector search:** `legacy/retrieval.py` → `vector_search()` using LanceDB

### v2 Pack Retrieval
- **Primary method:** FTS5 search via `pack_index.search_fts()`
- **Scoring:** `_compute_score()` combines FTS rank, role bonuses, priority, demotion, source routing
- **Selection:** `_select_items()` applies token budget, per-source/file limits

### Source Routing Benchmark
- **5 routing rules** defined in `context_packer.py`
- **Benchmark:** 14/16 queries correctly routed (87.5%)
- **Rules:** AgentField (+0.5), n8n-docs (+0.5), OSINT (+0.4), Memory (+0.5), Coding agents (+0.3)

### Context Pack Selection
- **Algorithm:** Greedy by score descending
- **Constraints:** max_tokens (8000), max_items (50), per_source_limit (15), per_file_limit (5)
- **Demotion:** README (-0.4 unless query matches readme terms), generated_catalog (-0.5 unless query matches catalog terms)

### Metadata Filters
- **Mandatory:** `metadata_filters_mandatory: true` in retrieval policy
- **Available filters:** source_id, artifact_role, priority_class, safety_status

### Hybrid/Fusion Logic
- **Not implemented in v2.** Retrieval policy declares hybrid as candidate (Haystack pattern).
- **Legacy:** `retrieval.py` has `hybrid_search()` combining FTS + vector results

---

## 5. Semantic Search Details

### Where Embeddings Are Created
- **Legacy:** `build_indexes.py` → ` SentenceTransformer("all-MiniLM-L6-v2")` → `all-MiniLM-L6-v2`
- **Embedding scope:** All units from `.runtime/units/**/*.json`
- **Batch size:** 128
- **Write target:** `.runtime/indexes/codex-vault-vectors/` (LanceDB)

### What Text Is Embedded
- **Units** extracted from artifacts: markdown sections, code symbols, workflow nodes, skill instructions
- **NOT embedded:** Raw source files, generated catalogs, READMEs (by policy: `readme_low_priority`, `generated_catalog_low_priority`)

### Generated Content Dominance Control
- **Retrieval policy:** `generated_catalog_low_priority: true`, `exclude_from_centrality: true`
- **Context packer:** Demotion penalty (-0.5) for generated catalogs unless query matches catalog terms
- **No explicit dedup** between generated catalogs and source content in semantic index

### Where LanceDB Is Written
- `.runtime/indexes/codex-vault-vectors/` — LanceDB database
- Table name: derived from unit source (not inspected in detail)

### Provenance in Semantic Results
- **Legacy vector search:** Returns raw LanceDB rows with source_id, artifact_id, candidate slug
- **v2:** No vector search yet — provenance is preserved in pack_chunks table (source_id, path, chunk_id)

### What Feeds Semantic Search
- **Legacy:** `.runtime/units/` → embedded → LanceDB
- **v2:** `.runtime/indexes/v2/repo-packs.sqlite` → FTS5 only (no vector)

---

## 6. Current v2 State

### v2 Context Packer
- **Status:** Production-quality, tested
- **Features:** FTS5 search, source routing, scoring, token-budget selection, provenance preservation
- **Output:** JSON or markdown context packs

### Pack Index
- **42 packs** indexed (full source set)
- **22,931 files** indexed
- **133,880 chunks** indexed
- **FTS5** virtual table for text search
- **Schema:** packs, pack_files, pack_chunks, pack_chunks_fts, pack_index_runs

### Source Routing Benchmark
- **14/16 (87.5%)** — still current
- **5 routing rules** covering AgentField, n8n-docs, OSINT, memory systems, coding agents

### Excluded Repos
- **Alibaba-NLP/DeepResearch** — 100+ MB (too large)
- **nusquama/n8nworkflows.xyz** — 57+ MB (too large, but partial acquisition exists in raw/)

### What Has Changed Since Vault Restructure
- **Vault reorganized** from flat wiki/ to JD structure (60-sources/, 20-domains/, 30-concepts/, etc.)
- **v2 pipeline is UNAFFECTED** — it reads from raw/ and .runtime/, not wiki/
- **Legacy scripts** reference wiki/ paths extensively but are not used for v2 operations
- **Graph projection** still references wiki/_graph/ but is not part of v2

---

## 7. Reingest/Rebuild Safety

### Hardcoded Paths to Old Wiki Layout
- **paths.py:** `wiki_root = root / "wiki"` — still points to wiki/ (correct, wiki/ still exists)
- **legacy/*.py:** 100+ references to `wiki/`, `wiki/_candidates/`, `wiki/n8n/`, `wiki/hermes-agent/`, `wiki/agentfield/`
- **v2 modules:** NO references to wiki/ — clean

### Assumptions About Source Notes Under wiki/
- **Legacy scripts** assume candidate notes live under `wiki/_candidates/`
- **Legacy scripts** assume source notes live under `wiki/n8n/`, `wiki/hermes-agent/`, etc.
- **v2 modules** do NOT read from wiki/ — they read from raw/ and .runtime/

### Assumptions About 60-sources Not Existing
- **v2 modules** have NO references to 60-sources/ — safe
- **Legacy scripts** have NO references to 60-sources/ — safe (they predate the restructure)

### Assumptions About raw/ Source IDs
- **Pack index** uses source_id format: `github:org/repo` or `local:path`
- **n8n scanner** uses source_slug format: `n8n-workflows`, `n8nworkflows-xyz`, etc.
- **No hardcoded source IDs** in v2 modules — safe

### Runtime Output Location Assumptions
- **v2 config:** `runtime_root = vault_root / ".runtime"` — correct
- **v2 index:** `.runtime/indexes/v2/` — correct
- **n8n catalog:** `.runtime/domain/n8n-workflows/` — correct
- **No conflicts** with legacy output locations

### Would Clean Reingest Overwrite Human-Curated Notes?
- **v2 pipeline:** NO — it reads from raw/ and writes to .runtime/ only
- **Legacy pipeline:** POTENTIALLY — legacy scripts write to wiki/_candidates/ which could conflict with promoted notes
- **Recommendation:** Do NOT run legacy clean reingest without verifying wiki/ state

### raw/ Freeze Policy
- **Enforced in code:** v2 modules only READ from raw/, never write
- **Legacy scripts:** Some modify raw/ metadata files — but not v2
- **Safe:** v2 operations respect raw/ freeze

### .runtime Output Reproducibility
- **v2 pack index:** Deterministic (same raw/ → same SQLite)
- **v2 context packs:** Deterministic for same query + index state
- **n8n catalog:** Deterministic (same raw/ → same catalog.jsonl)
- **Legacy indexes:** NOT fully deterministic (LanceDB embeddings depend on model version)

### Would Old Indexes Become Stale After 05C/05D/05E1?
- **v2 pack index:** STALE — built before vault restructure, but still valid (reads from raw/, not wiki/)
- **Legacy indexes:** STALE — built before vault restructure, may reference old wiki/ paths
- **Recommendation:** Rebuild v2 pack index after any raw/ changes (none since restructure)

---

## 8. n8n Scanner Audit

### Credential References vs Actual Secret Values
**CRITICAL BUG:** The scanner conflates credential references with actual secrets.

Current behavior:
- `_detect_credential_security()` scans raw JSON text for patterns: `credentials`, `password`, `secret`, `api_key`, `webhook_url`, `bearer`, `authorization`
- ANY match sets `credential_security_flag = true`
- **Result:** 2,913 workflows flagged as "security flagged" — almost all false positives

**The problem:** Every n8n workflow that uses credentials has a `"credentials"` field in its node configuration. This is NORMAL n8n behavior, not a security risk. Example:
```json
{
  "type": "n8n-nodes-base.slack",
  "credentials": {
    "slackApi": {
      "id": "123",
      "name": "My Slack Account"
    }
  }
}
```
This contains `"credentials"` and would trigger the security flag — but it's just a reference, not a leaked secret.

### Whether security_flagged Is Overbroad
**FIXED.** The `security_flagged` field has been replaced with a proper `security_state` enum that distinguishes between:
- `clean` — no credentials or secrets (135 workflows)
- `credential_refs_only` — has credential references but no actual values (2,238 workflows)
- `potential_secret_leak` — has actual API key/token/bearer patterns (789 workflows)
- `blocked` — content blocked from indexing (0 workflows)
- `not_scanned` — not scanned (0 workflows)

### Required Distinction
**IMPLEMENTED.** The scanner now distinguishes between:
- `credential_type_present`: Workflow references a credential type (e.g., `"credentials": {"id": "..."}`)
- `credential_reference_present`: Workflow has a credential reference object (normal n8n pattern)
- `credential_value_present`: Workflow contains actual credential VALUES (e.g., `"apiKey": "sk-..."`)
- `secret_value_detected`: Actual secret values embedded in workflow JSON
- `security_state`: `clean` | `credential_refs_only` | `potential_secret_leak` | `blocked` | `not_scanned`

### Whether Partial Acquisition Is Represented
**Partially.** The scanner counts files per source but doesn't track:
- Expected total files per source
- Whether acquisition was complete/partial/failed
- Coverage ratio

### Whether Counts Are Scoped to Acquired Files
**YES** — counts are based on files found in raw/, not expected totals. This is correct for a catalog of acquired files.

### Whether Invalid JSON Is Safely Reported
**YES** — invalid JSON files are classified as `invalid`, counted, and errors are logged in validation_report.json. No crash, no data loss.

### Whether metadata_json Classification Is Reliable
**MOSTLY** — the `_classify_json()` function correctly identifies `metada-*.json` files from n8nworkflows-xyz as metadata. However, it also checks for `user_name`/`user_username` keys as a fallback, which could misclassify non-metadata files that happen to have those keys.

### Whether Duplicate Detection Is Content-Only or Normalized
**Content-only** — SHA-256 of raw file bytes. No normalization (whitespace, key ordering). Two identical workflows with different whitespace would NOT be detected as duplicates. This is acceptable for a catalog.

### Whether Connection Counting Handles n8n Version Differences
**NOT APPLICABLE** — the scanner doesn't count connections. It counts nodes, node types, trigger types, and AI components. Connection structure varies across n8n versions but is not analyzed.

### Whether Output Schema Matches 05E1 Design
**MOSTLY** — the 05E1 design specified 22 fields. The scanner implements all of them. The credential fields need correction (see below).

### Whether Tests Cover These Distinctions
**YES** — 36 tests cover the credential reference vs value distinction, including:
- `test_credential_ref_not_secret_leak` — credential references are NOT flagged
- `test_google_sheets_credential_refs` — Google Sheets OAuth2 refs produce `credential_refs_only`
- `test_clean_workflow` — no credentials produce `clean`
- `test_actual_api_key_detected` — actual API keys trigger `potential_secret_leak`
- `test_placeholder_not_secret` — placeholder values do NOT trigger leak
- `test_secret_never_in_output` — secret values never appear in catalog output
- `test_summary_distinguishes_refs_from_leaks` — summary counts are correct
- `test_security_state_enum_values` — only valid enum values used

---

## 9. Major Risks / Bugs

| # | Risk | Severity | Status | Description |
|---|------|----------|--------|-------------|
| 1 | ~~**Credential semantics bug**~~ | ~~**BLOCKER**~~ | **FIXED** | ~~`security_flagged` conflates credential references with actual secrets.~~ Fixed: `security_state` enum with proper distinction. 557 tests pass. |
| 2 | **No v2 vector search** | **HIGH** | OPEN | Retrieval policy declares `vector_for_semantic_search: true` but no v2 vector index exists. FTS-only retrieval misses semantic similarity. |
| 3 | **Legacy wiki/ references** | **MEDIUM** | OPEN | 100+ hardcoded `wiki/` paths in legacy scripts. Not a v2 issue, but blocks legacy reingest safety. |
| 4 | **Pack index staleness** | **MEDIUM** | OPEN | v2 pack index was built before vault restructure. Still valid (reads raw/) but may be missing recent changes. |
| 5 | **n8n scanner no coverage tracking** | **MEDIUM** | OPEN | Scanner counts acquired files but doesn't track expected totals or acquisition completeness. |
| 6 | **Token estimation roughness** | **LOW** | OPEN | `chars / 4` is a rough estimate. Could cause context packs to exceed or underfill token budgets. |
| 7 | **FTS query fragility** | **LOW** | OPEN | FTS5 query splits on whitespace, skips short words (<3 chars). Queries with technical terms may lose precision. |
| 8 | **No content normalization for dedup** | **LOW** | OPEN | SHA-256 of raw bytes misses semantic duplicates with different formatting. |
| 9 | **metadata classification fallback** | **LOW** | OPEN | `user_name`/`user_username` key detection could misclassify non-metadata files. |
| 10 | **Graph projection stale** | **LOW** | OPEN | `graph/` module references `wiki/_graph/` which may not exist after restructure. Not used by v2. |

---

## 10. Recommended Repair Plan

### Before Committing 05E2 — COMPLETED
1. ~~**Fix credential semantics** (BLOCKER)~~ — **DONE.** Replaced `_detect_credential_security()` with proper distinction:
   - `credential_type_present`: Check for `"credentials"` key in node objects
   - `credential_reference_present`: Check for `"credentials": {"id": ..., "name": ...}` pattern
   - `credential_value_present`: Check for actual values matching `sk-`, `ghp_`, `xoxb-`, etc.
   - `secret_value_detected`: High-entropy strings in credential positions
   - `security_state`: `clean` | `credential_refs_only` | `potential_secret_leak` | `blocked` | `not_scanned`
   - Placeholder detection added (e.g., `YOUR_API_KEY_HERE` does not trigger leak)
   - False positive patterns added (short tokens, common words, domain names, HTML entities)
2. ~~**Update tests** to verify credential references are NOT flagged as secrets~~ — **DONE.** 36 tests pass.
3. ~~**Re-run dry-run** to get accurate security counts~~ — **DONE.** 3,774 files scanned, 3,162 workflows, 789 potential leaks (corrected from 2,913 false positives).

### Remaining
- Commit 05E2 (scanner + tests + CLI + report)
- v2 vector search implementation (separate project)
- Legacy script cleanup (not v2 scope)
- Token estimation improvement (low priority)
- Content normalization for dedup (low priority)

### Validation
- Re-run `uv run pytest` after credential fix
- Re-run `codex-vault v2 n8n catalog --vault-root ...` for accurate counts
- Verify catalog.jsonl field counts match 05E1 schema

### Vault-Only (Not Pipeline)
- 60-sources/ catalog notes are vault content, not pipeline
- Wiki notes are human-curated, not pipeline output
- Graph projection is Obsidian config, not pipeline

### Pipeline-Only
- n8n_workflow_catalog.py scanner logic
- CLI integration
- Tests

### Before Any Clean Reingest
- Verify wiki/ state (which notes are promoted, which are candidates)
- Verify .runtime/ state (which indexes are current)
- Run v2 pack index validation
- Do NOT run legacy clean reingest without explicit approval

---

## Appendix: Pipeline Architecture Diagram

```
                    ┌─────────────────────────────────────────┐
                    │              raw/ (frozen)               │
                    │  42 repos, 4 n8n workflow corpora        │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │         Repomix (v2 adapter)             │
                    │  Scans raw/, produces output.md packs    │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │      Pack Index (SQLite/FTS5)            │
                    │  42 packs, 22,931 files, 133,880 chunks │
                    │  .runtime/indexes/v2/repo-packs.sqlite   │
                    └──────────────┬──────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼─────────┐  ┌──────▼──────┐  ┌─────────▼─────────┐
    │  Context Packer    │  │  n8n Catalog │  │  Retrieval Policy  │
    │  FTS5 search +     │  │  Scanner     │  │  Routing rules +   │
    │  scoring + select  │  │  3,767 files │  │  demotion policies │
    └─────────┬─────────┘  └──────┬──────┘  └─────────┬─────────┘
              │                    │                    │
    ┌─────────▼─────────┐  ┌──────▼──────┐  ┌─────────▼─────────┐
    │  Context Pack      │  │  catalog    │  │  Policy file       │
    │  JSON/Markdown     │  │  .jsonl     │  │  (documentation)   │
    │  with provenance   │  │  summary    │  │                    │
    └───────────────────┘  │  .json      │  └───────────────────┘
                           └─────────────┘
```

---

**Report path:** `/Users/admin1/agent-brain/codex-vault-pipeline/reports/phase-05e2-pipeline-v2-architecture-audit.md`
