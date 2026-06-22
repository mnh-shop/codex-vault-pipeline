"""Tests for codex_vault_pipeline.graph.projection."""

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from codex_vault_pipeline.graph.projection import (
    GraphProjectionResult,
    project_graph_from_runtime,
)


# ---------------------------------------------------------------------------
# Helper: write a fake source.v1.yaml under <runtime_root>/sources/<dir>/
# ---------------------------------------------------------------------------


def _write_source_yaml(
    root: Path,
    source_id: str,
    *,
    primary_domain: str = "",
    related_domains=None,
    ecosystems=None,
    capabilities=None,
    artifact_role: str = "",
    source_role: str = "",
    authority_level: str = "",
    lifecycle_status: str = "",
    knowledge_status: str = "",
    title: str = "",
    canonical_url: str = "",
) -> Path:
    """Write a minimal source.v1.yaml and return its path."""
    if related_domains is None:
        related_domains = []
    if ecosystems is None:
        ecosystems = []
    if capabilities is None:
        capabilities = []

    # Deterministic sub-directory name (mirrors safe_source_id convention).
    subdir = source_id.replace(":", "-").replace("/", "-").replace("\\", "-")
    source_dir = root / "sources" / subdir
    source_dir.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = {"source_id": source_id}
    if primary_domain:
        data["primary_domain"] = primary_domain
    if related_domains:
        data["related_domains"] = related_domains
    if ecosystems:
        data["ecosystems"] = ecosystems
    if capabilities:
        data["capabilities"] = capabilities
    if artifact_role:
        data["artifact_role"] = artifact_role
    if source_role:
        data["source_role"] = source_role
    if authority_level:
        data["authority_level"] = authority_level
    if lifecycle_status:
        data["lifecycle_status"] = lifecycle_status
    if title:
        data["repo_identity"] = {"full_name": title}
    if canonical_url:
        data["canonical_url"] = canonical_url

    path = source_dir / "source.v1.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_with_two_sources(tmp_path: Path) -> Path:
    """Create a minimal runtime tree with two sources and return its root."""
    runtime_root = tmp_path / ".runtime"
    _write_source_yaml(
        runtime_root,
        "github:org/alpha",
        primary_domain="hermes-agent",
        ecosystems=["python", "langchain"],
        capabilities=["deep-research"],
        artifact_role="source-code",
    )
    _write_source_yaml(
        runtime_root,
        "github:org/beta",
        primary_domain="n8n",
        ecosystems=["typescript"],
        capabilities=["workflow-automation"],
        artifact_role="documentation",
    )
    return runtime_root


@pytest.fixture
def empty_runtime(tmp_path: Path) -> Path:
    """Create a runtime tree with no sources."""
    runtime_root = tmp_path / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    # sources dir exists but empty.
    (runtime_root / "sources").mkdir(parents=True, exist_ok=True)
    return runtime_root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectGraphFromRuntime:
    """Core projection behaviour."""

    def test_writes_hubs_and_cards(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """Projection writes both hub notes and source cards."""
        output_dir = tmp_path / "_graph"
        result = project_graph_from_runtime(runtime_with_two_sources, output_dir)
        assert result.source_count == 2
        assert result.hub_count > 0
        assert result.source_card_count == 2
        assert len(result.files_written) == result.hub_count + result.source_card_count

    def test_result_counts_are_correct(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """Returned counts match actual file system state."""
        output_dir = tmp_path / "_graph"
        result = project_graph_from_runtime(runtime_with_two_sources, output_dir)

        # Verify hubs exist on disk.
        hub_files = sorted(output_dir.rglob("*.md"))
        hub_dirs = sorted(
            p.relative_to(output_dir).parent.as_posix()
            for p in hub_files
            if p.parent.name != "sources"
        )
        source_card_files = sorted((output_dir / "sources").rglob("*.md"))

        assert result.hub_count == len(hub_files) - len(source_card_files)
        assert result.source_card_count == len(source_card_files)
        assert len(result.files_written) == len(hub_files)

    def test_all_files_under_output_dir(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """No files are written outside output_dir."""
        output_dir = tmp_path / "_graph"
        result = project_graph_from_runtime(runtime_with_two_sources, output_dir)

        for p in result.files_written:
            rel = p.relative_to(output_dir)
            assert not rel.as_posix().startswith(".."), f"File {p} is outside output_dir"

    def test_output_structure_has_expected_folders(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """Output contains expected axis sub-directories and sources/."""
        output_dir = tmp_path / "_graph"
        project_graph_from_runtime(runtime_with_two_sources, output_dir)

        # At minimum: domains/, ecosystems/, capabilities/, sources/, artifact-roles/
        axis_dirs = {
            "domains",
            "ecosystems",
            "capabilities",
            "artifact-roles",
            "sources",
        }
        present = {
            p.name
            for p in output_dir.iterdir()
            if p.is_dir()
        }
        assert axis_dirs.issubset(present), f"Missing dirs: {axis_dirs - present}"


class TestProjectionIdempotent:
    """Repeated projection runs produce identical results."""

    def test_repeated_run_is_idempotent(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """Second run produces zero new writes."""
        output_dir = tmp_path / "_graph"
        result1 = project_graph_from_runtime(runtime_with_two_sources, output_dir)
        result2 = project_graph_from_runtime(runtime_with_two_sources, output_dir)

        # Same source count (records are re-read).
        assert result1.source_count == result2.source_count

        # Second run writes zero new files — all content already matches.
        assert len(result2.files_written) == 0
        assert result2.hub_count == 0
        assert result2.source_card_count == 0

    def test_file_contents_unchanged(
        self, runtime_with_two_sources: Path, tmp_path: Path
    ):
        """File contents are identical after re-run."""
        output_dir = tmp_path / "_graph"
        project_graph_from_runtime(runtime_with_two_sources, output_dir)

        # Collect content hashes from first run.
        first = {}
        for md in sorted(output_dir.rglob("*.md")):
            first[md.relative_to(output_dir)] = md.read_text()

        # Second run.
        project_graph_from_runtime(runtime_with_two_sources, output_dir)

        # Contents match.
        for md in sorted(output_dir.rglob("*.md")):
            rel = md.relative_to(output_dir)
            assert md.read_text() == first[rel], f"Content changed: {rel}"


class TestProjectionEmptyRuntime:
    """Edge case: no source records."""

    def test_empty_runtime_produces_zero_counts(
        self, empty_runtime: Path, tmp_path: Path
    ):
        """No sources, no hubs, no cards — but no crash either."""
        output_dir = tmp_path / "_graph"
        result = project_graph_from_runtime(empty_runtime, output_dir)
        assert result.source_count == 0
        assert result.hub_count == 0
        assert result.source_card_count == 0
        assert result.files_written == ()

    def test_empty_runtime_no_dirs_created(
        self, empty_runtime: Path, tmp_path: Path
    ):
        """Output directory may be empty or absent after empty run."""
        output_dir = tmp_path / "_graph"
        project_graph_from_runtime(empty_runtime, output_dir)
        # It's acceptable if output_dir doesn't exist yet (nothing to write)
        # or exists but is empty.
        if output_dir.is_dir():
            children = list(output_dir.iterdir())
            assert children == [], f"Expected empty output dir, got {children}"


class TestProjectionResultDataclass:
    """GraphProjectionResult contract."""

    def test_is_frozen(self):
        """GraphProjectionResult instances cannot be mutated."""
        result = GraphProjectionResult(
            runtime_root=Path("/r"),
            output_dir=Path("/o"),
            source_count=0,
            hub_count=0,
            source_card_count=0,
            files_written=(),
        )
        with pytest.raises(AttributeError):
            result.source_count = 99  # type: ignore[misc]

    def test_repr_contains_counts(self, runtime_with_two_sources: Path, tmp_path: Path):
        """String representation includes counts."""
        output_dir = tmp_path / "_graph"
        result = project_graph_from_runtime(runtime_with_two_sources, output_dir)
        text = repr(result)
        assert "source_count=2" in text
        assert "hub_count=" in text
        assert "source_card_count=2" in text
