# Codex Vault Pipeline Agent Instructions

**Pipeline root:** `/Users/admin1/agent-brain/codex-vault-pipeline/`  
**Vault root:** `/Users/admin1/agent-brain/codex-vault/`  
**Vault runtime:** `/Users/admin1/agent-brain/codex-vault/.runtime/`

This repo contains reusable tooling for Codex Vault ingestion, validation, indexing, benchmarking, schema handling, and deterministic extraction.

The vault repo and the pipeline repo are separate Git repositories. Never treat `/Users/admin1/agent-brain/` as a Git repository.

---

## 1. Git Boundary

Before every commit, run:

```bash
git rev-parse --show-toplevel
```

It must return:

```text
/Users/admin1/agent-brain/codex-vault-pipeline
```

Never commit:

```text
raw/
.runtime/
*.db
*.sqlite
*.sqlite3
LanceDB / vector indexes
embeddings
backups
caches
.env or secrets
```

Commit only reviewed pipeline code, schemas, tests, docs, examples, and this `AGENTS.md`.

---

## 2. Task Modularity

Large tasks must be split into small, reviewable stages.

Do not combine:

```text
schema changes
ingest runs
validation repairs
index rebuilds
benchmark updates
candidate promotion
Obsidian graph projection
pipeline refactors
commits and pushes
```

Use one main writer. Optional helper agents may inspect and report, but must not edit files unless explicitly authorized.

Apply one small patch, run tests, then continue.

For long-running work, report:

```text
CURRENT_STAGE=
FILES_CHANGED=
TESTS=
NEXT_STEP=
```

If a task requires a large generated script, broad refactor, or cross-repo rewrite, stop and ask before writing.

---

## 3. Source of Truth

Pipeline code lives under:

```text
src/codex_vault_pipeline/
```

Tests live under:

```text
tests/
```

Reusable examples live under:

```text
examples/
```

Schemas may be mirrored in multiple locations. When changing schemas, keep mirrors byte-identical and report checksums.

Do not write machine runtime data into this repo.

---

## 4. Pipeline Principles

Prefer deterministic Python over LLM-generated processing.

Reusable logic belongs in small importable modules, not one-off `legacy/_phase*.py` scripts.

Legacy scripts may remain for reproducibility, but new shared behavior should move into stable modules.

Do not rewrite working legacy scripts in a broad pass. Extract one shared concern at a time.

Current preferred extraction order:

```text
1. file policy / media type / binary detection
2. checkpoints
3. source-local validation
4. report generation
5. batch runner
6. CLI wrappers
```

---

## 5. Ingest Safety

Ingest must run source-by-source.

Required order:

```text
1. acquire source
2. write source record
3. create artifacts
4. create occurrences
5. validate artifacts and occurrences
6. extract units
7. validate unit-to-artifact links
8. generate candidate note
9. generate migration report
10. source-local validation
11. checkpoint complete
```

Stop on first invariant failure unless explicitly asked for bulk diagnosis.

No unit may reference a missing or excluded artifact.

No candidate note may be generated before source-local validation passes.

---

## 6. Validation and Tests

Before committing code changes, run the relevant smallest test first.

For general pipeline changes:

```bash
python -m pytest
python -m compileall src/codex_vault_pipeline
```

For schema changes, also run strict vault validation if cheap:

```bash
codex-vault-validate --vault-root /Users/admin1/agent-brain/codex-vault --strict
```

Do not report `VALIDATED` unless the applicable tests and validations passed.

---

## 7. Completion Report

Every task must end with:

```text
FINAL_STATUS=
FILES_CHANGED=
TESTS=
VALIDATION=
COMMIT=
NEXT_STEP=
```

Allowed final statuses:

```text
VALIDATED | PARTIAL | FAILED | BLOCKED
```
