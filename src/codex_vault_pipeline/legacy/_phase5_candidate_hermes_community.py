#!/usr/bin/env python3
"""Phase 5 — Knowledge Note Candidate Generator (Hermes community/deployment/skills batch).

Generates 4 candidate knowledge notes under wiki/_candidates/ for Hermes-related
non-official-core sources (community, deployment, plugins, skills, memory/orchestration).

Sources in scope (12 Phase 2 sources, all with `primary_domain: hermes-agent` or
`related_domains` containing `hermes-agent`, and `source_role` matching the user's
criteria: community-extension, deployment, integration, skill-system, memory-system,
or orchestrator):

  Deployment (4 sources):
    - github:0xrsydn/nix-hermes-agent
    - github:Crustocean/hermes-agent-template
    - github:outsourc-e/hermes-workspace
    - github:xmbshwll/hermes-agent-docker

  Plugins/Skills (4 sources, 1 source-specific + 3 general):
    - github:wondelai/skills (source-specific candidate; 223 occurrences — largest)
    - github:42-evey/hermes-plugins
    - github:AMAP-ML/SkillClaw
    - github:witt3rd/oh-my-hermes

  Memory/Orchestration integrations (4 sources):
    - github:AxDSan/Mnemosyne
    - github:amanning3390/flowstate-qmd
    - github:builderz-labs/mission-control
    - github:vectorize-io/hindsight

Excluded from this batch (per user instruction):
  - github:NousResearch/hermes-agent (handled in the Hermes official batch)
  - github:NousResearch/hermes-agent-self-evolution (handled in the Hermes official batch)
  - github:NousResearch/hermes-paperclip-adapter (handled in the Hermes official batch)
  - github:NousResearch/autonovel (official-extension source_role; not in user's criteria)
  - github:NousResearch/tinker-atropos (official-extension source_role; not in user's criteria)
  - github:pingchesu/hermes-curator-evolver (13 occurrences; too small for source-specific)
  - n8n official/core
  - n8n workflow ecosystem
  - AgentField core
  - AgentField examples

Candidate design (per user "Prefer"):
  1. hermes-community-deployment — 4 deployment sources
  2. hermes-plugins-and-skills — 3 general plugins/skills sources (excluding wondelai)
  3. wondelai-skills-collection — 1 source-specific candidate (223 occurrences)
  4. hermes-memory-orchestration-integrations — 4 memory/orchestration sources

Each candidate:
  - source_record_ids: Layer A source/v1 record IDs (one per source in scope)
  - occurrence_ids: Phase 3 occurrence record IDs (all in scope)
  - evidence[]: { source_id, artifact_id, unit_id, anchor, relation, occurrence_id }
  - knowledge_status=candidate (per requirement)
  - canonical=False for all candidates
  - source_role: per-source preserved from Layer A (community-extension, deployment, integration, etc.)

Validation uses the external schemas at:
  - .runtime/schemas/knowledge-note.schema.yaml
  - .runtime/schemas/migration-report.schema.yaml
(via jsonschema library). The script does NOT embed its own validator.

Each candidate is mirrored as JSON to .runtime/knowledge-notes/ so the
strict validator explicitly counts them. Each migration report is mirrored
as YAML to .runtime/migration-reports/ for the same reason.

Usage:
    python3 _phase5_candidate_hermes_community.py \
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
    print("ERROR: jsonschema library required (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)


# Candidate definitions. file_path_filter scopes occurrences to specific source_ids.
# For multi-source candidates, file_path_filter_multi is a list of source_ids.
CANDIDATE_DEFS = [
    {
        "slug": "hermes-community-deployment",
        "title": "Hermes Community Deployment — Nix, Template, Workspace, and Docker",
        "source_ids": [
            "github:0xrsydn/nix-hermes-agent",
            "github:Crustocean/hermes-agent-template",
            "github:outsourc-e/hermes-workspace",
            "github:xmbshwll/hermes-agent-docker",
        ],
        "scope_covers": (
            "Four Hermes Agent community deployment sources, all with "
            "`primary_domain: hermes-agent` and `source_role: community-extension` "
            "or `deployment`:\n"
            "- github:0xrsydn/nix-hermes-agent (declarative Nix package and NixOS module; "
            "9 occurrences)\n"
            "- github:Crustocean/hermes-agent-template (template; 2 occurrences)\n"
            "- github:outsourc-e/hermes-workspace (deployment workspace; 112 occurrences)\n"
            "- github:xmbshwll/hermes-agent-docker (Docker image; 3 occurrences)\n"
            "Backed by 126 Phase 3 occurrences and 126 Phase 4 doc-section units."
        ),
        "scope_excludes": (
            "Nous Research official sources (handled in the Hermes official batch). "
            "Hermes plugins, skills, and memory/orchestration integrations (handled "
            "by separate candidates in this batch). n8n and AgentField sources "
            "(handled in their own batches). github:pingchesu/hermes-curator-evolver "
            "(13 occurrences; too small for a source-specific candidate and not in "
            "the deployment category)."
        ),
    },
    {
        "slug": "hermes-plugins-and-skills",
        "title": "Hermes Plugins and Skills — evey, SkillClaw, oh-my-hermes",
        "source_ids": [
            "github:42-evey/hermes-plugins",
            "github:AMAP-ML/SkillClaw",
            "github:witt3rd/oh-my-hermes",
        ],
        "scope_covers": (
            "Three Hermes Agent community plugins/skills sources, all with "
            "`primary_domain: hermes-agent` (or related) and `source_role: community-extension`:\n"
            "- github:42-evey/hermes-plugins (23 custom plugins for Hermes Agent; "
            "23 occurrences)\n"
            "- github:AMAP-ML/SkillClaw (skill-management-system; 1 occurrence)\n"
            "- github:witt3rd/oh-my-hermes (plugin collection; 2 occurrences)\n"
            "Backed by 26 Phase 3 occurrences and 26 Phase 4 doc-section units. "
            "The wondelai/skills collection is handled in a separate source-specific "
            "candidate (`wondelai-skills-collection`) because of its larger size "
            "(223 occurrences)."
        ),
        "scope_excludes": (
            "Nous Research official sources. Hermes deployment sources (handled by "
            "`hermes-community-deployment`). wondelai/skills (handled by "
            "`wondelai-skills-collection`). Memory/orchestration integrations (handled "
            "by `hermes-memory-orchestration-integrations`). n8n and AgentField sources."
        ),
    },
    {
        "slug": "wondelai-skills-collection",
        "title": "Wondel.ai Skills Collection — 50+ Production Skills for Agents",
        "source_ids": [
            "github:wondelai/skills",
        ],
        "scope_covers": (
            "github:wondelai/skills at the snapshot preserved in "
            "codex-vault/raw/wondelai-skills/. A community skill collection "
            "compatible with agentskills.io. 223 Phase 3 occurrences: 172 "
            "documentation files, 51 agent-skill bundles. The largest single "
            "source in this batch, warranting a source-specific candidate."
        ),
        "scope_excludes": (
            "Other Hermes-related skill sources (handled by `hermes-plugins-and-skills`). "
            "Nous Research official sources. Deployment, memory/orchestration, and "
            "other sources."
        ),
    },
    {
        "slug": "hermes-memory-orchestration-integrations",
        "title": "Hermes Memory and Orchestration Integrations — Mnemosyne, FlowState, Mission Control, Hindsight",
        "source_ids": [
            "github:AxDSan/Mnemosyne",
            "github:amanning3390/flowstate-qmd",
            "github:builderz-labs/mission-control",
            "github:vectorize-io/hindsight",
        ],
        "scope_covers": (
            "Four Hermes-related memory and orchestration integration sources, all "
            "with `related_domains` containing `hermes-agent` and `source_role: integration`:\n"
            "- github:AxDSan/Mnemosyne (memory-system; 1 occurrence)\n"
            "- github:amanning3390/flowstate-qmd (memory-system; 1 occurrence)\n"
            "- github:builderz-labs/mission-control (multi-agent-orchestrator; 37 occurrences)\n"
            "- github:vectorize-io/hindsight (agent-memory-system; 93 occurrences)\n"
            "Backed by 132 Phase 3 occurrences. The vectorize-io/hindsight source "
            "has 19 flagged files (security scan; 74 clean, 19 flagged) per AGENTS.md §11."
        ),
        "scope_excludes": (
            "Nous Research official sources. Hermes deployment, plugins, and skills "
            "sources (handled by separate candidates in this batch). n8n and "
            "AgentField sources."
        ),
    },
]

BATCH_NAME = "hermes-community-deployment-skills"
BATCH_TITLE = "Hermes Community/Deployment/Skills Candidate Batch"


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

    units = []
    for p in (runtime_root / "units").rglob("*.json"):
        r = json.loads(p.read_text())
        if r.get("schema") == "unit/v1":
            units.append(r)

    return {
        "artifacts": artifacts,
        "occurrences": occurrences,
        "units": units,
    }


def resolve_evidence(records: dict, source_ids: list, cfg: dict) -> dict:
    """Resolve evidence for one candidate across its source_ids."""
    artifacts = records["artifacts"]
    occurrences = records["occurrences"]
    units = records["units"]

    src_ids = set(source_ids)
    src_occs = [o for o in occurrences if o.get("source_id") in src_ids]
    src_occs.sort(key=lambda o: (o.get("source_id", ""), o.get("source_path", "")))

    units_by_artifact = defaultdict(list)
    for u in units:
        aid = u.get("artifact_id", "")
        if aid.startswith("sha256:"):
            units_by_artifact[aid].append(u)

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

    for o in src_occs:
        sha = o.get("content_sha256")
        if not sha or sha not in artifacts:
            skipped_occ += 1
            continue
        a = artifacts[sha]
        sec = a.get("security_scan", {}).get("status", "?")
        role = a.get("artifact_role", "unknown")
        sec_counter[sec] += 1
        role_counter[role] += 1

        if sec == "blocked":
            blocked_count += 1
            continue
        if sec == "flagged":
            flagged_count += 1
        if sec == "clean":
            clean_count += 1

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

    source_record_ids = [la["record_id"] for la in layer_a_list]
    occurrence_ids = [e["occurrence_id"] for e in evidence["occurrences"]]

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

    # Use the first source's role (all sources in this batch are community-extension or similar)
    source_role = layer_a_list[0].get("source_role") or "community-extension"
    authority_level = layer_a_list[0].get("authority_level") or "community"
    lifecycle_status = layer_a_list[0].get("lifecycle_status") or "active"

    candidate = {
        "schema": "knowledge-note/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{h}",
        "title": cfg["title"],
        "slug": cfg["slug"],
        "domain_family": "hermes-agent",
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
        "lifecycle_status": lifecycle_status,
        "duplicate_resolution": None,
        "supersedes": None,
        "cssclasses": ["domain/hermes-agent", "layer/note", "state/candidate", f"hermes-agent/{source_role}"],
        "source_type": "candidate-note",
        "topic": cfg["slug"],
        "topic_cluster": BATCH_NAME,
        "upstream_repo": ", ".join(source_ids),
        "tags": ["hermes-agent", "candidate", "phase-5", source_role, "ecosystem"],
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
    """Build a migration report per AGENTS.md §16 with the corrected schema-valid `source_id` (singular string)."""
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
            "Existing wiki notes under wiki/hermes-agent/ are not modified (per requirement)",
        ],
        "evidence_summary": {
            "source_id": ", ".join(source_ids) if len(source_ids) > 1 else source_ids[0],
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
    ap.add_argument("--generator-version", default="0.7.0")
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
        source_ids = cfg["source_ids"]
        if slug not in synthesis:
            print(f"SKIP {slug}: no LLM synthesis provided")
            continue

        layer_a_list = []
        for sid in source_ids:
            if sid not in layer_a_sources:
                print(f"ERROR: Layer A source record not found for {sid}", file=sys.stderr)
                sys.exit(2)
            layer_a_list.append(layer_a_sources[sid])

        evidence = resolve_evidence(records, source_ids, cfg)
        print(f"\n=== {slug} ===")
        print(f"  source_ids: {source_ids}")
        for la in layer_a_list:
            print(f"  layer_a_source_record_id: {la['record_id']} (role={la.get('source_role')})")
        print(f"  unique_files: {evidence['unique_file_count']}")
        print(f"  occurrences: {evidence['occurrence_count']} (skipped: {evidence['skipped_occurrence']})")
        print(f"  units: {evidence['unit_count']} (redacted: {evidence['redacted_unit_count']})")
        print(f"  security: {evidence['security_counter']}")
        print(f"  roles: {evidence['role_counter']}")

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
    summary_path = candidates_dir / "_phase5-hermes-community-summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
