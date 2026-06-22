"""Source card generator for Obsidian graph projection.

Produces per-source Markdown cards that serve as index pages for each
source in the graph view.  Each card lists metadata and hub links
matching the paths produced by :mod:`hub_generator`.

The module is pure computation: reads ``GraphSourceRecord`` objects,
writes Markdown to an explicit output directory.  No vault mutation,
no network, no side effects outside the output directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord
from codex_vault_pipeline.graph.hub_generator import (
    HUB_AXES,
    safe_source_id,
    slugify_graph_value,
)


# ---------------------------------------------------------------------------
# Public alias
# ---------------------------------------------------------------------------


def safe_source_slug(source_id: str) -> str:
    """Convert a source ID to a filesystem-safe slug for card filenames.

    Delegates to :func:`hub_generator.safe_source_id` for consistency.
    """
    return safe_source_id(source_id)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceCardSpec:
    """Specification for a single source card to be generated.

    Attributes:
        source_id:      Original source identifier.
        title:          Display title (from ``repo_identity.full_name``
                        or *source_id* fallback).
        path:           Target output file path.
        primary_domain: Primary domain value for frontmatter.
        tags:           Ordered tuple of graph tags.
        hub_links:      Ordered tuple of hub link strings, each in the
                        format ``"Axes label: [[_graph/<axis>/<slug>]]"``.
    """

    source_id: str
    title: str
    path: Path
    primary_domain: Optional[str] = None
    tags: Tuple[str, ...] = ()
    hub_links: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_source_card(record: GraphSourceRecord, output_dir: Path) -> SourceCardSpec:
    """Build a source card specification from a source record.

    Args:
        record:     The source record.
        output_dir: Base output directory (used to derive the card path).

    Returns:
        A :class:`SourceCardSpec`.
    """
    source_id = record.source_id
    slug = safe_source_slug(source_id)
    path = output_dir / "sources" / f"{slug}.md"
    title = record.title or source_id

    # Collect tags.
    tags: List[str] = ["graph/source"]
    if record.primary_domain:
        tags.append(f"graph/domain/{slugify_graph_value(record.primary_domain)}")
    if record.artifact_role:
        tags.append(f"graph/artifact-role/{slugify_graph_value(record.artifact_role)}")
    if record.source_role:
        tags.append(f"graph/source-role/{slugify_graph_value(record.source_role)}")
    if record.authority_level:
        tags.append(f"graph/authority-level/{slugify_graph_value(record.authority_level)}")
    if record.lifecycle_status:
        tags.append(f"graph/lifecycle-status/{slugify_graph_value(record.lifecycle_status)}")

    # Collect hub links — each axis → folder mapping from HUB_AXES.
    hub_links: List[str] = []
    for attr_name, folder_name in HUB_AXES:
        val = getattr(record, attr_name, ())
        if isinstance(val, tuple):
            values: Tuple[str, ...] = val
        elif val is not None:
            values = (str(val),)
        else:
            continue

        for v in values:
            if not v:
                continue
            value_slug = slugify_graph_value(v)
            link = f"[[_graph/{folder_name}/{value_slug}|{v}]]"
            # Label the first link of each axis with the axis display name.
            hub_links.append(link)

    return SourceCardSpec(
        source_id=source_id,
        title=title,
        path=path,
        primary_domain=record.primary_domain,
        tags=tuple(sorted(tags)),
        hub_links=tuple(hub_links),
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_source_card_markdown(card: SourceCardSpec, record: GraphSourceRecord) -> str:
    """Render a source card specification as Markdown with frontmatter.

    Args:
        card:   The source card specification.
        record: The full source record (used for the metadata table).

    Returns:
        Complete Markdown text suitable for writing to *card.path*.
    """
    lines: List[str] = []
    lines.append("---")
    lines.append("graph_node_type: source")
    lines.append(f"source_id: {card.source_id}")
    if card.primary_domain:
        lines.append(f"primary_domain: {card.primary_domain}")
    if record.artifact_role:
        lines.append(f"artifact_role: {record.artifact_role}")
    lines.append("tags:")
    for tag in card.tags:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {card.title}")
    lines.append("")

    # Graph links section
    lines.append("## Graph Links")
    lines.append("")
    link_sections = _build_link_sections(card, record)
    for section_label, section_links in link_sections:
        if not section_links:
            continue
        lines.append(f"- **{section_label}**:")
        for link in section_links:
            lines.append(f"  - {link}")
    lines.append("")

    # Source metadata table
    lines.append("## Source Metadata")
    lines.append("")
    if record.raw:
        _metadata_from_raw(lines, record.raw)
    else:
        _metadata_fallback(lines, record)

    lines.append("")
    return "\n".join(lines)


def _build_link_sections(
    card: SourceCardSpec, record: GraphSourceRecord
) -> List[Tuple[str, List[str]]]:
    """Group hub links by axis for display."""
    sections: List[Tuple[str, List[str]]] = []

    # Map folder_name → display label
    folder_to_label: Dict[str, str] = {
        "domains": "Domain",
        "related-domains": "Related domain",
        "ecosystems": "Ecosystem",
        "capabilities": "Capability",
        "artifact-roles": "Artifact role",
        "source-roles": "Source role",
        "authority-levels": "Authority level",
        "lifecycle-statuses": "Lifecycle status",
        "knowledge-statuses": "Knowledge status",
    }

    # Re-derive hub links grouped by axis, using the same iteration order as
    # HUB_AXES to keep output deterministic.
    for attr_name, folder_name in HUB_AXES:
        val = getattr(record, attr_name, ())
        if isinstance(val, tuple):
            values: Tuple[str, ...] = val
        elif val is not None:
            values = (str(val),)
        else:
            continue

        links: List[str] = []
        for v in values:
            if not v:
                continue
            value_slug = slugify_graph_value(v)
            links.append(f"[[_graph/{folder_name}/{value_slug}|{v}]]")

        if links:
            label = folder_to_label.get(folder_name, folder_name)
            sections.append((label, links))

    return sections


def _metadata_from_raw(lines: List[str], raw: Dict) -> None:
    """Emit metadata table from raw YAML fields."""
    fields: List[Tuple[str, str]] = [
        ("Source ID", "source_id"),
        ("Primary domain", "primary_domain"),
        ("Related domains", "related_domains"),
        ("Ecosystems", "ecosystems"),
        ("Capabilities", "capabilities"),
        ("Topics", "topics"),
        ("Integration targets", "integration_targets"),
        ("Artifact role", "artifact_role"),
        ("Source role", "source_role"),
        ("Authority level", "authority_level"),
        ("Lifecycle status", "lifecycle_status"),
    ]
    for label, key in fields:
        val = raw.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            if not val:
                continue
            display = ", ".join(str(v) for v in val)
        else:
            display = str(val)
        lines.append(f"- **{label}**: `{display}`")


def _metadata_fallback(lines: List[str], record: GraphSourceRecord) -> None:
    """Emit metadata table from record attributes (no raw dict available)."""
    fields: List[Tuple[str, Optional[str]]] = [
        ("Source ID", record.source_id),
        ("Primary domain", record.primary_domain),
        ("Related domains", ", ".join(record.related_domains) if record.related_domains else None),
        ("Ecosystems", ", ".join(record.ecosystems) if record.ecosystems else None),
        ("Capabilities", ", ".join(record.capabilities) if record.capabilities else None),
        ("Artifact role", record.artifact_role),
        ("Source role", record.source_role),
        ("Authority level", record.authority_level),
        ("Lifecycle status", record.lifecycle_status),
    ]
    for label, val in fields:
        if val is None:
            continue
        lines.append(f"- **{label}**: `{val}`")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_source_cards(
    records: Dict[str, GraphSourceRecord],
    output_dir: Path,
) -> List[Path]:
    """Build and write all source cards to *output_dir*.

    Idempotent: writes only files whose content changed.

    Args:
        records:    Source records keyed by source_id.
        output_dir: Base output directory (``sources/`` subdir created
                    automatically).

    Returns:
        Sorted list of paths that were written.
    """
    written: List[Path] = []

    # Process in deterministic source_id order.
    for source_id in sorted(records):
        record = records[source_id]
        card = build_source_card(record, output_dir)
        card.path.parent.mkdir(parents=True, exist_ok=True)
        content = render_source_card_markdown(card, record)
        if card.path.is_file() and card.path.read_text() == content:
            continue
        card.path.write_text(content)
        written.append(card.path)

    return sorted(written)
