#!/usr/bin/env python3
"""Telegram-safe query wrapper for Codex Vault unit search.

Read-only: opens the existing FTS index at
``<vault-root>/.runtime/indexes/units-fts.sqlite``, runs the query,
and prints compact plain-text results suitable for chat surfaces.

Usage::

    PYTHONPATH=src python3 scripts/query_units_chat.py \\
        --vault-root /path/to/codex-vault \\
        --query "Hermes Agent" \\
        --limit 5 \\
        --max-chars 3500

Optional::

    --source-id github:owner/repo   # filter to one source
    --json                          # output raw JSON instead of chat format
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query the Codex Vault unit FTS index for chat surfaces",
    )
    parser.add_argument(
        "--vault-root",
        required=True,
        type=str,
        help="Path to the codex-vault root directory",
    )
    parser.add_argument("--query", required=True, type=str, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=3500,
        help="Max output characters before truncation (default: 3500)",
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Optional: filter results to one source",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of chat format",
    )

    args = parser.parse_args(argv)

    # Resolve DB path
    db_path = Path(args.vault_root).resolve() / ".runtime" / "indexes" / "units-fts.sqlite"
    if not db_path.is_file():
        print(
            f"Codex Vault error: FTS index not found at {db_path}",
            file=sys.stderr,
        )
        return 1

    query = args.query.strip()
    if not query:
        print("Codex Vault error: --query must be a non-empty search string.", file=sys.stderr)
        return 2

    # Import — lazy so missing index module doesn't break argparse
    from codex_vault_pipeline.index.sqlite_fts import query_units_fts

    try:
        hits = query_units_fts(db_path, query, limit=args.limit)
    except Exception as exc:
        print(f"Codex Vault error: query failed: {exc}", file=sys.stderr)
        return 1

    # Optional source_id filter
    if args.source_id:
        hits = [h for h in hits if h.get("source_id") == args.source_id]

    if args.json:
        json.dump(hits, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    # Chat format
    from codex_vault_pipeline.query.chat_format import format_chat_results

    output = format_chat_results(hits, query, max_chars=args.max_chars)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
