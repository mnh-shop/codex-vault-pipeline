"""Unit tests for the path resolution module.

These tests do NOT touch the filesystem. They only exercise
the pure-Python logic in :mod:`codex_vault_pipeline.paths`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_vault_pipeline.paths import (
    ENV_VAR,
    VaultPaths,
    add_dry_run_arg,
    add_vault_root_arg,
    is_dry_run,
    require_vault_root,
    resolve_paths,
    resolve_vault_root,
)


# --- resolve_vault_root -------------------------------------------------


def test_resolve_vault_root_cli_value(tmp_path: Path) -> None:
    got = resolve_vault_root(cli_value=str(tmp_path), env={})
    assert got == tmp_path.resolve()


def test_resolve_vault_root_env_var(tmp_path: Path) -> None:
    got = resolve_vault_root(env={ENV_VAR: str(tmp_path)})
    assert got == tmp_path.resolve()


def test_resolve_vault_root_cli_wins_over_env(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    got = resolve_vault_root(cli_value=str(other), env={ENV_VAR: "/nope"})
    assert got == other.resolve()


def test_resolve_vault_root_raises_when_missing(tmp_path: Path) -> None:
    # Empty env, no CLI value, no recognizable parent: must raise
    with pytest.raises(SystemExit):
        resolve_vault_root(cli_value="", env={})


# --- resolve_paths -------------------------------------------------------


def test_resolve_paths_derives_all_subpaths(tmp_path: Path) -> None:
    (tmp_path / ".runtime").mkdir()
    p = resolve_paths(cli_value=str(tmp_path))
    assert isinstance(p, VaultPaths)
    assert p.vault_root == tmp_path.resolve()
    assert p.runtime_root == tmp_path / ".runtime"
    assert p.raw_root == tmp_path / "raw"
    assert p.wiki_root == tmp_path / "wiki"
    assert p.db_path == tmp_path / ".runtime" / "db" / "codex-vault.db"
    assert p.knowledge_notes_root == tmp_path / ".runtime" / "knowledge-notes"
    assert p.migration_reports_root == tmp_path / ".runtime" / "migration-reports"


def test_resolve_paths_requires_existing_root(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        resolve_paths(cli_value=str(tmp_path / "does-not-exist"))


def test_resolve_paths_optional_create(tmp_path: Path) -> None:
    # require_exists=False should not raise for a missing root
    p = resolve_paths(cli_value=str(tmp_path / "nope"), require_exists=False)
    assert p.vault_root == (tmp_path / "nope").resolve()


# --- add_vault_root_arg --------------------------------------------------


def test_add_vault_root_arg_default_is_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    monkeypatch.setenv(ENV_VAR, "/from/env")
    parser = argparse.ArgumentParser()
    add_vault_root_arg(parser)
    args = parser.parse_args([])
    assert args.vault_root == "/from/env"


def test_add_vault_root_arg_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    monkeypatch.setenv(ENV_VAR, "/from/env")
    parser = argparse.ArgumentParser()
    add_vault_root_arg(parser)
    args = parser.parse_args(["--vault-root", "/from/cli"])
    assert args.vault_root == "/from/cli"


# --- add_dry_run_arg / is_dry_run ---------------------------------------


def test_dry_run_default_false() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    add_dry_run_arg(parser)
    args = parser.parse_args([])
    assert is_dry_run(args) is False


def test_dry_run_flag_sets_true() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    add_dry_run_arg(parser)
    args = parser.parse_args(["--dry-run"])
    assert is_dry_run(args) is True
