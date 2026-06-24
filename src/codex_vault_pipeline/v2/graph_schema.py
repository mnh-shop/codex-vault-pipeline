# Codex Vault Pipeline — graph schema
"""Canonical graph schema for Codex Vault knowledge graph.

Defines GraphNode and GraphEdge dataclasses with provenance-first design.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib


class NodeType(str, Enum):
    """Controlled vocabulary for graph node types."""
    
    # Core layers
    ECOSYSTEM = "ecosystem"
    REPOSITORY = "repository"
    SOURCE = "source"
    ARTIFACT = "artifact"
    UNIT = "unit"
    
    # Operational assets
    OPERATIONAL_ASSET = "operational_asset"
    SKILL = "skill"
    WORKFLOW = "workflow"
    AGENT_PROFILE = "agent_profile"
    SOUL = "soul"
    PLUGIN = "plugin"
    
    # Knowledge layer
    INTEGRATION = "integration"
    CAPABILITY = "capability"
    CONCEPT = "concept"
    USE_CASE = "use_case"
    GUIDE = "guide"
    
    # Meta
    REPORT = "report"
    BENCHMARK = "benchmark"
    RUNTIME_INDEX = "runtime_index"


class EdgeType(str, Enum):
    """Controlled vocabulary for graph edge types."""
    
    BELONGS_TO_ECOSYSTEM = "belongs_to_ecosystem"
    IMPLEMENTS_CAPABILITY = "implements_capability"
    DEPENDS_ON = "depends_on"
    INTEGRATES_WITH = "integrates_with"
    DOCUMENTS = "documents"
    DERIVES_FROM = "derives_from"
    USES_ASSET = "uses_asset"
    HAS_SKILL = "has_skill"
    HAS_WORKFLOW = "has_workflow"
    HAS_PROFILE = "has_profile"
    HAS_SOUL = "has_soul"
    HAS_MEMORY = "has_memory"
    SUPPORTS_USE_CASE = "supports_use_case"
    REFERENCED_BY_GUIDE = "referenced_by_guide"
    EVIDENCE_FOR = "evidence_for"
    SUPERSEDES = "supersedes"
    GENERATED_FROM = "generated_from"
    CATALOGED_BY = "cataloged_by"
    INDEXED_BY = "indexed_by"


class ConfidenceLevel(str, Enum):
    """Confidence levels for graph edges."""
    
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"


class SecurityState(str, Enum):
    """Security state for graph nodes."""
    
    CLEAN = "clean"
    FLAGGED = "flagged"
    BLOCKED = "blocked"
    NOT_SCANNED = "not-scanned"


@dataclass
class EvidenceRef:
    """Reference to evidence supporting a graph edge."""
    
    source_id: str
    artifact_id: Optional[str] = None
    unit_id: Optional[str] = None
    path: Optional[str] = None
    line: Optional[int] = None


def make_deterministic_node_id(
    source_id: str,
    artifact_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    node_type: NodeType = NodeType.SOURCE,
) -> str:
    """Generate deterministic node ID from provenance.
    
    Format: source_id#artifact_id#unit_id#node_type
    or: sha256:content_hash#node_type for content-based nodes
    """
    parts = [source_id]
    if artifact_id:
        parts.append(artifact_id)
    if unit_id:
        parts.append(unit_id)
    parts.append(node_type.value)
    return "#".join(parts)


def make_deterministic_edge_id(
    from_node_id: str,
    edge_type: EdgeType,
    to_node_id: str,
) -> str:
    """Generate deterministic edge ID from endpoints."""
    return f"{from_node_id}#{edge_type.value}#{to_node_id}"


@dataclass
class GraphNode:
    """Canonical graph node for Codex Vault knowledge graph."""
    
    # Identity
    node_id: str
    node_type: NodeType
    
    # Labels
    label: str
    aliases: List[str] = field(default_factory=list)
    
    # Provenance (required for evidence-bearing nodes)
    source_id: Optional[str] = None
    artifact_id: Optional[str] = None
    unit_id: Optional[str] = None
    domain_record_id: Optional[str] = None
    
    # Location
    path: Optional[str] = None
    resolved_commit: Optional[str] = None
    
    # Status
    status: str = "active"
    security_state: SecurityState = SecurityState.CLEAN
    
    # Metadata
    ecosystem: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Audit
    created_by: str = "codex-vault-pipeline"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = "0.1.0"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSONL serialization."""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
            "aliases": self.aliases,
            "source_id": self.source_id,
            "artifact_id": self.artifact_id,
            "unit_id": self.unit_id,
            "domain_record_id": self.domain_record_id,
            "path": self.path,
            "resolved_commit": self.resolved_commit,
            "status": self.status,
            "security_state": self.security_state.value,
            "ecosystem": self.ecosystem,
            "metadata": self.metadata,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }
    
    def to_jsonl_line(self) -> str:
        """Serialize to JSONL line."""
        import json
        return json.dumps(self.to_dict())


@dataclass
class GraphEdge:
    """Canonical graph edge for Codex Vault knowledge graph."""
    
    # Identity
    edge_id: str
    edge_type: EdgeType
    
    # Endpoints
    from_node_id: str
    to_node_id: str
    
    # Evidence
    confidence: ConfidenceLevel = ConfidenceLevel.EXTRACTED
    evidence: List[EvidenceRef] = field(default_factory=list)
    
    # Provenance
    source_id: Optional[str] = None
    artifact_id: Optional[str] = None
    unit_id: Optional[str] = None
    path: Optional[str] = None
    
    # Audit
    relation_source: str = "deterministic"
    created_by: str = "codex-vault-pipeline"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = "0.1.0"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSONL serialization."""
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "confidence": self.confidence.value,
            "evidence": [e.__dict__ for e in self.evidence],
            "source_id": self.source_id,
            "artifact_id": self.artifact_id,
            "unit_id": self.unit_id,
            "path": self.path,
            "relation_source": self.relation_source,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }
    
    def to_jsonl_line(self) -> str:
        """Serialize to JSONL line."""
        import json
        return json.dumps(self.to_dict())


def validate_graph_node(data: Dict[str, Any]) -> List[str]:
    """Validate a graph node dictionary.
    
    Returns list of validation errors (empty if valid).
    """
    errors = []
    
    # Required fields
    if "node_id" not in data:
        errors.append("Missing required field: node_id")
    if "node_type" not in data:
        errors.append("Missing required field: node_type")
    else:
        try:
            NodeType(data["node_type"])
        except ValueError:
            errors.append(f"Invalid node_type: {data['node_type']}")
    
    if "label" not in data:
        errors.append("Missing required field: label")
    
    # Evidence-bearing nodes require provenance OR path
    # Source-layer notes ARE the source, so path is sufficient
    if data.get("node_type") in [t.value for t in [
        NodeType.SOURCE, NodeType.ARTIFACT, NodeType.UNIT,
        NodeType.OPERATIONAL_ASSET, NodeType.SKILL, NodeType.WORKFLOW,
    ]]:
        if not data.get("source_id") and not data.get("artifact_id") and not data.get("path"):
            errors.append("Evidence-bearing node requires source_id, artifact_id, or path")
    
    return errors


def validate_graph_edge(data: Dict[str, Any]) -> List[str]:
    """Validate a graph edge dictionary.
    
    Returns list of validation errors (empty if valid).
    """
    errors = []
    
    # Required fields
    if "edge_id" not in data:
        errors.append("Missing required field: edge_id")
    if "edge_type" not in data:
        errors.append("Missing required field: edge_type")
    else:
        try:
            EdgeType(data["edge_type"])
        except ValueError:
            errors.append(f"Invalid edge_type: {data['edge_type']}")
    
    if "from_node_id" not in data:
        errors.append("Missing required field: from_node_id")
    if "to_node_id" not in data:
        errors.append("Missing required field: to_node_id")
    
    # Evidence-bearing edges require evidence or provenance
    if data.get("confidence") in [c.value for c in [
        ConfidenceLevel.EXTRACTED, ConfidenceLevel.INFERRED,
    ]]:
        if not data.get("evidence") and not data.get("source_id"):
            errors.append("Evidence-bearing edge requires evidence or source_id")
    
    return errors