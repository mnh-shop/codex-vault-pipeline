#!/usr/bin/env python3
"""
phase6_schema_correction.py — Phase 6 schema correction for
schema-knowledge-note-missing-primary_domain.

Adds a structured `source_taxonomy` array to every Phase 5 candidate
knowledge-note record, mirroring the Layer A source/v1 record
(primary_domain, related_domains, source_role, authority_level).

For each candidate:
  - extract unique source_ids from evidence[].source_id
  - for each source_id, look up the Layer A source/v1 record
  - build a source_taxonomy entry
  - sort entries deterministically by source_id
  - if source_id has no Layer A record, skip but warn (should not happen)

Also updates the wiki/_candidates/*.md frontmatter mirror.

Recomputes content_hash and record_id so the values reflect the new
source_taxonomy. The hash is the sha256 of a canonical JSON
serialization of the full record (sorted keys) — same value for both
fields, preserving the existing record_id == content_hash invariant.

Read-only on raw/, schema files, and any non-candidate wiki notes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
SOURCES_DIR = RUNTIME / "sources"
KN_DIR = RUNTIME / "knowledge-notes"
WIKI_CANDIDATES = VAULT / "wiki" / "_candidates"
REPORT_PATH = RUNTIME / "reports" / "phase-6-knowledge-note-schema-correction.md"
AUDIT_PATH = RUNTIME / "reports" / "phase-5-global-acceptance-audit.md"

PRIMARY_DOMAIN_VOCAB = {
    "hermes-agent", "n8n", "agentfield", "coding-agents", "training-systems",
    "ai-content-generation", "memory-systems", "cross-domain",
    "general-development", "unknown",
}


def load_sources() -> dict[str, dict]:
    """Load all 32 Layer A source records, keyed by source_id."""
    out: dict[str, dict] = {}
    for sub in sorted(SOURCES_DIR.iterdir()):
        if not sub.is_dir():
            continue
        p = sub / "source.v1.yaml"
        if not p.exists():
            continue
        rec = yaml.safe_load(p.read_text())
        out[rec["source_id"]] = rec
    return out


def build_taxonomy(sources: dict[str, dict], source_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Build a deterministic source_taxonomy array from a list of source_ids.

    Returns (taxonomy, missing). missing is the list of source_ids that
    have no Layer A record (should be empty in normal operation).
    """
    missing: list[str] = []
    entries: list[dict] = []
    for sid in sorted(set(source_ids)):
        if not sid:
            continue
        if sid not in sources:
            missing.append(sid)
            continue
        s = sources[sid]
        pd = s.get("primary_domain") or "unknown"
        if pd not in PRIMARY_DOMAIN_VOCAB:
            # Should not happen — every source's primary_domain is in the
            # vocab. If it does, log but still emit the value.
            pass
        entries.append({
            "source_id": sid,
            "primary_domain": pd,
            "related_domains": list(s.get("related_domains") or []),
            "source_role": s.get("source_role") or "unknown",
            "authority_level": s.get("authority_level") or "unknown",
        })
    return entries, missing


def canonical_hash(record: dict) -> str:
    """sha256 of canonical JSON serialization of the record (sorted keys, no spaces)."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backfill_candidate(sources: dict, rec: dict) -> tuple[dict, list[str]]:
    """Add source_taxonomy to a candidate record; recompute content_hash and record_id.

    Source_id resolution:
      1. Direct source_ids from evidence[].source_id (per-evidence links).
      2. Reverse-lookup of source_record_ids (list of sha256:hash) against the
         Layer A source/v1 records. This catches multi-source candidates whose
         evidence may only link to one source but whose source_record_ids
         list all of them.

    Returns (new_record, missing_source_ids).
    """
    # Build a map from Layer A record_id -> source_id for reverse lookup.
    record_id_to_source_id: dict[str, str] = {
        s["record_id"]: sid for sid, s in sources.items() if s.get("record_id")
    }

    source_ids: set[str] = set()

    # (1) From evidence[].source_id
    for ev in rec.get("evidence", []):
        sid = ev.get("source_id")
        if sid:
            source_ids.add(sid)

    # (2) From source_record_ids (Layer A sha256:hash)
    for sri in rec.get("source_record_ids", []):
        if not sri:
            continue
        sid = record_id_to_source_id.get(sri)
        if sid:
            source_ids.add(sid)

    taxonomy, missing = build_taxonomy(sources, sorted(source_ids))

    # Build the new record: do not modify any existing fields except
    # (1) add source_taxonomy, (2) recompute content_hash and record_id,
    # (3) update last_verified_at to the current run timestamp.
    new_rec = dict(rec)
    new_rec["source_taxonomy"] = taxonomy
    new_rec["last_verified_at"] = datetime.now(timezone.utc).isoformat()
    new_rec["content_hash"] = ""  # placeholder, computed below
    new_rec["record_id"] = ""  # placeholder, computed below

    h = canonical_hash(new_rec)
    new_rec["content_hash"] = h
    new_rec["record_id"] = h
    return new_rec, missing


def update_md_frontmatter(md_path: Path, new_record: dict) -> bool:
    """Replace the YAML frontmatter of a candidate MD with values from new_record.

    Returns True if the file was modified.
    """
    text = md_path.read_text()
    if not text.startswith("---"):
        return False
    # Find the closing ---
    end = text.find("\n---\n", 3)
    if end < 0:
        return False
    # Build new frontmatter (only the fields that mirror the JSON schema)
    fm = new_record.get("__md_frontmatter__") or build_md_frontmatter(new_record)
    new_text = "---\n" + fm + "---\n" + text[end + 5:]
    if new_text == text:
        return False
    md_path.write_text(new_text)
    return True


def build_md_frontmatter(rec: dict) -> str:
    """Build a YAML frontmatter string from a candidate record."""
    # Use a curated subset that mirrors the JSON record
    keys = [
        "schema", "schema_version", "record_id", "title", "slug",
        "domain_family", "knowledge_status", "lifecycle_status",
        "source_role", "authority_level", "summary",
        "source_taxonomy",
        "source_record_ids", "occurrence_ids", "evidence",
        "created_at", "last_verified_at", "generator", "generator_version",
        "run_id", "content_hash", "tags", "cssclasses", "coverage_status",
        "coverage_ratio", "acquisition", "source_paths", "source_count",
        "topic", "topic_cluster", "upstream_repo", "source_type",
        "duplicate_resolution", "supersedes", "relations",
    ]
    fm: dict = {}
    for k in keys:
        if k in rec:
            fm[k] = rec[k]
    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)


def main() -> int:
    print("=== Phase 6 schema correction: source_taxonomy backfill ===")
    sources = load_sources()
    print(f"Loaded {len(sources)} Layer A source records")

    candidates = sorted(KN_DIR.glob("*.json"))
    print(f"Found {len(candidates)} candidate JSONs")

    missing_total: list[tuple[str, str]] = []
    taxonomy_summary: dict[str, int] = defaultdict(int)
    skipped: list[str] = []
    updated = 0

    for p in candidates:
        rec = json.loads(p.read_text())
        slug = rec.get("slug") or p.stem
        new_rec, missing = backfill_candidate(sources, rec)
        if missing:
            for sid in missing:
                missing_total.append((slug, sid))
        taxonomy_summary[len(new_rec["source_taxonomy"])] += 1
        # Only write if changed
        if new_rec["content_hash"] != rec.get("content_hash"):
            p.write_text(json.dumps(new_rec, indent=2, ensure_ascii=False) + "\n")
            updated += 1

    print(f"Updated {updated} / {len(candidates)} candidate JSONs")
    print(f"Taxonomy size distribution: {dict(taxonomy_summary)}")
    if missing_total:
        print(f"WARNING: {len(missing_total)} source_ids had no Layer A record:")
        for slug, sid in missing_total:
            print(f"  - {slug} -> {sid}")
    else:
        print("No missing source_ids.")

    # Now update wiki/_candidates/*.md frontmatter
    print()
    print("=== Updating wiki/_candidates/*.md frontmatter ===")
    md_updated = 0
    for p in sorted(WIKI_CANDIDATES.glob("*.md")):
        slug = p.stem
        json_path = KN_DIR / f"{slug}.json"
        if not json_path.exists():
            skipped.append(slug)
            continue
        rec = json.loads(json_path.read_text())
        if update_md_frontmatter(p, rec):
            md_updated += 1
    print(f"Updated {md_updated} MD frontmatter files")
    if skipped:
        print(f"Skipped (no JSON mirror): {skipped}")

    # Verify the 7 critical cases
    print()
    print("=== Verifying 7 critical cases ===")
    critical = [
        ("github:NousResearch/autonovel", "ai-content-generation"),
        ("github:NousResearch/tinker-atropos", "training-systems"),
        ("github:AxDSan/Mnemosyne", "coding-agents"),
        ("github:amanning3390/flowstate-qmd", "coding-agents"),
        ("github:builderz-labs/mission-control", "coding-agents"),
        ("github:vectorize-io/hindsight", "coding-agents"),
        ("github:wondelai/skills", "coding-agents"),
    ]
    all_ok = True
    for sid, expected_pd in critical:
        found = False
        for p in KN_DIR.glob("*.json"):
            rec = json.loads(p.read_text())
            for entry in rec.get("source_taxonomy", []):
                if entry.get("source_id") == sid:
                    actual_pd = entry.get("primary_domain")
                    if actual_pd == expected_pd:
                        print(f"  OK  {sid} -> {actual_pd} (candidate: {rec.get('slug')})")
                    else:
                        print(f"  FAIL {sid} -> expected={expected_pd} actual={actual_pd} (candidate: {rec.get('slug')})")
                        all_ok = False
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"  MISS {sid} not found in any candidate's source_taxonomy")
            all_ok = False
    print(f"All 7 critical cases: {'PASS' if all_ok else 'FAIL'}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
