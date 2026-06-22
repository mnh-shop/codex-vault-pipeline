"""v2 context packer — turns v2 FTS results into compact, provenance-rich context bundles.

Uses the v2 Repomix pack index, not the noisy Obsidian graph and not legacy retrieval.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from codex_vault_pipeline.v2.pack_index import get_db, search_fts

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOKENS = 8000
DEFAULT_MAX_ITEMS = 50
DEFAULT_PER_SOURCE_LIMIT = 15
DEFAULT_PER_FILE_LIMIT = 5

# README demotion: unless query mentions these terms, README gets low priority
README_QUERY_TERMS = re.compile(
    r"(readme|setup|install|overview|getting.started|quick.start|docs)",
    re.IGNORECASE,
)

# Generated catalog demotion: unless query mentions these terms
CATALOG_QUERY_TERMS = re.compile(
    r"(catalog|index|list|directory|table.of.contents)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ContextPackItem:
    """A single item in a v2 context pack."""

    source_id: str
    path: str
    artifact_role: str
    priority_class: str
    chunk_id: int
    rank: int
    score: float
    token_estimate: int
    text: str
    heading_or_symbol: Optional[str] = None

    # Provenance
    repo_url: Optional[str] = None
    local_path: Optional[str] = None
    commit_or_revision: Optional[str] = None

    # Recommended use
    recommended_use: str = "general"

    # Safety
    safety_status: str = "clean"

    # Flags
    is_readme: bool = False
    is_generated_catalog: bool = False
    is_quarantined: bool = False
    is_secret_risk: bool = False
    is_workflow_json: bool = False
    is_skill_file: bool = False
    is_soul_file: bool = False
    is_code_file: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "path": self.path,
            "artifact_role": self.artifact_role,
            "priority_class": self.priority_class,
            "chunk_id": self.chunk_id,
            "rank": self.rank,
            "score": self.score,
            "token_estimate": self.token_estimate,
            "text": self.text[:2000] + "..." if len(self.text) > 2000 else self.text,
            "heading_or_symbol": self.heading_or_symbol,
            "repo_url": self.repo_url,
            "local_path": self.local_path,
            "commit_or_revision": self.commit_or_revision,
            "recommended_use": self.recommended_use,
            "safety_status": self.safety_status,
            "flags": {
                "readme": self.is_readme,
                "generated_catalog": self.is_generated_catalog,
                "quarantine": self.is_quarantined,
                "secret_risk": self.is_secret_risk,
                "workflow_json": self.is_workflow_json,
                "skill_file": self.is_skill_file,
                "soul_file": self.is_soul_file,
                "code_file": self.is_code_file,
            },
        }


@dataclass
class ContextPack:
    """A v2 context pack with provenance and metadata."""

    pack_id: str
    query: str
    generated_at: str
    retrieval_method: str
    total_results_considered: int
    selected_items_count: int
    token_budget: int
    estimated_tokens: int
    items: list[ContextPackItem] = field(default_factory=list)

    # Summaries
    source_coverage: dict[str, int] = field(default_factory=dict)
    artifact_role_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pack_id": self.pack_id,
            "query": self.query,
            "generated_at": self.generated_at,
            "retrieval_method": self.retrieval_method,
            "total_results_considered": self.total_results_considered,
            "selected_items_count": self.selected_items_count,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "source_coverage": self.source_coverage,
            "artifact_role_summary": self.artifact_role_summary,
            "warnings": self.warnings,
            "items": [i.to_dict() for i in self.items],
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        """Convert to markdown suitable for pasting into Codex/Claude Code."""
        lines = [
            f"# Context Pack: {self.query}",
            "",
            f"**Generated:** {self.generated_at}",
            f"**Retrieval:** {self.retrieval_method}",
            f"**Results considered:** {self.total_results_considered}",
            f"**Items selected:** {self.selected_items_count}",
            f"**Estimated tokens:** {self.estimated_tokens} / {self.token_budget}",
            "",
        ]

        # Source coverage
        if self.source_coverage:
            lines.append("## Source Coverage")
            for src, count in sorted(self.source_coverage.items()):
                lines.append(f"- {src}: {count} chunks")
            lines.append("")

        # Artifact role summary
        if self.artifact_role_summary:
            lines.append("## Artifact Roles")
            for role, count in sorted(self.artifact_role_summary.items()):
                lines.append(f"- {role}: {count}")
            lines.append("")

        # Warnings
        if self.warnings:
            lines.append("## Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        # Context items
        lines.append("## Context Items")
        lines.append("")

        for i, item in enumerate(self.items, 1):
            lines.append(f"### {i}. [{item.source_id}] {item.path}")
            lines.append(f"- **Role:** {item.artifact_role}")
            lines.append(f"- **Priority:** {item.priority_class}")
            lines.append(f"- **Rank:** {item.rank}")
            lines.append(f"- **Tokens:** {item.token_estimate}")
            if item.heading_or_symbol:
                lines.append(f"- **Section:** {item.heading_or_symbol}")
            if item.repo_url:
                lines.append(f"- **Repo:** {item.repo_url}")
            elif item.local_path:
                lines.append(f"- **Local:** {item.local_path}")
            if item.commit_or_revision:
                lines.append(f"- **Commit:** {item.commit_or_revision}")
            lines.append(f"- **Use:** {item.recommended_use}")
            lines.append("")
            lines.append("```")
            lines.append(item.text[:3000])
            if len(item.text) > 3000:
                lines.append("... (truncated)")
            lines.append("```")
            lines.append("")

        # Suggested use notes
        lines.append("## Suggested Use")
        lines.append("")
        lines.append("- Paste relevant sections into your coding agent context")
        lines.append("- Source provenance is included for verification")
        lines.append("- Generated catalogs and READMEs are demoted unless relevant")
        lines.append("")

        return "\n".join(lines)

    def write(self, path: Path) -> None:
        """Write context pack to file (JSON or markdown based on extension)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text(self.to_json())
        else:
            path.write_text(self.to_markdown())


# ---------------------------------------------------------------------------
# Pack metadata lookup
# ---------------------------------------------------------------------------

def _get_pack_meta(conn: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    """Get pack metadata for a source_id."""
    row = conn.execute(
        "SELECT repo_url, local_path, commit_or_revision FROM packs WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if row:
        return {"repo_url": row[0], "local_path": row[1], "commit_or_revision": row[2]}
    return None


def _get_file_flags(conn: sqlite3.Connection, path: str, source_id: str) -> dict[str, bool]:
    """Get file flags from pack_files."""
    row = conn.execute(
        """SELECT is_readme, is_generated_catalog, is_workflow_json,
                  is_skill_file, is_soul_file, is_code_file
           FROM pack_files WHERE path = ? AND source_id = ? LIMIT 1""",
        (path, source_id),
    ).fetchone()
    if row:
        return {
            "is_readme": bool(row[0]),
            "is_generated_catalog": bool(row[1]),
            "is_workflow_json": bool(row[2]),
            "is_skill_file": bool(row[3]),
            "is_soul_file": bool(row[4]),
            "is_code_file": bool(row[5]),
        }
    return {
        "is_readme": False,
        "is_generated_catalog": False,
        "is_workflow_json": False,
        "is_skill_file": False,
        "is_soul_file": False,
        "is_code_file": False,
    }


# ---------------------------------------------------------------------------
# Ranking and filtering
# ---------------------------------------------------------------------------

def _should_demote_readme(query: str) -> bool:
    """Check if README should be demoted for this query."""
    return not README_QUERY_TERMS.search(query)


def _should_demote_catalog(query: str) -> bool:
    """Check if generated catalog should be demoted for this query."""
    return not CATALOG_QUERY_TERMS.search(query)


def _compute_score(
    rank: int,
    artifact_role: str,
    priority_class: str,
    query: str,
    flags: dict[str, bool],
) -> float:
    """Compute a ranking score for a search result."""
    # Start with FTS rank (lower is better, negate for higher-is-better)
    base_score = 1.0 / (1.0 + abs(rank))

    # Role bonuses
    role_bonuses = {
        "skill": 0.3,
        "soul": 0.3,
        "n8n_workflow": 0.2,
        "code": 0.15,
        "docs": 0.1,
        "config": 0.05,
    }
    bonus = role_bonuses.get(artifact_role, 0.0)

    # Demotion penalties
    penalty = 0.0
    if flags.get("is_readme") and _should_demote_readme(query):
        penalty += 0.4
    if flags.get("is_generated_catalog") and _should_demote_catalog(query):
        penalty += 0.5

    # Priority bonuses
    priority_bonuses = {"high": 0.2, "normal": 0.0, "low": -0.1}
    priority_bonus = priority_bonuses.get(priority_class, 0.0)

    return base_score + bonus + priority_bonus - penalty


def _select_items(
    candidates: list[ContextPackItem],
    token_budget: int,
    max_items: int,
    per_source_limit: int,
    per_file_limit: int,
) -> tuple[list[ContextPackItem], list[str]]:
    """Select items within token budget with diversity constraints."""
    selected: list[ContextPackItem] = []
    warnings: list[str] = []

    # Sort by score descending
    candidates.sort(key=lambda x: x.score, reverse=True)

    source_counts: dict[str, int] = defaultdict(int)
    file_counts: dict[str, tuple[str, int]] = defaultdict(lambda: ("", 0))
    total_tokens = 0

    for item in candidates:
        if len(selected) >= max_items:
            warnings.append(f"Max items ({max_items}) reached")
            break

        if total_tokens + item.token_estimate > token_budget:
            # Try to fit smaller items
            remaining = token_budget - total_tokens
            if item.token_estimate > remaining:
                continue

        # Per-source limit
        if source_counts[item.source_id] >= per_source_limit:
            continue

        # Per-file limit
        file_key = f"{item.source_id}:{item.path}"
        if file_counts[file_key][1] >= per_file_limit:
            continue

        selected.append(item)
        total_tokens += item.token_estimate
        source_counts[item.source_id] += 1
        file_counts[file_key] = (item.path, file_counts[file_key][1] + 1)

    omitted = len(candidates) - len(selected)
    if omitted > 0:
        warnings.append(f"{omitted} items omitted (budget: {token_budget}, max_items: {max_items})")

    return selected, warnings


# ---------------------------------------------------------------------------
# Main pack function
# ---------------------------------------------------------------------------

def pack_context(
    db_path: str | Path,
    query: str,
    *,
    source_id: str | None = None,
    artifact_role: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_items: int = DEFAULT_MAX_ITEMS,
    per_source_limit: int = DEFAULT_PER_SOURCE_LIMIT,
    per_file_limit: int = DEFAULT_PER_FILE_LIMIT,
) -> ContextPack:
    """Pack context from v2 FTS index.

    Args:
        db_path: Path to v2 pack index SQLite DB.
        query: User query string.
        source_id: Optional source filter.
        artifact_role: Optional artifact role filter.
        max_tokens: Maximum token budget.
        max_items: Maximum number of items.
        per_source_limit: Maximum chunks per source.
        per_file_limit: Maximum chunks per file.

    Returns:
        ContextPack with selected items and metadata.
    """
    conn = get_db(db_path)

    # Search FTS
    search_limit = max_items * 3  # get more candidates than needed
    raw_results = search_fts(conn, query, limit=search_limit, source_id=source_id)

    # Build candidates
    candidates: list[ContextPackItem] = []
    for r in raw_results:
        # Apply artifact role filter
        if artifact_role and r["artifact_role"] != artifact_role:
            continue

        # Get file flags
        flags = _get_file_flags(conn, r["path"], r["source_id"])

        # Get pack metadata
        meta = _get_pack_meta(conn, r["source_id"])

        # Compute score
        score = _compute_score(
            r["rank"], r["artifact_role"], r["priority_class"], query, flags
        )

        item = ContextPackItem(
            source_id=r["source_id"],
            path=r["path"],
            artifact_role=r["artifact_role"],
            priority_class=r["priority_class"],
            chunk_id=r["chunk_id"],
            rank=r["rank"],
            score=score,
            token_estimate=r.get("token_estimate", len(r.get("snippet", "")) // 4),
            text=r.get("snippet", ""),
            heading_or_symbol=r.get("heading_or_symbol"),
            repo_url=meta["repo_url"] if meta else None,
            local_path=meta["local_path"] if meta else None,
            commit_or_revision=meta["commit_or_revision"] if meta else None,
            recommended_use=_infer_recommended_use(r["artifact_role"], query),
            safety_status="clean",
            is_readme=flags["is_readme"],
            is_generated_catalog=flags["is_generated_catalog"],
            is_workflow_json=flags["is_workflow_json"],
            is_skill_file=flags["is_skill_file"],
            is_soul_file=flags["is_soul_file"],
            is_code_file=flags["is_code_file"],
        )
        candidates.append(item)

    conn.close()

    # Select items with budget
    selected, warnings = _select_items(
        candidates, max_tokens, max_items, per_source_limit, per_file_limit
    )

    # Compute summaries
    source_coverage: dict[str, int] = defaultdict(int)
    artifact_role_summary: dict[str, int] = defaultdict(int)
    for item in selected:
        source_coverage[item.source_id] += 1
        artifact_role_summary[item.artifact_role] += 1

    # Build pack
    pack = ContextPack(
        pack_id=f"v2ctx_{int(time.time())}",
        query=query,
        generated_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="fts",
        total_results_considered=len(candidates),
        selected_items_count=len(selected),
        token_budget=max_tokens,
        estimated_tokens=sum(i.token_estimate for i in selected),
        items=selected,
        source_coverage=dict(source_coverage),
        artifact_role_summary=dict(artifact_role_summary),
        warnings=warnings,
    )

    return pack


def _infer_recommended_use(artifact_role: str, query: str) -> str:
    """Infer recommended use from artifact role and query."""
    role_uses = {
        "skill": "skill implementation reference",
        "soul": "agent personality/behavior reference",
        "n8n_workflow": "workflow automation reference",
        "code": "code implementation reference",
        "docs": "documentation reference",
        "config": "configuration reference",
        "readme": "project overview reference",
        "generated_catalog": "catalog/index reference",
    }
    return role_uses.get(artifact_role, "general reference")
