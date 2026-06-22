"""Read-only matcher that maps existing wiki notes to source records.

Searches wiki notes for evidence that associates them with a known
source record (by source ID, repository URL, owner/repo pair, or
filename slug).  No files are modified.

Intended to feed the :mod:`wikilink_planner` so it can generate
graph-link insertion plans even when notes lack an explicit
``source_id`` in frontmatter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codex_vault_pipeline.graph.hub_generator import safe_source_id
from codex_vault_pipeline.graph.source_reader import GraphSourceRecord

# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteSourceMatch:
    """A proposed link between a wiki note and a source record.

    Attributes:
        note_path:  Absolute path to the wiki note.
        source_id:  The matched source identifier.
        confidence: Numeric score (0-100) indicating match certainty.
        reasons:    Why the match was made (one or more reason labels).
    """

    note_path: Path
    source_id: str
    confidence: int
    reasons: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def source_match_tokens(record: GraphSourceRecord) -> Tuple[str, ...]:
    """Extract search tokens from a source record for text matching.

    Tokens returned:

    * ``source_id`` — always present
    * ``repo_url`` — if the record has a repository URL
    * ``owner_repo`` — if the source ID encodes an ``owner/repo`` pair
    * ``safe_slug`` — filesystem-safe form of the source ID

    Args:
        record:  The source record.

    Returns:
        Sorted tuple of distinct non-empty token strings.
    """
    tokens: List[str] = [record.source_id]

    if record.repo_url:
        tokens.append(record.repo_url)

    owner_repo = _extract_owner_repo(record.source_id)
    if owner_repo:
        tokens.append(owner_repo)

    tokens.append(safe_source_id(record.source_id))

    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    return tuple(deduped)


def _extract_owner_repo(source_id: str) -> Optional[str]:
    """Extract ``owner/repo`` from a GitHub-style source ID.

    ``github:owner/repo`` → ``owner/repo``
    ``website:docs.example.com`` → ``None``
    """
    parts = source_id.split(":", 1)
    if len(parts) == 2 and "/" in parts[1]:
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Single note matching
# ---------------------------------------------------------------------------


def match_note_to_source(
    note_path: Path,
    markdown_text: str,
    record: GraphSourceRecord,
) -> Optional[NoteSourceMatch]:
    """Attempt to match a single wiki note to a source record.

    Matching rules (checked in priority order):

    ==================== ============ ================
    Rule                 Confidence   Reason label
    ==================== ============ ================
    ``source_id`` in text    100      ``exact_source_id``
    ``repo_url`` in text      98      ``exact_repo_url``
    ``owner/repo`` in text    95      ``exact_owner_repo``
    safe slug in filename     92      ``filename_safe_source_slug``
    ==================== ============ ================

    If no rule matches, returns ``None``.

    Args:
        note_path:      Absolute path to the wiki note.
        markdown_text:  Full text content of the note.
        record:         The source record to test against.

    Returns:
        A :class:`NoteSourceMatch` if any rule fires, else ``None``.
    """
    reasons: List[str] = []

    # 1. Exact source_id in text.
    if record.source_id in markdown_text:
        reasons.append("exact_source_id")

    # 2. Exact repo_url in text.
    if record.repo_url and record.repo_url in markdown_text:
        reasons.append("exact_repo_url")

    # 3. Exact owner/repo in text.
    owner_repo = _extract_owner_repo(record.source_id)
    if owner_repo and owner_repo in markdown_text:
        reasons.append("exact_owner_repo")

    # 4. Safe slug in filename (stem).
    slug = safe_source_id(record.source_id)
    if slug in note_path.stem:
        reasons.append("filename_safe_source_slug")

    if not reasons:
        return None

    # Compute confidence: take the highest-scoring reason.
    confidence = _max_confidence(reasons)
    return NoteSourceMatch(
        note_path=note_path,
        source_id=record.source_id,
        confidence=confidence,
        reasons=tuple(reasons),
    )


def _max_confidence(reasons: List[str]) -> int:
    """Return the highest confidence value for a set of reason labels."""
    scores: Dict[str, int] = {
        "exact_source_id": 100,
        "exact_repo_url": 98,
        "exact_owner_repo": 95,
        "filename_safe_source_slug": 92,
    }
    best = 0
    for r in reasons:
        s = scores.get(r, 0)
        if s > best:
            best = s
    return best


# ---------------------------------------------------------------------------
# Batch matcher
# ---------------------------------------------------------------------------


def match_notes_to_sources(
    wiki_root: Path,
    records: Dict[str, GraphSourceRecord],
    *,
    min_confidence: int = 90,
) -> Tuple[NoteSourceMatch, ...]:
    """Scan wiki notes and match them to source records.

    For each markdown note under *wiki_root* (excluding ``_graph/``),
    test every source record for a match.  Returns all matches that
    meet or exceed *min_confidence*.

    Args:
        wiki_root:      Path to the ``wiki/`` directory.
        records:        Source records keyed by ``source_id``.
        min_confidence: Minimum confidence threshold (default 90).

    Returns:
        Sorted tuple of :class:`NoteSourceMatch`, ordered by
        ``(note_path, source_id)``.
    """
    notes = _find_notes(wiki_root)
    matches: List[NoteSourceMatch] = []

    for note_path in notes:
        text = note_path.read_text(encoding="utf-8")
        for record in records.values():
            m = match_note_to_source(note_path, text, record)
            if m is not None and m.confidence >= min_confidence:
                matches.append(m)

    matches.sort(key=lambda m: (str(m.note_path), m.source_id))
    return tuple(matches)


def _find_notes(wiki_root: Path) -> List[Path]:
    """Find all ``.md`` files under *wiki_root*, excluding ``_graph/``."""
    graph_dir = wiki_root / "_graph"
    notes: List[Path] = []

    for md_path in sorted(wiki_root.rglob("*.md")):
        if graph_dir in md_path.parents:
            continue
        notes.append(md_path.resolve())

    return notes
