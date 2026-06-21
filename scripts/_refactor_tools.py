#!/usr/bin/env python3
"""Refactor the legacy Codex Vault tools to use --vault-root.

Reads each ``.runtime/tools/*.py`` file in the source vault and
writes a refactored copy into ``src/codex_vault_pipeline/legacy/``
in the pipeline repo. The transformation:

1. Adds the ``add_vault_root_arg`` / ``require_vault_root`` import.
2. Replaces every hardcoded ``default="/Users/admin1/agent-brain/codex-vault[/...]"``
   with a default that resolves from the ``CODEX_VAULT_ROOT`` env
   var (or the ``--vault-root`` CLI flag).
3. Replaces every ``Path("/Users/admin1/agent-brain/codex-vault[/...]")``
   in the body with a path constructed from the resolved vault root.

The transformation is mechanical and deterministic. The original
files in the vault are NEVER modified (read-only on the source).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

VAULT = Path("/Users/admin1/agent-brain/codex-vault")
PIPELINE = Path("/Users/admin1/agent-brain/codex-vault-pipeline")
LEGACY_BASE = PIPELINE / "src" / "codex_vault_pipeline" / "legacy"

# The hardcoded path that must be removed everywhere.
HARDCODED = "/Users/admin1/agent-brain/codex-vault"

# Files to copy (relative to .runtime/tools/).
TOP_LEVEL = [
    "incremental_ingest.py",
    "build_indexes.py",
    "run_retrieval_benchmarks.py",
    "validate.py",
    "phase6_schema_correction.py",
    "phase6_taxonomy_benchmark.py",
]
HELPERS = ["benchmark.py", "retrieval.py"]
PHASE3 = ["_phase3_artifact_manifest.py", "_phase3_completion.py"]
PHASE4 = [
    "_phase4_completion.py",
    "_phase4_config_extract.py",
    "_phase4_deploy_extract.py",
    "_phase4_doc_extract.py",
    "_phase4_hermes_skill_soul_extract.py",
    "_phase4_n8n_extract.py",
    "_phase4_script_extract.py",
]
PHASE5 = [
    "_phase5_candidate_agentfield_core.py",
    "_phase5_candidate_agentfield_examples.py",
    "_phase5_candidate_hermes_community.py",
    "_phase5_candidate_hermes_core.py",
    "_phase5_candidate_hermes_official.py",
    "_phase5_candidate_n8n_ecosystem.py",
    "_phase5_candidate_n8n_official.py",
    "_phase5_candidate_remaining.py",
    "_phase5_n8n_ecosystem_reconcile.py",
    "_phase5_n8n_migration_schema_correction.py",
]

ALL_FILES = TOP_LEVEL + HELPERS + PHASE3 + PHASE4 + PHASE5


# --- transform -----------------------------------------------------------

IMPORT_LINE = (
    "from codex_vault_pipeline.paths import "
    "resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR"
)


def add_imports(src: str) -> str:
    """Inject the path-resolution import after the existing imports.

    The injection is placed *after* any ``from __future__ import``
    line (which must remain the first import), after any module
    docstring, and after the contiguous top-level import block.
    Indented imports inside ``try:`` or other compound statements
    are NOT considered.
    """
    if "from codex_vault_pipeline.paths import" in src:
        return src  # already done
    lines = src.splitlines(keepends=True)
    n = len(lines)

    # Phase 1: skip shebang, comments, blanks, and any leading
    # docstring. We consume everything that is NOT real code
    # at the top of the file before we look for imports.
    i = 0
    in_docstring = False
    while i < n:
        s = lines[i]
        stripped = s.strip()
        if not stripped:
            i += 1
            continue
        if s.startswith("#"):
            # top-level comment (shebang, encoding, etc.)
            i += 1
            continue
        # Detect a docstring boundary. We use a simple toggle:
        # the FIRST triple-quote opens the docstring, the SECOND
        # closes it. This handles the common pattern where the
        # opening line is `"""` alone, followed by content, and
        # closed by `"""` alone.
        if '"""' in stripped or "'''" in stripped:
            count = stripped.count('"""') + stripped.count("'''")
            if not in_docstring:
                # opening — toggle to in_docstring
                in_docstring = True
                # single-line docstring (count >= 2 or all in one line)
                if count >= 2:
                    in_docstring = False
            else:
                # closing
                in_docstring = False
            i += 1
            continue
        if in_docstring:
            i += 1
            continue
        break  # first real code line

    # Phase 2: skip the __future__ import if present
    if i < n and lines[i].lstrip().startswith("from __future__"):
        i += 1

    # Phase 3: walk the contiguous top-level import block
    last_top_import_idx = -1
    while i < n:
        s = lines[i]
        stripped = s.strip()
        if not stripped:
            i += 1
            continue
        if s.startswith("import ") or s.startswith("from "):
            last_top_import_idx = i
            i += 1
            continue
        break  # first non-import, non-blank line

    if last_top_import_idx == -1:
        return IMPORT_LINE + "\n" + src
    lines.insert(last_top_import_idx + 1, IMPORT_LINE + "\n")
    return "".join(lines)


def replace_hardcoded_default(src: str) -> str:
    """Replace ``default="/Users/admin1/agent-brain/codex-vault[/...]"``.

    For the bare vault root, the new default is
    ``os.environ.get("CODEX_VAULT_ROOT", "")`` (a string that
    resolve_paths() will resolve later). For subpaths, the new
    default is ``os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""),
    "<subpath>")``.
    """
    # Match default="/Users/admin1/agent-brain/codex-vault[/...]" forms
    # including the closing quote.
    pattern = re.compile(r'default="' + re.escape(HARDCODED) + r'([^"]*)"')

    def repl(m: re.Match[str]) -> str:
        sub = m.group(1)
        if sub == "":
            return 'default=os.environ.get("CODEX_VAULT_ROOT", "")'
        return (
            f'default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), '
            f'"{sub.lstrip("/")}")'
        )

    return pattern.sub(repl, src)


def replace_hardcoded_path_constructor(src: str) -> str:
    """Replace ``Path("/Users/admin1/agent-brain/codex-vault[/...]")``.

    The new form is ``Path(os.environ.get("CODEX_VAULT_ROOT", "")) /
    "<subpath>"`` (string concatenation, not join, to keep the
    expression readable inside larger statements).
    """
    pattern = re.compile(
        r'Path\("' + re.escape(HARDCODED) + r'([^"]*)"\)'
    )

    def repl(m: re.Match[str]) -> str:
        sub = m.group(1).lstrip("/")
        if sub == "":
            return 'Path(os.environ.get("CODEX_VAULT_ROOT", ""))'
        return f'Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "{sub}"'

    return pattern.sub(repl, src)


def add_vault_root_arg(src: str) -> str:
    """Insert ``add_vault_root_arg(ap)`` call right after the parser is built.

    The pattern looks for the first ``ArgumentParser(...)`` or
    ``ap = argparse.ArgumentParser(...)`` line and injects the
    call immediately after it.
    """
    pattern = re.compile(
        r"(\bap\s*=\s*argparse\.ArgumentParser\([^)]*\))",
        re.MULTILINE,
    )
    if pattern.search(src) is None:
        # try a different naming convention
        pattern2 = re.compile(
            r"(\bparser\s*=\s*argparse\.ArgumentParser\([^)]*\))",
            re.MULTILINE,
        )
        m = pattern2.search(src)
        if m is None:
            return src  # no parser found, skip
        return pattern2.sub(
            lambda mm: mm.group(1) + "\n    add_vault_root_arg(parser)",
            src,
            count=1,
        )
    return pattern.sub(
        lambda m: m.group(1) + "\n    add_vault_root_arg(ap)",
        src,
        count=1,
    )


def transform(src: str) -> str:
    src = add_imports(src)
    src = replace_hardcoded_default(src)
    src = replace_hardcoded_path_constructor(src)
    src = add_vault_root_arg(src)
    return src


# --- driver --------------------------------------------------------------


def dest_path(name: str) -> Path:
    """Map a tool filename to its destination under ``legacy/``."""
    if name in TOP_LEVEL:
        return LEGACY_BASE / name
    if name in HELPERS:
        return LEGACY_BASE / name
    if name in PHASE3:
        return LEGACY_BASE / name
    if name in PHASE4:
        return LEGACY_BASE / name
    if name in PHASE5:
        return LEGACY_BASE / name
    raise ValueError(f"Unknown tool: {name}")


def copy_and_transform(name: str) -> tuple[Path, int]:
    src_path = VAULT / ".runtime" / "tools" / name
    dst_path = dest_path(name)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    text = src_path.read_text()
    new = transform(text)
    dst_path.write_text(new)
    return dst_path, len(new)


def main() -> int:
    desc = (__doc__ or "").splitlines()[0] if __doc__ else "refactor"
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--list", action="store_true", help="list files and exit")
    args = ap.parse_args()

    if args.list:
        for n in ALL_FILES:
            print(dest_path(n))
        return 0

    written: list[Path] = []
    sizes: list[int] = []
    for n in ALL_FILES:
        try:
            dst, sz = copy_and_transform(n)
            written.append(dst)
            sizes.append(sz)
            print(f"  copied+refactored: {n} -> {dst.relative_to(PIPELINE)} ({sz} bytes)")
        except Exception as e:
            print(f"  ERROR on {n}: {e}", file=sys.stderr)
            return 1

    # Sanity: no hardcoded path should remain in the destination files.
    leftover = []
    for p in written:
        text = p.read_text()
        # The path resolution helpers themselves contain the hardcoded
        # string as a fallback example — but the legacy files should
        # not. Strip the helper-string mention from the check.
        for line in text.splitlines():
            if HARDCODED in line and not line.strip().startswith("#"):
                # ignore docstring lines that mention the hardcoded path
                # as a "do not commit" example
                leftover.append((p, line))
    if leftover:
        print("\nWARNING: hardcoded path remains in some files:", file=sys.stderr)
        for p, line in leftover[:5]:
            print(f"  {p.relative_to(PIPELINE)}: {line.strip()}", file=sys.stderr)

    print(f"\nTotal: {len(written)} files written, {sum(sizes)} bytes total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
