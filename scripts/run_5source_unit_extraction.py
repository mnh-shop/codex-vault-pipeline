#!/usr/bin/env python3
"""Run the new deterministic unit_extractor on 5 target sources using
scratch artifacts and raw source content, then report unit counts.

Usage:
    python3 scripts/run_5source_unit_extraction.py

Uses:
    - Scratch artifacts:  vault/.runtime/scratch/.runtime/artifacts/
    - Scratch occurrences: vault/.runtime/scratch/.runtime/occurrences/
    - Raw source content:  vault/.runtime/scratch/raw/<source_name>/
    - Output units:        vault/.runtime/scratch/.runtime/units/<unit_type>/

The script:
  1. Scans all artifacts matching 5 target source_ids
  2. Loads the raw file content from the scratch raw/ snapshot
  3. Calls extract_units_from_artifact for each
  4. Writes unit JSON to scratch units directory
  5. Reports counts per source and per unit_type
"""

import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure the pipeline package is importable
HERE = Path(__file__).resolve().parent
PIPELINE_SRC = HERE.parent / "src"
sys.path.insert(0, str(PIPELINE_SRC))

# Also add the vault root for file_policy assets
VAULT_ROOT = Path(os.environ.get(
    "CODEX_VAULT_ROOT",
    "/Users/admin1/agent-brain/codex-vault"
))

SCRATCH_RT = VAULT_ROOT / ".runtime" / "scratch" / ".runtime"
ARTIFACT_DIR = SCRATCH_RT / "artifacts"
OCCURRENCE_DIR = SCRATCH_RT / "occurrences"
RAW_BASE = VAULT_ROOT / ".runtime" / "scratch" / "raw"
UNIT_DIR = SCRATCH_RT / "units"

# 5 target sources
TARGET_SOURCES = {
    "github:Alibaba-NLP/DeepResearch",
    "github:langchain-ai/open_deep_research",
    "github:n8n-io/n8n-docs",
    "github:NousResearch/hermes-agent",
    "github:Agent-Field/agentfield",
}

# Safe source → raw directory name mapping
SAFE_TO_RAW = {
    "github_Alibaba-NLP_DeepResearch": "DeepResearch",
    "github_langchain-ai_open_deep_research": "open_deep_research",
    "github_n8n-io_n8n-docs": "n8n-docs",
    "github_NousResearch_hermes-agent": "hermes-agent",
    "github_Agent-Field_agentfield": "agentfield",
}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def safe_source_id(sid: str) -> str:
    return sid.replace(":", "_").replace("/", "_")


def resolve_raw_path(source_id: str, source_path: str) -> Path:
    """Resolve raw content path from scratch raw/ snapshot."""
    safe = safe_source_id(source_id)
    raw_name = SAFE_TO_RAW.get(safe, safe)
    # source_path is like "raw/n8n-docs/docs/foo.md" — strip the raw/<name>/ prefix
    # Try relative first
    rel = source_path
    if rel.startswith("raw/"):
        rel = rel[len("raw/"):]
    # strip leading source-name if present
    parts = rel.split("/", 1)
    if len(parts) > 1 and parts[0] == raw_name:
        rel = parts[1]
    return RAW_BASE / raw_name / rel


def find_occurrence(artifact, occurrence_dir) -> dict:
    """Find the occurrence record for this artifact."""
    sha = artifact.get("content_sha256", "")
    if not sha:
        return artifact  # fallback to artifact as occurrence-like
    # Occurrences are stored in subdirs named by safe source_id
    safe = safe_source_id(artifact.get("source_id", ""))
    occ_path = occurrence_dir / safe / f"{sha}.json"
    if occ_path.exists():
        return load_json(occ_path)
    # Fallback: search all occurrence dirs
    for sub in occurrence_dir.iterdir():
        p = sub / f"{sha}.json"
        if p.exists():
            return load_json(p)
    return artifact  # fallback


def main():
    run_id = f"6s-unit-extract-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now = datetime.now(timezone.utc).isoformat()

    # Phase 1: Collect all artifacts for target sources
    print(f"[{run_id}] Collecting artifacts for 5 target sources...")
    artifacts_by_source = defaultdict(list)
    total_checked = 0

    for art_dir in sorted(ARTIFACT_DIR.iterdir()):
        json_files = list(art_dir.glob("*.json"))
        if not json_files:
            continue
        art = load_json(json_files[0])
        sid = art.get("source_id", "")
        if sid in TARGET_SOURCES:
            artifacts_by_source[sid].append(art)
        total_checked += 1

    print(f"  Checked {total_checked} artifact directories.")
    for sid in sorted(TARGET_SOURCES):
        print(f"  Found {len(artifacts_by_source[sid]):5d} artifacts for {sid}")

    # Phase 2: Run extraction
    print(f"\n[{run_id}] Running unit extraction...")
    from codex_vault_pipeline.ingest.unit_extractor import extract_units_from_artifact

    total_units = 0
    unit_type_counts = Counter()
    source_unit_counts = Counter()
    errors = []
    duration = 0.0
    art_count = sum(len(v) for v in artifacts_by_source.values())

    for sid in sorted(TARGET_SOURCES):
        artifacts = artifacts_by_source[sid]
        source_art_count = 0
        source_unit_count = 0
        source_errors = 0

        for art in artifacts:
            occ = find_occurrence(art, OCCURRENCE_DIR)
            source_path = occ.get("source_path", art.get("source_path", ""))
            sha = art.get("content_sha256", "")

            # Load raw content
            raw_path = resolve_raw_path(sid, source_path)
            if raw_path.exists():
                raw_bytes = raw_path.read_bytes()
            else:
                # Try fallback locations
                safe = safe_source_id(sid)
                raw_name = SAFE_TO_RAW.get(safe, safe)
                alt_paths = [
                    RAW_BASE / raw_name / source_path,
                    RAW_BASE / raw_name / source_path.replace(f"raw/{raw_name}/", ""),
                ]
                found = False
                for ap in alt_paths:
                    if ap.exists():
                        raw_bytes = ap.read_bytes()
                        found = True
                        break
                if not found:
                    errors.append(f"  MISSING: {sid} {source_path}")
                    source_errors += 1
                    continue

            try:
                units = extract_units_from_artifact(
                    art, occ, raw_bytes, run_id, now=now
                )
            except Exception as e:
                errors.append(f"  ERROR: {sid} {source_path}: {e}")
                source_errors += 1
                continue

            source_art_count += 1
            source_unit_count += len(units)
            total_units += len(units)
            for u in units:
                unit_type_counts[u["unit_type"]] += 1

            # Write units
            for u in units:
                unit_id = u["unit_id"]
                unit_hash = hashlib.sha256(unit_id.encode()).hexdigest()
                utype_dir = UNIT_DIR / u["unit_type"]
                utype_dir.mkdir(parents=True, exist_ok=True)
                unit_path = utype_dir / f"{unit_hash}.json"
                with open(unit_path, "w") as f:
                    json.dump(u, f, indent=2)

        source_unit_counts[sid] = source_unit_count
        print(f"  {sid}: {source_art_count} artifacts → {source_unit_count} units ({source_errors} errors)")

    # Phase 3: Report
    print(f"\n{'='*60}")
    print(f"EXTRACTION REPORT")
    print(f"{'='*60}")
    print(f"Run ID:     {run_id}")
    print(f"Targets:    5")
    print(f"Artifacts:  {art_count}")
    print(f"Units:      {total_units}")
    print(f"Errors:     {len(errors)}")
    print()
    print("Units by type:")
    for utype, count in unit_type_counts.most_common():
        print(f"  {utype:25s} {count}")
    print()
    print("Units by source:")
    for sid in sorted(TARGET_SOURCES):
        print(f"  {sid:50s} {source_unit_counts[sid]}")
    print()

    if errors:
        print("Errors:")
        for e in errors[:20]:
            print(e)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")
        print()

    # Comparison with old scratch units
    old_unit_types = Counter()
    for f in UNIT_DIR.rglob("*.json"):
        try:
            d = json.loads(f.read_text())
            old_unit_types[d.get("unit_type", "?")] += 1
        except Exception:
            pass
    print("Scratch unit directory now contains:")
    for utype, count in old_unit_types.most_common():
        print(f"  {utype:25s} {count}")

    return total_units


if __name__ == "__main__":
    main()
