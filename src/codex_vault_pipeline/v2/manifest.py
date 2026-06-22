# Codex Vault Pipeline — v2 manifest
"""Manifest generation for v2 repo-context lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import yaml


@dataclass
class RepoPackManifest:
    """Manifest for a single repo pack."""
    
    source_id: str
    source_type: str  # "github" or "local"
    repo_url: Optional[str] = None
    local_path: Optional[str] = None
    revision: Optional[str] = None
    commit: Optional[str] = None
    output_dir: str = ""
    output_format: str = "markdown"
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    security_check: bool = True
    compression: bool = False
    expected_output_file: Optional[str] = None
    expected_manifest_file: Optional[str] = None
    expected_token_count: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "repo_url": self.repo_url,
            "local_path": self.local_path,
            "revision": self.revision,
            "commit": self.commit,
            "output_dir": self.output_dir,
            "output_format": self.output_format,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "security_check": self.security_check,
            "compression": self.compression,
            "expected_output_file": self.expected_output_file,
            "expected_manifest_file": self.expected_manifest_file,
            "expected_token_count": self.expected_token_count,
        }
    
    def to_yaml(self) -> str:
        """Convert to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False)
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class PilotManifest:
    """Manifest for the Repomix pilot run."""
    
    phase: str = "05a"
    pilot_name: str = "repomix_pilot"
    sources: List[RepoPackManifest] = field(default_factory=list)
    
    def add_source(self, manifest: RepoPackManifest):
        """Add a source to the pilot."""
        self.sources.append(manifest)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "phase": self.phase,
            "pilot_name": self.pilot_name,
            "sources": [s.to_dict() for s in self.sources],
        }
    
    def to_yaml(self) -> str:
        """Convert to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False)
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    def write_yaml(self, path: Path):
        """Write manifest to YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml())
    
    def write_json(self, path: Path):
        """Write manifest to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())
