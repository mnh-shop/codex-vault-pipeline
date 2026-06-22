"""Deterministic writer for Obsidian graph colour groups.

Produces the ``graph.json`` colour-group configuration that Obsidian
uses to tint nodes in the graph view.  The module is pure computation:
build a config dict from :class:`GraphColorGroup` specs, write it to
an explicit path.  No vault mutation, no network, no side effects
outside the target path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphColorGroup:
    """A single colour group for Obsidian's graph view.

    Attributes:
        name:   Human-readable label (e.g. ``"hermes-agent"``).
        query:  Obsidian tag query (e.g. ``"tag:#graph/domain/hermes-agent"``).
        color:  Hex colour string (e.g. ``"#1f4e79"``).
    """

    name: str
    query: str
    color: str


# ---------------------------------------------------------------------------
# Default groups
# ---------------------------------------------------------------------------

# Mapping: display name → (tag query, hex colour)
_DEFAULT_GROUPS: Tuple[Tuple[str, str, str], ...] = (
    ("hermes-agent", "tag:#graph/domain/hermes-agent", "#1f4e79"),
    ("n8n", "tag:#graph/domain/n8n", "#2e7d32"),
    ("agentfield", "tag:#graph/domain/agentfield", "#6a1b9a"),
    ("deep-research", "tag:#graph/domain/deep-research", "#ef6c00"),
    ("osint", "tag:#graph/domain/osint", "#b71c1c"),
    ("coding-agents", "tag:#graph/domain/coding-agents", "#455a64"),
    ("memory-systems", "tag:#graph/domain/memory-systems", "#00838f"),
    ("source", "tag:#graph/source", "#607d8b"),
    ("hub", "tag:#graph/hub", "#f9a825"),
    ("source-catalog", "tag:#graph/artifact-role/source-catalog", "#00acc1"),
    ("canonical", "tag:#graph/knowledge-status/canonical", "#ffd54f"),
    ("candidate", "tag:#graph/knowledge-status/candidate", "#9e9e9e"),
)


def default_color_groups() -> Tuple[GraphColorGroup, ...]:
    """Return the standard set of graph colour groups.

    Returns a tuple of 12 :class:`GraphColorGroup` instances covering
    the seven vault domains plus source/hub/role/status groups.
    """
    return tuple(
        GraphColorGroup(name=name, query=query, color=color)
        for name, query, color in _DEFAULT_GROUPS
    )


# ---------------------------------------------------------------------------
# Colour conversion
# ---------------------------------------------------------------------------


def hex_to_rgb_int(hex_color: str) -> int:
    """Convert a hex colour string to a 24-bit RGB integer.

    ``"#1f4e79"`` → ``2051705``  (``R=0x1f``, ``G=0x4e``, ``B=0x79``)

    Args:
        hex_color:  Colour string starting with ``#`` (e.g. ``"#RRGGBB"``).

    Returns:
        24-bit RGB integer suitable for Obsidian's ``color.rgb`` field.
    """
    return int(hex_color.lstrip("#"), 16)


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def build_obsidian_graph_config(
    color_groups: Optional[Tuple[GraphColorGroup, ...]] = None,
) -> Dict[str, Any]:
    """Build an Obsidian ``graph.json`` configuration dict.

    Args:
        color_groups:  Sequence of colour groups.  Defaults to
                       :func:`default_color_groups()`.

    Returns:
        A dict with a single ``"colorGroups"`` key, suitable for
        serialisation to ``graph.json``.

    Example output::

        {
            "colorGroups": [
                {
                    "query": "tag:#graph/domain/deep-research",
                    "color": {
                        "a": 1,
                        "rgb": 15625216
                    }
                }
            ]
        }
    """
    if color_groups is None:
        color_groups = default_color_groups()

    groups: List[Dict[str, Any]] = []
    for group in color_groups:
        rgb = hex_to_rgb_int(group.color)
        groups.append(
            {
                "query": group.query,
                "color": {
                    "a": 1,
                    "rgb": rgb,
                },
            }
        )

    return {"colorGroups": groups}


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_obsidian_graph_config(path: Path, config: Dict[str, Any]) -> Path:
    """Write an Obsidian ``graph.json`` configuration to *path*.

    The write is atomic: content is first written to a temporary file
    in the same directory, then renamed to the target path.  This
    prevents partial writes from corrupting the configuration.

    Args:
        path:   Target file path (e.g. ``.obsidian/graph.json``).
        config: Configuration dict (from :func:`build_obsidian_graph_config`).

    Returns:
        *path*, resolved, for convenience.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    serialised = json.dumps(config, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(serialised)
    tmp.replace(path)

    return path
