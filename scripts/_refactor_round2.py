#!/usr/bin/env python3
"""Apply the remaining refactors to the legacy scripts.

The initial refactor handled ``default="/Users/..."`` and
``Path("/Users/...")`` literals. This script handles the
remaining cases:

1. ``Path(__file__).resolve().parents[2]`` — replaces with a
   runtime path that resolves from ``CODEX_VAULT_ROOT`` (or
   the --vault-root arg passed to main()).
2. Adds the ``--no-vector`` flag to ``build_indexes.py`` and
   wires it to skip the LanceDB indexing step.
3. Adds the ``--quick`` flag to ``run_retrieval_benchmarks.py``
   and wires it to use a smaller QUERIES list.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PIPELINE = Path("/Users/admin1/agent-brain/codex-vault-pipeline")
LEGACY = PIPELINE / "src" / "codex_vault_pipeline" / "legacy"


def fix_file_vault_path(p: Path) -> bool:
    """Replace ``Path(__file__).resolve().parents[2]`` with env-var lookup.

    Returns True if the file was changed.
    """
    text = p.read_text()
    new = re.sub(
        r"Path\(__file__\)\.resolve\(\)\.parents\[2\]",
        'Path(os.environ.get("CODEX_VAULT_ROOT") or ".")',
        text,
    )
    if new != text:
        p.write_text(new)
        return True
    return False


def add_no_vector_to_build_indexes() -> None:
    """Add the ``--no-vector`` flag to build_indexes.py.

    The flag is parsed in the main() and skips the
    ``build_vector_index`` step.
    """
    p = LEGACY / "build_indexes.py"
    text = p.read_text()
    if "--no-vector" in text:
        return
    # Insert a `no_vector` flag in the (currently absent) argparse
    # block of the main(). Since build_indexes.py has no argparse,
    # we add the flag handling in main() itself via sys.argv.
    # Find the main() function and inject the flag handling.
    # The function currently looks like:
    #   def main() -> dict:
    # We will rewrite it to accept argv from sys.argv.
    new_text = text.replace(
        "def main() -> dict:\n    t0 = time.time()\n    log(\"=== Phase 6 index build ===\")\n    deps = detect_dependencies()",
        '''def main(argv: list | None = None) -> dict:
    import argparse
    if argv is None:
        argv = sys.argv[1:]
    _ap = argparse.ArgumentParser(add_help=True)
    _ap.add_argument("--no-vector", action="store_true", help="Skip LanceDB vector index construction")
    _ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""), help="Path to vault root")
    _args, _rest = _ap.parse_known_args(argv)
    no_vector = _args.no_vector
    t0 = time.time()
    log("=== Phase 6 index build ===")
    deps = detect_dependencies()'''
    )
    # Wire no_vector into build_vector_index
    new_text = new_text.replace(
        "    vec_summary = build_vector_index(deps)\n",
        "    vec_summary = ({\"skipped\": True, \"reason\": \"--no-vector\"} if no_vector else build_vector_index(deps))\n"
    )
    p.write_text(new_text)


def add_quick_to_benchmark() -> None:
    """Add the ``--quick`` flag to run_retrieval_benchmarks.py.

    When --quick is set, the QUERIES list is truncated to a
    3-query smoke subset.
    """
    p = LEGACY / "run_retrieval_benchmarks.py"
    text = p.read_text()
    if "--quick" in text:
        return
    # Find the main() entry point and inject argparse
    new_text = text.replace(
        'def main() -> int:',
        '''def main(argv: list | None = None) -> int:
    import argparse
    if argv is None:
        argv = sys.argv[1:]
    _ap = argparse.ArgumentParser(add_help=True)
    _ap.add_argument("--quick", action="store_true", help="Use a 3-query smoke subset")
    _ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""), help="Path to vault root")
    _args, _rest = _ap.parse_known_args(argv)
    global QUERIES
    if _args.quick and len(QUERIES) > 3:
        QUERIES = QUERIES[:3]
'''
    )
    p.write_text(new_text)


def main() -> int:
    print("=== Replacing Path(__file__).resolve().parents[2] ===")
    for p in sorted(LEGACY.glob("*.py")):
        if fix_file_vault_path(p):
            print(f"  patched: {p.name}")
    print()
    print("=== Adding --no-vector to build_indexes.py ===")
    add_no_vector_to_build_indexes()
    print("  done")
    print()
    print("=== Adding --quick to run_retrieval_benchmarks.py ===")
    add_quick_to_benchmark()
    print("  done")
    print()
    print("=== Verifying compilation ===")
    import py_compile
    for p in sorted(LEGACY.glob("*.py")):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"  ERR {p.name}: {str(e)[:200]}")
            return 1
    print("  all OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
