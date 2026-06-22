#!/usr/bin/env python3
"""Allow ``python -m codex_vault_pipeline`` to invoke the CLI.

Usage::

    python -m codex_vault_pipeline [args ...]
"""
from __future__ import annotations

from codex_vault_pipeline.cli import main

if __name__ == "__main__":
    main()
