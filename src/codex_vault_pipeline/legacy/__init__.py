"""Refactored copies of the Codex Vault's phase 0–6 tools.

Each module is a faithful refactor of the corresponding
``.runtime/tools/<name>.py`` from the vault, with the
hardcoded ``/Users/admin1/agent-brain/codex-vault`` path
removed. Path resolution is delegated to
:mod:`codex_vault_pipeline.paths`.

These modules are NOT meant to be invoked directly. The
public entry points are the subcommands in
:mod:`codex_vault_pipeline.cli`, which synthesize argv and
call each script's ``main()`` with the right vault root.
"""
