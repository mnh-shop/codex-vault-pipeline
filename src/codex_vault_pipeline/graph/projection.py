"""Graph projection composer — combine source reading, hub generation,
and source card generation into a single deterministic pipeline step.

Usage::

    from pathlib import Path
    from codex_vault_pipeline.graph.projection import project_graph_from_runtime

    result = project_graph_from_runtime(
        runtime_root=Path("/path/to/.runtime"),
        output_dir=Path("/path/to/_graph"),
    )

The composer is pure operation: reads ``GraphSourceRecord`` objects from the
runtime tree, writes hub notes and source cards under *output_dir*, and returns
a frozen result dataclass with counts and written file paths.  No vault
mutation, no side effects outside *output_dir*.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from codex_vault_pipeline.graph.hub_generator import write_hubs
from codex_vault_pipeline.graph.source_card_generator import write_source_cards
from codex_vault_pipeline.graph.source_reader import read_sources_from_runtime


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphProjectionResult:
    """Immutable result of a graph projection run.

    Attributes:
        runtime_root:      The runtime tree that was read.
        output_dir:        The output directory that was written to.
        source_count:      Number of source records read.
        hub_count:         Number of hub notes written.
        source_card_count: Number of source cards written.
        files_written:     Sorted tuple of all file paths that were written
                           (hubs and source cards, combined).
    """

    runtime_root: Path
    output_dir: Path
    source_count: int
    hub_count: int
    source_card_count: int
    files_written: Tuple[Path, ...]


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def project_graph_from_runtime(
    runtime_root: Path,
    output_dir: Path,
    *,
    min_sources: int = 1,
) -> GraphProjectionResult:
    """Read source records from *runtime_root* and write graph artifacts
    under *output_dir*.

    The pipeline is:

    1. Read all source records from ``<runtime_root>/sources/*/source.v1.yaml``.
    2. Write hub notes under ``<output_dir>/`` (one sub-directory per axis).
    3. Write source cards under ``<output_dir>/sources/``.

    All writes are idempotent — files whose content has not changed since
    the previous run are skipped.  No files are created or modified outside
    *output_dir*.

    Args:
        runtime_root:  Path to the vault ``.runtime/`` directory.
        output_dir:    Base output directory for generated graph notes
                       (e.g. ``wiki/_graph/``).
        min_sources:   Minimum source count per hub note (default 1;
                       use 2+ to suppress singleton hubs).

    Returns:
        A :class:`GraphProjectionResult` with counts and written file paths.

    Raises:
        ValueError:  If any source file lacks a ``source_id``.
    """
    records = read_sources_from_runtime(runtime_root)
    source_count = len(records)

    hub_paths: List[Path] = write_hubs(records, output_dir, min_sources=min_sources)
    card_paths: List[Path] = write_source_cards(records, output_dir)

    all_files: List[Path] = sorted(hub_paths + card_paths)

    return GraphProjectionResult(
        runtime_root=runtime_root,
        output_dir=output_dir,
        source_count=source_count,
        hub_count=len(hub_paths),
        source_card_count=len(card_paths),
        files_written=tuple(all_files),
    )
