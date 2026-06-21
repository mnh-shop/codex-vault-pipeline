#!/usr/bin/env python3
"""Phase 5 — n8n ecosystem reconciliation correction (no redesign, no rerun).

Corrects the 4 affected candidates' workflow/source coverage table to use
the Phase 3 n8n-reconciliation categories as the source of truth:

  Zie619:      2088 files, 2065 valid_n8n, 1 blocked
  enescingoz:  329 files, 171 valid_n8n, 7 invalid, 129 unknown (was: 136 config — reclassification)
  nusquama:    1200 files, 371 valid_n8n, 605 metadata, 224 unknown (was: 224 config — reclassification)
  wassupjay:   227 files, 202 valid_n8n
  czlonkowski: 88 files (skills/docs, excluded from workflow JSON totals)

Affected candidates (4):
  - n8n-workflows-awesome-n8n-templates: split 136 → 7 invalid + 129 unknown
  - n8n-workflows-nusquama: rename 224 config → 224 unknown
  - n8n-workflows-nusquama-partial-coverage: rename 224 config → 224 unknown
  - n8n-workflow-search-guide: rename 360 config → 0 config + 7 invalid + 353 unknown

The other 3 candidates are unchanged (Zie619, wassupjay, czlonkowski already
use the correct Phase 3 n8n-reconciliation categories — they had no reclassification
issue).

This script:
  1. Loads each existing candidate JSON from .runtime/knowledge-notes/
  2. Updates the relevant fields (config_json, invalid_json, unknown_json) to match Phase 3
  3. Updates body_markdown and summary text to reflect the correct categories
  4. Re-computes content_hash and record_id
  5. Updates the corresponding migration record
  6. Updates the corresponding candidate MD
  7. Does NOT create or delete any records; only modifies existing ones
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)


# Phase 3 n8n-reconciliation truth (per phase-3-n8n-reconciliation.json)
PHASE3_TRUTH = {
    "github:Zie619/n8n-workflows": {
        "total_files": 2088, "total_json": 2065,
        "valid_workflows": 2065, "metadata": 0, "config": 0,
        "invalid": 0, "unknown": 0, "blocked": 1,
    },
    "github:enescingoz/awesome-n8n-templates": {
        "total_files": 329, "total_json": 307,
        "valid_workflows": 171, "metadata": 0, "config": 0,
        "invalid": 7, "unknown": 129, "blocked": 0,
    },
    "github:nusquama/n8nworkflows.xyz": {
        "total_files": 1200, "total_json": 1200,
        "valid_workflows": 371, "metadata": 605, "config": 0,
        "invalid": 0, "unknown": 224, "blocked": 0,
    },
    "github:wassupjay/n8n-free-templates": {
        "total_files": 227, "total_json": 202,
        "valid_workflows": 202, "metadata": 0, "config": 0,
        "invalid": 0, "unknown": 0, "blocked": 0,
    },
    "github:czlonkowski/n8n-skills": {
        "total_files": 88, "total_json": 0,
        "valid_workflows": 0, "metadata": 0, "config": 0,
        "invalid": 0, "unknown": 0, "blocked": 0,
        "excluded_from_workflow_totals": True,
    },
}

# Deterministic rule that caused the original reclassification
RECLASS_RULE = (
    "Phase 3's main `classify_artifact` function in `_phase3_artifact_manifest.py` "
    "(line 84: '# Other JSON — keep as configuration (could be a generic data file)') "
    "returns artifact_role=configuration for non-workflow JSONs that don't match the "
    "specific patterns (n8n-workflow, metadata, dependencies/scripts, name+version). "
    "Phase 3's n8n-reconciliation (per-source table from `classify_n8n_json`) returns "
    "`unknown` (or `invalid` if parse_status=invalid) for these same files. The "
    "Phase 5 n8n ecosystem batch originally used the artifact_role field which said "
    "`configuration`; this correction uses the Phase 3 n8n-reconciliation categories "
    "as the source of truth per the user instruction."
)

# Affected candidates and their corrections
CORRECTIONS = {
    "n8n-workflows-awesome-n8n-templates": {
        "source_id": "github:enescingoz/awesome-n8n-templates",
        "fix": {"config_json": 0, "invalid_json": 7, "unknown_json": 129},
        "body_text_changes": [
            ("Configuration JSON (n8n meta stubs): 136", "Configuration JSON: 0 | Invalid JSON: 7 | Unknown JSON: 129"),
            ("171 valid + 136 configuration", "171 valid + 0 configuration + 7 invalid + 129 unknown"),
            ("136 configuration stubs", "0 configuration + 7 invalid + 129 unknown (per Phase 3 n8n-reconciliation)"),
            ("**Phase 4 domain-record/v1 emitted:** 169 (= 171 valid − 2 dedup-collapsed)",
             "**Phase 4 domain-record/v1 emitted:** 169 (= 171 valid − 2 dedup-collapsed); the 7 invalid + 129 unknown JSONs are preserved in raw/ but not extracted as workflows (they don't pass the structural check; they are reclassified by Phase 3 n8n-reconciliation into `invalid` or `unknown`, not `configuration`)"),
            ("**Configuration JSON (n8n meta stubs):** 224",
             "**Configuration JSON (per Phase 3 n8n-reconciliation):** 0 (the 224 files reclassified to `unknown` in the reconciliation)"),
        ],
    },
    "n8n-workflows-nusquama": {
        "source_id": "github:nusquama/n8nworkflows.xyz",
        "fix": {"config_json": 0, "invalid_json": 0, "unknown_json": 224},
        "body_text_changes": [
            ("Configuration JSON (n8n meta stubs): 224",
             "Configuration JSON (per Phase 3 n8n-reconciliation): 0 | Invalid JSON: 0 | Unknown JSON: 224 (per Phase 3, these are `unknown`; they are not configuration, invalid, or metadata)"),
            ("224 configuration", "224 unknown (per Phase 3 n8n-reconciliation)"),
            ("**Configuration JSON (n8n meta stubs):** 224 — JSONs that pass the structural check for being valid JSON but do not have `name`/`nodes`/`connections` keys. Per the Phase 3 classifier (`classify_artifact` in `_phase3_artifact_manifest.py` line 84), JSONs that are dicts without those keys fall into `configuration` (the catch-all for non-workflow JSONs).",
             "**Unknown JSON:** 224 — JSONs that pass the structural check for being valid JSON but do not have `name`/`nodes`/`connections` keys. Per Phase 3's n8n-reconciliation (`classify_n8n_json` in `_phase3_artifact_manifest.py` line 111), these are classified as `unknown` (not `configuration`). The 224 files are preserved in raw/ but are NOT importable as workflows."),
        ],
    },
    "n8n-workflows-nusquama-partial-coverage": {
        "source_id": "github:nusquama/n8nworkflows.xyz",
        "fix": {"config_json": 0, "invalid_json": 0, "unknown_json": 224},
        "body_text_changes": [
            ("Configuration JSON (n8n meta stubs)", "Unknown JSON (per Phase 3 n8n-reconciliation)"),
            ("Configuration JSON (artifact_role=configuration)", "Unknown JSON (per Phase 3 n8n-reconciliation)"),
            ("the 605 metadata JSONs and 224 configuration JSONs", "the 605 metadata JSONs and 224 unknown JSONs"),
            ("605 metadata + 224 configuration", "605 metadata + 224 unknown"),
        ],
    },
    "n8n-workflow-search-guide": {
        "source_id": "(multiple)",
        "fix": {"config_json": 0, "invalid_json": 7, "unknown_json": 353},
        "body_text_changes": [
            ("valid_workflows=2809; metadata=605; config=360; docs=70; invalid=0; unknown=0; blocked=1",
             "valid_workflows=2809; metadata=605; config=0; invalid=7; unknown=353; blocked=1 (per Phase 3 n8n-reconciliation; the 136 enescingoz 'configuration' files are 7 invalid + 129 unknown; the 224 nusquama 'configuration' files are 224 unknown; total unknown = 129 + 224 = 353)"),
            ("config_json': 360", "config_json': 0"),
            ("Configuration JSON", "Configuration/Invalid/Unknown JSONs (per Phase 3 n8n-reconciliation: 0 config + 7 invalid + 353 unknown)"),
        ],
    },
}

BATCH_NAME = "n8n-ecosystem"
BATCH_TITLE = "n8n Ecosystem Workflow/Template Candidate Batch"


def compute_content_hash(candidate: dict) -> str:
    """Re-compute content_hash for a candidate after corrections."""
    placeholder_body = {
        "title": candidate["title"],
        "slug": candidate["slug"],
        "summary": candidate["summary"],
        "body_markdown": candidate["body_markdown"],
        "evidence": candidate["evidence"],
    }
    body_bytes = json.dumps(placeholder_body, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()
    return h


def update_candidate(runtime_root: Path, slug: str, correction: dict) -> dict:
    """Update an existing candidate JSON in place.

    Returns the updated candidate with:
      - config_json, invalid_json, unknown_json fields corrected
      - body_markdown text updated
      - summary text updated
      - content_hash and record_id recomputed
      - evidence_summary in the migration record updated
    """
    json_path = runtime_root / "knowledge-notes" / f"{slug}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Candidate JSON not found: {json_path}")
    candidate = json.loads(json_path.read_text())
    old_record_id = candidate["record_id"]
    old_content_hash = candidate["content_hash"]

    # Update the JSON category fields
    es = candidate.get("evidence_summary", {})
    fix = correction["fix"]
    es.update(fix)
    candidate["evidence_summary"] = es

    # Update body_markdown and summary text
    body = candidate.get("body_markdown", "")
    summary = candidate.get("summary", "")
    for old_text, new_text in correction["body_text_changes"]:
        body = body.replace(old_text, new_text)
        summary = summary.replace(old_text, new_text)
    candidate["body_markdown"] = body
    candidate["summary"] = summary

    # Re-compute content_hash and record_id
    new_h = compute_content_hash(candidate)
    candidate["content_hash"] = f"sha256:{new_h}"
    candidate["record_id"] = f"sha256:{new_h}"

    # Save back
    json_path.write_text(json.dumps(candidate, sort_keys=True, indent=2))

    return {
        "slug": slug,
        "old_record_id": old_record_id,
        "old_content_hash": old_content_hash,
        "new_record_id": candidate["record_id"],
        "new_content_hash": candidate["content_hash"],
        "field_changes": fix,
    }


def update_migration(runtime_root: Path, slug: str, candidate: dict) -> dict:
    """Update the corresponding migration record in place."""
    mig_path = runtime_root / "migration-reports" / f"{slug}-migration.yaml"
    if not mig_path.exists():
        raise FileNotFoundError(f"Migration record not found: {mig_path}")
    mig = yaml.safe_load(mig_path.read_text())
    old_record_id = mig["record_id"]
    old_content_hash = mig["content_hash"]

    # Update the evidence_summary with the corrected counts
    es = mig.get("evidence_summary", {})
    es["valid_workflows"] = candidate["evidence_summary"].get("valid_workflows", 0)
    es["metadata_json"] = candidate["evidence_summary"].get("metadata_json", 0)
    es["config_json"] = candidate["evidence_summary"].get("config_json", 0)
    es["invalid_json"] = candidate["evidence_summary"].get("invalid_json", 0)
    es["unknown_json"] = candidate["evidence_summary"].get("unknown_json", 0)
    es["blocked_excluded"] = candidate["evidence_summary"].get("blocked_excluded", 0)
    es["flagged_redacted"] = candidate["evidence_summary"].get("flagged_redacted", 0)
    es["clean_count"] = candidate["evidence_summary"].get("clean_count", 0)
    mig["evidence_summary"] = es

    # Re-compute content_hash and record_id
    body_bytes = json.dumps(mig, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()
    mig["content_hash"] = f"sha256:{h}"
    mig["record_id"] = f"sha256:{h}"

    # Save back
    mig_path.write_text(yaml.safe_dump(mig, sort_keys=False, allow_unicode=True, default_flow_style=False))

    return {
        "slug": slug,
        "old_record_id": old_record_id,
        "old_content_hash": old_content_hash,
        "new_record_id": mig["record_id"],
        "new_content_hash": mig["content_hash"],
    }


def update_candidate_md(vault_root: Path, slug: str, candidate: dict) -> bool:
    """Re-render the candidate MD from the updated candidate JSON.

    This is needed because the body_markdown and summary have changed.
    """
    # Find the MD
    candidates_dir = vault_root / "wiki" / "_candidates"
    md_path = candidates_dir / f"{slug}.md"
    if not md_path.exists():
        return False
    # Re-render using the same approach as the original script
    fm = {
        "schema": candidate["schema"],
        "schema_version": candidate["schema_version"],
        "record_id": candidate["record_id"],
        "title": candidate["title"],
        "slug": candidate["slug"],
        "domain_family": candidate["domain_family"],
        "knowledge_status": candidate["knowledge_status"],
        "scope": candidate["scope"],
        "summary": candidate["summary"],
        "source_record_ids": candidate["source_record_ids"],
        "occurrence_ids": candidate["occurrence_ids"],
        "evidence": candidate["evidence"],
        "created_at": candidate["created_at"],
        "last_verified_at": candidate["last_verified_at"],
        "generator": candidate["generator"],
        "generator_version": candidate["generator_version"],
        "run_id": candidate["run_id"],
        "content_hash": candidate["content_hash"],
        "source_role": candidate["source_role"],
        "authority_level": candidate["authority_level"],
        "lifecycle_status": candidate["lifecycle_status"],
        "duplicate_resolution": candidate["duplicate_resolution"],
        "supersedes": candidate["supersedes"],
        "cssclasses": candidate["cssclasses"],
        "source_type": candidate["source_type"],
        "topic": candidate["topic"],
        "topic_cluster": candidate["topic_cluster"],
        "upstream_repo": candidate["upstream_repo"],
        "tags": candidate["tags"],
        "source_paths": candidate["source_paths"],
        "source_count": candidate["source_count"],
        "coverage_ratio": candidate["coverage_ratio"],
        "coverage_status": candidate["coverage_status"],
        "acquisition": candidate["acquisition"],
        "canonical": candidate["canonical"],
        "synthesis_provenance": candidate["synthesis_provenance"],
        "coverage_notes": candidate["coverage_notes"],
        "unresolved_claims": candidate["unresolved_claims"],
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = candidate.get("body_markdown", "") or "(no body content)"
    md_path.write_text(f"---\n{fm_text}---\n\n# {candidate['title']}\n\n{body}\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""))
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    vault = Path(args.vault_root)

    print("=" * 60)
    print("Phase 5 n8n ecosystem reconciliation correction")
    print("=" * 60)
    print()
    print("Phase 3 n8n-reconciliation truth (source of truth):")
    for sid, t in PHASE3_TRUTH.items():
        print(f"  {sid}:")
        for k, v in t.items():
            print(f"    {k}: {v}")
    print()
    print(f"Reclassification rule: {RECLASS_RULE}")
    print()

    # Process each affected candidate
    results = []
    for slug, correction in CORRECTIONS.items():
        print(f"=== {slug} ===")
        print(f"  source_id: {correction['source_id']}")
        print(f"  field changes: {correction['fix']}")
        # Update candidate JSON
        cand_result = update_candidate(runtime, slug, correction)
        print(f"  candidate: {cand_result['old_record_id'][:24]}... → {cand_result['new_record_id'][:24]}...")
        # Update migration record
        candidate = json.loads((runtime / "knowledge-notes" / f"{slug}.json").read_text())
        mig_result = update_migration(runtime, slug, candidate)
        print(f"  migration: {mig_result['old_record_id'][:24]}... → {mig_result['new_record_id'][:24]}...")
        # Update MD
        md_updated = update_candidate_md(vault, slug, candidate)
        print(f"  MD updated: {md_updated}")
        # Update human-readable migration YAML
        # The human-readable MD/YAML in wiki/_candidates/ is regenerated from the JSON
        mig_yaml_path = vault / "wiki" / "_candidates" / "_migration" / f"{slug}-migration.yaml"
        if mig_yaml_path.exists():
            mig_yaml_path.write_text(yaml.safe_dump(yaml.safe_load((runtime / "migration-reports" / f"{slug}-migration.yaml").read_text()), sort_keys=False, allow_unicode=True, default_flow_style=False))
        results.append({
            "slug": slug,
            "candidate_change": cand_result,
            "migration_change": mig_result,
        })
        print()

    print("=" * 60)
    print(f"Updated {len(results)} candidates + {len(results)} migration records")
    print("Candidate count unchanged: 7 candidates + 7 migrations")
    print("=" * 60)


if __name__ == "__main__":
    main()
