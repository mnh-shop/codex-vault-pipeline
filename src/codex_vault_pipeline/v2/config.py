# Codex Vault Pipeline — v2 configuration
"""Configuration for the v2 repo-context lane."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class V2Config:
    """Configuration for v2 repo-context lane."""
    
    # Roots
    pipeline_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent.parent)
    vault_root: Path = field(default_factory=lambda: Path(os.environ.get("CODEX_VAULT_ROOT", "")))
    runtime_root: Path = field(default_factory=lambda: Path(""))
    repo_pack_root: Path = field(default_factory=lambda: Path(""))
    v2_index_root: Path = field(default_factory=lambda: Path(""))
    v2_report_root: Path = field(default_factory=lambda: Path(""))
    
    # Repomix settings
    repomix_enabled: bool = True
    repomix_output_format: str = "markdown"  # markdown or xml
    repomix_security_check: bool = True
    repomix_compression: bool = False
    
    # Optional adapters
    cocoindex_available: bool = False
    llamaindex_available: bool = False
    haystack_available: bool = False
    
    def __post_init__(self):
        """Initialize derived paths."""
        if not self.vault_root:
            self.vault_root = self.pipeline_root.parent / "codex-vault"
        if not self.runtime_root:
            self.runtime_root = self.vault_root / ".runtime"
        if not self.repo_pack_root:
            self.repo_pack_root = self.runtime_root / "repo-packs"
        if not self.v2_index_root:
            self.v2_index_root = self.runtime_root / "indexes" / "v2"
        if not self.v2_report_root:
            self.v2_report_root = self.runtime_root / "reports" / "v2"
    
    def ensure_dirs(self):
        """Create required directories."""
        self.repo_pack_root.mkdir(parents=True, exist_ok=True)
        self.v2_index_root.mkdir(parents=True, exist_ok=True)
        self.v2_report_root.mkdir(parents=True, exist_ok=True)


# Default config instance
_default_config: Optional[V2Config] = None


def get_config() -> V2Config:
    """Get or create default v2 config."""
    global _default_config
    if _default_config is None:
        _default_config = V2Config()
    return _default_config


def set_config(config: V2Config):
    """Set custom v2 config."""
    global _default_config
    _default_config = config
