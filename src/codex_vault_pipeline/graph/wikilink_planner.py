"""Read-only planner for inserting graph wikilinks into existing wiki notes.

Determines which existing wiki notes should receive a generated
``Graph Links`` section with back-links to the Obsidian graph
projection (source cards and axis hubs).  No files are modified.

The generated section uses HTML-comment markers so it can be recognised
on subsequent runs::

    <!-- BEGIN GENERATED CODEX GRAPH LINKS -->
    ## Graph Links
    ...
    <!-- END GENERATED CODEX GRAPH LINKS -->
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codex_vault_pipeline.graph.hub_generator import (
    HUB_AXES,
    safe_source_id,
    slugify_graph_value,
)
from codex_vault_pipeline.graph.source_reader import GraphSourceRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BEGIN_MARKER = "<!-- BEGIN GENERATED CODEX GRAPH LINKS -->"
_END_MARKER = "<!-- END GENERATED CODEX GRAPH LINKS -->"

# Pattern to detect whether a note already has the generated section.
_HAS_SECTION_RE = re.compile(
    re.escape(_BEGIN_MARKER) + r".*?" + re.escape(_END_MARKER),
    re.DOTALL,
)

# Pattern to extract source_id from YAML frontmatter block.
# Looks for ``source_id: <value>`` on its own line inside the frontmatter.
_SOURCE_ID_RE = re.compile(r"^source_id:\s*(.+?)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WikilinkInsertionPlan:
    """Describes a single wiki note that *could* receive graph links.

    Attributes:
        note_path:              Absolute path to the existing wiki note.
        source_id:              The source identifier extracted from the note.
        graph_links_markdown:   The complete ``## Graph Links`` section
                                (with begin/end markers) to insert, or an
                                empty string if the section already exists.
        already_has_graph_section:  Whether the note already contains the
                                    generated section markers.
    """

    note_path: Path
    source_id: str
    graph_links_markdown: str
    already_has_graph_section: bool


# ---------------------------------------------------------------------------
# Note discovery
# ---------------------------------------------------------------------------


def find_markdown_notes(wiki_root: Path) -> Tuple[Path, ...]:
    """Find all ``.md`` files under *wiki_root*, excluding ``_graph/``.

    Args:
        wiki_root:  Path to the ``wiki/`` directory of the vault.

    Returns:
        Sorted tuple of absolute ``.md`` file paths.
    """
    graph_dir = wiki_root / "_graph"
    notes: List[Path] = []

    for md_path in sorted(wiki_root.rglob("*.md")):
        # Skip files inside wiki/_graph/.
        if graph_dir in md_path.parents:
            continue
        notes.append(md_path.resolve())

    return tuple(notes)


# ---------------------------------------------------------------------------
# Source ID extraction
# ---------------------------------------------------------------------------


def extract_source_id_from_note(markdown_text: str) -> Optional[str]:
    """Extract the ``source_id`` value from YAML frontmatter.

    Looks for a ``source_id: <value>`` line within the frontmatter block
    delimited by ``---`` at the start of the file.

    Handles bare values and single/double-quoted values::

        source_id: github:owner/repo
        source_id: "github:owner/repo"

    Args:
        markdown_text:  Full content of a wiki note.

    Returns:
        The extracted source ID, or ``None`` if not found.
    """
    # Frontmatter: first --- to second --- at the start of the file.
    fm_match = re.match(r"^---\s*\n(.*?)\n---", markdown_text, re.DOTALL)
    if not fm_match:
        return None

    frontmatter = fm_match.group(1)
    sid_match = _SOURCE_ID_RE.search(frontmatter)
    if not sid_match:
        return None

    raw = sid_match.group(1).strip()
    # Strip surrounding quotes.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    return raw.strip()


# ---------------------------------------------------------------------------
# Graph links section renderer
# ---------------------------------------------------------------------------


def render_graph_links_section(record: GraphSourceRecord) -> str:
    """Render the ``## Graph Links`` section for a source record.

    Produces a Markdown section wrapped in begin/end markers::

        <!-- BEGIN GENERATED CODEX GRAPH LINKS -->
        ## Graph Links

        - **Source card**: [[_graph/sources/<slug>|source card]]
        - **Domain**: [[_graph/domains/<slug>|<value>]]

        ... (more axis links)

        <!-- END GENERATED CODEX GRAPH LINKS -->

    Args:
        record:  The source record for which to generate links.

    Returns:
        Complete Markdown section text.
    """
    lines: List[str] = []
    lines.append(_BEGIN_MARKER)
    lines.append("## Graph Links")
    lines.append("")

    # Source card link.
    slug = safe_source_id(record.source_id)
    lines.append(f"- **Source card**: [[_graph/sources/{slug}|source card]]")

    # Hub links — iterate in HUB_AXES order for deterministic output.
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
            lines.append(f"- **{_axis_label(folder_name)}**: [[_graph/{folder_name}/{value_slug}|{v}]]")

    lines.append("")
    lines.append(_END_MARKER)
    return "\n".join(lines)


def _axis_label(folder_name: str) -> str:
    """Derive a human-readable axis label from the folder name."""
    labels: Dict[str, str] = {
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
    return labels.get(folder_name, folder_name)


# ---------------------------------------------------------------------------
# Planner orchestrator
# ---------------------------------------------------------------------------


def plan_wikilink_insertions(
    wiki_root: Path,
    records: Dict[str, GraphSourceRecord],
) -> Tuple[WikilinkInsertionPlan, ...]:
    """Scan wiki notes and plan graph-link insertions.

    For each markdown note under *wiki_root* (excluding ``_graph/``):

    1. Extract ``source_id`` from YAML frontmatter.
    2. If present and matching a known source record, create a plan.
    3. If the note already has a generated graph section (detected by
       begin/end markers), mark ``already_has_graph_section=True`` and
       provide an empty ``graph_links_markdown``.
    4. Otherwise, render the graph links section and provide it in the plan.

    Args:
        wiki_root:  Path to the ``wiki/`` directory.
        records:    Source records keyed by ``source_id`` (from
                    :func:`~codex_vault_pipeline.graph.source_reader.read_sources_from_runtime`).

    Returns:
        Sorted tuple of :class:`WikilinkInsertionPlan`, ordered by
        ``source_id``.
    """
    notes = find_markdown_notes(wiki_root)
    plans: List[WikilinkInsertionPlan] = []

    for note_path in notes:
        text = note_path.read_text(encoding="utf-8")
        source_id = extract_source_id_from_note(text)

        if source_id is None:
            continue
        if source_id not in records:
            continue

        already_has = bool(_HAS_SECTION_RE.search(text))

        if already_has:
            graph_links = ""
        else:
            record = records[source_id]
            graph_links = render_graph_links_section(record)

        plans.append(
            WikilinkInsertionPlan(
                note_path=note_path,
                source_id=source_id,
                graph_links_markdown=graph_links,
                already_has_graph_section=already_has,
            )
        )

    # Sort by source_id for deterministic output.
    plans.sort(key=lambda p: p.source_id)
    return tuple(plans)
