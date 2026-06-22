# Codex Vault Pipeline — context pack schema
"""Context pack schema for v2 repo-context lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path
import json


class SecurityStatus(str, Enum):
    """Security status for context items."""
    CLEAN = "clean"
    FLAGGED = "flagged"
    BLOCKED = "blocked"
    NOT_SCANNED = "not-scanned"


class ArtifactRole(str, Enum):
    """Artifact role types."""
    WORKFLOW = "workflow"
    SKILL = "skill"
    SOUL = "soul"
    DOCUMENTATION = "documentation"
    CODE = "code"
    CONFIG = "config"
    DEPLOYMENT = "deployment"
    SCRIPT = "script"
    UNKNOWN = "unknown"


class RetrievalMethod(str, Enum):
    """Retrieval method types."""
    METADATA = "metadata"
    FTS = "fts"
    VECTOR = "vector"
    HYBRID = "hybrid"
    REPOMIX = "repomix"
    MANUAL = "manual"


@dataclass
class SourceProvenance:
    """Provenance information for a context item."""
    
    source_id: str
    repo_url: Optional[str] = None
    commit: Optional[str] = None
    path: Optional[str] = None
    file_hash: Optional[str] = None
    artifact_role: ArtifactRole = ArtifactRole.UNKNOWN
    acquisition_status: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "repo_url": self.repo_url,
            "commit": self.commit,
            "path": self.path,
            "file_hash": self.file_hash,
            "artifact_role": self.artifact_role.value,
            "acquisition_status": self.acquisition_status,
        }


@dataclass
class RetrievalTrace:
    """Trace information for retrieval."""
    
    method: RetrievalMethod
    rank: int
    score: float
    query: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "method": self.method.value,
            "rank": self.rank,
            "score": self.score,
            "query": self.query,
        }


@dataclass
class ContextItem:
    """A single item in a context pack."""
    
    item_id: str
    text: str
    token_estimate: int
    provenance: SourceProvenance
    retrieval_trace: RetrievalTrace
    
    # Safety flags
    security_status: SecurityStatus = SecurityStatus.CLEAN
    is_quarantined: bool = False
    is_generated_catalog: bool = False
    is_readme: bool = False
    
    # Recommended use
    recommended_use: str = "general"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "item_id": self.item_id,
            "text": self.text[:500] + "..." if len(self.text) > 500 else self.text,
            "token_estimate": self.token_estimate,
            "provenance": self.provenance.to_dict(),
            "retrieval_trace": self.retrieval_trace.to_dict(),
            "security_status": self.security_status.value,
            "is_quarantined": self.is_quarantined,
            "is_generated_catalog": self.is_generated_catalog,
            "is_readme": self.is_readme,
            "recommended_use": self.recommended_use,
        }


@dataclass
class ContextPack:
    """A collection of context items."""
    
    pack_id: str
    items: List[ContextItem] = field(default_factory=list)
    total_tokens: int = 0
    query: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_item(self, item: ContextItem):
        """Add an item to the pack."""
        self.items.append(item)
        self.total_tokens += item.token_estimate
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pack_id": self.pack_id,
            "items": [i.to_dict() for i in self.items],
            "total_tokens": self.total_tokens,
            "query": self.query,
            "metadata": self.metadata,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    def write_json(self, path: Path):
        """Write context pack to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())


def validate_context_pack(data: Dict[str, Any]) -> List[str]:
    """Validate a context pack dictionary.
    
    Returns list of validation errors (empty if valid).
    """
    errors = []
    
    # Required fields
    if "pack_id" not in data:
        errors.append("Missing required field: pack_id")
    
    if "items" not in data:
        errors.append("Missing required field: items")
    elif not isinstance(data["items"], list):
        errors.append("Field 'items' must be a list")
    else:
        for i, item in enumerate(data["items"]):
            # Validate each item
            if "item_id" not in item:
                errors.append(f"Item {i}: Missing required field: item_id")
            if "text" not in item:
                errors.append(f"Item {i}: Missing required field: text")
            if "token_estimate" not in item:
                errors.append(f"Item {i}: Missing required field: token_estimate")
            if "provenance" not in item:
                errors.append(f"Item {i}: Missing required field: provenance")
            else:
                prov = item["provenance"]
                if "source_id" not in prov:
                    errors.append(f"Item {i}: Missing provenance.source_id")
            if "retrieval_trace" not in item:
                errors.append(f"Item {i}: Missing required field: retrieval_trace")
    
    return errors
