#!/usr/bin/env python3
"""Codex Vault Pipeline — unified CLI entrypoint.

Every subcommand is a thin wrapper around a refactored
``codex_vault_pipeline.legacy.*`` script. The wrappers set
``CODEX_VAULT_ROOT`` (or pass ``--vault-root``) so the
underlying scripts resolve every subpath from the single
vault root.

Usage:

    codex-vault-validate --vault-root /path/to/codex-vault
    codex-vault-ingest    --vault-root /path/to/codex-vault --github URL [--dry-run]
    codex-vault-build-indexes --vault-root /path/to/codex-vault [--no-vector]
    codex-vault-benchmark --vault-root /path/to/codex-vault [--quick]
    codex-vault-query-units --vault-root /path/to/codex-vault --query <text> [--limit N] [--source-id SID] [--json]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from codex_vault_pipeline.ingest.batch import load_batch_config, validate_batch_config
from codex_vault_pipeline.ingest.checkpoints import list_checkpoints
from codex_vault_pipeline.paths import (
    ENV_VAR,
    add_dry_run_arg,
    add_vault_root_arg,
    is_dry_run,
    require_vault_root,
    resolve_paths,
)


# Subcommand registry. Each entry maps a command name to:
#   - a function that implements the command
#   - a list of argparse arguments to add
SUBCOMMANDS: dict = {}


def subcommand(name: str, *, help: str):
    """Decorator: register a function as a subcommand."""
    def deco(fn):
        SUBCOMMANDS[name] = {"fn": fn, "help": help}
        return fn
    return deco


# --- shared helpers ------------------------------------------------------


def _run_legacy(module_name: str, argv: list[str], *, dry_run: bool = False) -> int:
    """Invoke a legacy script's main() with a synthesized argv.

    The module is imported lazily (only when the subcommand
    runs) so the CLI stays importable even when optional
    dependencies of a particular legacy script are missing.

    Parameters
    ----------
    module_name : str
        Dotted path like ``codex_vault_pipeline.legacy.validate``.
    argv : list[str]
        The argv to pass to the script's argparse.
    dry_run : bool
        If True and the script supports a ``--dry-run`` flag,
        it will be appended to argv.

    Returns
    -------
    int
        The script's exit code.
    """
    import importlib

    # Set up the environment so the legacy script's module-level
    # ``Path(os.environ.get("CODEX_VAULT_ROOT") or ".")`` resolves
    # to the right vault. Without this, scripts that compute
    # their ``RUNTIME`` global at import time would point to the
    # current working directory.
    for a in argv:
        if a == "--vault-root" or a.startswith("--vault-root="):
            value = a.split("=", 1)[1] if "=" in a else argv[argv.index(a) + 1]
            os.environ[ENV_VAR] = value
            break

    # Use importlib.import_module (not runpy.run_module) so that
    # the module lands in sys.modules with its real __name__. This
    # matters for @dataclass, which inspects sys.modules at class
    # creation time to resolve forward references.
    mod = importlib.import_module(module_name)
    main_fn = getattr(mod, "main", None)
    if main_fn is None:
        print(f"ERROR: {module_name} has no main()", file=sys.stderr)
        return 1

    saved_argv = sys.argv
    try:
        sys.argv = [module_name.rsplit(".", 1)[-1] + ".py"] + argv
        result = main_fn()
        # Some legacy scripts return a dict (e.g. build_indexes
        # returns a manifest); others return an int. Pass through
        # the dict, coerce None to 0, coerce int to int.
        if result is None:
            return 0
        if isinstance(result, int):
            return result
        if isinstance(result, dict):
            return 0  # success; the dict is the manifest payload
        return 0
    finally:
        sys.argv = saved_argv


# --- subcommands ---------------------------------------------------------


@subcommand("validate", help="Run the strict 20-rule validator against the vault's .runtime/")
def cmd_validate(args: argparse.Namespace) -> int:
    paths = require_vault_root(args)
    if is_dry_run(args):
        print(f"[dry-run] would validate: data_root={paths.runtime_root}")
        print(f"[dry-run] schemas_dir={paths.schemas_root}")
        return 0
    argv = [
        "--vault-root", str(paths.vault_root),
        "--data-root", str(paths.runtime_root),
    ]
    if args.strict:
        argv.append("--strict")
    return _run_legacy("codex_vault_pipeline.legacy.validate", argv)


@subcommand("ingest", help="Ingest a GitHub source into the vault (or run --dry-run to plan only)")
def cmd_ingest(args: argparse.Namespace) -> int:
    paths = require_vault_root(args)
    if not args.github:
        print("ERROR: --github <url> is required for ingest", file=sys.stderr)
        return 2
    argv = [
        "--vault-root", str(paths.vault_root),
        "--runtime-root", str(paths.runtime_root),
        "--raw-root", str(paths.raw_root),
        "--github", args.github,
    ]
    if args.run_id:
        argv.extend(["--run-id", args.run_id])
    if is_dry_run(args):
        argv.append("--dry-run")
    if args.skip_cloning:
        argv.append("--skip-cloning")
    return _run_legacy("codex_vault_pipeline.legacy.incremental_ingest", argv)


@subcommand("build-indexes", help="Build the SQLite metadata DB, FTS5, and (optionally) LanceDB vector indexes")
def cmd_build_indexes(args: argparse.Namespace) -> int:
    paths = require_vault_root(args)
    if is_dry_run(args):
        print(f"[dry-run] would build indexes in: {paths.indexes_root}")
        print(f"[dry-run] metadata DB: {paths.db_path}")
        print(f"[dry-run] vector: {'skipped' if args.no_vector else 'enabled'}")
        return 0
    argv = [
        "--vault-root", str(paths.vault_root),
        "--data-root", str(paths.runtime_root),
    ]
    if args.no_vector:
        argv.append("--no-vector")
    return _run_legacy("codex_vault_pipeline.legacy.build_indexes", argv)


@subcommand("benchmark", help="Run the retrieval benchmark (FTS / vector / hybrid) against the vault")
def cmd_benchmark(args: argparse.Namespace) -> int:
    paths = require_vault_root(args)
    if is_dry_run(args):
        print(f"[dry-run] would run benchmark against: {paths.vault_root}")
        return 0
    argv = [
        "--vault-root", str(paths.vault_root),
    ]
    if args.quick:
        argv.append("--quick")
    return _run_legacy("codex_vault_pipeline.legacy.run_retrieval_benchmarks", argv)


@subcommand("ingest-batch", help="Load and validate a batch file (--dry-run required until real ingest is wired)")
def cmd_ingest_batch(args: argparse.Namespace) -> int:
    """Load a batch file, validate it, and (in dry-run mode) print a summary."""
    if not is_dry_run(args):
        print(
            "ERROR: real ingest execution is not wired yet; rerun with --dry-run",
            file=sys.stderr,
        )
        return 2

    batch_path = args.batch_file
    if not batch_path.is_file():
        print(f"ERROR: batch file not found: {batch_path}", file=sys.stderr)
        return 2

    try:
        config = load_batch_config(batch_path)
    except Exception as exc:
        print(f"ERROR: failed to load batch file: {exc}", file=sys.stderr)
        return 2

    validation_errors = validate_batch_config(config)
    print(f"run_id:     {config.run_id}")
    print(f"sources:    {len(config.sources)}")
    if validation_errors:
        print(f"validation: FAILED ({len(validation_errors)} errors)")
        for err in validation_errors:
            print(f"  - {err}")
        return 1
    else:
        print("validation: PASSED")
        return 0


@subcommand("ingest-status", help="Show checkpoint status for a batch run")
def cmd_ingest_status(args: argparse.Namespace) -> int:
    """Read checkpoints for a run and print a compact status summary."""
    paths = require_vault_root(args)
    checkpoints = list_checkpoints(paths.vault_root, args.run_id)

    print(f"run_id:      {args.run_id}")
    print(f"checkpoints: {len(checkpoints)}")

    for cp in checkpoints:
        source_id = cp.get("source_id", "?")
        status = cp.get("status", "?")
        stage = cp.get("stage", "?")
        errors = cp.get("errors", [])
        if errors:
            print(f"  {source_id}: {status} (stage={stage}, errors={len(errors)})")
        else:
            print(f"  {source_id}: {status} (stage={stage})")

    return 0


@subcommand("query-units", help="Search the unit FTS index (read-only)")
def cmd_query_units(args: argparse.Namespace) -> int:
    """Query the units SQLite FTS5 index and print results."""
    from codex_vault_pipeline.index.sqlite_fts import query_units_fts

    db_path = Path(args.vault_root) / ".runtime" / "indexes" / "units-fts.sqlite"

    if not db_path.is_file():
        print(
            f"ERROR: FTS index not found at {db_path}",
            file=sys.stderr,
        )
        print("HINT: Build the index first with the unit extractor pipeline.", file=sys.stderr)
        return 1

    query = args.query.strip()
    if not query:
        print("ERROR: --query must be a non-empty search string.", file=sys.stderr)
        return 2

    try:
        hits = query_units_fts(db_path, query, limit=args.limit)
    except Exception as exc:
        print(f"ERROR: query failed: {exc}", file=sys.stderr)
        return 1

    # Optional source_id filter (post-query to avoid DB schema change)
    if args.source_id:
        hits = [h for h in hits if h.get("source_id") == args.source_id]

    if args.json:
        import json
        json.dump(hits, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    if not hits:
        print("No results.")
        return 0

    for i, h in enumerate(hits, 1):
        sid = h.get("source_id", "")
        utype = h.get("unit_type", "")
        spath = h.get("source_path", "")
        title = h.get("title", "")
        preview = (h.get("text_preview") or "").replace("\n", " ")
        # Truncate preview for terminal
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"{i:3d}. [{sid}] [{utype}]")
        print(f"     path:  {spath}")
        print(f"     title: {title}")
        if preview:
            print(f"     match: {preview}")
        print()

    return 0


# --- main ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="codex-vault-pipeline",
        description="Codex Vault ingestion/indexing/validation engine",
    )
    ap.add_argument(
        "--version",
        action="store_true",
        help="print version and exit",
    )
    sub = ap.add_subparsers(dest="command", required=False)
    for name, info in SUBCOMMANDS.items():
        sp = sub.add_parser(name, help=info["help"])
        add_vault_root_arg(sp)
        add_dry_run_arg(sp)
        if name == "ingest":
            sp.add_argument("--github", required=False, help="GitHub source URL (owner/repo or full URL)")
            sp.add_argument("--run-id", default=None, help="Optional explicit run id (default: auto)")
            sp.add_argument("--skip-cloning", action="store_true",
                            help="Plan only; do not actually clone")
        elif name == "build-indexes":
            sp.add_argument("--no-vector", action="store_true",
                            help="Skip LanceDB vector index construction")
        elif name == "benchmark":
            sp.add_argument("--quick", action="store_true",
                            help="Run a small subset of benchmark queries for smoke testing")
        elif name == "validate":
            sp.add_argument("--strict", action="store_true",
                            help="Enable strict validator mode")
        elif name == "ingest-batch":
            sp.add_argument("--batch-file", type=Path, required=True,
                            help="Path to batch YAML/JSON file")
        elif name == "ingest-status":
            sp.add_argument("--run-id", required=True,
                            help="Run identifier to query")
        elif name == "query-units":
            sp.add_argument("--query", required=True,
                            help="FTS5 search query string")
            sp.add_argument("--limit", type=int, default=10,
                            help="Maximum results (default: 10)")
            sp.add_argument("--source-id", default=None,
                            help="Filter by exact source_id, e.g. github:NousResearch/hermes-agent")
            sp.add_argument("--json", action="store_true",
                            help="Output as JSON array instead of readable format")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from codex_vault_pipeline import __version__
        print(f"codex-vault-pipeline {__version__}")
        return 0
    if not args.command:
        parser.print_help()
        return 0
    fn = SUBCOMMANDS[args.command]["fn"]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
