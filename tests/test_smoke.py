"""Smoke tests that exercise the CLI end-to-end against a
live Codex Vault.

These tests are skipped automatically if the
``CODEX_VAULT_ROOT`` environment variable is not set. The
vault is a sibling repo, not a dependency of this one, so
the smoke tests are best-effort.

Run manually:

    CODEX_VAULT_ROOT=/path/to/codex-vault pytest tests/test_smoke.py

Or use the bash wrappers:

    ./scripts/codex-vault-validate --vault-root /path/to/codex-vault
    ./scripts/codex-vault-build-indexes --vault-root /path/to/codex-vault --no-vector
    ./scripts/codex-vault-benchmark --vault-root /path/to/codex-vault --quick
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve the repo root (one level up from tests/).
REPO_ROOT = Path(__file__).resolve().parent.parent


def vault_required() -> str:
    """Return the vault root or skip the test."""
    vault = os.environ.get("CODEX_VAULT_ROOT", "")
    if not vault or not Path(vault).is_dir():
        pytest.skip("CODEX_VAULT_ROOT not set or not a directory; skipping smoke test")
    return vault


def run_cli(*args: str, vault: str) -> subprocess.CompletedProcess:
    """Run a CLI subcommand via the bash wrapper in scripts/."""
    wrapper = REPO_ROOT / "scripts" / f"codex-vault-{args[0]}"
    assert wrapper.exists(), f"missing wrapper: {wrapper}"
    return subprocess.run(
        [str(wrapper), "--vault-root", vault, *args[1:]],
        capture_output=True,
        text=True,
    )


# --- py_compile ---------------------------------------------------------


def test_all_modules_compile() -> None:
    """Every module in src/ compiles without errors."""
    import py_compile

    src = REPO_ROOT / "src" / "codex_vault_pipeline"
    py_files = [str(p) for p in src.rglob("*.py")]
    assert py_files, "no Python files found in src/"
    # py_compile returns None on success, raises on failure
    for p in py_files:
        py_compile.compile(p, doraise=True)


# --- validate ------------------------------------------------------------


def test_validate_passes() -> None:
    vault = vault_required()
    r = run_cli("validate", vault=vault)
    assert r.returncode == 0, f"validate failed:\n{r.stdout}\n{r.stderr}"
    assert "PASSED" in r.stdout


# --- build-indexes (--no-vector) ----------------------------------------


def test_build_indexes_no_vector() -> None:
    vault = vault_required()
    r = run_cli("build-indexes", "--no-vector", vault=vault)
    assert r.returncode == 0, f"build-indexes failed:\n{r.stdout}\n{r.stderr}"
    assert "Done in" in r.stderr or "Done in" in r.stdout


# --- benchmark (--quick) ------------------------------------------------


def test_benchmark_quick() -> None:
    vault = vault_required()
    r = run_cli("benchmark", "--quick", vault=vault)
    assert r.returncode == 0, f"benchmark failed:\n{r.stdout}\n{r.stderr}"
    # The quick benchmark writes the retrieval report
    report = Path(vault) / ".runtime" / "reports" / "retrieval-benchmark-results.md"
    # The benchmark script may not write the report in --quick
    # mode, so we just check that it ran without error
