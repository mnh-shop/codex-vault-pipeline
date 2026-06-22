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
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure the pipeline package is importable
HERE = Path(__file__).resolve().parent
PIPELINE_SRC = HERE.parent / "src"
sys.path.insert(0, str(PIPELINE_SRC))

try:
    from codex_vault_pipeline.ingest.occurrence_identity import occurrence_id as make_occ_id
except ImportError:
    def make_occ_id(sid, spath):
        return hashlib.sha256(f"{sid}\0{spath}".encode("utf-8")).hexdigest()

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

# Safe source → raw directory name mapping.
# Build dynamically from the vault scratch raw/ directory.
# Each raw/ subdirectory maps to a safe source_id by the common
# naming convention: the raw dir name appears in the source path.
# We build a reverse lookup: find every source_id whose artifacts
# reference a given raw subdir, then cache by safe source_id.
# (Pre-populated for the 5 original targets as a fallback.)
DEFAULT_SAFE_TO_RAW = {
    "github_Alibaba-NLP_DeepResearch": "DeepResearch",
    "github_langchain-ai_open_deep_research": "open_deep_research",
    "github_n8n-io_n8n-docs": "n8n-docs",
    "github_NousResearch_hermes-agent": "hermes-agent",
    "github_Agent-Field_agentfield": "agentfield",
}
SAFE_TO_RAW = dict(DEFAULT_SAFE_TO_RAW)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_batch_config(path):
    """Load a batch config file (JSON or YAML)."""
    text = Path(path).read_text().strip()
    if not text:
        raise ValueError(f"Empty batch config: {path}")
    # Try JSON first
    if text.startswith("{"):
        return json.loads(text)
    # Try YAML
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        # No YAML available -- is it valid JSON wrapped with list?
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"Batch config must be JSON or YAML (pyyaml not installed): {path}")


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


def build_safe_to_raw_mapping(raw_base: Path, artifact_dir: Path):
    """Dynamically build safe→raw directory mapping from artifact source_paths.

    Scans a sample of artifacts to discover which raw subdirectory name
    corresponds to each source_id.  This avoids maintaining a static 43-entry
    mapping.
    """
    mapping = {}
    # Collect (safe_source_id, candidate_raw_name) from artifacts
    for art_dir in sorted(artifact_dir.iterdir()):
        json_files = list(art_dir.glob("*.json"))
        if not json_files:
            continue
        art = load_json(json_files[0])
        sid = art.get("source_id", "")
        if not sid:
            continue
        sp = art.get("source_path", "")
        # source_path is like "raw/<raw_name>/rest/of/path"
        if not sp.startswith("raw/"):
            continue
        raw_name = sp.split("/")[1]  # second component after "raw/"
        safe = safe_source_id(sid)
        mapping[safe] = raw_name
        break  # one sample from one artifact is enough per source

    # For unvisited sources (only one artifact processed above), scan all
    # artifact dirs fully.
    seen_sids = set()
    for art_dir in sorted(artifact_dir.iterdir()):
        json_files = list(art_dir.glob("*.json"))
        if not json_files:
            continue
        art = load_json(json_files[0])
        sid = art.get("source_id", "")
        if not sid:
            continue
        safe = safe_source_id(sid)
        if safe in mapping:
            seen_sids.add(safe)
            continue
        sp = art.get("source_path", "")
        if sp.startswith("raw/"):
            raw_name = sp.split("/")[1]
            mapping[safe] = raw_name
            seen_sids.add(safe)

    return mapping


def build_occurrence_index(occurrence_dir, source_id):
    """Build a content_sha256 → occurrence dict for a source.

    Occurrence files are named by occurrence_id, not content_sha256,
    so we index by content_sha256 for artifact lookup.
    """
    safe = safe_source_id(source_id)
    src_dir = occurrence_dir / safe
    if not src_dir.exists():
        return {}
    index = {}
    for f in src_dir.glob("*.json"):
        occ = load_json(f)
        cs = occ.get("content_sha256", "")
        if cs:
            index[cs] = occ
    return index


def find_occurrence(artifact, occurrence_index) -> dict:
    """Find the occurrence record for this artifact using a pre-built index."""
    sha = artifact.get("content_sha256", "")
    if not sha:
        return artifact
    occ = occurrence_index.get(sha)
    if occ is not None:
        return occ
    return artifact


def ensure_occurrence_id(occ, source_id, source_path):
    """Inject deterministic occurrence_id if the record lacks one."""
    if "occurrence_id" in occ and occ["occurrence_id"]:
        return occ
    occ = dict(occ)
    h = make_occ_id(source_id, source_path)
    occ["occurrence_id"] = f"sha256:{h}"
    return occ


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run deterministic unit extraction on target sources"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Write output units here (default: existing scratch .runtime/units)"
    )
    parser.add_argument(
        "--batch-file", type=Path, default=None,
        help="Batch YAML file with sources (default: built-in 5 sources)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_id = f"6s-unit-extract-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now = datetime.now(timezone.utc).isoformat()

    # Override unit output directory if provided (artifacts/occurrences still
    # read from the existing vault scratch tree)
    if args.output_dir is not None:
        global UNIT_DIR
        UNIT_DIR = args.output_dir.resolve()
        UNIT_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve target sources: batch file overrides the built-in 5
    global TARGET_SOURCES, SAFE_TO_RAW
    if args.batch_file is not None:
        if not args.batch_file.exists():
            print(f"[{run_id}] Batch file not found: {args.batch_file}")
            sys.exit(1)
        batch = load_batch_config(args.batch_file)
        sources_list = batch.get("sources", [])
        TARGET_SOURCES = {s["source_id"] for s in sources_list if "source_id" in s}
        run_id = batch.get("run_id", run_id)
        print(f"[{run_id}] Loaded {len(TARGET_SOURCES)} sources from {args.batch_file}")

    # Build dynamic safe→raw mapping for raw path resolution
    SAFE_TO_RAW.update(build_safe_to_raw_mapping(RAW_BASE, ARTIFACT_DIR))
    no_raw = [s for s in sorted(TARGET_SOURCES) if safe_source_id(s) not in SAFE_TO_RAW]
    if no_raw:
        print(f"[{run_id}] WARNING: No raw directory mapping for {len(no_raw)} sources:")
        for s in no_raw:
            print(f"         {s}")

    # Phase 1: Collect all artifacts for target sources
    print(f"[{run_id}] Collecting artifacts for {len(TARGET_SOURCES)} target sources...")
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

        # Build content_sha256 → occurrence index for this source
        occ_index = build_occurrence_index(OCCURRENCE_DIR, sid)

        for art in artifacts:
            occ = find_occurrence(art, occ_index)
            source_path = occ.get("source_path", art.get("source_path", ""))
            # Ensure deterministic occurrence_id
            occ = ensure_occurrence_id(occ, sid, source_path)
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
