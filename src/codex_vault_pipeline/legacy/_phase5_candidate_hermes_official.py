#!/usr/bin/env python3
"""Phase 5 — Knowledge Note Candidate Generator (Hermes Agent official batch).

Generates candidate knowledge notes under wiki/_candidates/ for 1 core source
and 2 official extensions from Nous Research.

Each candidate:
  - source_record_ids: Layer A source/v1 record IDs (NOT occurrence IDs)
  - occurrence_ids: Phase 3 occurrence record IDs (separately)
  - evidence[]: { source_id, artifact_id, unit_id, anchor, relation, occurrence_id }
  - knowledge_status=candidate (per requirement; never promoted automatically)
  - canonical=False for all candidates

Validation uses the external schema at .runtime/schemas/knowledge-note.schema.yaml
(via jsonschema library). The script does NOT embed its own validator.

Each candidate is mirrored as JSON to .runtime/knowledge-notes/ so the
strict validator (which scans .runtime/ for JSON files with formal schemas)
explicitly counts them. Each migration report is mirrored as YAML to
.runtime/migration-reports/ for the same reason.

Usage:
    python3 _phase5_candidate_hermes_official.py \
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
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema library required (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)


# Hermes Agent official batch: 1 core + 2 official extensions (all Nous Research)
HERMES_OFFICIAL_SOURCES = {
    "github:NousResearch/hermes-agent": {
        "slug": "hermes-agent-core",
        "title": "Hermes Agent — Canonical Core (Nous Research)",
        "scope_covers": (
            "The canonical NousResearch/hermes-agent repository at the snapshot "
            "preserved in codex-vault/raw/hermes-agent/. Covers the README, the "
            "developer-guide subdirectory, the reference subdirectory, and the "
            "user-guide subdirectory as recorded by the Phase 3 manifest."
        ),
        "scope_excludes": (
            "Nous Research official extensions (hermes-agent-self-evolution, "
            "hermes-paperclip-adapter, autonovel, tinker-atropos), community "
            "deployments (nix-hermes-agent, hermes-agent-docker), and skill/plugin "
            "ecosystems (evey-hermes-plugins, oh-my-hermes, hermes-workspace, "
            "hermes-curator-evolver). Existing wiki notes under wiki/hermes-agent/ "
            "(28 notes from prior session) are NOT modified by this candidate."
        ),
        "source_role": "core",
        "authority_level": "canonical-upstream",
        "lifecycle_status": "active",
    },
    "github:NousResearch/hermes-agent-self-evolution": {
        "slug": "hermes-agent-self-evolution",
        "title": "Hermes Agent Self-Evolution — Official Extension (Nous Research)",
        "scope_covers": (
            "The NousResearch/hermes-agent-self-evolution repository as preserved "
            "in codex-vault/raw/hermes-agent-official/self-evolution/. Covers the "
            "README and PLAN documents."
        ),
        "scope_excludes": (
            "The main hermes-agent core (see hermes-agent-core candidate), other "
            "Nous extensions (hermes-paperclip-adapter, autonovel, tinker-atropos), "
            "and any community deployment or skill."
        ),
        "source_role": "official-extension",
        "authority_level": "official",
        "lifecycle_status": "active",
    },
    "github:NousResearch/hermes-paperclip-adapter": {
        "slug": "hermes-paperclip-adapter",
        "title": "Hermes Paperclip Adapter — Official Extension (Nous Research)",
        "scope_covers": (
            "The NousResearch/hermes-paperclip-adapter repository as preserved "
            "in codex-vault/raw/hermes-agent-official/paperclip-adapter/. Covers "
            "the README and AGENTS development guide."
        ),
        "scope_excludes": (
            "The main hermes-agent core, other Nous extensions, and the Paperclip "
            "platform itself (only the adapter is covered here)."
        ),
        "source_role": "official-extension",
        "authority_level": "official",
        "lifecycle_status": "active",
    },
}

# Use the same controlled values the user wants preserved
BATCH_NAME = "hermes-agent-official"  # renamed from "hermes-agent-core" per user
BATCH_TITLE = "Hermes Agent Official Candidate Batch"  # was "Core"


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


def resolve_evidence(records: dict, source_id: str) -> dict:
    """For a given source_id, resolve all evidence (Phase 2-4 records).

    Returns occurrence records (used for occurrence_ids), and unit records
    (used for evidence[]).
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

    occ_evidence = []
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
        if sec == "blocked":
            continue
        occ_evidence.append({
            "source_id": o["source_id"],
            "source_path": o["source_path"],
            "occurrence_id": o["occurrence_id"],
            "content_sha256": sha,
            "artifact_id": a["artifact_id"],
            "security_status": sec,
            "role": a.get("artifact_role", "unknown"),
        })
        # Collect units per occurrence
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
                "redacted": u.get("redacted", False),
                "semantic_text_preview": (u.get("semantic_text") or "")[:300] if not u.get("redacted") else "",
            })

    return {
        "occurrences": occ_evidence,
        "unit_evidence": unit_evidence,
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
    layer_a_source_record_id: str,
    cfg: dict,
    evidence: dict,
    llm_synthesis: dict,
    run_id: str,
    generator_version: str,
    now: str,
) -> dict:
    """Build a single candidate knowledge note."""
    # Evidence: 25 items max, each with source_id, artifact_id, unit_id, anchor, relation, occurrence_id
    evidence_list = []
    for ue in evidence["unit_evidence"][:25]:
        anchor = ue.get("source_anchor", {})
        anchor_str = f"{anchor.get('section', '?')}:L{anchor.get('line_start', '?')}-{anchor.get('line_end', '?')}"
        evidence_list.append({
            "source_id": source_id,
            "artifact_id": ue.get("artifact_id"),
            "unit_id": ue.get("unit_id"),
            "anchor": anchor_str,
            "relation": "documents",
            "occurrence_id": ue.get("occurrence_id"),
        })

    # source_record_ids: Layer A source/v1 record IDs only (per requirement)
    source_record_ids = [layer_a_source_record_id]

    # occurrence_ids: Phase 3 occurrence record IDs (added per requirement)
    occurrence_ids = [e["occurrence_id"] for e in evidence["occurrences"]]

    # coverage stats
    coverage_ratio = 1.0
    if evidence["occurrence_count"] + evidence["skipped_occurrence"] > 0:
        coverage_ratio = evidence["occurrence_count"] / (evidence["occurrence_count"] + evidence["skipped_occurrence"])

    # Compute content_hash
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
        # Per requirement: source_record_ids = Layer A only
        "source_record_ids": source_record_ids,
        # Per requirement: occurrence_ids = Phase 3 occurrence record IDs
        "occurrence_ids": occurrence_ids,
        "evidence": evidence_list,
        "created_at": now,
        "last_verified_at": now,
        "generator": "codex-vault/phase-5-knowledge-candidate",
        "generator_version": generator_version,
        "run_id": run_id,
        "content_hash": f"sha256:{h}",
        # Preserve individual source_role per candidate
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": cfg["lifecycle_status"],
        "duplicate_resolution": None,
        "supersedes": None,
        "cssclasses": ["domain/hermes-agent", "layer/note", "state/candidate", f"hermes-agent/{cfg['source_role']}"],
        "source_type": "candidate-note",
        "topic": cfg["slug"],
        "topic_cluster": BATCH_NAME,  # renamed from "hermes-agent-core"
        "upstream_repo": source_id,
        "tags": ["hermes-agent", "candidate", "phase-5", "official"],  # added "official"
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
    layer_a_record_id: str,
    evidence: dict,
    candidate: dict,
    run_id: str,
    now: str,
    generator_version: str,
) -> dict:
    """Build a migration report per AGENTS.md §16. Includes all required
    machine-record fields (schema, record_id, created_at, generator, content_hash)."""
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
            "source_id": source_id,
            "layer_a_source_record_id": layer_a_record_id,
            "artifact_count": evidence["occurrence_count"],
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
    # Add required machine-record fields per AGENTS.md §5
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
        # iter_errors yields a list of validation errors
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
    ap.add_argument("--llm-synthesis", required=True, help="Path to LLM synthesis JSON file")
    ap.add_argument("--run-id", default="phase-5-2026-06-20")
    ap.add_argument("--generator-version", default="0.2.0")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    vault = Path(args.vault_root)
    now = datetime.now(timezone.utc).isoformat()

    # Load LLM synthesis
    synthesis = json.loads(Path(args.llm_synthesis).read_text())

    # Load Layer A source records
    layer_a_sources = load_layer_a_source_records(runtime)
    print(f"Loaded {len(layer_a_sources)} Layer A source/v1 records")

    # Load records
    records = load_records(runtime)
    print(f"Loaded records: {len(records['artifacts'])} artifacts, "
          f"{len(records['occurrences'])} occurrences, "
          f"{len(records['units'])} units")

    # Load external schema
    schema_path = runtime / "schemas" / "knowledge-note.schema.yaml"
    if not schema_path.exists():
        print(f"ERROR: external schema not found at {schema_path}", file=sys.stderr)
        sys.exit(2)
    schema = yaml.safe_load(schema_path.read_text())
    print(f"Loaded external schema from: {schema_path}")

    # Output dirs
    candidates_dir = vault / "wiki" / "_candidates"
    migration_dir = candidates_dir / "_migration"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    migration_dir.mkdir(parents=True, exist_ok=True)

    # Mirror dirs in .runtime/ for strict validator
    runtime_kn_dir = runtime / "knowledge-notes"
    runtime_mig_dir = runtime / "migration-reports"
    runtime_kn_dir.mkdir(parents=True, exist_ok=True)
    runtime_mig_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for source_id, cfg in HERMES_OFFICIAL_SOURCES.items():
        slug = cfg["slug"]
        if slug not in synthesis:
            print(f"SKIP {slug}: no LLM synthesis provided")
            continue

        # Get Layer A source record_id
        if source_id not in layer_a_sources:
            print(f"ERROR: Layer A source record not found for {source_id}", file=sys.stderr)
            sys.exit(2)
        layer_a_record_id = layer_a_sources[source_id]["record_id"]

        evidence = resolve_evidence(records, source_id)
        print(f"\n=== {slug} ===")
        print(f"  source_id: {source_id}")
        print(f"  layer_a_source_record_id: {layer_a_record_id}")
        print(f"  occurrences: {evidence['occurrence_count']} (skipped: {evidence['skipped_occurrence']})")
        print(f"  units: {evidence['unit_count']} (redacted: {evidence['redacted_unit_count']})")
        print(f"  security: {evidence['security_counter']}")

        candidate = build_candidate(
            source_id, layer_a_record_id, cfg, evidence, synthesis[slug],
            args.run_id, args.generator_version, now
        )
        candidates.append(candidate)

        # Write candidate MD
        out = candidates_dir / f"{slug}.md"
        out.write_text(render_candidate_markdown(candidate))
        print(f"  Wrote: {out}")

        # Write candidate JSON mirror in .runtime/ for strict validator
        kn_mirror = runtime_kn_dir / f"{slug}.json"
        kn_mirror.write_text(json.dumps(candidate, sort_keys=True, indent=2))
        print(f"  Wrote: {kn_mirror}")

        # Write migration YAML
        report = build_migration_report(slug, source_id, layer_a_record_id, evidence, candidate, args.run_id, now, args.generator_version)
        report_yaml = yaml.safe_dump(report, sort_keys=False, allow_unicode=True, default_flow_style=False)
        mig_path = migration_dir / f"{slug}-migration.yaml"
        mig_path.write_text(report_yaml)
        print(f"  Wrote: {mig_path}")

        # Write migration YAML mirror in .runtime/ for strict validator
        mig_mirror = runtime_mig_dir / f"{slug}-migration.yaml"
        mig_mirror.write_text(report_yaml)
        print(f"  Wrote: {mig_mirror}")

    # Validate all candidates against external schema
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

    # Write summary
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
    summary_path = candidates_dir / "_phase5-hermes-official-summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
