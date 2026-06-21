#!/usr/bin/env python3
"""Codex Vault Pipeline — path resolution and shared CLI plumbing.

Every script in this package resolves a *vault root* and derives
its runtime/raw/wiki subpaths from it. The vault root is supplied
by exactly one of:

  1. ``--vault-root`` on the command line
  2. The ``CODEX_VAULT_ROOT`` environment variable
  3. A reasonable default fallback (the directory above the
     pipeline repo if it can be detected; otherwise the current
     working directory)

The intent is to remove every hardcoded absolute path so the
pipeline can be installed and run against any vault on any host.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "VaultPaths",
    "resolve_vault_root",
    "resolve_paths",
    "add_vault_root_arg",
    "require_vault_root",
]


# --- constants ------------------------------------------------------------

ENV_VAR = "CODEX_VAULT_ROOT"


# --- data class -----------------------------------------------------------


@dataclass(frozen=True)
class VaultPaths:
    """Resolved paths derived from a single vault root.

    Attributes
    ----------
    vault_root : Path
        The top-level vault directory. Must exist.
    runtime_root : Path
        ``${vault_root}/.runtime`` — all machine data.
    raw_root : Path
        ``${vault_root}/raw`` — raw source captures.
    wiki_root : Path
        ``${vault_root}/wiki`` — human-readable vault (Obsidian).
    archive_root : Path
        ``${vault_root}/archive`` — deprecated material.
    reports_root : Path
        ``${vault_root}/reports`` — vault-level reports.
    queries_root : Path
        ``${vault_root}/queries`` — vault-level query logs.
    tools_root : Path
        ``${vault_root}/.runtime/tools`` — preserved for compat.
    schemas_root : Path
        ``${vault_root}/.runtime/schemas`` — preserved for compat.
    """

    vault_root: Path
    runtime_root: Path
    raw_root: Path
    wiki_root: Path
    archive_root: Path
    reports_root: Path
    queries_root: Path
    tools_root: Path
    schemas_root: Path

    # Sub-roots of ``.runtime/`` (the v3 machine-data root).
    sources_root: Path
    artifacts_root: Path
    occurrences_root: Path
    units_root: Path
    domain_root: Path
    relations_root: Path
    indexes_root: Path
    runs_root: Path
    benchmarks_root: Path
    reports_runtime_root: Path
    quarantine_root: Path
    tmp_root: Path
    knowledge_notes_root: Path
    migration_reports_root: Path
    bundles_root: Path
    db_path: Path

    def ensure_runtime_subdirs(self) -> None:
        """Create the standard ``.runtime/`` subdirectories if missing.

        The pipeline never *deletes* data; it only ensures that the
        expected layout exists so downstream scripts can write
        records into the right places.
        """
        for sub in (
            self.runtime_root,
            self.sources_root,
            self.artifacts_root,
            self.occurrences_root,
            self.units_root,
            self.domain_root,
            self.relations_root,
            self.indexes_root,
            self.runs_root,
            self.benchmarks_root,
            self.reports_runtime_root,
            self.quarantine_root,
            self.tmp_root,
            self.knowledge_notes_root,
            self.migration_reports_root,
            self.bundles_root,
        ):
            sub.mkdir(parents=True, exist_ok=True)


# --- resolution -----------------------------------------------------------


def resolve_vault_root(
    *,
    cli_value: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the vault root from one of three sources.

    Order of precedence:

    1. ``cli_value`` (from ``--vault-root``).
    2. ``CODEX_VAULT_ROOT`` in ``env`` (or the process environment).
    3. The parent of the pipeline repo's working tree, if it looks
       like a vault (has a ``.runtime/`` subdir).

    Returns
    -------
    Path
        The resolved vault root, expanded and absolute.

    Raises
    ------
    SystemExit
        If no vault root can be resolved. The exit message tells
        the user exactly which flag or env var to set.
    """
    env = env if env is not None else os.environ

    # 1. explicit CLI value
    if cli_value:
        return Path(cli_value).expanduser().resolve()

    # 2. env var
    env_value = env.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()

    # 3. heuristic: if the current working directory's parent looks
    #    like a vault (has a .runtime/ subdir), use it. This makes
    #    the pipeline pleasant to use when it lives next to the
    #    vault (e.g. ``codex-vault-pipeline/`` sibling of
    #    ``codex-vault/``).
    cwd = Path.cwd().resolve()
    for candidate in (cwd.parent, cwd):
        if (candidate / ".runtime").is_dir():
            return candidate.resolve()

    raise SystemExit(
        f"Could not resolve vault root. Pass --vault-root <path>, "
        f"set the {ENV_VAR} environment variable, or run from a "
        f"directory whose parent is a Codex Vault (has a .runtime/ "
        f"subdirectory)."
    )


def resolve_paths(
    *,
    cli_value: str | None = None,
    env: Mapping[str, str] | None = None,
    require_exists: bool = True,
) -> VaultPaths:
    """Resolve a vault root and derive every standard subpath.

    Parameters
    ----------
    cli_value : str | None
        Value of ``--vault-root`` from the CLI (may be empty).
    env : dict[str, str] | None
        Environment mapping (defaults to ``os.environ``).
    require_exists : bool
        If True (default), the vault root must exist; otherwise
        ``SystemExit`` is raised. Set False to allow the pipeline
        to *create* the vault layout (e.g. for ``codex-vault-init``).
    """
    root = resolve_vault_root(cli_value=cli_value, env=env)
    if require_exists and not root.exists():
        raise SystemExit(f"Vault root does not exist: {root}")
    if require_exists and not root.is_dir():
        raise SystemExit(f"Vault root is not a directory: {root}")

    runtime = root / ".runtime"
    return VaultPaths(
        vault_root=root,
        runtime_root=runtime,
        raw_root=root / "raw",
        wiki_root=root / "wiki",
        archive_root=root / "archive",
        reports_root=root / "reports",
        queries_root=root / "queries",
        tools_root=runtime / "tools",
        schemas_root=runtime / "schemas",
        sources_root=runtime / "sources",
        artifacts_root=runtime / "artifacts",
        occurrences_root=runtime / "occurrences",
        units_root=runtime / "units",
        domain_root=runtime / "domain",
        relations_root=runtime / "relations",
        indexes_root=runtime / "indexes",
        runs_root=runtime / "runs",
        benchmarks_root=runtime / "benchmarks",
        reports_runtime_root=runtime / "reports",
        quarantine_root=runtime / "quarantine",
        tmp_root=runtime / "tmp",
        knowledge_notes_root=runtime / "knowledge-notes",
        migration_reports_root=runtime / "migration-reports",
        bundles_root=runtime / "bundles",
        db_path=runtime / "db" / "codex-vault.db",
    )


# --- argparse plumbing ---------------------------------------------------


def add_vault_root_arg(parser: argparse.ArgumentParser) -> None:
    """Attach ``--vault-root`` to an argparse parser.

    The default is taken from the ``CODEX_VAULT_ROOT`` environment
    variable. If neither is set, the default is an empty string;
    callers should call :func:`require_vault_root` after parsing.
    """
    parser.add_argument(
        "--vault-root",
        default=os.environ.get(ENV_VAR, ""),
        help=(
            "Path to the Codex Vault root directory. "
            f"May also be set via the {ENV_VAR} environment variable. "
            f"Defaults to the env var, else to the current working "
            f"directory's parent if it has a .runtime/ subdir."
        ),
    )


def require_vault_root(args: argparse.Namespace, *, require_exists: bool = True) -> VaultPaths:
    """Resolve :class:`VaultPaths` from parsed argparse args.

    Convenience wrapper used by the per-tool main() bodies.
    """
    return resolve_paths(
        cli_value=getattr(args, "vault_root", None) or None,
        require_exists=require_exists,
    )


# --- dry-run support -----------------------------------------------------


def is_dry_run(args: argparse.Namespace) -> bool:
    """Return True if ``--dry-run`` was set (default False)."""
    return bool(getattr(args, "dry_run", False))


def add_dry_run_arg(parser: argparse.ArgumentParser) -> None:
    """Attach ``--dry-run`` to a parser."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the actions that would be taken without writing "
            "any file, creating any directory, or mutating any state. "
            "Use to verify scope and intent before running for real."
        ),
    )
