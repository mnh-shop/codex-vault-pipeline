"""Schema YAMLs bundled with the package for runtime access.

The package's :mod:`paths` module resolves ``${vault_root}/.runtime/schemas``
for the vault's own schemas, but tests, examples, and offline
modes may want to use the schemas bundled with the package
instead. This subpackage exposes the package's bundled copies
through the standard ``importlib.resources`` API.
"""
