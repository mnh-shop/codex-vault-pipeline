"""Chat-safe formatting for unit FTS query results.

Produces compact plain-text output suitable for Telegram, Slack, or
other chat surfaces — no markup, no rich formatting.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_chat_results(
    hits: List[Dict[str, Any]],
    query: str,
    max_chars: int = 3500,
) -> str:
    """Format FTS query hits into a compact chat-safe string.

    Args:
        hits:     Result list from :func:`query_units_fts`.
        query:    Original user query (shown in header).
        max_chars: Maximum output length before truncation.

    Returns:
        Plain-text string, ready to send over a chat surface.
    """
    parts: List[str] = [f"Codex Vault results for: {query}", ""]

    for i, h in enumerate(hits, 1):
        sid = h.get("source_id", "?")
        utype = h.get("unit_type", "?")
        spath = h.get("source_path", "?")
        title = h.get("title", "")
        preview = (h.get("text_preview") or "").replace("\n", " ")

        block = (
            f"{i}. {sid}"
            f"\n   type: {utype}"
            f"\n   path: {spath}"
            f"\n   title: {title}"
        )
        if preview:
            # single-line preview, truncated per block to keep compact
            if len(preview) > 200:
                preview = preview[:197] + "..."
            block += f"\n   {preview}"

        parts.append(block)

    parts.append("")
    parts.append(f"Showing {len(hits)} results.")

    output = "\n".join(parts)

    if len(output) > max_chars:
        output = output[: max_chars - 60]
        output += "\n... truncated; refine query or lower limit"

    return output


def format_chat_error(message: str) -> str:
    """Format an error message as a chat-safe string."""
    return f"Codex Vault error: {message}"
