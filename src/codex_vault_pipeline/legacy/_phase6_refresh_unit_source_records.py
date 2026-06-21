"""Phase 6 — One-shot unit record refresh: rewrite stale source_record_ids.

After the deep-research/OSINT ingest changed the source-record formula
(commit-and-tree_sha aware), the existing 40k+ unit records still
reference the old record_ids. This script rewrites each unit's
`source_record_ids` to point at the current record_id of its source.

The source is derived from the unit's parent directory name
(`units/<kind>/<safe_source_id>/<unit_id>.json`). The `safe_source_id`
is the source_id with `:` -> `_` and `/` -> `_`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict

import yaml


VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
SOURCES_DIR = RUNTIME / "sources"
UNITS_DIR = RUNTIME / "units"


def safe_to_source(safe_id: str) -> str:
    """Inverse of `source_id.replace(':', '_').replace('/', '_')`.
    e.g. github_Agent-Field_agentfield -> github:Agent-Field/agentfield
    """
    if not safe_id.startswith("github_"):
        return safe_id
    rest = safe_id[len("github_"):]
    # The owner has no underscores (e.g. "NousResearch"); the repo may have
    # hyphens but no underscores in our 32 existing safe_ids. We split on the
    # first '/' after the first segment.
    if "_" in rest:
        # For our existing safe_ids, the owner doesn't contain underscores.
        # So we split on the first underscore.
        first_under = rest.index("_")
        owner = rest[:first_under]
        repo = rest[first_under + 1:]
    else:
        owner, repo = rest, ""
    return f"github:{owner}/{repo}"


def main() -> int:
    # Build source_id -> record_id map
    src_to_rec: Dict[str, str] = {}
    for src_dir in SOURCES_DIR.iterdir():
        if not src_dir.is_dir():
            continue
        sf = src_dir / "source.v1.yaml"
        if not sf.exists():
            continue
        try:
            rec = yaml.safe_load(sf.read_text())
        except Exception:
            continue
        sid = rec.get("source_id", "")
        rid = rec.get("record_id", "")
        if sid and rid:
            src_to_rec[sid] = rid

    # Walk all units, rewrite source_record_ids
    updated = 0
    skipped = 0
    errors = 0
    if not UNITS_DIR.exists():
        print(f"ERROR: units dir not found: {UNITS_DIR}")
        return 1
    for unit_file in UNITS_DIR.rglob("*.json"):
        try:
            u = json.load(open(unit_file))
        except Exception:
            errors += 1
            continue
        # Find the source from the path
        # Path: <units>/<kind>/<safe_source_id>/<unit_id>.json
        try:
            safe_id = unit_file.parent.name
        except Exception:
            errors += 1
            continue
        source_id = safe_to_source(safe_id)
        new_rid = src_to_rec.get(source_id)
        if not new_rid:
            skipped += 1
            continue
        sri = u.get("source_record_ids", [])
        if sri and sri[0] == new_rid:
            # Already up-to-date
            continue
        u["source_record_ids"] = [new_rid] + [s for s in sri[1:] if s != new_rid]
        unit_file.write_text(json.dumps(u, indent=2, sort_keys=True))
        updated += 1
    print(f"Updated: {updated}, Skipped: {skipped}, Errors: {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
