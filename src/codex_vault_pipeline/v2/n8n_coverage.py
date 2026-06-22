# Codex Vault Pipeline — n8n coverage planner
"""n8n coverage analysis and reacquisition planning."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path


@dataclass
class N8nSource:
    """n8n source information."""
    
    source_id: str
    name: str
    raw_path: str
    status: str  # complete, partial, stale
    coverage: float
    workflow_count: int
    total_expected: int
    authority_level: str  # canonical-upstream, community
    source_type: str  # official docs, workflow collection, etc.
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "name": self.name,
            "raw_path": self.raw_path,
            "status": self.status,
            "coverage": self.coverage,
            "workflow_count": self.workflow_count,
            "total_expected": self.total_expected,
            "authority_level": self.authority_level,
            "source_type": self.source_type,
        }


@dataclass
class N8nCoverageReport:
    """n8n coverage analysis report."""
    
    sources: List[N8nSource] = field(default_factory=list)
    total_raw_files: int = 0
    total_unit_files: int = 0
    total_metadata_records: int = 0
    total_fts_rows: int = 0
    total_vector_rows: int = 0
    missing_extraction_count: int = 0
    coverage_status: str = "UNKNOWN"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "sources": [s.to_dict() for s in self.sources],
            "total_raw_files": self.total_raw_files,
            "total_unit_files": self.total_unit_files,
            "total_metadata_records": self.total_metadata_records,
            "total_fts_rows": self.total_fts_rows,
            "total_vector_rows": self.total_vector_rows,
            "missing_extraction_count": self.missing_extraction_count,
            "coverage_status": self.coverage_status,
        }


class N8nCoverageAnalyzer:
    """Analyzer for n8n workflow coverage."""
    
    def __init__(self, vault_root: Path):
        """Initialize analyzer."""
        self.vault_root = vault_root
        self.runtime_root = vault_root / ".runtime"
        self.sources: List[N8nSource] = []
    
    def analyze(self) -> N8nCoverageReport:
        """Analyze n8n coverage."""
        report = N8nCoverageReport()
        
        # Known n8n sources from Phase 04b2
        known_sources = [
            N8nSource(
                source_id="github:n8n-io/n8n-docs",
                name="Official n8n documentation",
                raw_path="raw/n8n/",
                status="complete",
                coverage=1.0,
                workflow_count=28,
                total_expected=28,
                authority_level="canonical-upstream",
                source_type="official docs",
            ),
            N8nSource(
                source_id="github:enescingoz/awesome-n8n-templates",
                name="Community templates",
                raw_path="raw/awesome-n8n-templates/",
                status="complete",
                coverage=1.0,
                workflow_count=307,
                total_expected=307,
                authority_level="community",
                source_type="workflow collection",
            ),
            N8nSource(
                source_id="github:nusquama/n8nworkflows.xyz",
                name="Massive workflow catalog",
                raw_path="raw/n8nworkflows-xyz/",
                status="partial",
                coverage=0.0763,
                workflow_count=1200,
                total_expected=15744,
                authority_level="community",
                source_type="workflow catalog",
            ),
            N8nSource(
                source_id="github:wassupjay/n8n-free-templates",
                name="Community templates",
                raw_path="raw/n8n-free-templates/",
                status="complete",
                coverage=1.0,
                workflow_count=202,
                total_expected=202,
                authority_level="community",
                source_type="workflow collection",
            ),
            N8nSource(
                source_id="github:Zie619/n8n-workflows",
                name="Community workflows",
                raw_path="raw/n8n-workflows/",
                status="complete",
                coverage=1.0,
                workflow_count=2065,
                total_expected=2065,
                authority_level="community",
                source_type="workflow collection",
            ),
            N8nSource(
                source_id="github:czlonkowski/n8n-skills",
                name="Claude Code skills",
                raw_path="raw/n8n-skills/",
                status="complete",
                coverage=1.0,
                workflow_count=88,
                total_expected=88,
                authority_level="community",
                source_type="skills/templates",
            ),
        ]
        
        report.sources = known_sources
        
        # Calculate totals
        report.total_raw_files = sum(s.workflow_count for s in known_sources)
        report.total_unit_files = 2804  # From Phase 04b2
        report.total_metadata_records = 2804
        report.total_fts_rows = 2804
        report.total_vector_rows = 2804
        report.missing_extraction_count = 393  # From Phase 04b2
        
        # Determine coverage status
        partial_sources = [s for s in known_sources if s.status == "partial"]
        if partial_sources:
            report.coverage_status = "PARTIAL"
        else:
            report.coverage_status = "COMPLETE"
        
        return report
    
    def create_reacquisition_plan(self, report: N8nCoverageReport) -> Dict[str, Any]:
        """Create reacquisition plan."""
        plan = {
            "phase": "05a",
            "date": "2026-06-22",
            "coverage_status": report.coverage_status,
            "reacquisition_needed": report.coverage_status == "PARTIAL",
            "sources_to_reacquire": [],
        }
        
        # Add partial sources to reacquisition plan
        for source in report.sources:
            if source.status == "partial":
                plan["sources_to_reacquire"].append({
                    "source_id": source.source_id,
                    "name": source.name,
                    "current_coverage": source.coverage,
                    "current_count": source.workflow_count,
                    "target_count": source.total_expected,
                    "method": "GitHub clone",
                    "expected_output_folder": source.raw_path,
                    "validation_count": source.total_expected,
                })
        
        return plan
