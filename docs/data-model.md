# Data Model

The Codex Vault data model is a five-layer hierarchy. The
pipeline produces and consumes records at every layer except
Layer E (which is owner-managed). This document is the
authoritative reference for the record schemas and their
relationships.

## 1.0 The Five Layers

```
  Layer A: Source           one record per upstream repo / website / dataset
       ↓
  Layer B: Artifact         one record per unique content sha256
       ↓
  Layer C: Occurrence       one record per (source, path) — provenance
       ↓
  Layer C: Bundle / Unit    one record per multi-file operational asset
       ↓
  Layer D: Domain Record    one record per typed n8n / hermes-skill / etc.
       ↓
  Layer E: Knowledge Note   one record per human-readable synthesis
                            (wiki article / candidate note)
```

The pipeline writes Layers A through D and validates Layer E
(read-only) via the strict 20-rule validator.

## 2.0 Layer A — Source

**Schema:** `schemas/source.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/sources/<encoded_source_id>/source.v1.yaml`

A source record is the immutable declaration of an upstream
repository or dataset. The pipeline creates exactly **one**
source record per ingest. Key fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `source_id` | string | yes | `github:<owner>/<repo>` for GitHub |
| `resolved_commit` | sha256 | yes | pinned at ingest time |
| `tree_sha` | sha256 | yes | the resolved revision's tree |
| `primary_domain` | enum | yes | `vocab-primary-domain.yaml` |
| `source_role` | enum | yes | `vocab-source-role.yaml` |
| `authority_level` | enum | yes | `vocab-authority-level.yaml` |
| `lifecycle_status` | enum | yes | `vocab-lifecycle-status.yaml` |
| `acquisition.acquired_files` | int | yes | equals `expected_files` minus `.git/` |
| `coverage.coverage_ratio` | float | yes | must equal 1.0 for `complete` status |

## 3.0 Layer B — Artifact

**Schema:** `schemas/artifact.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/artifacts/<sha256:content_hash>.json`

An artifact is a unique content blob, keyed by `content_sha256`.
Identical files from different sources collapse to a single
artifact record (deduplication). Key fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `artifact_id` | `sha256:<content_hash>` | yes | equals the content hash |
| `content_sha256` | hex | yes | the actual file content hash |
| `media_type` | string | yes | MIME (e.g. `text/python`) |
| `artifact_role` | enum | yes | `vocab-artifact-role.yaml` |
| `size_bytes` | int | yes | file size |
| `parse_status` | enum | yes | `valid`, `invalid`, `empty` |
| `security_status` | enum | yes | `clean`, `flagged`, `blocked` |
| `index_policy` | enum | yes | `include`, `metadata-only`, `exclude` |

## 4.0 Layer C — Occurrence

**Schema:** `schemas/artifact-occurrence.schema.yaml` (older form)
**Storage:** `${VAULT_ROOT}/.runtime/occurrences/<encoded_source_id>/<sha256>.json`

An occurrence is the (source, path) → artifact join. Each
unique path in each source has one occurrence record.
Key fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `occurrence_id` | `sha256:<hash>` | yes | `sha256(sha256(source_id\|source_path))` |
| `source_id` | string | yes | must match a Layer A `source_id` |
| `source_path` | string | yes | vault-relative path |
| `artifact_id` | `sha256:...` | yes | must match a Layer B `artifact_id` |
| `content_sha256` | hex | yes | must match the artifact's |
| `redacted` | bool | yes | true iff artifact is `flagged` |
| `redaction_reason` | string | no | populated iff `redacted` |

## 5.0 Layer C — Unit

**Schema:** `schemas/unit.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/units/<kind>/<source_id>/<sha256>.json`

A unit is a retrieval chunk extracted from an artifact. Units
are what the retrieval benchmark and the FTS index operate on.
The pipeline's `build_indexes.py` populates them.

Unit kinds:

- `doc-section` — a heading-anchored slice of a documentation
  file
- `configuration` — a parsed YAML/JSON/TOML config
- `deployment-component` — a Dockerfile, docker-compose, Helm
  chart, or Nix expression
- `script-and-supporting` — a runnable script plus its
  surrounding support files
- `hermes-skill`, `hermes-soul`, `n8n-workflow` — domain-specific
  unit kinds

## 6.0 Layer D — Domain Record

**Schema:** `schemas/domain-record.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/domain/<kind>/<sha256>.json`

A domain record is a typed, business-meaningful extraction. The
two main kinds are `n8n-workflow` (the importable workflow JSON)
and `hermes-skill` (the `SKILL.md` and its companions). The
pipeline produces these during phase 4.

## 7.0 Layer E — Knowledge Note (read-only)

**Schema:** `schemas/knowledge-note.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/knowledge-notes/<slug>.json`
            and `${VAULT_ROOT}/wiki/<domain>/<slug>.md` (mirrors)

A knowledge note is the human-readable synthesis produced by an
LLM (or a careful human). The pipeline **never** writes these;
the owner does. The pipeline **validates** them via the strict
20-rule validator and **indexes** them in the FTS index for
retrieval.

## 8.0 Relations

**Schema:** `schemas/relation.schema.yaml`
**Storage:** `${VAULT_ROOT}/.runtime/relations/*.yaml`

Relations are first-class typed edges between records. The
controlled vocabulary is in `schemas/vocab-relation.yaml`:

`part-of | extends | integrates-with | wraps | deploys | forks |
mirrors | documents | examples-for | depends-on | implements |
replaces | supersedes | duplicate-of | variant-of | derived-from |
references`

The pipeline's `phase6_schema_correction.py` ensures every
candidate's `source_taxonomy[]` block resolves against the
Layer A record. Future relations will be validated by the
strict validator's `R14` rule (no orphan relation targets).

## 9.0 Vocabularies

Eight controlled vocabularies back the schema enums. They live
in `schemas/vocab-*.yaml` and are loaded by the strict
validator at startup. Any new controlled value must be added
to the appropriate vocab file *and* a candidate that uses it
must be re-validated.

## 10.0 Where the Pipeline Touches Each Layer

| Layer | Pipeline writes? | Pipeline reads? |
|---|---|---|
| A Source | yes (ingest) | yes (build-indexes, benchmark) |
| B Artifact | yes (ingest) | yes (validate, build-indexes) |
| C Occurrence | yes (ingest) | yes (validate, build-indexes) |
| C Unit | yes (build-indexes) | yes (benchmark) |
| D Domain Record | yes (ingest) | yes (validate, build-indexes) |
| E Knowledge Note | **NO** | yes (validate, build-indexes) |
| Relation | no (out of scope) | yes (validate) |
