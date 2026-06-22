"""Hub note generator for Obsidian graph projection.

Produces Markdown hub notes that group sources by shared taxonomy axes
(domain, ecosystem, capability, role, …).  Each hub lists the sources
that share that value and links to their future source-card path.

The module is pure computation: reads ``GraphSourceRecord`` objects,
writes Markdown to an explicit output directory.  No vault mutation,
no network, no side effects outside the output directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord

# ---------------------------------------------------------------------------
# Axis definitions
# ---------------------------------------------------------------------------
# Each entry: (attr_name_on_GraphSourceRecord, folder_name_in_output)

HUB_AXES: Tuple[Tuple[str, str], ...] = (
    ("primary_domain", "domains"),
    ("related_domains", "related-domains"),
    ("ecosystems", "ecosystems"),
    ("capabilities", "capabilities"),
    ("artifact_role", "artifact-roles"),
    ("source_role", "source-roles"),
    ("authority_level", "authority-levels"),
    ("lifecycle_status", "lifecycle-statuses"),
    ("knowledge_status", "knowledge-statuses"),
)

# ----- Safe filename helpers ----------------------------------------------


def slugify_graph_value(value: str) -> str:
    """Convert a human-readable graph-axis value into a filesystem-safe slug.

    Examples::

        slugify_graph_value("deep-research")         → "deep-research"
        slugify_graph_value("LangChain")              → "langchain"
        slugify_graph_value("AI/ML")                  → "ai-ml"
        slugify_graph_value("n8n Workflows")          → "n8n-workflows"
    """
    safe = value.lower().strip()
    # Replace slashes and runs of non-alphanumeric (except hyphens) with '-'
    result: List[str] = []
    for ch in safe:
        if ch.isalnum() or ch == "-":
            result.append(ch)
        else:
            # Use '-' for separators, collapse later
            result.append("-")
    collapsed = _collapse_hyphens("".join(result))
    return collapsed.strip("-")


def _collapse_hyphens(s: str) -> str:
    """Collapse consecutive hyphens into one."""
    result: List[str] = []
    prev_hyphen = False
    for ch in s:
        if ch == "-":
            if not prev_hyphen:
                result.append(ch)
            prev_hyphen = True
        else:
            result.append(ch)
            prev_hyphen = False
    return "".join(result)


def safe_source_id(source_id: str) -> str:
    """Convert a source ID into a safe slug suitable for filenames.

    This mirrors the convention used by ``checkpoints.safe_source_filename``:
    ``github:owner/repo`` → ``github-owner-repo``.
    """
    safe = source_id.replace(":", "-").replace("/", "-").replace("\\", "-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-")
    return safe


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HubSpec:
    """Specification for a single hub note to be generated.

    Attributes:
        axis:       Graph axis name (e.g. ``"domains"``).
        value:      The raw value being grouped (e.g. ``"deep-research"``).
        title:      Human-readable title (e.g. ``"Domain: deep-research"``).
        path:       Target output file path.
        source_ids: Source IDs that share this value, in sorted order.
        count:      Number of sources.
    """

    axis: str
    value: str
    title: str
    path: Path
    source_ids: Tuple[str, ...]
    count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "count", len(self.source_ids))


# ---------------------------------------------------------------------------
# Collect hubs
# ---------------------------------------------------------------------------


def collect_hubs(
    records: Dict[str, GraphSourceRecord],
    output_dir: Path,
    *,
    min_sources: int = 1,
) -> List[HubSpec]:
    """Collect hub specifications from source records.

    Args:
        records:     Source records keyed by source_id.
        output_dir:  Base directory for generated hub notes (used to derive
                     each HubSpec's path).
        min_sources: Minimum number of sources required to generate a hub
                     (default 1; use 2+ to suppress singleton hubs).

    Returns:
        List of :class:`HubSpec`, ordered by (axis, value).
    """
    # Group by (axis_folder, value)
    groups: Dict[Tuple[str, str], List[str]] = {}

    for source_id, rec in records.items():
        for attr_name, folder_name in HUB_AXES:
            val = getattr(rec, attr_name, ())
            if isinstance(val, tuple):
                values: Tuple[str, ...] = val
            elif val is not None:
                values = (str(val),)
            else:
                continue

            for v in values:
                if not v:
                    continue
                key = (folder_name, str(v))
                if key not in groups:
                    groups[key] = []
                groups[key].append(source_id)

    # Build HubSpec list
    specs: List[HubSpec] = []
    for (axis, value), source_ids in groups.items():
        if len(source_ids) < min_sources:
            continue
        slug = slugify_graph_value(value)
        path = output_dir / axis / f"{slug}.md"
        # Title: capitalize axis name for display
        title = f"{axis.rstrip('s').title()}: {value}"
        specs.append(
            HubSpec(
                axis=axis,
                value=value,
                title=title,
                path=path,
                source_ids=tuple(sorted(source_ids)),
            )
        )

    specs.sort(key=lambda s: (s.axis, s.value))
    return specs


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_hub_markdown(hub: HubSpec) -> str:
    """Render a hub specification as a Markdown note with frontmatter.

    Args:
        hub:  The hub specification.

    Returns:
        Complete Markdown text suitable for writing to *hub.path*.
    """
    # Tags
    axis_tag = f"graph/axis/{hub.axis}" if "/" not in hub.axis else f"graph/axis/{hub.axis}"
    value_slug = slugify_graph_value(hub.value)
    value_tag = f"graph/{hub.axis}/{value_slug}"

    lines: List[str] = []
    lines.append("---")
    lines.append(f"graph_node_type: hub")
    lines.append(f"graph_axis: {hub.axis}")
    lines.append(f"graph_value: {hub.value}")
    lines.append(f"source_count: {hub.count}")
    lines.append("tags:")
    lines.append("  - graph/hub")
    lines.append(f"  - {axis_tag}")
    lines.append(f"  - {value_tag}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {hub.title}")
    lines.append("")
    lines.append("## Sources")
    lines.append("")

    for sid in hub.source_ids:
        safe = safe_source_id(sid)
        link = f"_graph/sources/{safe}"
        lines.append(f"- [[{link}|{sid}]]")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_hubs(
    records: Dict[str, GraphSourceRecord],
    output_dir: Path,
    *,
    min_sources: int = 1,
) -> List[Path]:
    """Collect and write all hub notes to *output_dir*.

    Idempotent: writes only the files that need to exist.  Leaves
    unmanaged files in *output_dir* untouched.

    Args:
        records:     Source records keyed by source_id.
        output_dir:  Base directory for generated hub notes.
        min_sources: Minimum source count per hub.

    Returns:
        Sorted list of paths that were written.
    """
    hubs = collect_hubs(records, output_dir, min_sources=min_sources)
    written: List[Path] = []

    for hub in hubs:
        hub.path.parent.mkdir(parents=True, exist_ok=True)
        content = render_hub_markdown(hub)
        # Idempotent: only write if content changed.
        if hub.path.is_file() and hub.path.read_text() == content:
            continue
        hub.path.write_text(content)
        written.append(hub.path)

    return sorted(written)
