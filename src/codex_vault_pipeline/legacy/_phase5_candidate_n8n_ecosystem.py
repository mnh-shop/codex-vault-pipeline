#!/usr/bin/env python3
"""Phase 5 — Knowledge Note Candidate Generator (n8n ecosystem batch).

Generates 7 candidate knowledge notes under wiki/_candidates/ for the n8n
ecosystem workflow/template sources (community + example-collection):

  Per-source overview/catalog candidates (1 each):
    1. n8n-workflows-zie619              — github:Zie619/n8n-workflows (2065 valid workflows)
    2. n8n-workflows-awesome-n8n-templates — github:enescingoz/awesome-n8n-templates (171 valid)
    3. n8n-workflows-nusquama             — github:nusquama/n8nworkflows.xyz (371 valid + 605 metadata)
    4. n8n-workflows-wassupjay            — github:wassupjay/n8n-free-templates (202 valid)
    5. n8n-workflows-czlonkowski-skills   — github:czlonkowski/n8n-skills (15 skills + 73 docs)

  Special candidates:
    6. n8n-workflows-nusquama-partial-coverage — explicit sampled/partial coverage
                                              for nusquama (the nusquama repo has many
                                              metadata JSONs that are not importable
                                              workflows)
    7. n8n-workflow-search-guide — cross-source search-use guide covering
                                    integrations, triggers, AI components, and
                                    common use cases (YouTube, Gmail, Telegram,
                                    AI agent, Google Sheets, webhook, Slack)

The user instruction: "create one overview/catalog candidate per source" +
"one workflow-search guide candidate" + "one partial-coverage candidate for
nusquama/n8nworkflows.xyz". 1 + 1 + 1 + 5 = 7 candidates total (5 sources, plus
the 2 special candidates).

czlonkowski/n8n-skills is included because it has primary_domain: n8n and
source_role: community-extension (matching the user's criteria). The user did
not explicitly exclude it for this batch (it was excluded from the n8n official
batch but matches this batch's criteria). It is not a workflow/template repo
(it's a SKILLS repo) but it is part of the n8n ecosystem.

Excluded from this batch (per user instruction):
  - github:n8n-io/n8n-docs (handled in the official/core batch)

Each candidate:
  - source_record_ids: Layer A source/v1 record IDs (NOT occurrence IDs)
  - occurrence_ids: Phase 3 occurrence record IDs (separately)
  - evidence[]: { source_id, artifact_id, unit_id, anchor, relation, occurrence_id }
  - knowledge_status=candidate (per requirement; never promoted automatically)
  - canonical=False for all candidates
  - source_role: official-extension|community-extension (preserved from Layer A)
  - evidence references Phase 4 workflow/domain/unit records with occurrence_id
  - workflow claims preserve path to importable original JSON
  - blocked content is excluded; flagged content uses only redacted-safe metadata

Validation uses the external schema at .runtime/schemas/knowledge-note.schema.yaml
(via jsonschema library). The script does NOT embed its own validator.

Each candidate is mirrored as JSON to .runtime/knowledge-notes/ so the
strict validator explicitly counts them. Each migration report is mirrored
as YAML to .runtime/migration-reports/ for the same reason.

Usage:
    python3 _phase5_candidate_n8n_ecosystem.py \
        --llm-synthesis <path-to-llm-synthesis.json> \
        [--runtime-root PATH] [--vault-root PATH] \
        [--run-id ID]
"""
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema library required", file=sys.stderr)
    sys.exit(2)


# Candidate definitions. Per-source overview/catalog candidates get the
# entire source as their evidence scope. The search-guide candidate uses
# all 4 workflow-collection/workflow-catalog sources. The partial-coverage
# candidate focuses on nusquama's non-workflow JSONs.
CANDIDATE_DEFS = [
    # 1. Zie619 overview/catalog
    {
        "slug": "n8n-workflows-zie619",
        "title": "n8n Workflows — Zie619/n8n-workflows Collection",
        "source_id": "github:Zie619/n8n-workflows",
        "scope": "source",
        "scope_covers": (
            "github:Zie619/n8n-workflows at the snapshot preserved in "
            "codex-vault/raw/n8n-workflows/. Covers the 2,065 valid n8n-workflow "
            "JSON files (out of 2,088 total occurrences) plus 23 documentation "
            "files. The collection is the largest n8n community workflow "
            "repository. Backed by 2,088 Phase 3 occurrences and 2,064 Phase 4 "
            "domain-record/v1 (1 blocked content excluded per AGENTS.md §11)."
        ),
        "scope_excludes": (
            "Other n8n ecosystem sources (covered by their own candidates in "
            "this batch and the n8n official/core batch). n8n-io/n8n-docs is "
            "the official source and is handled in the n8n official/core batch."
        ),
    },
    # 2. awesome-n8n-templates overview/catalog
    {
        "slug": "n8n-workflows-awesome-n8n-templates",
        "title": "n8n Workflows — enescingoz/awesome-n8n-templates Collection",
        "source_id": "github:enescingoz/awesome-n8n-templates",
        "scope": "source",
        "scope_covers": (
            "github:enescingoz/awesome-n8n-templates at the snapshot preserved in "
            "codex-vault/raw/awesome-n8n-templates/. Covers 171 valid n8n-workflow "
            "JSON files, 136 configuration JSONs (mostly n8n meta stubs), and 22 "
            "documentation files. The collection is curated and themed (Telegram, "
            "OpenAI, Google Drive, Slack, etc.). Backed by 329 Phase 3 occurrences "
            "and 169 Phase 4 domain-record/v1."
        ),
        "scope_excludes": (
            "Other n8n ecosystem sources (covered by their own candidates in "
            "this batch and the n8n official/core batch)."
        ),
    },
    # 3. nusquama overview/catalog (mix of workflows, metadata, configuration)
    {
        "slug": "n8n-workflows-nusquama",
        "title": "n8n Workflows — nusquama/n8nworkflows.xyz Catalog",
        "source_id": "github:nusquama/n8nworkflows.xyz",
        "scope": "source",
        "scope_covers": (
            "github:nusquama/n8nworkflows.xyz at the snapshot preserved in "
            "codex-vault/raw/n8nworkflows-xyz/. Covers 371 valid n8n-workflow "
            "JSON files, 605 metadata JSONs (package listings), 224 configuration "
            "JSONs (n8n meta stubs), and a small number of documentation files. "
            "The repo is a workflow catalog with many directory trees. Backed by "
            "1,200 Phase 3 occurrences and 369 Phase 4 domain-record/v1."
        ),
        "scope_excludes": (
            "Other n8n ecosystem sources (covered by their own candidates in "
            "this batch and the n8n official/core batch). The detailed "
            "partial-coverage analysis for nusquama is in a separate candidate "
            "(n8n-workflows-nusquama-partial-coverage)."
        ),
    },
    # 4. nusquama partial-coverage candidate (explicit sampled/partial coverage)
    {
        "slug": "n8n-workflows-nusquama-partial-coverage",
        "title": "n8n Workflows — nusquama/n8nworkflows.xyz Partial Coverage",
        "source_id": "github:nusquama/n8nworkflows.xyz",
        "scope": "partial-coverage",
        "scope_covers": (
            "Partial-coverage analysis of github:nusquama/n8nworkflows.xyz. Of "
            "1,200 Phase 3 occurrences: 371 are valid n8n-workflow JSONs "
            "(domain-record/v1 emitted), 605 are metadata JSONs (package "
            "listings, not importable), 224 are configuration JSONs (n8n meta "
            "stubs), and 0 are blocked. The 605 metadata JSONs and 224 "
            "configuration JSONs are not importable as workflows; they are "
            "preserved in raw/ but not extracted as domain records. This "
            "candidate explicitly states the sampled/partial coverage."
        ),
        "scope_excludes": (
            "The general overview/catalog of nusquama (covered by "
            "n8n-workflows-nusquama). Other n8n ecosystem sources. The "
            "n8n-workflows-nusquama candidate does the high-level catalog; "
            "this candidate focuses specifically on the coverage breakdown."
        ),
    },
    # 5. wassupjay overview/catalog
    {
        "slug": "n8n-workflows-wassupjay",
        "title": "n8n Workflows — wassupjay/n8n-free-templates Collection",
        "source_id": "github:wassupjay/n8n-free-templates",
        "scope": "source",
        "scope_covers": (
            "github:wassupjay/n8n-free-templates at the snapshot preserved in "
            "codex-vault/raw/n8n-free-templates/. Covers 202 valid n8n-workflow "
            "JSON files and 25 documentation files. The collection focuses on "
            "AI agent workflows (heavy use of langchain nodes). Backed by 227 "
            "Phase 3 occurrences and 202 Phase 4 domain-record/v1."
        ),
        "scope_excludes": (
            "Other n8n ecosystem sources (covered by their own candidates in "
            "this batch and the n8n official/core batch)."
        ),
    },
    # 6. czlonkowski/n8n-skills overview/catalog
    {
        "slug": "n8n-workflows-czlonkowski-skills",
        "title": "n8n Skills — czlonkowski/n8n-skills Bundle",
        "source_id": "github:czlonkowski/n8n-skills",
        "scope": "source",
        "scope_covers": (
            "github:czlonkowski/n8n-skills at the snapshot preserved in "
            "codex-vault/raw/n8n-skills/. Covers 15 agent-skill bundles "
            "(SKILL.md entrypoints with siblings) and 73 documentation files. "
            "The repo is a workflow-authoring-skills collection, not a workflow "
            "collection. Backed by 88 Phase 3 occurrences."
        ),
        "scope_excludes": (
            "Other n8n ecosystem sources (covered by their own candidates in "
            "this batch and the n8n official/core batch). This repo is a "
            "SKILLS bundle, not a workflows/templates collection; it is "
            "included because the user's criteria (primary_domain: n8n, "
            "source_role: community-extension) matches it."
        ),
    },
    # 7. Cross-source workflow search guide
    {
        "slug": "n8n-workflow-search-guide",
        "title": "n8n Workflow Search Guide — Integrations, Triggers, and Use Cases",
        "source_ids_multi": [
            "github:Zie619/n8n-workflows",
            "github:enescingoz/awesome-n8n-templates",
            "github:nusquama/n8nworkflows.xyz",
            "github:wassupjay/n8n-free-templates",
        ],
        "scope": "search-guide",
        "scope_covers": (
            "Cross-source search-use guide for the 4 n8n workflow/template "
            "collections (Zie619, enescingoz, nusquama, wassupjay). Aggregates "
            "extracted integrations, triggers, AI components, and common use "
            "cases (YouTube, Gmail, Telegram, AI agent, Google Sheets, webhook, "
            "Slack) from all 4 sources. Backed by ~3,830 Phase 3 occurrences "
            "and ~2,814 Phase 4 domain-record/v1 across the 4 sources."
        ),
        "scope_excludes": (
            "The 5 per-source overview/catalog candidates (which give deeper "
            "detail on each source). The n8n-skills repo (czlonkowski) is not "
            "a workflow collection and is not included in this search guide. "
            "The official n8n-docs source is handled in the n8n official/core "
            "batch."
        ),
    },
]

BATCH_NAME = "n8n-ecosystem"
BATCH_TITLE = "n8n Ecosystem Workflow/Template Candidate Batch"


def load_layer_a_source_records(runtime_root: Path) -> dict:
    """Load all source/v1 records (Layer A) from .runtime/sources/."""
    sources = {}
    sources_dir = runtime_root / "sources"
    if not sources_dir.exists():
        return sources
    for p in sources_dir.rglob("source.v1.yaml"):
        try:
            r = yaml.safe_load(p.read_text())
            if isinstance(r, dict) and r.get("schema") == "source/v1":
                sources[r["source_id"]] = {
                    "record_id": r["record_id"],
                    "content_hash": r.get("content_hash"),
                    "source_path": str(p.relative_to(runtime_root.parent)),
                    "source_role": r.get("source_role"),
                    "authority_level": r.get("authority_level"),
                    "lifecycle_status": r.get("lifecycle_status"),
                }
        except Exception as e:
            print(f"WARN: failed to load {p}: {e}", file=sys.stderr)
    return sources


def load_records(runtime_root: Path) -> dict:
    """Load all relevant records into in-memory indices."""
    artifacts = {}
    for p in (runtime_root / "artifacts").glob("*.json"):
        r = json.loads(p.read_text())
        artifacts[r["content_sha256"]] = r

    occurrences = []
    for p in (runtime_root / "occurrences").rglob("*.json"):
        r = json.loads(p.read_text())
        occurrences.append(r)

    domain_records = []
    for p in (runtime_root / "domain").rglob("*.json"):
        r = json.loads(p.read_text())
        if r.get("schema") == "domain-record/v1":
            domain_records.append(r)

    units = []
    for p in (runtime_root / "units").rglob("*.json"):
        r = json.loads(p.read_text())
        if r.get("schema") == "unit/v1":
            units.append(r)

    return {
        "artifacts": artifacts,
        "occurrences": occurrences,
        "domain_records": domain_records,
        "units": units,
    }


def resolve_evidence(records: dict, cfg: dict) -> dict:
    """Resolve evidence for one candidate.

    For scope=source: all occurrences and units for the given source_id.
    For scope=partial-coverage: occurrences with artifact_role != n8n-workflow
      (i.e., the non-importable metadata + configuration JSONs).
    For scope=search-guide: all occurrences and units across source_ids_multi.
    """
    artifacts = records["artifacts"]
    occurrences = records["occurrences"]
    units = records["units"]
    domain_records = records["domain_records"]

    if cfg.get("scope") == "search-guide":
        source_ids = set(cfg["source_ids_multi"])
    else:
        source_ids = {cfg["source_id"]}

    # Filter occurrences
    src_occs = [o for o in occurrences if o.get("source_id") in source_ids]
    src_occs.sort(key=lambda o: o.get("source_path", ""))

    # For partial-coverage, also filter by artifact_role
    if cfg.get("scope") == "partial-coverage":
        src_occs = [o for o in src_occs if artifacts.get(o["content_sha256"], {}).get("artifact_role") != "n8n-workflow"]

    # Index: content_sha256 -> unit list
    units_by_artifact = defaultdict(list)
    for u in units:
        aid = u.get("artifact_id", "")
        if aid.startswith("sha256:"):
            units_by_artifact[aid].append(u)

    # Index: content_sha256 -> domain record list
    domain_by_artifact = defaultdict(list)
    for d in domain_records:
        sha = d.get("content_sha256")
        if sha:
            domain_by_artifact[sha].append(d)

    # Phase 4 unit records
    # We also need to find phase 4 units for the workflow/domain records
    occ_evidence = []
    unit_evidence = []
    sec_counter = Counter()
    role_counter = Counter()
    skipped_occ = 0
    redacted_unit_count = 0
    total_unit_count = 0
    unique_paths = set()
    blocked_count = 0
    flagged_count = 0
    clean_count = 0

    # Per-artifact type counts (for partial-coverage)
    valid_workflows = 0
    metadata_json = 0
    config_json = 0
    invalid_json = 0
    unknown_json = 0

    # Need to re-classify JSONs into valid_workflows/metadata/config/invalid/unknown
    # For valid_workflows: have a domain-record (artifact_role = n8n-workflow)
    # For metadata: artifact_role = metadata (605 in nusquama)
    # For config: artifact_role = configuration (224 in nusquama, plus awesome has 136)
    # For invalid: phase 3 had parse_status=invalid
    # For unknown: phase 3 had structural check as unknown

    for o in src_occs:
        sha = o.get("content_sha256")
        if not sha or sha not in artifacts:
            skipped_occ += 1
            continue
        a = artifacts[sha]
        role = a.get("artifact_role", "unknown")
        sec = a.get("security_scan", {}).get("status", "?")
        sec_counter[sec] += 1
        role_counter[role] += 1

        if sec == "blocked":
            blocked_count += 1
            continue
        if sec == "flagged":
            flagged_count += 1
        if sec == "clean":
            clean_count += 1

        # JSON type classification
        if role == "n8n-workflow":
            valid_workflows += 1
        elif role == "metadata":
            metadata_json += 1
        elif role == "configuration":
            config_json += 1
        elif role == "documentation":
            pass  # documentation is in scope but not a workflow; not counted as valid_workflows
        else:
            unknown_json += 1

        unique_paths.add(o["source_path"])
        occ_evidence.append({
            "source_id": o["source_id"],
            "source_path": o["source_path"],
            "occurrence_id": o["occurrence_id"],
            "content_sha256": sha,
            "artifact_id": a["artifact_id"],
            "security_status": sec,
            "role": role,
        })
        # Collect units
        for u in units_by_artifact.get(f"sha256:{sha}", []):
            total_unit_count += 1
            if u.get("redacted"):
                redacted_unit_count += 1
            unit_evidence.append({
                "unit_id": u.get("unit_id"),
                "unit_type": u.get("unit_type"),
                "title": u.get("title"),
                "artifact_id": u.get("artifact_id"),
                "source_anchor": u.get("source_anchor"),
                "occurrence_id": o["occurrence_id"],
                "source_path": o["source_path"],
                "source_id": o["source_id"],
                "redacted": u.get("redacted", False),
                "semantic_text_preview": (u.get("semantic_text") or "")[:300] if not u.get("redacted") else "",
            })

    return {
        "occurrences": occ_evidence,
        "unit_evidence": unit_evidence,
        "unique_file_count": len(unique_paths),
        "occurrence_count": len(occ_evidence),
        "unit_count": total_unit_count,
        "redacted_unit_count": redacted_unit_count,
        "security_counter": dict(sec_counter),
        "skipped_occurrence": skipped_occ,
        "blocked_count": blocked_count,
        "flagged_count": flagged_count,
        "clean_count": clean_count,
        "role_counter": dict(role_counter),
        "valid_workflows": valid_workflows,
        "metadata_json": metadata_json,
        "config_json": config_json,
        "invalid_json": invalid_json,
        "unknown_json": unknown_json,
    }


def build_candidate(
    source_ids: list,
    layer_a_list: list,
    cfg: dict,
    evidence: dict,
    llm_synthesis: dict,
    run_id: str,
    generator_version: str,
    now: str,
) -> dict:
    """Build a single candidate knowledge note."""
    # source_record_ids: Layer A for all relevant sources
    source_record_ids = [la["record_id"] for la in layer_a_list]
    # occurrence_ids: Phase 3 occurrences in scope
    occurrence_ids = [e["occurrence_id"] for e in evidence["occurrences"]]

    # Evidence: 25 items max, each with source_id, artifact_id, unit_id, anchor, relation, occurrence_id
    evidence_list = []
    for ue in evidence["unit_evidence"][:25]:
        anchor = ue.get("source_anchor", {})
        anchor_str = f"{anchor.get('section', '?')}:L{anchor.get('line_start', '?')}-{anchor.get('line_end', '?')}"
        evidence_list.append({
            "source_id": ue.get("source_id"),
            "artifact_id": ue.get("artifact_id"),
            "unit_id": ue.get("unit_id"),
            "anchor": anchor_str,
            "relation": "documents",
            "occurrence_id": ue.get("occurrence_id"),
        })

    coverage_ratio = 1.0
    if evidence["occurrence_count"] + evidence["skipped_occurrence"] > 0:
        coverage_ratio = evidence["occurrence_count"] / (evidence["occurrence_count"] + evidence["skipped_occurrence"])

    placeholder_body = {
        "title": cfg["title"],
        "slug": cfg["slug"],
        "summary": llm_synthesis.get("summary", ""),
        "llm_synthesis": llm_synthesis,
        "evidence_list": evidence_list,
    }
    body_bytes = json.dumps(placeholder_body, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()

    # source_role: use the first source's role (all community-extension in this batch)
    source_role = layer_a_list[0].get("source_role") or "community-extension"
    authority_level = layer_a_list[0].get("authority_level") or "community"

    candidate = {
        "schema": "knowledge-note/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{h}",
        "title": cfg["title"],
        "slug": cfg["slug"],
        "domain_family": "n8n",
        "knowledge_status": "candidate",
        "scope": {
            "covers": cfg["scope_covers"],
            "excludes": cfg["scope_excludes"],
        },
        "summary": llm_synthesis.get("summary", ""),
        "source_record_ids": source_record_ids,
        "occurrence_ids": occurrence_ids,
        "evidence": evidence_list,
        "created_at": now,
        "last_verified_at": now,
        "generator": "codex-vault/phase-5-knowledge-candidate",
        "generator_version": generator_version,
        "run_id": run_id,
        "content_hash": f"sha256:{h}",
        "source_role": source_role,
        "authority_level": authority_level,
        "lifecycle_status": "active",
        "duplicate_resolution": None,
        "supersedes": None,
        "cssclasses": ["domain/n8n", "layer/note", "state/candidate", f"n8n/{source_role}"],
        "source_type": "candidate-note",
        "topic": cfg["slug"],
        "topic_cluster": BATCH_NAME,
        "upstream_repo": ", ".join(source_ids),
        "tags": ["n8n", "candidate", "phase-5", source_role, "ecosystem"],
        "source_paths": sorted({e["source_path"] for e in evidence["occurrences"]})[:50],
        "source_count": evidence["occurrence_count"],
        "coverage_ratio": round(coverage_ratio, 4),
        "coverage_status": "complete" if coverage_ratio >= 0.999 else ("partial" if coverage_ratio > 0 else "unknown"),
        "acquisition": {
            "status": "complete" if evidence["blocked_count"] == 0 and evidence["skipped_occurrence"] == 0 else "partial",
            "expected_files": evidence["occurrence_count"] + evidence["skipped_occurrence"] + evidence["blocked_count"],
            "acquired_files": evidence["occurrence_count"],
            "failed_files": 0,
            "excluded_files": evidence["blocked_count"] + evidence["skipped_occurrence"],
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "canonical": False,
        "synthesis_provenance": {
            "summary_model": llm_synthesis.get("model", "minimax-m3"),
            "summary_run_id": run_id,
            "summary_generated_at": now,
            "summary_method": "LLM synthesis over Phase 2-4 evidence; deterministic metadata and evidence resolution produced by Python",
        },
        "coverage_notes": llm_synthesis.get("coverage_notes", ""),
        "unresolved_claims": llm_synthesis.get("unresolved_claims", []),
        "body_markdown": llm_synthesis.get("body_markdown", ""),
    }
    return candidate


def render_candidate_markdown(c: dict) -> str:
    """Render the candidate as a markdown file with YAML frontmatter."""
    fm = {
        "schema": c["schema"],
        "schema_version": c["schema_version"],
        "record_id": c["record_id"],
        "title": c["title"],
        "slug": c["slug"],
        "domain_family": c["domain_family"],
        "knowledge_status": c["knowledge_status"],
        "scope": c["scope"],
        "summary": c["summary"],
        "source_record_ids": c["source_record_ids"],
        "occurrence_ids": c["occurrence_ids"],
        "evidence": c["evidence"],
        "created_at": c["created_at"],
        "last_verified_at": c["last_verified_at"],
        "generator": c["generator"],
        "generator_version": c["generator_version"],
        "run_id": c["run_id"],
        "content_hash": c["content_hash"],
        "source_role": c["source_role"],
        "authority_level": c["authority_level"],
        "lifecycle_status": c["lifecycle_status"],
        "duplicate_resolution": c["duplicate_resolution"],
        "supersedes": c["supersedes"],
        "cssclasses": c["cssclasses"],
        "source_type": c["source_type"],
        "topic": c["topic"],
        "topic_cluster": c["topic_cluster"],
        "upstream_repo": c["upstream_repo"],
        "tags": c["tags"],
        "source_paths": c["source_paths"],
        "source_count": c["source_count"],
        "coverage_ratio": c["coverage_ratio"],
        "coverage_status": c["coverage_status"],
        "acquisition": c["acquisition"],
        "canonical": c["canonical"],
        "synthesis_provenance": c["synthesis_provenance"],
        "coverage_notes": c["coverage_notes"],
        "unresolved_claims": c["unresolved_claims"],
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = c["body_markdown"] or "(no body content)"
    return f"---\n{fm_text}---\n\n# {c['title']}\n\n{body}\n"


def build_migration_report(
    slug: str,
    source_ids: list,
    layer_a_list: list,
    evidence: dict,
    candidate: dict,
    run_id: str,
    now: str,
    generator_version: str,
) -> dict:
    """Build a migration report per AGENTS.md §16. Includes all required machine-record fields."""
    body = {
        "schema": "migration-report/v1",
        "schema_version": "1.0.0",
        "candidate_slug": slug,
        "run_id": run_id,
        "generated_at": now,
        "old_note": None,
        "candidate_note": f"wiki/_candidates/{slug}.md",
        "preserved_sections": [],
        "removed_sections": [],
        "new_evidence_links": [
            {
                "source_id": e.get("source_id"),
                "artifact_id": e.get("artifact_id"),
                "unit_id": e.get("unit_id"),
                "anchor": e.get("anchor"),
                "relation": e.get("relation"),
                "occurrence_id": e.get("occurrence_id"),
            }
            for e in candidate["evidence"]
        ],
        "unresolved_claims": candidate.get("unresolved_claims", []),
        "validation_status": "pending",
        "validation_notes": "Validated externally against .runtime/schemas/knowledge-note.schema.yaml via jsonschema library",
        "promotion_eligible": False,
        "promotion_blockers": [
            "knowledge_status=candidate (per Phase 5 requirement; not yet validated)",
            "Coverage is per Phase 2-4 evidence; no benchmarks run",
            "Existing wiki notes under wiki/n8n/ are not modified (per requirement)",
        ],
        "evidence_summary": {
            "source_ids": source_ids,
            "layer_a_source_record_ids": [la["record_id"] for la in layer_a_list],
            "unique_file_count": evidence.get("unique_file_count", 0),
            "artifact_count": evidence["occurrence_count"],
            "occurrence_count": evidence["occurrence_count"],
            "unit_count": evidence["unit_count"],
            "redacted_unit_count": evidence["redacted_unit_count"],
            "security_counter": evidence["security_counter"],
            "blocked_excluded": evidence["blocked_count"],
            "flagged_redacted": evidence["flagged_count"],
            "clean_count": evidence["clean_count"],
            "role_counter": evidence.get("role_counter", {}),
            "valid_workflows": evidence.get("valid_workflows", 0),
            "metadata_json": evidence.get("metadata_json", 0),
            "config_json": evidence.get("config_json", 0),
            "invalid_json": evidence.get("invalid_json", 0),
            "unknown_json": evidence.get("unknown_json", 0),
        },
        "provenance": {
            "deterministic_metadata": "Python (this script)",
            "llm_synthesis_model": candidate["synthesis_provenance"]["summary_model"],
            "llm_synthesis_run_id": candidate["synthesis_provenance"]["summary_run_id"],
        },
    }
    body_bytes = json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()
    body["record_id"] = f"sha256:{h}"
    body["content_hash"] = f"sha256:{h}"
    body["created_at"] = now
    body["generator"] = "codex-vault/phase-5-migration-report"
    body["generator_version"] = generator_version
    return body


def validate_against_schema(candidate: dict, schema: dict) -> list:
    """Validate a candidate against the external knowledge-note schema using jsonschema."""
    errors = []
    try:
        validator = Draft202012Validator(schema)
        for err in validator.iter_errors(candidate):
            errors.append(f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}")
    except Exception as e:
        errors.append(f"schema validation failed: {e}")
    return errors


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""))
    ap.add_argument("--llm-synthesis", required=True)
    ap.add_argument("--run-id", default="phase-5-2026-06-20")
    ap.add_argument("--generator-version", default="0.6.0")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    vault = Path(args.vault_root)
    now = datetime.now(timezone.utc).isoformat()

    synthesis = json.loads(Path(args.llm_synthesis).read_text())

    layer_a_sources = load_layer_a_source_records(runtime)
    print(f"Loaded {len(layer_a_sources)} Layer A source/v1 records")

    records = load_records(runtime)
    print(f"Loaded records: {len(records['artifacts'])} artifacts, "
          f"{len(records['occurrences'])} occurrences, "
          f"{len(records['domain_records'])} domain, "
          f"{len(records['units'])} units")

    schema_path = runtime / "schemas" / "knowledge-note.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    print(f"Loaded external schema from: {schema_path}")

    candidates_dir = vault / "wiki" / "_candidates"
    migration_dir = candidates_dir / "_migration"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    migration_dir.mkdir(parents=True, exist_ok=True)

    runtime_kn_dir = runtime / "knowledge-notes"
    runtime_mig_dir = runtime / "migration-reports"
    runtime_kn_dir.mkdir(parents=True, exist_ok=True)
    runtime_mig_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for cfg in CANDIDATE_DEFS:
        slug = cfg["slug"]
        if slug not in synthesis:
            print(f"SKIP {slug}: no LLM synthesis provided")
            continue

        if cfg.get("scope") == "search-guide":
            source_ids = cfg["source_ids_multi"]
        else:
            source_ids = [cfg["source_id"]]

        # Get Layer A for each source
        layer_a_list = []
        for sid in source_ids:
            if sid not in layer_a_sources:
                print(f"ERROR: Layer A source record not found for {sid}", file=sys.stderr)
                sys.exit(2)
            layer_a_list.append(layer_a_sources[sid])

        evidence = resolve_evidence(records, cfg)
        print(f"\n=== {slug} ===")
        print(f"  source_ids: {source_ids}")
        for la in layer_a_list:
            print(f"  layer_a_source_record_id: {la['record_id']}")
        print(f"  scope: {cfg.get('scope')}")
        print(f"  unique_files: {evidence['unique_file_count']}")
        print(f"  occurrences: {evidence['occurrence_count']} (skipped: {evidence['skipped_occurrence']})")
        print(f"  units: {evidence['unit_count']} (redacted: {evidence['redacted_unit_count']})")
        print(f"  security: {evidence['security_counter']}")
        print(f"  roles: {evidence['role_counter']}")
        if cfg.get("scope") == "partial-coverage" or cfg.get("scope") == "search-guide":
            print(f"  valid_workflows: {evidence.get('valid_workflows')}")
            print(f"  metadata_json: {evidence.get('metadata_json')}")
            print(f"  config_json: {evidence.get('config_json')}")
            print(f"  invalid_json: {evidence.get('invalid_json')}")
            print(f"  unknown_json: {evidence.get('unknown_json')}")

        candidate = build_candidate(
            source_ids, layer_a_list, cfg, evidence, synthesis[slug],
            args.run_id, args.generator_version, now
        )
        candidates.append(candidate)

        out = candidates_dir / f"{slug}.md"
        out.write_text(render_candidate_markdown(candidate))
        print(f"  Wrote: {out}")

        kn_mirror = runtime_kn_dir / f"{slug}.json"
        kn_mirror.write_text(json.dumps(candidate, sort_keys=True, indent=2))
        print(f"  Wrote: {kn_mirror}")

        report = build_migration_report(slug, source_ids, layer_a_list, evidence, candidate, args.run_id, now, args.generator_version)
        report_yaml = yaml.safe_dump(report, sort_keys=False, allow_unicode=True, default_flow_style=False)
        mig_path = migration_dir / f"{slug}-migration.yaml"
        mig_path.write_text(report_yaml)
        print(f"  Wrote: {mig_path}")

        mig_mirror = runtime_mig_dir / f"{slug}-migration.yaml"
        mig_mirror.write_text(report_yaml)
        print(f"  Wrote: {mig_mirror}")

    print("\n=== Validation against external knowledge-note.schema.yaml ===")
    valid = 0
    invalid = 0
    for c in candidates:
        errors = validate_against_schema(c, schema)
        if errors:
            invalid += 1
            print(f"  FAIL: {c['slug']}:")
            for e in errors[:5]:
                print(f"    - {e}")
        else:
            valid += 1
            print(f"  OK:   {c['slug']}")
    print(f"\nValid: {valid}, Invalid: {invalid}")

    summary = {
        "batch_name": BATCH_NAME,
        "batch_title": BATCH_TITLE,
        "run_id": args.run_id,
        "generated_at": now,
        "candidate_count": len(candidates),
        "valid_count": valid,
        "invalid_count": invalid,
        "candidates": [c["slug"] for c in candidates],
    }
    summary_path = candidates_dir / "_phase5-n8n-ecosystem-summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
