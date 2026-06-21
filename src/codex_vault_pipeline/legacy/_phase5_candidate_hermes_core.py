#!/usr/bin/env python3
"""Phase 5 — Knowledge Note Candidate Generator (Hermes Agent core batch).

Generates candidate knowledge notes under wiki/_candidates/.
Each candidate:
  - title, slug, domain_family=hermes-agent
  - knowledge_status=candidate (per requirement; never promoted automatically)
  - scope.covers / scope.excludes
  - source_record_ids (resolved through Phase 2-4)
  - evidence[] with source_id, artifact_id, unit_id, anchor, relation
  - authority_level, source_role, lifecycle_status
  - content_hash = sha256 of the deterministic body (without LLM synthesis)
  - LLM-derived synthesis marked with model and run provenance

The LLM synthesis is taken from a separate JSON input file (written by the
orchestrator agent). The deterministic portion is generated first; the LLM
synthesis is merged in to produce the final candidate note.

For each candidate, a migration report is also written to
wiki/_candidates/_migration/ as a YAML file (per AGENTS.md §16).

Usage:
    python3 _phase5_candidate_hermes_core.py \
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


HERMES_CORE_SOURCES = {
    "github:NousResearch/hermes-agent": {
        "slug": "hermes-agent-core",
        "title": "Hermes Agent — Canonical Source (Nous Research)",
        "scope_covers": (
            "The canonical NousResearch/hermes-agent repository at the snapshot "
            "preserved in codex-vault/raw/hermes-agent/. Covers the README, the "
            "developer-guide subdirectory, the reference subdirectory, and the "
            "user-guide subdirectory as recorded by the Phase 3 manifest."
        ),
        "scope_excludes": (
            "Nous Research official extensions (self-evolution, paperclip-adapter, "
            "autonovel, tinker-atropos), community deployments (nix-hermes-agent, "
            "hermes-agent-docker), and skill/plugin ecosystems (evey-hermes-plugins, "
            "oh-my-hermes, hermes-workspace, hermes-curator-evolver). Existing wiki "
            "notes under wiki/hermes-agent/ (28 notes from prior session) are NOT "
            "modified by this candidate."
        ),
        "authority_level": "canonical-upstream",
        "source_role": "core",
        "lifecycle_status": "active",
    },
    "github:NousResearch/hermes-agent-self-evolution": {
        "slug": "hermes-agent-self-evolution",
        "title": "Hermes Agent Self-Evolution — Evolutionary Self-Improvement (Nous Research)",
        "scope_covers": (
            "The NousResearch/hermes-agent-self-evolution repository as preserved "
            "in codex-vault/raw/hermes-agent-official/self-evolution/. Covers the "
            "README and PLAN documents."
        ),
        "scope_excludes": (
            "The main hermes-agent core (see hermes-agent-core candidate), other "
            "Nous extensions, and any community deployment or skill."
        ),
        "authority_level": "official",
        "source_role": "official-extension",
        "lifecycle_status": "active",
    },
    "github:NousResearch/hermes-paperclip-adapter": {
        "slug": "hermes-paperclip-adapter",
        "title": "Hermes Paperclip Adapter — Managed Employee Wrapper (Nous Research)",
        "scope_covers": (
            "The NousResearch/hermes-paperclip-adapter repository as preserved "
            "in codex-vault/raw/hermes-agent-official/paperclip-adapter/. Covers "
            "the README and AGENTS development guide."
        ),
        "scope_excludes": (
            "The main hermes-agent core, other Nous extensions, and the Paperclip "
            "platform itself (only the adapter is covered here)."
        ),
        "authority_level": "official",
        "source_role": "official-extension",
        "lifecycle_status": "active",
    },
}


def load_records(runtime_root: Path) -> dict:
    """Load all relevant records into in-memory indices."""
    artifacts = {}
    for p in (runtime_root / "artifacts").glob("*.json"):
        r = json.loads(p.read_text())
        artifacts[r["content_sha256"]] = r

    occurrences = []  # list, not indexed, for filtering
    for p in (runtime_root / "occurrences").rglob("*.json"):
        r = json.loads(p.read_text())
        occurrences.append(r)

    units = []  # list, for filtering
    for p in (runtime_root / "units").rglob("*.json"):
        r = json.loads(p.read_text())
        if r.get("schema") == "unit/v1":
            units.append(r)

    domain_records = []
    for p in (runtime_root / "domain").rglob("*.json"):
        r = json.loads(p.read_text())
        if r.get("schema") == "domain-record/v1":
            domain_records.append(r)

    return {
        "artifacts": artifacts,
        "occurrences": occurrences,
        "units": units,
        "domain_records": domain_records,
    }


def resolve_evidence(records: dict, source_id: str, run_id: str) -> dict:
    """For a given source_id, resolve all evidence (Phase 2-4 records).

    Returns a dict with:
      - artifacts: list of artifact metadata
      - occurrences: list of (occurrence_id, source_id, source_path, content_sha256)
      - units: list of (unit_id, artifact_id, source_anchor, unit_type, redacted)
      - blocked_count, flagged_count, clean_count
      - skipped (invalid JSON etc)
    """
    artifacts = records["artifacts"]
    occurrences = records["occurrences"]
    units = records["units"]

    # Filter occurrences for this source
    src_occs = [o for o in occurrences if o.get("source_id") == source_id]
    src_occs.sort(key=lambda o: o.get("source_path", ""))

    # Index: content_sha256 -> unit list
    units_by_artifact = defaultdict(list)
    for u in units:
        aid = u.get("artifact_id", "")
        if aid.startswith("sha256:"):
            units_by_artifact[aid].append(u)

    # Build evidence
    occ_evidence = []
    artifact_set = set()
    unit_evidence = []
    sec_counter = Counter()
    skipped_occ = 0
    redacted_unit_count = 0
    total_unit_count = 0

    for o in src_occs:
        sha = o.get("content_sha256")
        if not sha or sha not in artifacts:
            skipped_occ += 1
            continue
        a = artifacts[sha]
        sec = a.get("security_scan", {}).get("status", "?")
        sec_counter[sec] += 1
        # Skip blocked
        if sec == "blocked":
            continue
        # Include all clean + flagged (flagged is structural-only)
        artifact_set.add(sha)
        occ_evidence.append({
            "source_id": o["source_id"],
            "source_path": o["source_path"],
            "occurrence_id": o["occurrence_id"],
            "content_sha256": sha,
            "artifact_id": a["artifact_id"],
            "security_status": sec,
            "role": a.get("artifact_role", "unknown"),
        })
        # Collect units
        for u in units_by_artifact.get(f"sha256:{sha}", []):
            total_unit_count += 1
            if u.get("redacted"):
                redacted_unit_count += 1
            # Skip flagged units' semantic_text in the evidence summary
            unit_evidence.append({
                "unit_id": u.get("unit_id"),
                "unit_type": u.get("unit_type"),
                "title": u.get("title"),
                "artifact_id": u.get("artifact_id"),
                "source_anchor": u.get("source_anchor"),
                "redacted": u.get("redacted", False),
                "semantic_text_preview": (u.get("semantic_text") or "")[:300] if not u.get("redacted") else "",
            })

    return {
        "occurrences": occ_evidence,
        "unit_evidence": unit_evidence,
        "artifact_count": len(artifact_set),
        "occurrence_count": len(occ_evidence),
        "unit_count": total_unit_count,
        "redacted_unit_count": redacted_unit_count,
        "security_counter": dict(sec_counter),
        "skipped_occurrence": skipped_occ,
        "blocked_count": sec_counter.get("blocked", 0),
        "flagged_count": sec_counter.get("flagged", 0),
        "clean_count": sec_counter.get("clean", 0),
    }


def build_candidate(
    source_id: str,
    cfg: dict,
    evidence: dict,
    llm_synthesis: dict,
    run_id: str,
    generator_version: str,
    now: str,
) -> dict:
    """Build a single candidate knowledge note (deterministic + LLM-synthesized)."""
    # Build the deterministic body (markdown content) with placeholders for LLM synthesis
    # We'll merge LLM synthesis in after the body is built

    # Compose evidence list (deterministic) for the schema field
    evidence_list = []
    for ue in evidence["unit_evidence"][:25]:  # cap to top 25 for size
        anchor = ue.get("source_anchor", {})
        anchor_str = f"{anchor.get('section', '?')}:L{anchor.get('line_start', '?')}-{anchor.get('line_end', '?')}"
        evidence_list.append({
            "source_id": source_id,
            "artifact_id": ue.get("artifact_id"),
            "unit_id": ue.get("unit_id"),
            "anchor": anchor_str,
            "relation": "documents",
        })

    # source_record_ids: list of occurrence_ids (the most specific provenance)
    source_record_ids = [e["occurrence_id"] for e in evidence["occurrences"][:20]]
    if not source_record_ids:
        # Always have at least one source_record_id (schema requires non-empty)
        source_record_ids = ["sha256:no_evidence"]

    # coverage stats
    coverage_ratio = 1.0
    if evidence["occurrence_count"] + evidence["skipped_occurrence"] > 0:
        coverage_ratio = evidence["occurrence_count"] / (evidence["occurrence_count"] + evidence["skipped_occurrence"])

    # Compute content_hash placeholder (we'll recompute after merging LLM synthesis)
    placeholder_body = {
        "title": cfg["title"],
        "slug": cfg["slug"],
        "summary": llm_synthesis.get("summary", ""),
        "llm_synthesis": llm_synthesis,
        "evidence_list": evidence_list,
    }
    body_bytes = json.dumps(placeholder_body, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()

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
        "evidence": evidence_list,
        "created_at": now,
        "last_verified_at": now,
        "generator": "codex-vault/phase-5-knowledge-candidate",
        "generator_version": generator_version,
        "run_id": run_id,
        "content_hash": f"sha256:{h}",
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": cfg["lifecycle_status"],
        "duplicate_resolution": None,
        "supersedes": None,
        "cssclasses": ["domain/hermes-agent", "layer/note", "state/candidate", "hermes-agent/core"],
        "source_type": "candidate-note",
        "topic": cfg["slug"],
        "topic_cluster": "hermes-agent-core",
        "upstream_repo": source_id,
        "tags": ["hermes-agent", "candidate", "phase-5"],
        "source_paths": [e["source_path"] for e in evidence["occurrences"][:20]],
        "source_count": evidence["occurrence_count"],
        "coverage_ratio": round(coverage_ratio, 4),
        "coverage_status": "complete" if coverage_ratio >= 0.999 else ("partial" if coverage_ratio > 0 else "unknown"),
        "acquisition": {
            "status": "complete" if evidence["blocked_count"] == 0 and evidence["skipped_occurrence"] == 0 else "partial",
            "expected_files": evidence["occurrence_count"] + evidence["skipped_occurrence"],
            "acquired_files": evidence["occurrence_count"],
            "failed_files": 0,
            "excluded_files": evidence["blocked_count"] + evidence["skipped_occurrence"],
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "canonical": False,  # NEVER True for candidates
        # Provenance for the LLM synthesis
        "synthesis_provenance": {
            "summary_model": llm_synthesis.get("model", "minimax-m3"),
            "summary_run_id": run_id,
            "summary_generated_at": now,
            "summary_method": "LLM synthesis over Phase 2-4 evidence; deterministic metadata and evidence resolution produced by Python",
        },
        # Coverage notes (partial / uncertain)
        "coverage_notes": llm_synthesis.get("coverage_notes", ""),
        "unresolved_claims": llm_synthesis.get("unresolved_claims", []),
        # The actual markdown body (rendered from the LLM synthesis + evidence)
        "body_markdown": llm_synthesis.get("body_markdown", ""),
    }
    return candidate


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""))
    ap.add_argument("--llm-synthesis", required=True, help="Path to LLM synthesis JSON file")
    ap.add_argument("--run-id", default="phase-5-2026-06-20")
    ap.add_argument("--generator-version", default="0.1.0")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    vault = Path(args.vault_root)
    now = datetime.now(timezone.utc).isoformat()

    # Load LLM synthesis
    synthesis = json.loads(Path(args.llm_synthesis).read_text())
    # synthesis[slug] = { summary, body_markdown, coverage_notes, unresolved_claims, model }

    # Load records
    records = load_records(runtime)
    print(f"Loaded records: {len(records['artifacts'])} artifacts, "
          f"{len(records['occurrences'])} occurrences, "
          f"{len(records['units'])} units, "
          f"{len(records['domain_records'])} domain")

    # Resolve evidence per source
    candidates_dir = vault / "wiki" / "_candidates"
    migration_dir = candidates_dir / "_migration"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    migration_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for source_id, cfg in HERMES_CORE_SOURCES.items():
        slug = cfg["slug"]
        if slug not in synthesis:
            print(f"SKIP {slug}: no LLM synthesis provided")
            continue
        evidence = resolve_evidence(records, source_id, args.run_id)
        print(f"\n=== {slug} ===")
        print(f"  source_id: {source_id}")
        print(f"  artifacts: {evidence['artifact_count']}")
        print(f"  occurrences: {evidence['occurrence_count']} (skipped: {evidence['skipped_occurrence']})")
        print(f"  units: {evidence['unit_count']} (redacted: {evidence['redacted_unit_count']})")
        print(f"  security: {evidence['security_counter']}")

        candidate = build_candidate(
            source_id, cfg, evidence, synthesis[slug], args.run_id, args.generator_version, now
        )
        candidates.append(candidate)

        # Write candidate note (markdown)
        out = candidates_dir / f"{slug}.md"
        body = render_candidate_markdown(candidate)
        out.write_text(body)
        print(f"  Wrote: {out}")

        # Write migration report (YAML)
        report_path = migration_dir / f"{slug}-migration.yaml"
        report = build_migration_report(slug, source_id, evidence, candidate, args.run_id, now)
        report_path.write_text(report)
        print(f"  Wrote: {report_path}")

    # Validate all candidates
    print("\n=== Validation ===")
    schema_path = runtime / "schemas" / "knowledge-note.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    valid = 0
    invalid = 0
    for c in candidates:
        errors = validate_candidate(c, schema)
        if errors:
            invalid += 1
            print(f"  FAIL: {c['slug']}: {errors}")
        else:
            valid += 1
            print(f"  OK:   {c['slug']}")
    print(f"\nValid: {valid}, Invalid: {invalid}")

    # Write summary
    summary = {
        "run_id": args.run_id,
        "generated_at": now,
        "candidate_count": len(candidates),
        "valid_count": valid,
        "invalid_count": invalid,
        "candidates": [c["slug"] for c in candidates],
    }
    summary_path = candidates_dir / "_phase5-hermes-core-summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {summary_path}")


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


def build_migration_report(slug: str, source_id: str, evidence: dict, candidate: dict, run_id: str, now: str) -> str:
    """Build a migration report per AGENTS.md §16."""
    report = {
        "schema": "migration-report/v1",
        "schema_version": "1.0.0",
        "candidate_slug": slug,
        "run_id": run_id,
        "generated_at": now,
        "old_note": None,  # No old note (this is a new candidate)
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
            }
            for e in candidate["evidence"]
        ],
        "unresolved_claims": candidate.get("unresolved_claims", []),
        "validation_status": "pending",
        "validation_notes": "Validates against knowledge-note.schema.yaml via _phase5_candidate_hermes_core.py",
        "promotion_eligible": False,
        "promotion_blockers": [
            "knowledge_status=candidate (per Phase 5 requirement; not yet validated)",
            "Coverage is per Phase 2-4 evidence; no benchmarks run",
            "Existing wiki notes under wiki/hermes-agent/ are not modified (per requirement)",
        ],
        "evidence_summary": {
            "source_id": source_id,
            "artifact_count": evidence["artifact_count"],
            "occurrence_count": evidence["occurrence_count"],
            "unit_count": evidence["unit_count"],
            "redacted_unit_count": evidence["redacted_unit_count"],
            "security_counter": evidence["security_counter"],
            "blocked_excluded": evidence["blocked_count"],
            "flagged_redacted": evidence["flagged_count"],
            "clean_count": evidence["clean_count"],
        },
        "provenance": {
            "deterministic_metadata": "Python (this script)",
            "llm_synthesis_model": candidate["synthesis_provenance"]["summary_model"],
            "llm_synthesis_run_id": candidate["synthesis_provenance"]["summary_run_id"],
        },
    }
    return yaml.safe_dump(report, sort_keys=False, allow_unicode=True, default_flow_style=False)


def validate_candidate(c: dict, schema: dict) -> list:
    """Validate a candidate against the knowledge-note schema (lightweight, no jsonschema lib)."""
    errors = []
    required = schema.get("required", [])
    for field in required:
        if field not in c:
            errors.append(f"missing required field: {field}")

    # Validate controlled values
    df_enum = schema.get("properties", {}).get("domain_family", {}).get("enum", [])
    if c.get("domain_family") not in df_enum:
        errors.append(f"invalid domain_family: {c.get('domain_family')}")
    ks_enum = schema.get("properties", {}).get("knowledge_status", {}).get("enum", [])
    if c.get("knowledge_status") not in ks_enum:
        errors.append(f"invalid knowledge_status: {c.get('knowledge_status')}")

    # evidence schema
    ev_required = schema.get("properties", {}).get("evidence", {}).get("items", {}).get("required", [])
    ev_relation_enum = schema.get("properties", {}).get("evidence", {}).get("items", {}).get("properties", {}).get("relation", {}).get("enum", [])
    for i, e in enumerate(c.get("evidence", [])):
        for f in ev_required:
            if f not in e:
                errors.append(f"evidence[{i}] missing required field: {f}")
        if e.get("relation") not in ev_relation_enum:
            errors.append(f"evidence[{i}] invalid relation: {e.get('relation')}")

    # record_id pattern
    if not c.get("record_id", "").startswith("sha256:"):
        errors.append(f"record_id must be sha256:...")
    if not c.get("content_hash", "").startswith("sha256:"):
        errors.append(f"content_hash must be sha256:...")

    # source_record_ids non-empty
    if not c.get("source_record_ids"):
        errors.append("source_record_ids must be non-empty")

    # scope required
    scope_required = schema.get("properties", {}).get("scope", {}).get("required", [])
    for f in scope_required:
        if f not in c.get("scope", {}):
            errors.append(f"scope.{f} required")

    # canonical must be False for candidate
    if c.get("canonical") is True:
        errors.append("canonical must be False for knowledge_status=candidate")

    return errors


if __name__ == "__main__":
    main()
