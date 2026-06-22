"""Source runner skeleton — orchestrates per-source ingest stages.

The runner executes a sequence of named stages for a single source,
writes checkpoints before and after each stage, and returns a
structured result.  It contains no ingest logic itself; callers
supply handler functions for each stage.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from codex_vault_pipeline.ingest.checkpoints import write_checkpoint

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_STAGES: tuple[str, ...] = (
    "acquire_source",
    "write_source_record",
    "create_artifacts",
    "create_occurrences",
    "validate_artifacts_occurrences",
    "extract_units",
    "validate_unit_artifact_links",
    "generate_candidate_note",
    "generate_migration_report",
    "source_local_validation",
    "checkpoint_complete",
)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

SourceHandler = Callable[["SourceRunContext"], Optional[Dict[str, Any]]]


@dataclass
class SourceRunContext:
    """Context passed to every stage handler.

    Attributes:
        vault_root:  Path to the vault root.
        run_id:      Unique identifier for this ingest run.
        source_id:   Source identifier (e.g. ``github:owner/repo``).
        repo_url:    Optional repository URL for display / checkpoints.
    """

    vault_root: Any  # pathlib.Path, avoided here to keep import-light
    run_id: str
    source_id: str
    repo_url: Optional[str] = None


@dataclass
class SourceRunResult:
    """Result of executing source stages.

    Attributes:
        source_id:        The source that was processed.
        status:           Overall status: ``complete``, ``failed``, or ``skipped``.
        completed_stages: Names of stages that completed successfully.
        failed_stage:     Name of the stage that failed, or ``None``.
        errors:           Error messages collected during the run.
    """

    source_id: str
    status: str
    completed_stages: List[str] = field(default_factory=list)
    failed_stage: Optional[str] = None
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_source_stages(
    context: SourceRunContext,
    handlers: Mapping[str, SourceHandler],
    *,
    stages: Sequence[str] = DEFAULT_SOURCE_STAGES,
    stop_on_first_failure: bool = True,
) -> SourceRunResult:
    """Execute source ingest stages in order.

    For each stage:

    1. A ``running`` checkpoint is written.
    2. The handler is called with *context*.
    3. If the handler raises or returns ``{"status": "fail"}``, a
       ``failed`` checkpoint is written and execution stops.
    4. Otherwise a ``running`` checkpoint with an updated
       ``last_successful_step`` is written.

    After all stages pass, a ``complete`` checkpoint is written.

    Arguments:
        context:              Per-source context.
        handlers:             Mapping of stage name to callable.  Every
                              stage in *stages* must have an entry.
        stages:               Ordered sequence of stage names.  Defaults to
                              :attr:`DEFAULT_SOURCE_STAGES`.
        stop_on_first_failure:
                              If True (the default), halt on the first
                              failure.  If False, continue through remaining
                              stages (the result will list all errors).

    Returns a :class:`SourceRunResult`.

    Raises:
        KeyError:  If a stage name in *stages* has no entry in *handlers*.
    """
    completed: List[str] = []
    errors: List[str] = []
    failed_stage: Optional[str] = None

    # Validate that all requested stages have handlers.
    for stage_name in stages:
        if stage_name not in handlers:
            raise KeyError(
                f"Missing handler for stage {stage_name!r}; "
                f"registered handlers: {list(handlers)}"
            )

    for stage_name in stages:
        # Write "running" checkpoint before the stage.
        _write_stage_checkpoint(
            context, stage_name, status="running",
            last_successful_step=completed[-1] if completed else None,
        )

        handler = handlers[stage_name]
        try:
            result = handler(context)
        except Exception as exc:
            msg = _format_error(stage_name, exc)
            errors.append(msg)
            failed_stage = stage_name
            _write_stage_checkpoint(
                context, stage_name, status="failed",
                last_successful_step=completed[-1] if completed else None,
                errors=errors,
            )
            if stop_on_first_failure:
                break
            continue

        if isinstance(result, dict) and result.get("status") == "fail":
            msg = result.get("message", f"Stage {stage_name!r} returned fail status")
            errors.append(msg)
            failed_stage = stage_name
            _write_stage_checkpoint(
                context, stage_name, status="failed",
                last_successful_step=completed[-1] if completed else None,
                errors=errors,
            )
            if stop_on_first_failure:
                break
            continue

        completed.append(stage_name)

    if not failed_stage:
        status = "complete"
        _write_stage_checkpoint(
            context, None, status="complete",
            last_successful_step=completed[-1] if completed else None,
            errors=errors,
        )
    else:
        status = "failed"

    return SourceRunResult(
        source_id=context.source_id,
        status=status,
        completed_stages=completed,
        failed_stage=failed_stage,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_stage_checkpoint(
    context: SourceRunContext,
    stage: Optional[str],
    status: str,
    last_successful_step: Optional[str] = None,
    errors: Optional[List[str]] = None,
) -> None:
    """Write a checkpoint reflecting the current stage state."""
    data: Dict[str, Any] = {
        "source_id": context.source_id,
        "stage": stage or "final",
        "status": status,
        "last_successful_step": last_successful_step,
        "errors": errors or [],
    }
    if context.repo_url:
        data["repo_url"] = context.repo_url
    write_checkpoint(context.vault_root, context.run_id, context.source_id, data)


def _format_error(stage: str, exc: Exception) -> str:
    """Format an exception as an error message for the result."""
    tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return f"Stage {stage!r} raised: {tb}"
