"""Deterministic occurrence identifier helpers.

Provides a pure function to compute a deterministic ``occurrence_id``
from a ``(source_id, source_path)`` pair.  The ID is a sha256 of
the concatenation separated by a null byte, ensuring no collisions
between different sources and paths.

The scheme is identical to the one used in
``legacy/_phase6_ingest_deep_research_osint.py`` (line 817).
"""

from __future__ import annotations

import hashlib


def occurrence_id(source_id: str, source_path: str) -> str:
    """Compute a deterministic occurrence identifier.

    Arguments:
        source_id:   Platform-qualified source identifier
                     (e.g. ``github:owner/repo``).
        source_path: Exact path of the file within the source
                     (e.g. ``docs/guide.md``).

    Returns:
        The hex-encoded sha256 digest of the null-byte-joined pair.
        The caller should prefix with ``sha256:`` when writing the
        ``occurrence_id`` field.

    The ID is deterministic: same (source_id, source_path) always
    produces the same hash.

    Does not touch the filesystem.
    """
    if not source_id:
        raise ValueError("source_id is required and must be non-empty")
    if not source_path:
        raise ValueError("source_path is required and must be non-empty")
    raw = f"{source_id}\0{source_path}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def format_occurrence_id(source_id: str, source_path: str) -> str:
    """Return a full ``sha256:hex`` occurrence_id string."""
    return f"sha256:{occurrence_id(source_id, source_path)}"
