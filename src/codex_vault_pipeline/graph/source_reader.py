"""Read source metadata from vault runtime for graph projection.

Provides a frozen dataclass (:class:`GraphSourceRecord`) and two pure
functions:

* :func:`read_source_file` — parse a single ``source.v1.yaml``
* :func:`read_sources_from_runtime` — read all sources from a runtime tree
* :func:`summarize_graph_axes` — count values across multiple axes
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphSourceRecord:
    """Structured metadata for a single source, extracted from
    ``source.v1.yaml``.

    Scalar fields that may be absent in the YAML are ``None``.
    List-type fields are normalised to tuples (empty tuple if absent).
    The raw YAML dict is preserved in *raw* for any field not covered
    by the named attributes.
    """

    source_id: str
    source_path: Path

    # --- taxonomy axes ---
    primary_domain: Optional[str] = None
    related_domains: Tuple[str, ...] = ()
    ecosystems: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    topics: Tuple[str, ...] = ()
    integration_targets: Tuple[str, ...] = ()
    project_use_cases: Tuple[str, ...] = ()

    # --- classification ---
    artifact_role: Optional[str] = None
    source_role: Optional[str] = None
    authority_level: Optional[str] = None
    lifecycle_status: Optional[str] = None

    # --- display / linking ---
    knowledge_status: Optional[str] = None
    title: Optional[str] = None
    repo_url: Optional[str] = None

    # --- raw payload ---
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _list_or_none(raw: Dict[str, Any], key: str) -> List[str]:
    """Return the list under *key*, or an empty list if absent / null."""
    val = raw.get(key)
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


def _str_or_none(raw: Dict[str, Any], key: str) -> Optional[str]:
    """Return the string under *key*, or None if absent / null."""
    val = raw.get(key)
    if val is None:
        return None
    return str(val)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_source_file(path: Path) -> GraphSourceRecord:
    """Parse a single ``source.v1.yaml`` file.

    Args:
        path:  Absolute path to the source record file.

    Returns:
        A populated :class:`GraphSourceRecord`.

    Raises:
        ValueError:  If the file has no ``source_id`` field.
    """
    raw: Dict[str, Any] = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a YAML mapping in {path}, got {type(raw).__name__}")

    source_id: Any = raw.get("source_id", "")
    if not source_id:
        raise ValueError(
            f"source.v1.yaml has no source_id: {path}"
        )

    # Derive title from repo_identity.full_name if available
    # (e.g. ``langchain-ai/open_deep_research``).
    repo_identity = raw.get("repo_identity")
    title: Optional[str] = None
    if isinstance(repo_identity, dict):
        full_name = repo_identity.get("full_name")
        if full_name:
            title = str(full_name)

    return GraphSourceRecord(
        source_id=str(source_id),
        source_path=path.resolve(),
        primary_domain=_str_or_none(raw, "primary_domain"),
        related_domains=tuple(_list_or_none(raw, "related_domains")),
        ecosystems=tuple(_list_or_none(raw, "ecosystems")),
        capabilities=tuple(_list_or_none(raw, "capabilities")),
        topics=tuple(_list_or_none(raw, "topics")),
        integration_targets=tuple(_list_or_none(raw, "integration_targets")),
        project_use_cases=tuple(_list_or_none(raw, "project_use_cases")),
        artifact_role=_str_or_none(raw, "artifact_role"),
        source_role=_str_or_none(raw, "source_role"),
        authority_level=_str_or_none(raw, "authority_level"),
        lifecycle_status=_str_or_none(raw, "lifecycle_status"),
        title=title,
        repo_url=_str_or_none(raw, "canonical_url"),
        raw=raw,
    )


def read_sources_from_runtime(runtime_root: Path) -> Dict[str, GraphSourceRecord]:
    """Read all source records from a vault runtime tree.

    Expected layout::

        <runtime_root>/sources/<encoded-source-id>/source.v1.yaml

    Args:
        runtime_root:  Path to the ``.runtime/`` directory of a vault.

    Returns:
        ``{source_id: GraphSourceRecord}``, ordered alphabetically by
        *source_id*.

    Raises:
        ValueError:  If any source file lacks a ``source_id``.
    """
    sources_dir = runtime_root / "sources"
    if not sources_dir.is_dir():
        return {}

    records: List[GraphSourceRecord] = []

    for subdir in sorted(sources_dir.iterdir()):
        if not subdir.is_dir():
            continue
        yaml_path = subdir / "source.v1.yaml"
        if not yaml_path.is_file():
            continue
        rec = read_source_file(yaml_path)
        records.append(rec)

    return {r.source_id: r for r in records}


def summarize_graph_axes(
    records: Dict[str, GraphSourceRecord],
) -> Dict[str, Dict[str, int]]:
    """Count value frequencies across multiple graph axes.

    Axes included:

    * ``primary_domain``
    * ``related_domains``
    * ``ecosystems``
    * ``capabilities``
    * ``artifact_role``
    * ``source_role``
    * ``authority_level``
    * ``lifecycle_status``
    * ``knowledge_status``

    Args:
        records:  Source records keyed by *source_id*.

    Returns:
        ``{axis_name: {value: count}}``, each value sorted
        descending by count.
    """
    AXES: Tuple[str, ...] = (
        "primary_domain",
        "related_domains",
        "ecosystems",
        "capabilities",
        "artifact_role",
        "source_role",
        "authority_level",
        "lifecycle_status",
        "knowledge_status",
    )

    # Initialise counters.
    counters: Dict[str, Dict[str, int]] = {a: {} for a in AXES}

    for rec in records.values():
        for axis in AXES:
            val = getattr(rec, axis, ())
            # Tuples → iterate values.
            if isinstance(val, tuple):
                for v in val:
                    if v:
                        counters[axis][str(v)] = counters[axis].get(str(v), 0) + 1
            # Scalar values.
            elif val is not None:
                s = str(val)
                if s:
                    counters[axis][s] = counters[axis].get(s, 0) + 1

    # Sort each axis descending by count.
    result: Dict[str, Dict[str, int]] = {}
    for axis, cnt in counters.items():
        sorted_items: List[Tuple[str, int]] = sorted(
            cnt.items(), key=lambda kv: (-kv[1], kv[0])
        )
        result[axis] = dict(sorted_items)

    return result
