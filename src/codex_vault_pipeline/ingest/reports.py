"""Report utilities for incremental ingest.

Produces structured JSON and human-readable Markdown reports to
.runtime/reports/.  All functions are pure (no mutation outside the
explicit output path), and none require network access or real vault
content.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def report_dir(vault_root: Path) -> Path:
    """Return the standard report directory under a vault root.

    Creates the directory if it does not exist.
    """
    d = vault_root / ".runtime" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_path(vault_root: Path, name: str, suffix: str = ".json") -> Path:
    """Return the full path to a named report file.

    Parent directories are created automatically.
    """
    d = report_dir(vault_root)
    return d / f"{name}{suffix}"


# ---------------------------------------------------------------------------
# Atomic writers
# ---------------------------------------------------------------------------

def write_json_report(path: Path, data: Dict[str, Any]) -> Path:
    """Atomically write a JSON report.

    Arguments:
        path:  destination path (parent dirs are created as needed).
        data:  dict to serialize (keys are sorted for deterministic output).

    Returns the resolved *path*.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="rpt_",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return path


def write_markdown_report(
    path: Path,
    title: str,
    sections: List[Tuple[str, str]],
) -> Path:
    """Atomically write a human-readable Markdown report.

    Arguments:
        path:      destination path.
        title:     top-level heading text (``# `` is prepended).
        sections:  list of ``(heading, body)`` tuples.  Each heading
                   is rendered as ``## heading``.  Body text is inserted
                   verbatim (callers may include sub-headings, lists,
                   code blocks, etc.).

    Returns the resolved *path*.
    """
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    for heading, body in sections:
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    content = "\n".join(lines)

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="rpt_",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return path


# ---------------------------------------------------------------------------
# Structured helpers
# ---------------------------------------------------------------------------

def build_ingest_summary(
    run_id: str,
    sources: List[Dict[str, Any]],
    validation: Optional[Dict[str, Any]] = None,
    counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Build a standard ingest summary dict suitable for JSON serialisation.

    Arguments:
        run_id:      unique identifier for this run.
        sources:     list of per-source summary dicts (each must have
                     at least ``source_id`` and ``status``).
        validation:  optional validation aggregate (from
                     ``source_validators.validate_source_local`` or
                     equivalent).
        counts:      optional top-level counters, e.g.
                     ``{"artifacts": 42, "occurrences": 99}``.

    Returns a dict with metadata, source summaries, validation status,
    and final status.
    """
    source_summaries: List[Dict[str, Any]] = []
    for s in sources:
        source_summaries.append({
            "source_id": s.get("source_id", "?"),
            "status": s.get("status", "?"),
        })

    total = len(source_summaries)
    ok = sum(1 for s in source_summaries if s["status"] == "complete")
    failed = sum(1 for s in source_summaries if s["status"] == "failed")
    skipped = sum(1 for s in source_summaries if s["status"] == "skipped")

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources_total": total,
        "sources_complete": ok,
        "sources_failed": failed,
        "sources_skipped": skipped,
        "sources": source_summaries,
    }

    if validation:
        summary["validation"] = {
            "status": validation.get("status", "?"),
            "failures": len(validation.get("failures", [])),
            "warnings": len(validation.get("warnings", [])),
            "details": validation,
        }
        summary["final_status"] = build_final_status(validation)
    else:
        summary["final_status"] = "VALIDATED" if failed == 0 else "FAILED"

    if counts:
        summary["counts"] = dict(counts)

    return summary


def build_final_status(validation: Dict[str, Any]) -> str:
    """Derive a final status string from a validation dict.

    Rules:

        * ``validation["blocked"] is True`` → ``BLOCKED``
        * ``validation["errors"] > 0``       → ``FAILED``
        * ``validation["warnings"] > 0``     → ``PARTIAL``
        * otherwise                          → ``VALIDATED``

    The validation dict is expected to contain (at minimum) ``blocked``
    (bool), ``errors`` (int), and ``warnings`` (int) keys.  Missing
    keys default to zero / false.
    """
    if validation.get("blocked"):
        return "BLOCKED"
    errors = validation.get("errors", 0)
    if isinstance(errors, list):
        errors = len(errors)
    if errors > 0:
        return "FAILED"
    warnings = validation.get("warnings", 0)
    if isinstance(warnings, list):
        warnings = len(warnings)
    if warnings > 0:
        return "PARTIAL"
    return "VALIDATED"
