"""Batch runner — orchestrates multiple source ingest runs from a batch file.

A batch file (YAML or JSON) lists sources to ingest.  The batch runner
loads the file, validates its structure, then executes each source
through :func:`run_source_stages` with per-source handlers provided by
a caller-supplied factory.

The batch runner contains no ingest logic itself.  Real stage handlers
are injected by the caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from codex_vault_pipeline.ingest.source_runner import (
    SourceRunContext,
    SourceRunResult,
    run_source_stages,
)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class BatchSource:
    """A single source entry within a batch.

    Attributes:
        source_id:   Source identifier (e.g. ``github:owner/repo``).
        repo_url:    Repository URL.
        metadata:    Arbitrary key-value metadata for the source.
    """

    source_id: str
    repo_url: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchConfig:
    """Parsed and validated batch configuration.

    Attributes:
        run_id:   Unique identifier for this batch run.
        sources:  List of sources to ingest.
        metadata: Arbitrary key-value metadata for the batch.
    """

    run_id: str
    sources: List[BatchSource]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchRunResult:
    """Aggregated result of executing a batch.

    Attributes:
        run_id:         The batch run identifier.
        status:         ``complete``, ``partial``, or ``failed``.
        source_results: Per-source results in execution order.
        errors:         Global-level error messages (not per-source).
    """

    run_id: str
    status: str
    source_results: List[SourceRunResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

SourceStageHandlers = Mapping[str, Callable[[SourceRunContext], Optional[Dict[str, Any]]]]
"""Type alias for a mapping of stage names to handler callables."""

SourceHandlersFactory = Callable[[BatchSource], SourceStageHandlers]
"""Factory that returns stage handlers for a given batch source."""

# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_batch_config(path: Path) -> BatchConfig:
    """Load a batch configuration from a YAML or JSON file.

    Arguments:
        path:  Path to a ``.yaml``, ``.yml``, or ``.json`` file.

    Returns:
        A :class:`BatchConfig` parsed from the file.

    Raises:
        ValueError:  If the file format is not supported.
        FileNotFoundError, yaml.YAMLError, json.JSONDecodeError:
                     Propagated from the underlying parser.
    """
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    elif suffix == ".json":
        return _load_json(path)
    else:
        raise ValueError(
            f"Unsupported batch file format {suffix!r}; "
            f"supported: .yaml, .yml, .json"
        )


def _load_yaml(path: Path) -> BatchConfig:
    """Load a YAML batch file."""
    import yaml  # type: ignore[import-untyped]

    with path.open("r") as f:
        raw: Dict[str, Any] = yaml.safe_load(f)
    return _raw_to_config(raw)


def _load_json(path: Path) -> BatchConfig:
    """Load a JSON batch file."""
    raw: Dict[str, Any] = json.loads(path.read_text())
    return _raw_to_config(raw)


def _raw_to_config(raw: Dict[str, Any]) -> BatchConfig:
    """Convert a parsed dict to a BatchConfig."""
    sources_raw: List[Dict[str, Any]] = raw.get("sources", [])
    sources = [
        BatchSource(
            source_id=s["source_id"],
            repo_url=s.get("repo_url", ""),
            metadata=s.get("metadata", {}),
        )
        for s in sources_raw
    ]
    return BatchConfig(
        run_id=raw.get("run_id", ""),
        sources=sources,
        metadata=raw.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_batch_config(config: BatchConfig) -> List[str]:
    """Validate a batch configuration.

    Checks:
        * ``run_id`` is non-empty.
        * Each source has a non-empty ``source_id``.
        * Each source has a non-empty ``repo_url``.
        * No duplicate ``source_id`` values.

    Returns a list of error messages (empty if valid).
    """
    errors: List[str] = []

    if not config.run_id:
        errors.append("run_id is required and must be non-empty")

    seen: Dict[str, int] = {}
    for i, source in enumerate(config.sources):
        if not source.source_id:
            errors.append(f"sources[{i}].source_id is required and must be non-empty")
        else:
            if source.source_id in seen:
                errors.append(
                    f"Duplicate source_id {source.source_id!r} "
                    f"at sources[{i}] (first at sources[{seen[source.source_id]}])"
                )
            seen[source.source_id] = i

        if not source.repo_url:
            errors.append(
                f"sources[{i}]: repo_url is required and must be non-empty "
                f"(source_id={source.source_id!r})"
            )

    return errors


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_batch(
    vault_root: Path,
    config: BatchConfig,
    handlers_factory: SourceHandlersFactory,
    *,
    stages: Optional[Sequence[str]] = None,
    stop_on_first_failure: bool = True,
) -> BatchRunResult:
    """Execute a batch of source ingest runs.

    For each source in *config.sources*:

    1. Produce per-source stage handlers via *handlers_factory*.
    2. Call :func:`~codex_vault_pipeline.ingest.source_runner.run_source_stages`.
    3. Record the result.

    Arguments:
        vault_root:       Path to the vault root (forwarded to
                          :class:`SourceRunContext`).
        config:           Batch configuration.
        handlers_factory: Callable that receives a :class:`BatchSource`
                          and returns a mapping of stage names to handler
                          callables.
        stages:           Optional custom stage list.  Defaults to
                          :attr:`DEFAULT_SOURCE_STAGES
                          <codex_vault_pipeline.ingest.source_runner.DEFAULT_SOURCE_STAGES>`.
        stop_on_first_failure:
                          If True (default), halt the batch after the
                          first source that fails.  If False, continue
                          through remaining sources.

    Returns:
        A :class:`BatchRunResult`.

    Raises:
        ValueError:  If the config has validation errors.  Callers
                     should call :func:`validate_batch_config` before
                     :func:`run_batch` to check preconditions.
    """
    validation_errors = validate_batch_config(config)
    if validation_errors:
        raise ValueError(
            f"Invalid batch config: {'; '.join(validation_errors)}"
        )

    source_results: List[SourceRunResult] = []
    errors: List[str] = []

    for idx, batch_source in enumerate(config.sources):
        handlers = handlers_factory(batch_source)

        context = SourceRunContext(
            vault_root=vault_root,
            run_id=config.run_id,
            source_id=batch_source.source_id,
            repo_url=batch_source.repo_url,
        )

        result = run_source_stages(
            context,
            handlers,
            stages=stages,  # type: ignore[arg-type]
            stop_on_first_failure=stop_on_first_failure,
        )
        source_results.append(result)

        if result.status != "complete":
            msg = (
                f"Source {batch_source.source_id!r} "
                f"failed at stage {result.failed_stage!r}: "
                f"{'; '.join(result.errors)}"
            )
            errors.append(msg)
            if stop_on_first_failure:
                break

    # Determine overall status.
    all_complete = all(r.status == "complete" for r in source_results)
    processed_count = len(source_results)
    total_count = len(config.sources)

    if all_complete:
        status = "complete"
    elif processed_count < total_count and errors:
        status = "partial"
    else:
        status = "failed"

    return BatchRunResult(
        run_id=config.run_id,
        status=status,
        source_results=source_results,
        errors=errors,
    )
