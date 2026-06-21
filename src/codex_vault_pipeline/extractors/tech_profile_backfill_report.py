"""Backfill feasibility report for the technical-profile fields.

This script does NOT modify any source record. It walks every
Layer A source directory in `${VAULT_ROOT}/.runtime/sources/`,
runs the deterministic extractor on the corresponding `raw/`
tree, and produces a JSON report summarizing what can be
inferred for each existing source.

The report is the user-facing input to decide whether a
backfill is safe. It explicitly does not rewrite `source.v1.yaml`
files — the user must run a separate, explicit backfill step
to overlay the profile onto the source record (and that step
must be opt-in per-source).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

from codex_vault_pipeline.extractors.tech_profile import extract_tech_profile


# Map a `source_id` (e.g. `github:Agent-Field/agentfield`) to a
# raw/ tree path. The vault's raw/ tree does not use a single
# consistent encoding — some entries are `<owner>-<repo>` (e.g.
# `vectorize-io-hindsight`), some are `<repo>` only (e.g.
# `agentfield`), and some preserve mixed case (e.g.
# `AMAP-ML-SkillClaw`). We do the best-effort match:
#   1. canonical `<owner>-<repo>`
#   2. case-insensitive `<owner>-<repo>`
#   3. case-preserving `<repo>`
#   4. case-insensitive `<repo>`
#   5. substring match on the repo name
def raw_path_for_source(source_id: str, raw_root: Path) -> Path:
    if not source_id.startswith("github:"):
        return Path()
    path = source_id[len("github:"):]
    parts = path.split("/", 1)
    if len(parts) != 2:
        return Path()
    owner, repo = parts
    candidates = [
        f"{owner}-{repo}",
        f"{owner.lower()}-{repo.lower()}",
        repo,
        repo.lower(),
    ]
    for c in candidates:
        p = raw_root / c
        if p.is_dir():
            return p
    # Substring fallback: look for any dir whose name contains
    # the repo name (case-insensitive). This catches the
    # case where the repo was preserved with the original case
    # but a different owner encoding.
    repo_low = repo.lower()
    for p in raw_root.iterdir():
        if not p.is_dir():
            continue
        if repo_low in p.name.lower():
            return p
    return Path()


def build_backfill_report(
    sources_dir: Path,
    raw_root: Path,
) -> Dict[str, Any]:
    """Walk every Layer A source and compute a feasibility row
    for each. Returns a report dict."""
    rows: List[Dict[str, Any]] = []
    counts = {
        "total_sources": 0,
        "can_infer_source_platform_github": 0,
        "can_infer_repo_owner_repo": 0,
        "have_raw_tree": 0,
        "have_dependency_manifests": 0,
        "have_language_signals": 0,
        "have_interfaces": 0,
        "have_workflow_synthesis_signals": 0,
    }

    for src_dir in sorted(sources_dir.iterdir()):
        if not src_dir.is_dir():
            continue
        # find the source record
        record = None
        for cand in ("source.v1.yaml", "source.yaml"):
            p = src_dir / cand
            if p.exists():
                try:
                    record = yaml.safe_load(p.read_text())
                except Exception:
                    record = None
                break
        if not record:
            continue
        counts["total_sources"] += 1
        source_id = record.get("source_id", "")
        row: Dict[str, Any] = {
            "source_id": source_id,
            "record_path": str(src_dir / (p.name if p else "source.v1.yaml")),
            "existing_source_platform": record.get("source_platform"),
            "existing_repo_identity_present": "repo_identity" in record,
        }

        # can_infer_source_platform_github
        if source_id.startswith("github:"):
            counts["can_infer_source_platform_github"] += 1
            row["can_infer_source_platform_github"] = True
        else:
            row["can_infer_source_platform_github"] = False

        # can_infer_repo_owner_repo
        if source_id.startswith("github:") and "/" in source_id[len("github:"):]:
            counts["can_infer_repo_owner_repo"] += 1
            row["can_infer_repo_owner_repo"] = True
        else:
            row["can_infer_repo_owner_repo"] = False

        # Run extractor if raw/ tree is present
        rp = raw_path_for_source(source_id, raw_root)
        # NOTE: Path() (no argument) defaults to ".", the cwd, so
        # `rp.is_dir()` is True for an empty Path. We must also
        # check that `str(rp)` is non-empty (i.e. the lookup
        # actually found a directory).
        if str(rp) and str(rp) != "." and rp.is_dir():
            counts["have_raw_tree"] += 1
            try:
                profile = extract_tech_profile(
                    rp,
                    source_id=source_id,
                    pinned_commit=record.get("resolved_commit", ""),
                )
                row["have_raw_tree"] = True
                row["languages"] = profile["repo_profile"]["languages"]
                row["dependency_manifest_count"] = len(
                    profile["repo_profile"]["dependency_manifests"]
                )
                row["interface_count"] = len(profile["interfaces"])
                row["entrypoint_count"] = len(profile["repo_profile"]["entrypoints"])
                row["config_file_count"] = len(profile["repo_profile"]["config_files"])
                row["service_count"] = len(profile["repo_profile"]["services"])
                row["data_store_count"] = len(profile["repo_profile"]["data_stores"])
                if row["dependency_manifest_count"] > 0:
                    counts["have_dependency_manifests"] += 1
                if row["languages"]:
                    counts["have_language_signals"] += 1
                if row["interface_count"] > 0:
                    counts["have_interfaces"] += 1
            except Exception as e:
                row["have_raw_tree"] = False
                row["extractor_error"] = str(e)
        else:
            row["have_raw_tree"] = False
            row["raw_path_guess"] = str(rp)

        rows.append(row)

    return {
        "run_id": f"tech-profile-backfill-report-{datetime.now(timezone.utc).isoformat()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "sources": rows,
    }


# ----- CLI ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex_vault_pipeline.extractors.tech_profile_backfill_report",
        description=(
            "Walk every Layer A source and summarize what the "
            "deterministic tech-profile extractor CAN infer. "
            "Does NOT modify any source record."
        ),
    )
    p.add_argument(
        "--vault-root",
        type=Path,
        required=True,
        help="Path to the Codex Vault root (the directory that contains .runtime/).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the report to this JSON path. Default: stdout.",
    )
    return p


def main(argv: "List[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    vault_root = args.vault_root.resolve()
    sources_dir = vault_root / ".runtime" / "sources"
    raw_root = vault_root / "raw"
    if not sources_dir.is_dir():
        print(f"ERROR: sources dir not found: {sources_dir}", file=sys.stderr)
        return 1
    if not raw_root.is_dir():
        print(f"ERROR: raw dir not found: {raw_root}", file=sys.stderr)
        return 1
    report = build_backfill_report(sources_dir, raw_root)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
