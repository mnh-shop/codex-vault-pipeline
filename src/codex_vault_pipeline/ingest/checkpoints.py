"""Checkpoint utilities for incremental ingest.

Checkpoints record per-source ingest progress so that interrupted
runs can be resumed without re-processing already-completed sources.

Checkpoint directory structure:

    .runtime/checkpoints/incremental-ingest/<run-id>/
        <safe-source-filename-1>.json
        <safe-source-filename-2>.json
        ...
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def checkpoint_dir(vault_root: Path, run_id: str) -> Path:
    """Return the checkpoint directory for a given run.

    Creates the directory if it does not exist.
    """
    d = vault_root / ".runtime" / "checkpoints" / "incremental-ingest" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_path(vault_root: Path, run_id: str, source_id: str) -> Path:
    """Return the full path to the checkpoint file for a single source.

    Does not create parent directories (call *checkpoint_dir* first
    or rely on *write_checkpoint* to do so).
    """
    filename = safe_source_filename(source_id) + ".json"
    return vault_root / ".runtime" / "checkpoints" / "incremental-ingest" / run_id / filename


def safe_source_filename(source_id: str) -> str:
    """Produce a deterministic filesystem-safe name from a source ID.

    Example:

        safe_source_filename("github:owner/repo")
        # → "github_owner_repo"
    """
    safe = source_id.replace(":", "_").replace("/", "_").replace("\\", "_")
    # Collapse consecutive underscores from repeated replacements
    while "__" in safe:
        safe = safe.replace("__", "_")
    # Strip leading/trailing underscores
    safe = safe.strip("_")
    return safe


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def load_checkpoint(vault_root: Path, run_id: str, source_id: str) -> Optional[Dict[str, Any]]:
    """Load the checkpoint for a single source.

    Returns None if the checkpoint file does not exist or is corrupt.
    """
    cp = checkpoint_path(vault_root, run_id, source_id)
    if not cp.is_file():
        return None
    try:
        data = json.loads(cp.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def write_checkpoint(
    vault_root: Path,
    run_id: str,
    source_id: str,
    data: Dict[str, Any],
) -> Path:
    """Atomically write a checkpoint file.

    The *updated_at* field is always set to the current UTC ISO timestamp
    before writing, regardless of whether the caller included it.

    Returns the path of the written checkpoint file.
    """
    cp = checkpoint_path(vault_root, run_id, source_id)
    cp.parent.mkdir(parents=True, exist_ok=True)

    record = dict(data)
    record["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Atomic write: write to temp file in the same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="chk_",
        dir=str(cp.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(cp))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return cp


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_checkpoints(vault_root: Path, run_id: str) -> List[Dict[str, Any]]:
    """Return all checkpoint records for a run, ordered by filename.

    Files that cannot be parsed are silently skipped.
    """
    d = vault_root / ".runtime" / "checkpoints" / "incremental-ingest" / run_id
    if not d.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results
