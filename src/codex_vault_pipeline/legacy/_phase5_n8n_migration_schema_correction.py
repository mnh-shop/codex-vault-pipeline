#!/usr/bin/env python3
"""Phase 5 — n8n ecosystem migration-schema correction (schema-only).

Corrects the 7 n8n ecosystem migration records so they validate against
the authoritative migration-report.schema.yaml:

  - Renames `evidence_summary.source_ids` (plural list) to `source_id` (singular string)
  - For the multi-source `n8n-workflow-search-guide`, the `source_id` field
    contains a comma-separated list of all 4 source_ids (schema-valid string)
    so no provenance is hidden
  - The original `source_ids` (plural list) is preserved as a custom field
    in the search-guide migration for structured access

This script:
  1. Loads each affected migration record
  2. Renames `source_ids` (plural) to `source_id` (singular, schema-valid)
  3. For the search-guide: keeps `source_ids` (plural) as a custom field
  4. Recomputes `content_hash` and `record_id` per AGENTS.md §5 (every machine record)
  5. Saves back to both `.runtime/migration-reports/` (strict-validator visible)
     and `wiki/_candidates/_migration/` (human-readable mirror)

No new records are created; no records are deleted. No candidate count
change. No migration count change. No candidate body synthesis change.
No workflow reconciliation count change.
"""
import argparse, hashlib, json, sys
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)


# The 7 n8n ecosystem migration records to correct.
# For 6 of them: 1 source each. For search-guide: 4 sources.
# The fix is to rename `source_ids` (plural) to `source_id` (singular).
# For search-guide: `source_id` is a comma-separated string; `source_ids`
# is preserved as a custom field.
N8N_ECOSYSTEM_SLUGS = [
    "n8n-workflows-zie619",
    "n8n-workflows-awesome-n8n-templates",
    "n8n-workflows-nusquama",
    "n8n-workflows-nusquama-partial-coverage",
    "n8n-workflows-wassupjay",
    "n8n-workflows-czlonkowski-skills",
    "n8n-workflow-search-guide",
]


def correct_migration(runtime_root: Path, vault_root: Path, slug: str) -> dict:
    """Correct a single migration record: rename `source_ids` to `source_id`.

    For the search-guide, `source_id` is a comma-separated string of all 4
    source_ids, and the original `source_ids` (plural list) is preserved as
    a custom field.

    Returns a dict with the changes made.
    """
    mig_path = runtime_root / "migration-reports" / f"{slug}-migration.yaml"
    if not mig_path.exists():
        raise FileNotFoundError(f"Migration record not found: {mig_path}")

    mig = yaml.safe_load(mig_path.read_text())
    old_record_id = mig.get("record_id")
    old_content_hash = mig.get("content_hash")

    es = mig.get("evidence_summary", {})
    if "source_ids" not in es:
        # Already correct
        return {
            "slug": slug,
            "status": "already_correct",
            "old_record_id": old_record_id,
            "new_record_id": old_record_id,
        }

    # Get the plural list
    source_ids_list = es["source_ids"]
    # Remove the plural
    del es["source_ids"]

    if slug == "n8n-workflow-search-guide":
        # Multi-source: use comma-separated string for source_id (schema-valid)
        # and preserve source_ids (plural) as a custom field
        es["source_id"] = ", ".join(source_ids_list)
        es["source_ids"] = source_ids_list  # custom field, preserved
    else:
        # Single-source: take the only element
        assert len(source_ids_list) == 1, f"Expected 1 source_id, got {len(source_ids_list)}"
        es["source_id"] = source_ids_list[0]
        # source_ids removed; do not re-add

    # Re-compute content_hash and record_id per AGENTS.md §5
    body_bytes = json.dumps(mig, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body_bytes).hexdigest()
    mig["content_hash"] = f"sha256:{h}"
    mig["record_id"] = f"sha256:{h}"

    # Save to .runtime/migration-reports/ (strict-validator visible)
    mig_path.write_text(yaml.safe_dump(mig, sort_keys=False, allow_unicode=True, default_flow_style=False))

    # Mirror to wiki/_candidates/_migration/ (human-readable)
    human_path = vault_root / "wiki" / "_candidates" / "_migration" / f"{slug}-migration.yaml"
    if human_path.exists():
        human_path.write_text(yaml.safe_dump(mig, sort_keys=False, allow_unicode=True, default_flow_style=False))

    return {
        "slug": slug,
        "status": "corrected",
        "old_record_id": old_record_id,
        "new_record_id": mig["record_id"],
        "old_content_hash": old_content_hash,
        "new_content_hash": mig["content_hash"],
        "source_id_value": es.get("source_id"),
        "source_ids_preserved": slug == "n8n-workflow-search-guide",
    }


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""))
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    vault = Path(args.vault_root)

    print("=" * 60)
    print("Phase 5 n8n ecosystem migration-schema correction")
    print("=" * 60)
    print()
    print("Source of truth: .runtime/schemas/migration-report.schema.yaml")
    print("Schema requirement: evidence_summary.source_id is a string (singular), required.")
    print("Pre-correction state: 7 n8n-ecosystem migration records use source_ids (plural list).")
    print()

    results = []
    for slug in N8N_ECOSYSTEM_SLUGS:
        result = correct_migration(runtime, vault, slug)
        results.append(result)
        print(f"=== {slug} ===")
        for k, v in result.items():
            if k != "slug":
                v_str = str(v)[:80] if v else "None"
                print(f"  {k}: {v_str}")
        print()

    corrected = sum(1 for r in results if r["status"] == "corrected")
    already = sum(1 for r in results if r["status"] == "already_correct")

    print("=" * 60)
    print(f"Summary: {corrected} corrected, {already} already correct, {len(results)} total")
    print(f"Migration count unchanged: 7 (no records created or deleted)")
    print(f"Strict-validator delta: +0 (records modified in place, content_hash and record_id recomputed)")
    print("=" * 60)


if __name__ == "__main__":
    main()
