"""Tests for codex_vault_pipeline.graph.graph_config_writer."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from codex_vault_pipeline.graph.graph_config_writer import (
    GraphColorGroup,
    build_obsidian_graph_config,
    default_color_groups,
    hex_to_rgb_int,
    write_obsidian_graph_config,
)


# ---------------------------------------------------------------------------
# GraphColorGroup dataclass
# ---------------------------------------------------------------------------


class TestGraphColorGroup:
    """Frozen dataclass contract."""

    def test_is_frozen(self):
        group = GraphColorGroup(name="test", query="tag:#x", color="#ffffff")
        with pytest.raises(AttributeError):
            group.name = "changed"  # type: ignore[misc]

    def test_required_fields(self):
        group = GraphColorGroup(name="test", query="tag:#x", color="#ff0000")
        assert group.name == "test"
        assert group.query == "tag:#x"
        assert group.color == "#ff0000"


# ---------------------------------------------------------------------------
# Default groups
# ---------------------------------------------------------------------------


class TestDefaultColorGroups:
    """The standard set covers all required queries."""

    def test_returns_twelve_groups(self):
        groups = default_color_groups()
        assert len(groups) == 12

    def test_all_are_graph_color_group_instances(self):
        for g in default_color_groups():
            assert isinstance(g, GraphColorGroup)

    def test_includes_all_domain_queries(self):
        queries = {g.query for g in default_color_groups()}
        expected_domains = {
            "tag:#graph/domain/hermes-agent",
            "tag:#graph/domain/n8n",
            "tag:#graph/domain/agentfield",
            "tag:#graph/domain/deep-research",
            "tag:#graph/domain/osint",
            "tag:#graph/domain/coding-agents",
            "tag:#graph/domain/training-systems",
            "tag:#graph/domain/ai-content-generation",
        }
        assert expected_domains.issubset(queries)

    def test_includes_source_and_hub_queries(self):
        queries = {g.query for g in default_color_groups()}
        assert "tag:#graph/source" in queries
        assert "tag:#graph/hub" in queries

    def test_includes_status_and_role_queries(self):
        queries = {g.query for g in default_color_groups()}
        assert "tag:#graph/artifact-role/source-catalog" in queries
        assert "tag:#graph/authority-level/canonical-upstream" in queries
        assert "tag:#graph/domain/ai-content-generation" in queries

    def test_names_are_unique(self):
        names = [g.name for g in default_color_groups()]
        assert len(names) == len(set(names))

    def test_queries_are_unique(self):
        queries = [g.query for g in default_color_groups()]
        assert len(queries) == len(set(queries))

    def test_all_colors_start_with_hash(self):
        for g in default_color_groups():
            assert g.color.startswith("#"), f"{g.name}: color missing #"

    def test_deterministic_order(self):
        """Repeated calls return identical tuples."""
        assert default_color_groups() == default_color_groups()


# ---------------------------------------------------------------------------
# Hex colour conversion
# ---------------------------------------------------------------------------


class TestHexToRgbInt:
    """Colour conversion is deterministic and correct."""

    def test_known_color(self):
        # #1f4e79 → R=0x1f G=0x4e B=0x79 → (31<<16)|(78<<8)|121
        assert hex_to_rgb_int("#1f4e79") == 2051705

    def test_red(self):
        assert hex_to_rgb_int("#ff0000") == 0xFF0000

    def test_green(self):
        assert hex_to_rgb_int("#00ff00") == 0x00FF00

    def test_blue(self):
        assert hex_to_rgb_int("#0000ff") == 0x0000FF

    def test_white(self):
        assert hex_to_rgb_int("#ffffff") == 0xFFFFFF

    def test_black(self):
        assert hex_to_rgb_int("#000000") == 0

    def test_strips_hash(self):
        assert hex_to_rgb_int("#2e7d32") == hex_to_rgb_int("2e7d32")

    def test_deterministic(self):
        """Same input always produces same output."""
        assert hex_to_rgb_int("#6a1b9a") == 6953882
        assert hex_to_rgb_int("#6a1b9a") == 6953882

    def test_all_default_colors(self):
        """All default colours convert without error."""
        for g in default_color_groups():
            rgb = hex_to_rgb_int(g.color)
            assert 0 <= rgb <= 0xFFFFFF


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


class TestBuildObsidianGraphConfig:
    """Config dict structure and content."""

    def test_contains_color_groups_key(self):
        config = build_obsidian_graph_config()
        assert "colorGroups" in config

    def test_color_groups_is_list(self):
        config = build_obsidian_graph_config()
        assert isinstance(config["colorGroups"], list)

    def test_default_config_has_twelve_groups(self):
        config = build_obsidian_graph_config()
        assert len(config["colorGroups"]) == 12

    def test_each_group_has_query(self):
        config = build_obsidian_graph_config()
        for group in config["colorGroups"]:
            assert "query" in group
            assert isinstance(group["query"], str)

    def test_each_group_has_color_with_a_and_rgb(self):
        config = build_obsidian_graph_config()
        for group in config["colorGroups"]:
            color = group["color"]
            assert "a" in color
            assert "rgb" in color
            assert color["a"] == 1
            assert isinstance(color["rgb"], int)
            assert 0 <= color["rgb"] <= 0xFFFFFF

    def test_deterministic_order(self):
        """Same inputs produce identical config."""
        c1 = build_obsidian_graph_config()
        c2 = build_obsidian_graph_config()
        assert c1 == c2

    def test_custom_groups_override_defaults(self):
        custom = (GraphColorGroup("custom", "tag:#x/y", "#ff0000"),)
        config = build_obsidian_graph_config(custom)
        assert len(config["colorGroups"]) == 1
        assert config["colorGroups"][0]["query"] == "tag:#x/y"
        assert config["colorGroups"][0]["color"]["rgb"] == 0xFF0000

    def test_empty_groups(self):
        config = build_obsidian_graph_config(())
        assert config["colorGroups"] == []

    def test_json_serialisable(self):
        """Config can be serialised to JSON without error."""
        config = build_obsidian_graph_config()
        text = json.dumps(config)
        assert isinstance(text, str)
        # Round-trip.
        assert json.loads(text) == config

    def test_deterministic_json(self):
        """Same config always serialises to identical JSON."""
        config = build_obsidian_graph_config()
        text1 = json.dumps(config, indent=2, sort_keys=True)
        text2 = json.dumps(config, indent=2, sort_keys=True)
        assert text1 == text2


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class TestWriteObsidianGraphConfig:
    """File-writing behaviour."""

    def test_writes_valid_json(self, tmp_path: Path):
        path = tmp_path / ".obsidian" / "graph.json"
        config = build_obsidian_graph_config()
        written = write_obsidian_graph_config(path, config)
        assert written == path.resolve()

        data = json.loads(path.read_text())
        assert "colorGroups" in data
        assert len(data["colorGroups"]) == 12

    def test_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "a" / "b" / "c" / "graph.json"
        config = build_obsidian_graph_config()
        write_obsidian_graph_config(path, config)
        assert path.is_file()

    def test_idempotent_write(self, tmp_path: Path):
        path = tmp_path / "graph.json"
        config = build_obsidian_graph_config()
        write_obsidian_graph_config(path, config)
        content1 = path.read_text()
        write_obsidian_graph_config(path, config)
        content2 = path.read_text()
        assert content1 == content2

    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        path = tmp_path / "graph.json"
        config = build_obsidian_graph_config()
        write_obsidian_graph_config(path, config)
        # No .tmp.json remnants.
        temp_files = list(tmp_path.glob("*.tmp.json"))
        assert temp_files == []

    def test_no_files_outside_provided_path(self, tmp_path: Path):
        path = tmp_path / "graph.json"
        config = build_obsidian_graph_config()
        write_obsidian_graph_config(path, config)
        # Only the expected file exists.
        all_files = list(tmp_path.rglob("*"))
        # The .tmp.json might exist briefly but should be cleaned up.
        non_temp = [f for f in all_files if ".tmp.json" not in f.name]
        assert non_temp == [path]

    def test_resolves_path(self, tmp_path: Path):
        """Writer returns resolved absolute path."""
        config = build_obsidian_graph_config()
        written = write_obsidian_graph_config(tmp_path / "graph.json", config)
        assert isinstance(written, Path)
        assert written.is_absolute()
        assert written.name == "graph.json"
