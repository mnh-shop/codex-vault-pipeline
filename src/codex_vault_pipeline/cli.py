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


@subcommand("env", help="Environment diagnostics and doctor checks")
def cmd_env(args: argparse.Namespace) -> int:
    """Print environment info or run doctor checks."""
    import platform
    import shutil
    from pathlib import Path

    if args.env_action == "doctor":
        return _env_doctor()
    elif args.env_action == "info":
        return _env_info()
    else:
        print(f"ERROR: unknown env action: {args.env_action}", file=sys.stderr)
        return 2


def _env_info() -> int:
    """Print environment information."""
    import platform
    import shutil
    from pathlib import Path

    print("=== Environment Info ===")
    print(f"Python:       {sys.version}")
    print(f"Executable:   {sys.executable}")
    print(f"Platform:     {platform.platform()}")
    print(f"Architecture: {platform.machine()}")

    # Check for uv
    uv_path = shutil.which("uv")
    print(f"uv:           {uv_path or 'NOT FOUND'}")

    # Check for venv
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    print(f"In venv:      {in_venv}")
    if in_venv:
        print(f"Venv path:    {sys.prefix}")

    # Check CODEX_VAULT_ROOT
    env_root = os.environ.get(ENV_VAR)
    print(f"{ENV_VAR}: {env_root or 'NOT SET'}")

    return 0


def _env_doctor() -> int:
    """Run environment diagnostics and report issues."""
    import platform
    import shutil
    from pathlib import Path

    issues = []
    warnings = []

    # Check Python version
    py_ver = sys.version_info
    if py_ver < (3, 9):
        issues.append(f"Python {py_ver.major}.{py_ver.minor} is too old; need >=3.9")
    elif py_ver < (3, 11):
        warnings.append(f"Python {py_ver.major}.{py_ver.minor} works but 3.11+ recommended")

    # Check if running in venv
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    if not in_venv:
        issues.append("Not running in a virtual environment")

    # Check uv
    uv_path = shutil.which("uv")
    if not uv_path:
        issues.append("uv not found in PATH")

    # Check CODEX_VAULT_ROOT
    env_root = os.environ.get(ENV_VAR)
    if not env_root:
        warnings.append(f"{ENV_VAR} not set (will auto-detect from CWD)")
    elif not Path(env_root).is_dir():
        issues.append(f"{ENV_VAR} points to nonexistent directory: {env_root}")

    # Check for __main__.py
    main_file = Path(__file__).parent / "__main__.py"
    if not main_file.exists():
        issues.append("__main__.py missing; python -m codex_vault_pipeline will fail")

    # Check entrypoint in pyproject.toml
    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    if pyproject.is_file():
        content = pyproject.read_text()
        if 'codex-vault = "codex_vault_pipeline.cli:main"' not in content:
            issues.append("codex-vault entrypoint missing from pyproject.toml")
    else:
        warnings.append("pyproject.toml not found (cannot verify entrypoints)")

    # Report
    print("=== Environment Doctor ===")
    if issues:
        print(f"\nISSUES ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")
    if not issues and not warnings:
        print("\nAll checks passed.")

    return 1 if issues else 0


@subcommand("paths", help="Path resolution diagnostics and doctor checks")
def cmd_paths(args: argparse.Namespace) -> int:
    """Print resolved paths or run doctor checks."""
    if args.paths_action == "doctor":
        return _paths_doctor(args)
    elif args.paths_action == "show":
        return _paths_show(args)
    else:
        print(f"ERROR: unknown paths action: {args.paths_action}", file=sys.stderr)
        return 2


def _paths_show(args: argparse.Namespace) -> int:
    """Show resolved paths."""
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("=== Resolved Paths ===")
    print(f"vault_root:       {paths.vault_root}")
    print(f"runtime_root:     {paths.runtime_root}")
    print(f"raw_root:         {paths.raw_root}")
    print(f"wiki_root:        {paths.wiki_root}")
    print(f"indexes_root:     {paths.indexes_root}")
    print(f"db_path:          {paths.db_path}")
    print(f"quarantine_root:  {paths.quarantine_root}")
    print(f"tmp_root:         {paths.tmp_root}")
    return 0


def _paths_doctor(args: argparse.Namespace) -> int:
    """Validate that all expected paths exist and are usable."""
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    issues = []
    warnings = []

    # Check vault root exists
    if not paths.vault_root.exists():
        issues.append(f"Vault root does not exist: {paths.vault_root}")
    elif not paths.vault_root.is_dir():
        issues.append(f"Vault root is not a directory: {paths.vault_root}")

    # Check runtime root
    if not paths.runtime_root.exists():
        issues.append(f"Runtime root does not exist: {paths.runtime_root}")

    # Check indexes root
    if not paths.indexes_root.exists():
        warnings.append(f"Indexes root does not exist: {paths.indexes_root}")

    # Check DB
    if not paths.db_path.exists():
        warnings.append(f"Metadata DB does not exist: {paths.db_path}")

    # Check LanceDB
    lancedb_dir = paths.indexes_root / "codex-vault-vectors"
    if not lancedb_dir.exists():
        warnings.append(f"LanceDB index does not exist: {lancedb_dir}")

    # Check quarantine
    if paths.quarantine_root.exists():
        quarantined = list(paths.quarantine_root.iterdir())
        if quarantined:
            warnings.append(f"Quarantine contains {len(quarantined)} items")

    # Check raw is frozen (no writes expected)
    if paths.raw_root.exists():
        # Just verify it's readable
        try:
            list(paths.raw_root.iterdir())
        except PermissionError:
            issues.append(f"Raw root is not readable: {paths.raw_root}")

    # Report
    print("=== Paths Doctor ===")
    print(f"Vault root: {paths.vault_root}")
    if issues:
        print(f"\nISSUES ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")
    if not issues and not warnings:
        print("\nAll path checks passed.")

    return 1 if issues else 0


@subcommand("vector", help="Vector index diagnostics and doctor checks")
def cmd_vector(args: argparse.Namespace) -> int:
    """Run vector diagnostics and doctor checks."""
    if args.vector_action == "doctor":
        return _vector_doctor(args)
    elif args.vector_action == "info":
        return _vector_info(args)
    else:
        print(f"ERROR: unknown vector action: {args.vector_action}", file=sys.stderr)
        return 2


def _vector_info(args: argparse.Namespace) -> int:
    """Show vector index information."""
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    import lancedb

    lancedb_dir = paths.indexes_root / "codex-vault-vectors"
    print("=== Vector Index Info ===")
    print(f"LanceDB dir:  {lancedb_dir}")
    print(f"Exists:       {lancedb_dir.exists()}")

    if lancedb_dir.exists():
        try:
            db = lancedb.connect(str(lancedb_dir))
            tables = db.table_names()
            print(f"Tables:       {len(tables)}")
            for name in tables:
                table = db.open_table(name)
                print(f"  - {name}: {len(table)} rows")
        except Exception as e:
            print(f"Error reading LanceDB: {e}")

    return 0


def _vector_doctor(args: argparse.Namespace) -> int:
    """Run vector diagnostics and report issues."""
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    issues = []
    warnings = []

    # Check if vector deps are installed
    try:
        import lancedb
        print(f"lancedb: {lancedb.__version__}")
    except ImportError:
        issues.append("lancedb not installed (run: uv add lancedb)")
        lancedb = None

    try:
        import numpy
        print(f"numpy: {numpy.__version__}")
    except ImportError:
        issues.append("numpy not installed (run: uv add numpy)")

    try:
        import sentence_transformers
        print(f"sentence_transformers: {sentence_transformers.__version__}")
    except ImportError:
        issues.append("sentence_transformers not installed (run: uv add sentence-transformers)")

    try:
        import torch
        print(f"torch: {torch.__version__}")
    except ImportError:
        issues.append("torch not installed (run: uv add torch)")

    # Check LanceDB index
    lancedb_dir = paths.indexes_root / "codex-vault-vectors"
    if not lancedb_dir.exists():
        warnings.append(f"LanceDB index does not exist: {lancedb_dir}")
    elif lancedb is not None:
        try:
            db = lancedb.connect(str(lancedb_dir))
            tables = db.table_names()
            if not tables:
                warnings.append("LanceDB index is empty (no tables)")
            else:
                total_rows = 0
                for name in tables:
                    table = db.open_table(name)
                    total_rows += len(table)
                print(f"LanceDB tables: {len(tables)}, total rows: {total_rows}")
        except Exception as e:
            issues.append(f"Cannot read LanceDB index: {e}")

    # Check embedding model cache
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        print(f"Embedding model loaded: all-MiniLM-L6-v2")
    except Exception as e:
        warnings.append(f"Cannot load embedding model: {e}")

    # Report
    print("\n=== Vector Doctor ===")
    if issues:
        print(f"\nISSUES ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")
    if not issues and not warnings:
        print("\nAll vector checks passed.")

    return 1 if issues else 0


@subcommand("v2", help="v2 repo-context lane commands")
def cmd_v2(args: argparse.Namespace) -> int:
    """Handle v2 repo-context lane commands."""
    if args.v2_action == "doctor":
        return _v2_doctor(args)
    elif args.v2_action == "repomix":
        if args.v2_subaction == "plan":
            return _v2_repomix_plan(args)
        elif args.v2_subaction == "run":
            return _v2_repomix_run(args)
        else:
            print("ERROR: repomix requires subaction: plan or run", file=sys.stderr)
            return 2
    elif args.v2_action == "deepwiki":
        if args.v2_subaction == "sanity":
            return _v2_deepwiki_sanity(args)
        else:
            print("ERROR: deepwiki requires subaction: sanity", file=sys.stderr)
            return 2
    elif args.v2_action == "n8n":
        if args.v2_subaction == "coverage":
            return _v2_n8n_coverage(args)
        else:
            print("ERROR: n8n requires subaction: coverage", file=sys.stderr)
            return 2
    elif args.v2_action == "retrieval":
        if args.v2_subaction == "policy":
            return _v2_retrieval_policy(args)
        else:
            print("ERROR: retrieval requires subaction: policy", file=sys.stderr)
            return 2
    elif args.v2_action == "context":
        if args.v2_subaction == "schema":
            return _v2_context_schema(args)
        else:
            print("ERROR: context requires subaction: schema", file=sys.stderr)
            return 2
    else:
        print(f"ERROR: unknown v2 action: {args.v2_action}", file=sys.stderr)
        return 2


def _v2_doctor(args: argparse.Namespace) -> int:
    """Run v2 doctor diagnostics."""
    import shutil
    from pathlib import Path
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    issues = []
    warnings = []
    
    # Check roots
    print("=== v2 Doctor ===")
    print(f"Pipeline root: {Path(__file__).parent.parent.parent}")
    print(f"Vault root: {paths.vault_root}")
    print(f"Runtime root: {paths.runtime_root}")
    
    repo_pack_root = paths.runtime_root / "repo-packs"
    v2_index_root = paths.runtime_root / "indexes" / "v2"
    v2_report_root = paths.runtime_root / "reports" / "v2"
    
    print(f"Repo-pack root: {repo_pack_root}")
    print(f"v2 index root: {v2_index_root}")
    
    # Check Node/npm/npx
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    npx_path = shutil.which("npx")
    
    print(f"\nNode: {node_path or 'NOT FOUND'}")
    print(f"npm: {npm_path or 'NOT FOUND'}")
    print(f"npx: {npx_path or 'NOT FOUND'}")
    
    if not npx_path:
        warnings.append("npx not found - Repomix will not be available")
    
    # Check Repomix availability
    repomix_available = False
    if npx_path:
        try:
            import subprocess
            proc = subprocess.run(
                [npx_path, "repomix", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                repomix_available = True
                print(f"Repomix: {proc.stdout.strip()}")
            else:
                print("Repomix: not available")
        except Exception:
            print("Repomix: not available")
    else:
        print("Repomix: npx not available")
    
    # Check optional adapters
    print("\nOptional adapters:")
    
    try:
        import cocoindex
        print(f"  cocoindex: {cocoindex.__version__}")
    except ImportError:
        print("  cocoindex: not installed")
    
    try:
        import llama_index
        print(f"  llama_index: {llama_index.__version__}")
    except ImportError:
        print("  llama_index: not installed")
    
    try:
        import haystack
        print(f"  haystack: {haystack.__version__}")
    except ImportError:
        print("  haystack: not installed")
    
    # Check DeepWiki
    print("\nDeepWiki: external sanity check only (manual verification)")
    
    # Check legacy retrieval
    legacy_retrieval = Path(__file__).parent / "legacy" / "retrieval.py"
    if legacy_retrieval.exists():
        print(f"Legacy retrieval: {legacy_retrieval}")
    else:
        warnings.append("Legacy retrieval not found")
    
    # Report
    print("\n=== v2 Doctor Summary ===")
    if issues:
        print(f"\nISSUES ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")
    if not issues and not warnings:
        print("\nAll v2 checks passed.")
    
    return 1 if issues else 0


def _v2_repomix_plan(args: argparse.Namespace) -> int:
    """Create Repomix pilot manifest."""
    from pathlib import Path
    from codex_vault_pipeline.v2.manifest import PilotManifest, RepoPackManifest
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    # Create pilot manifest
    manifest = PilotManifest()
    
    # Add pilot sources
    pilot_sources = [
        RepoPackManifest(
            source_id="github:NousResearch/hermes-agent",
            source_type="github",
            repo_url="https://github.com/NousResearch/hermes-agent",
            output_dir=str(paths.runtime_root / "repo-packs" / "repomix" / "NousResearch_hermes-agent"),
            output_format="markdown",
            security_check=True,
            compression=True,
        ),
        RepoPackManifest(
            source_id="github:Agent-Field/agentfield",
            source_type="github",
            repo_url="https://github.com/Agent-Field/agentfield",
            output_dir=str(paths.runtime_root / "repo-packs" / "repomix" / "Agent-Field_agentfield"),
            output_format="markdown",
            security_check=True,
            compression=True,
        ),
        RepoPackManifest(
            source_id="github:n8n-io/n8n-docs",
            source_type="github",
            repo_url="https://github.com/n8n-io/n8n-docs",
            output_dir=str(paths.runtime_root / "repo-packs" / "repomix" / "n8n-io_n8n-docs"),
            output_format="markdown",
            security_check=True,
            compression=False,
        ),
        RepoPackManifest(
            source_id="github:Zie619/n8n-workflows",
            source_type="github",
            repo_url="https://github.com/Zie619/n8n-workflows",
            output_dir=str(paths.runtime_root / "repo-packs" / "repomix" / "Zie619_n8n-workflows"),
            output_format="markdown",
            security_check=True,
            compression=False,
        ),
        RepoPackManifest(
            source_id="local:codex-vault-pipeline",
            source_type="local",
            local_path=str(Path(__file__).parent.parent.parent),
            output_dir=str(paths.runtime_root / "repo-packs" / "repomix" / "codex-vault-pipeline"),
            output_format="markdown",
            security_check=True,
            compression=True,
        ),
    ]
    
    for source in pilot_sources:
        manifest.add_source(source)
    
    # Write manifest
    manifest_path = paths.runtime_root / "repo-packs" / "manifests" / "phase_05a_repomix_pilot.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_yaml(manifest_path)
    
    print(f"Pilot manifest written to: {manifest_path}")
    print(f"Sources: {len(manifest.sources)}")
    
    return 0


def _v2_repomix_run(args: argparse.Namespace) -> int:
    """Run Repomix pilot."""
    from pathlib import Path
    from codex_vault_pipeline.v2.repomix_adapter import RepomixAdapter
    from codex_vault_pipeline.v2.manifest import PilotManifest, RepoPackManifest
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    # Check Repomix availability
    adapter = RepomixAdapter()
    availability = adapter.check_availability()
    
    if not availability["repomix_available"]:
        print("ERROR: Repomix not available. Run 'v2 doctor' to check.", file=sys.stderr)
        return 1
    
    print(f"Repomix available: {availability['repomix_version']}")
    
    # Load pilot manifest
    manifest_path = paths.runtime_root / "repo-packs" / "manifests" / "phase_05a_repomix_pilot.yaml"
    if not manifest_path.exists():
        print("ERROR: Pilot manifest not found. Run 'v2 repomix plan' first.", file=sys.stderr)
        return 1
    
    import yaml
    manifest_data = yaml.safe_load(manifest_path.read_text())
    manifest = PilotManifest()
    for source_data in manifest_data.get("sources", []):
        source = RepoPackManifest(**source_data)
        manifest.add_source(source)
    
    print(f"Pilot sources: {len(manifest.sources)}")
    
    # Run Repomix for each source
    results = []
    for source in manifest.sources:
        print(f"\n--- Source: {source.source_id} ---")
        output_dir = Path(source.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        result = adapter.run_repomix(source, output_dir)
        results.append(result)
        
        print(f"  Command: {result.command[:100]}...")
        print(f"  Exit code: {result.exit_code}")
        print(f"  Success: {result.success}")
        
        if result.output_file:
            output_path = Path(result.output_file)
            print(f"  Output file: {result.output_file}")
            print(f"  Output size: {result.file_size} bytes")
        
        if result.token_count:
            print(f"  Token count: {result.token_count}")
        
        if result.security_findings:
            print(f"  Security findings: {len(result.security_findings)}")
        
        if result.error:
            print(f"  Error: {result.error}")
    
    # Summary
    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    
    print(f"\n=== Pilot Summary ===")
    print(f"Total sources: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    
    return 0 if failed == 0 else 1


def _v2_deepwiki_sanity(args: argparse.Namespace) -> int:
    """Run DeepWiki sanity checks."""
    from codex_vault_pipeline.v2.deepwiki_sanity import DeepWikiSanityChecker
    
    print("=== DeepWiki Sanity Check ===")
    print("DeepWiki is external sanity check only.")
    print("Verify manually in browser for public repos.")
    
    # Example conversion
    test_urls = [
        "https://github.com/NousResearch/hermes-agent",
        "https://github.com/Agent-Field/agentfield",
        "https://github.com/n8n-io/n8n-docs",
    ]
    
    print("\nDeepWiki URLs:")
    for url in test_urls:
        deepwiki_url = DeepWikiSanityChecker.convert_to_deepwiki_url(url)
        print(f"  {url}")
        print(f"    -> {deepwiki_url}")
    
    return 0


def _v2_n8n_coverage(args: argparse.Namespace) -> int:
    """Report n8n coverage."""
    from pathlib import Path
    from codex_vault_pipeline.v2.n8n_coverage import N8nCoverageAnalyzer
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    analyzer = N8nCoverageAnalyzer(paths.vault_root)
    report = analyzer.analyze()
    
    print("=== n8n Coverage Report ===")
    print(f"Coverage status: {report.coverage_status}")
    print(f"Total raw files: {report.total_raw_files}")
    print(f"Total unit files: {report.total_unit_files}")
    print(f"Total metadata records: {report.total_metadata_records}")
    print(f"Total FTS rows: {report.total_fts_rows}")
    print(f"Total vector rows: {report.total_vector_rows}")
    print(f"Missing extraction count: {report.missing_extraction_count}")
    
    print("\nSources:")
    for source in report.sources:
        print(f"  {source.source_id}: {source.status} ({source.coverage:.1%})")
    
    # Write report
    report_path = paths.runtime_root / "reports" / "v2" / "phase_05a_n8n_coverage.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    
    content = f"""# n8n Coverage Report

**Date:** 2026-06-22
**Status:** {report.coverage_status}

## Summary

- Total raw files: {report.total_raw_files}
- Total unit files: {report.total_unit_files}
- Total metadata records: {report.total_metadata_records}
- Total FTS rows: {report.total_fts_rows}
- Total vector rows: {report.total_vector_rows}
- Missing extraction count: {report.missing_extraction_count}

## Sources

| Source | Status | Coverage |
|--------|--------|----------|
"""
    
    for source in report.sources:
        content += f"| {source.source_id} | {source.status} | {source.coverage:.1%} |\n"
    
    report_path.write_text(content)
    print(f"\nReport written to: {report_path}")
    
    return 0


def _v2_retrieval_policy(args: argparse.Namespace) -> int:
    """Write retrieval v2 policy."""
    from pathlib import Path
    from codex_vault_pipeline.v2.retrieval_policy import RetrievalPolicy
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    policy = RetrievalPolicy()
    
    # Write policy file
    policy_path = paths.runtime_root / "reports" / "v2" / "phase_05a_retrieval_v2_policy.md"
    policy.write_policy_file(policy_path)
    
    print(f"Retrieval v2 policy written to: {policy_path}")
    
    return 0


def _v2_context_schema(args: argparse.Namespace) -> int:
    """Write context pack schema."""
    from pathlib import Path
    
    try:
        paths = require_vault_root(args)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    
    # Write schema documentation
    schema_path = paths.runtime_root / "reports" / "v2" / "phase_05a_context_pack_schema.md"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    
    content = """# Context Pack Schema

**Date:** 2026-06-22
**Status:** Active

## 1.0 Overview

The context pack schema defines the structure for AI-ready context packs.

## 2.0 Schema Classes

### ContextPack

Top-level container for context items.

Fields:
- `pack_id`: Unique identifier
- `items`: List of ContextItem objects
- `total_tokens`: Total token estimate
- `query`: Optional query string
- `metadata`: Additional metadata

### ContextItem

A single item in a context pack.

Fields:
- `item_id`: Unique identifier
- `text`: Content text
- `token_estimate`: Estimated token count
- `provenance`: SourceProvenance object
- `retrieval_trace`: RetrievalTrace object
- `security_status`: Security status (clean/flagged/blocked/not-scanned)
- `is_quarantined`: Whether item is quarantined
- `is_generated_catalog`: Whether item is a generated catalog
- `is_readme`: Whether item is a README
- `recommended_use`: Recommended use case

### SourceProvenance

Provenance information for a context item.

Fields:
- `source_id`: Source identifier
- `repo_url`: Repository URL
- `commit`: Commit hash
- `path`: File path
- `file_hash`: File content hash
- `artifact_role`: Artifact role type
- `acquisition_status`: Acquisition status

### RetrievalTrace

Trace information for retrieval.

Fields:
- `method`: Retrieval method (metadata/fts/vector/hybrid/repomix/manual)
- `rank`: Result rank
- `score`: Relevance score
- `query`: Original query

## 3.0 Enums

### SecurityStatus

- `clean`: No security issues
- `flagged`: Has security flags
- `blocked`: Blocked due to security
- `not-scanned`: Not yet scanned

### ArtifactRole

- `workflow`: n8n workflow
- `skill`: Agent skill
- `soul`: Agent soul
- `documentation`: Documentation
- `code`: Source code
- `config`: Configuration
- `deployment`: Deployment
- `script`: Script
- `unknown`: Unknown

### RetrievalMethod

- `metadata`: Metadata lookup
- `fts`: Full-text search
- `vector`: Vector similarity
- `hybrid`: Hybrid search
- `repomix`: Repomix pack
- `manual`: Manual selection

## 4.0 Validation

The schema includes validation functions to ensure context packs are well-formed.
"""
    
    schema_path.write_text(content)
    print(f"Context pack schema written to: {schema_path}")
    
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
        elif name == "env":
            sp.add_argument("env_action", choices=["doctor", "info"],
                            help="Action: doctor (diagnostics) or info (print env)")
        elif name == "paths":
            sp.add_argument("paths_action", choices=["doctor", "show"],
                            help="Action: doctor (validate paths) or show (print paths)")
        elif name == "vector":
            sp.add_argument("vector_action", choices=["doctor", "info"],
                            help="Action: doctor (diagnostics) or info (print vector info)")
        elif name == "v2":
            sp.add_argument("v2_action", choices=["doctor", "repomix", "deepwiki", "n8n", "retrieval", "context"],
                            help="Action: doctor, repomix, deepwiki, n8n, retrieval, or context")
            sp.add_argument("v2_subaction", nargs="?", default=None,
                            help="Sub-action (e.g., plan, run, sanity, coverage, policy, schema)")
            sp.add_argument("--pilot", action="store_true",
                            help="Run in pilot mode")
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
