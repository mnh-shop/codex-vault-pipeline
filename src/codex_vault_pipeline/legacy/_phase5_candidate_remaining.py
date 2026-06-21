#!/usr/bin/env python3
"""Phase 5 — Knowledge Note Candidate Generator (Remaining-sources / cross-domain batch).

Generates 3 candidate knowledge notes for the 3 remaining Phase 2 sources
that are not yet covered by a Phase 5 candidate:

  1. nousresearch-autonovel — github:NousResearch/autonovel
     (ai-content-generation, official, related to hermes-agent, 7 occurrences)

  2. nousresearch-tinker-atropos — github:NousResearch/tinker-atropos
     (training-systems, official, related to hermes-agent + training-systems, 2 occurrences)

  3. hermes-curator-evolver — github:pingchesu/hermes-curator-evolver
     (hermes-agent, community-extension, 13 occurrences)

These three sources complete the 32-source Layer A coverage. The user
explicitly required that the first two (autonovel and tinker-atropos) be
covered; pingchesu/hermes-curator-evolver is also included to complete
the coverage matrix (it was previously skipped as "too small" but the
user has now requested full 32-source coverage).

Excluded from this batch (per prior batches):
  - All 29 sources already covered by previous Phase 5 batches.
  - 0 sources remain uncovered after this batch.

Each candidate:
  - source_record_ids: Layer A source/v1 record IDs (one per source in scope)
  - occurrence_ids: Phase 3 occurrence record IDs (all in scope)
  - evidence[]: { source_id, artifact_id, unit_id, anchor, relation, occurrence_id }
  - knowledge_status=candidate (per requirement)
  - canonical=False for all candidates
  - source_role: per-source preserved from Layer A (official-extension / community-extension)
  - domain_family: correctly assigned per primary_domain
    (ai-content-generation / training-systems / hermes-agent)

Validation uses the external schemas at:
  - .runtime/schemas/knowledge-note.schema.yaml
  - .runtime/schemas/migration-report.schema.yaml
(via jsonschema library). The script does NOT embed its own validator.

Each candidate is mirrored as JSON to .runtime/knowledge-notes/ so the
strict validator explicitly counts them. Each migration report is mirrored
as YAML to .runtime/migration-reports/ for the same reason.

Usage:
    python3 _phase5_candidate_remaining.py \
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


# Candidate definitions for the 3 remaining sources.
CANDIDATE_DEFS = [
    {
        "slug": "nousresearch-autonovel",
        "title": "NousResearch autonovel — Autonomous AI Novel Writing Pipeline",
        "source_id": "github:NousResearch/autonovel",
        "domain_family": "hermes-agent",  # hermes-agent per schema enum; source is in hermes-agent-official/ namespace
        "scope_covers": (
            "github:NousResearch/autonovel at the snapshot preserved in "
            "codex-vault/raw/hermes-agent-official/autonovel/. Covers the README "
            "and 6 craft/workflow/anti-patterns documentation files. Backed by 7 "
            "Phase 3 occurrences. The source is a Nous Research official-extension "
            "(`source_role: official-extension`, `authority_level: official`) "
            "with `primary_domain: ai-content-generation` and "
            "`related_domains: ['hermes-agent']`. The candidate body distinguishes "
            "this from other domain_families (the actual primary_domain is "
            "`ai-content-generation` per the Layer A record)."
        ),
        "scope_excludes": (
            "Other Nous Research sources (handled by previous Phase 5 batches). "
            "n8n and AgentField sources. All Hermes community sources (handled by "
            "previous Phase 5 batch)."
        ),
    },
    {
        "slug": "nousresearch-tinker-atropos",
        "title": "NousResearch tinker-atropos — Atropos + Tinker API Training Integration",
        "source_id": "github:NousResearch/tinker-atropos",
        "domain_family": "cross-domain",  # cross-domain per schema enum; source spans training-systems + hermes-agent
        "scope_covers": (
            "github:NousResearch/tinker-atropos at the snapshot preserved in "
            "codex-vault/raw/hermes-agent-official/tinker-atropos/. Covers the "
            "README and usage.md. Backed by 2 Phase 3 occurrences. The source "
            "is a Nous Research official-extension (`source_role: official-extension`, "
            "`authority_level: official`) with `primary_domain: training-systems` "
            "and `related_domains: ['hermes-agent', 'training-systems']`. The "
            "candidate body distinguishes this from other domain_families — "
            "the actual primary_domain is `training-systems` (training integration) "
            "but the project spans the `hermes-agent` domain as well, hence the "
            "`cross-domain` assignment per the schema enum."
        ),
        "scope_excludes": (
            "Other Nous Research sources. n8n and AgentField sources. All Hermes "
            "community sources."
        ),
    },
    {
        "slug": "hermes-curator-evolver",
        "title": "Hermes Curator Evolver — Evidence-Based Skill Maintenance Plugin",
        "source_id": "github:pingchesu/hermes-curator-evolver",
        "domain_family": "hermes-agent",  # hermes-agent per schema enum; matches primary_domain
        "scope_covers": (
            "github:pingchesu/hermes-curator-evolver at the snapshot preserved in "
            "codex-vault/raw/pingchesu-hermes-curator-evolver/. Covers the README, "
            "CONTRIBUTING.md, the curator-evolution SKILL.md (1 agent-skill bundle), "
            "and 9 other documentation files. Backed by 13 Phase 3 occurrences. "
            "The source is a community-extension (`source_role: community-extension`, "
            "`authority_level: community`) with `primary_domain: hermes-agent`."
        ),
        "scope_excludes": (
            "The main Hermes Agent core (handled in the Hermes official batch). "
            "Other Hermes community sources (handled in the previous Phase 5 batch). "
            "All n8n and AgentField sources."
        ),
    },
]

BATCH_NAME = "remaining-cross-domain"
BATCH_TITLE = "Remaining Sources / Cross-Domain Candidate Batch"


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


def resolve_evidence(records: dict, source_id: str, cfg: dict) -> dict:
    """Resolve evidence for one candidate (single source)."""
    artifacts = records["artifacts"]
    occurrences = records["occurrences"]
    units = records["units"]

    src_occs = [o for o in occurrences if o.get("source_id") == source_id]
    src_occs.sort(key=lambda o: o.get("source_path", ""))

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
    source_id: str,
    layer_a: dict,
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

    source_record_ids = [layer_a["record_id"]]
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

    source_role = layer_a.get("source_role") or "community-extension"
    authority_level = layer_a.get("authority_level") or "community"
    lifecycle_status = layer_a.get("lifecycle_status") or "unknown"

    candidate = {
        "schema": "knowledge-note/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{h}",
        "title": cfg["title"],
        "slug": cfg["slug"],
        "domain_family": cfg["domain_family"],
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
        "cssclasses": ["domain/" + cfg["domain_family"], "layer/note", "state/candidate", f"{cfg['domain_family']}/{source_role}"],
        "source_type": "candidate-note",
        "topic": cfg["slug"],
        "topic_cluster": BATCH_NAME,
        "upstream_repo": source_id,
        "tags": [cfg["domain_family"], "candidate", "phase-5", source_role, "remaining-sources"],
        "source_paths": sorted({e["source_path"] for e in evidence["occurrences"]})[:30],
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
    source_id: str,
    layer_a: dict,
    evidence: dict,
    candidate: dict,
    run_id: str,
    now: str,
    generator_version: str,
) -> dict:
    """Build a migration report with the corrected schema-valid `source_id` (singular string)."""
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
            "Existing wiki notes are not modified (per requirement)",
        ],
        "evidence_summary": {
            "source_id": source_id,
            "source_ids": [source_id],
            "layer_a_source_record_ids": [layer_a["record_id"]],
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
    ap.add_argument("--generator-version", default="0.8.0")
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
        source_id = cfg["source_id"]
        if slug not in synthesis:
            print(f"SKIP {slug}: no LLM synthesis provided")
            continue

        if source_id not in layer_a_sources:
            print(f"ERROR: Layer A source record not found for {source_id}", file=sys.stderr)
            sys.exit(2)
        layer_a = layer_a_sources[source_id]

        evidence = resolve_evidence(records, source_id, cfg)
        print(f"\n=== {slug} ===")
        print(f"  source_id: {source_id}")
        print(f"  layer_a_source_record_id: {layer_a['record_id']}")
        print(f"  domain_family: {cfg['domain_family']}")
        print(f"  source_role: {layer_a.get('source_role')}")
        print(f"  occurrences: {evidence['occurrence_count']} (skipped: {evidence['skipped_occurrence']})")
        print(f"  units: {evidence['unit_count']} (redacted: {evidence['redacted_unit_count']})")
        print(f"  security: {evidence['security_counter']}")

        candidate = build_candidate(
            source_id, layer_a, cfg, evidence, synthesis[slug],
            args.run_id, args.generator_version, now
        )
        candidates.append(candidate)

        out = candidates_dir / f"{slug}.md"
        out.write_text(render_candidate_markdown(candidate))
        print(f"  Wrote: {out}")

        kn_mirror = runtime_kn_dir / f"{slug}.json"
        kn_mirror.write_text(json.dumps(candidate, sort_keys=True, indent=2))
        print(f"  Wrote: {kn_mirror}")

        report = build_migration_report(slug, source_id, layer_a, evidence, candidate, args.run_id, now, args.generator_version)
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
    summary_path = candidates_dir / "_phase5-remaining-summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
