# Phase 05F2b: Codex Vault Canonical Graph Schema

**Date:** 2026-06-23
**Status:** Design Complete
**Phase:** 05F2b

## Executive Summary

Codex Vault requires a provenance-first graph schema that maps its five-layer model (Source → Artifact → Unit → Domain Record → Knowledge Note) to graph nodes and edges. This schema will serve as the canonical source for large-scale visualization and cross-repo analysis, while Obsidian remains the curated human navigation graph.

## Canonical Graph Node Schema

```python
@dataclass
class GraphNode:
    """Canonical graph node for Codex Vault knowledge graph."""
    
    # Identity
    node_id: str                    # Deterministic ID (e.g., "sha256:abc123#heading:configuration")
    node_type: NodeType             # Enum: ecosystem, repository, source, artifact, unit, etc.
    
    # Labels
    label: str                      # Human-readable name
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
    status: str = "active"          # active, deprecated, superseded, candidate
    security_state: SecurityState = SecurityState.CLEAN
    
    # Metadata
    ecosystem: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Audit
    created_by: str = "codex-vault-pipeline"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = "0.1.0"
```

### Node Types (Controlled Vocabulary)

| Type | Description | Evidence Source |
|------|-------------|-----------------|
| ecosystem | Agent ecosystem (hermes-agent, n8n, agentfield) | Domain record |
| repository | Source repository | Source record |
| source | Acquired source | Source record |
| artifact | Preserved file/object | Artifact record |
| unit | Retrieval unit (section, symbol) | Unit record |
| operational_asset | Skill, SOUL, workflow, plugin | Artifact + type detection |
| skill | Skill definition | SKILL.md detection |
| workflow | n8n workflow JSON | Workflow catalog |
| agent_profile | Agent profile/definition | Profile detection |
| soul | SOUL.md behavior definition | SOUL.md detection |
| plugin | Plugin definition | Plugin detection |
| integration | Integration target | Domain record |
| capability | Agent capability | Domain record |
| concept | Knowledge concept | Wiki page |
| use_case | Use case documentation | Wiki page |
| guide | How-to guide | Wiki page |
| report | Phase report | Report file |
| benchmark | Benchmark result | Benchmark file |
| runtime_index | Index entry | Index record |

## Canonical Graph Edge Schema

```python
@dataclass
class GraphEdge:
    """Canonical graph edge for Codex Vault knowledge graph."""
    
    # Identity
    edge_id: str                    # Deterministic: "from_node#edge_type#to_node"
    edge_type: EdgeType             # Enum: belongs_to, implements, depends_on, etc.
    
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
    relation_source: str = "deterministic"  # deterministic, inferred, human-verified
    created_by: str = "codex-vault-pipeline"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = "0.1.0"
```

### Edge Types (Controlled Vocabulary)

| Type | Description | Deterministic? |
|------|-------------|----------------|
| belongs_to_ecosystem | Source/repository belongs to ecosystem | ✅ Yes |
| implements_capability | Artifact implements capability | ⚠️ Partial (code analysis) |
| depends_on | Source depends on another source | ✅ Yes (package.json, requirements.txt) |
| integrates_with | Integration with external tool | ⚠️ Partial (detected or declared) |
| documents | Wiki page documents source/artifact | ✅ Yes |
| derives_from | Derived from source/artifact | ✅ Yes |
| uses_asset | Uses operational asset | ⚠️ Partial (detected) |
| has_skill | Repository/source has skill | ✅ Yes |
| has_workflow | Repository/source has workflow | ✅ Yes |
| has_profile | Repository/source has profile | ✅ Yes |
| has_soul | Repository/source has SOUL | ✅ Yes |
| has_memory | Agent has memory system | ⚠️ Partial |
| supports_use_case | Supports specific use case | ❌ Human review required |
| referenced_by_guide | Guide references concept | ✅ Yes |
| evidence_for | Evidence for domain record | ✅ Yes |
| supersedes | Supersedes previous version | ✅ Yes |
| generated_from | Generated from source | ✅ Yes |
| cataloged_by | Cataloged by report | ✅ Yes |
| indexed_by | Indexed by runtime index | ✅ Yes |

### Confidence Levels

```python
class ConfidenceLevel(str, Enum):
    EXTRACTED = "extracted"      # Explicitly stated in source
    INFERRED = "inferred"        # Reasonable deduction
    AMBIGUOUS = "ambiguous"      # Uncertain, flagged for review
```

## Graph Output Layout

### Canonical Outputs (Runtime)

```
.runtime/graph/
├── nodes.jsonl                 # One node per line (canonical)
├── edges.jsonl                 # One edge per line (canonical)
├── graph-summary.json          # Statistics and metadata
└── validation-report.json      # Validation results
```

### Derived Exports

```
.runtime/graph/exports/
├── codex-vault.graphml         # For Gephi, yEd
├── codex-vault-3d.json         # For 3D visualization
└── codex-vault-cytoscape.json  # For Cytoscape.js
```

## Extraction Rules

### Deterministic Edges (Auto-generated)

1. **source → repository**: From source record `repo_url`
2. **repository → ecosystem**: From source classification
3. **source → artifact**: From artifact creation during ingest
4. **artifact → unit**: From unit extraction
5. **source/catalog → operational_asset**: From catalog detection
6. **n8n source → workflow**: From workflow catalog
7. **skill repo → skill**: From SKILL.md detection
8. **wiki-visible note → concept/use_case/guide**: From page classification

### Human-Review Required Edges

1. **supports_use_case**: Must be explicitly declared or human-verified
2. **implements_capability**: If not directly evident from code
3. **integrates_with**: When inferred from usage patterns
4. **depends_on**: Outside package metadata (e.g., conceptual dependencies)
5. **reusable_for**: MaxiOS design edges

### Forbidden Auto-Inferences

- Adding links only for graph shape
- Semantic similarity as factual edge
- README mention as dependency
- Generated summary as sole evidence
- Unverified ecosystem membership

## Integration with Existing v2 Data Model

The graph schema reuses existing provenance patterns:

- `SourceProvenance` → `GraphNode.source_id`, `GraphNode.resolved_commit`
- `ArtifactRole` → `GraphNode.node_type` (operational_asset subtypes)
- `ContextItem` → `GraphNode` (with additional graph-specific fields)
- `RepoPackManifest` → `GraphNode` (repository nodes)

## Next Implementation Phases

| Phase | Description |
|-------|-------------|
| 05F2c | Graph schema code and validators |
| 05F2d | Graph builder from existing runtime records |
| 05F2e | Graph export adapters for GraphML/Cytoscape/3D JSON |
| 05F2f | Optional Graphify/GitNexus comparison/export bridge |
| 05F2g | Semantic candidate-edge suggester (human-reviewed only) |

## Validation Rules

1. Evidence-bearing nodes/edges must have `source_id` or `artifact_id`
2. Node IDs must be deterministic (content-hash based, not path-only)
3. Edge IDs must be stable across runs
4. All provenance fields must resolve to preserved evidence
5. Security state must be validated before indexing