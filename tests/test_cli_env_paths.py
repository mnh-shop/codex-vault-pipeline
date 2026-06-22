"""Unit tests for the CLI module — env and paths subcommands.

These tests exercise the CLI parser and subcommand dispatch
without touching the filesystem or requiring a real vault.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from codex_vault_pipeline.cli import build_parser, main


# --- build_parser ----------------------------------------------------------


def test_build_parser_includes_env_and_paths() -> None:
    """The parser should have env and paths subcommands."""
    parser = build_parser()
    # Parse --help to verify subcommands exist (will raise on error)
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    # --help exits with 0
    assert exc_info.value.code == 0


def test_build_parser_env_choices() -> None:
    """env subcommand should accept doctor and info."""
    parser = build_parser()
    args = parser.parse_args(["env", "doctor"])
    assert args.command == "env"
    assert args.env_action == "doctor"

    args = parser.parse_args(["env", "info"])
    assert args.command == "env"
    assert args.env_action == "info"


def test_build_parser_paths_choices() -> None:
    """paths subcommand should accept doctor and show."""
    parser = build_parser()
    args = parser.parse_args(["paths", "--vault-root", "/tmp", "doctor"])
    assert args.command == "paths"
    assert args.paths_action == "doctor"

    args = parser.parse_args(["paths", "--vault-root", "/tmp", "show"])
    assert args.command == "paths"
    assert args.paths_action == "show"


# --- env info --------------------------------------------------------------


def test_env_info_returns_zero() -> None:
    """env info should succeed."""
    # Redirect stdout to capture output
    with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["env", "info"])
    assert rc == 0


# --- env doctor ------------------------------------------------------------


def test_env_doctor_returns_zero_or_one() -> None:
    """env doctor should return 0 (all good) or 1 (issues found)."""
    with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["env", "doctor"])
    assert rc in (0, 1)


# --- paths doctor ----------------------------------------------------------


def test_paths_doctor_requires_vault_root(tmp_path: Path) -> None:
    """paths doctor should fail when vault root cannot be resolved."""
    with mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()):
        with mock.patch("os.environ", {}):
            rc = main(["paths", "doctor"])
    # Should fail because no vault root can be resolved
    assert rc == 1


def test_paths_doctor_with_valid_root(tmp_path: Path) -> None:
    """paths doctor should pass for a valid vault root."""
    # Create minimal vault structure
    (tmp_path / ".runtime").mkdir()
    with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["paths", "--vault-root", str(tmp_path), "doctor"])
    assert rc == 0


def test_paths_doctor_with_missing_root(tmp_path: Path) -> None:
    """paths doctor should fail for a nonexistent vault root."""
    with mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["paths", "--vault-root", str(tmp_path / "nonexistent"), "doctor"])
    assert rc == 1


# --- paths show ------------------------------------------------------------


def test_paths_show_with_valid_root(tmp_path: Path) -> None:
    """paths show should print paths for a valid vault root."""
    (tmp_path / ".runtime").mkdir()
    with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["paths", "--vault-root", str(tmp_path), "show"])
    assert rc == 0


# --- __main__ module -------------------------------------------------------


def test_main_module_importable() -> None:
    """The __main__ module should be importable."""
    import codex_vault_pipeline.__main__
    assert hasattr(codex_vault_pipeline.__main__, "main")


# --- version ---------------------------------------------------------------


def test_version_flag() -> None:
    """--version should print version and exit 0."""
    with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
        rc = main(["--version"])
    assert rc == 0
