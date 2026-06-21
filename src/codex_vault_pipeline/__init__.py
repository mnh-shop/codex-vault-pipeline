"""Codex Vault Pipeline — ingestion, indexing, validation, retrieval.

A standalone, installable Python package that operates on a
Codex Vault directory. The package never holds the vault's
data itself; it reads from and writes to a vault root that is
resolved at every invocation from ``--vault-root`` or the
``CODEX_VAULT_ROOT`` environment variable.

Public entry points:

* :func:`codex_vault_pipeline.paths.resolve_paths` — vault root
  resolution and standard subpath derivation.
* :mod:`codex_vault_pipeline.cli` — subcommand dispatcher
  (validate, ingest, build-indexes, benchmark).
* :mod:`codex_vault_pipeline.legacy` — refactored copies of the
  vault's phase 0–6 scripts, ready to import or invoke.

Schema files live under :mod:`codex_vault_pipeline.schemas`.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "__version__",
]
