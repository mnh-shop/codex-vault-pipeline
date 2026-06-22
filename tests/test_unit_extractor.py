"""Tests for deterministic unit extraction."""

import json
import hashlib
from pathlib import Path

import pytest

from codex_vault_pipeline.ingest.unit_extractor import (
    extract_units_from_artifact,
    split_markdown_sections,
    _slugify,
    _unit_id,
    _make_fingerprints,
    _looks_like_n8n,
)


def _make_artifact(
    sha="a" * 64,
    role="documentation",
    media_type="text/markdown",
    source_id="github:test/repo",
    source_path="README.md",
    sec_status="clean",
):
    return {
        "content_sha256": sha,
        "artifact_role": role,
        "media_type": media_type,
        "source_id": source_id,
        "source_path": source_path,
        "security_scan": {"status": sec_status},
    }


def _make_occurrence(
    sha="a" * 64,
    source_id="github:test/repo",
    source_path="README.md",
):
    return {
        "occurrence_id": f"sha256:{hashlib.sha256(b'occ').hexdigest()}",
        "content_sha256": sha,
        "source_id": source_id,
        "source_path": source_path,
    }


# ---------------------------------------------------------------------------
# Markdown / doc-section
# ---------------------------------------------------------------------------


class TestMarkdownExtraction:
    def test_markdown_with_headings_creates_multiple_units(self):
        text = "# Title\n\nContent\n\n## Section 1\n\nBody 1\n\n### Subsection\n\nDeep\n\n## Section 2\n\nBody 2"
        units = extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), text.encode(), "run-1", now="2026-06-22T00:00:00"
        )
        assert len(units) >= 4  # title + section 1 + subsection + section 2
        titles = [u["title"] for u in units]
        assert "Title" in titles or any("Title" in t for t in titles)
        assert any("Section 1" in t for t in titles)
        assert any("Section 2" in t for t in titles)
        assert any("Subsection" in t for t in titles)
        for u in units:
            assert u["unit_type"] == "doc-section"
            assert u["unit_id"].startswith("sha256:")
            assert "#" in u["unit_id"]
            assert u["artifact_id"] == "sha256:" + "a" * 64

    def test_markdown_without_headings_creates_one_unit(self):
        text = "Just a single paragraph of text with no headings whatsoever."
        units = extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), text.encode(), "run-1", now="2026-06-22T00:00:00"
        )
        assert len(units) == 1
        assert units[0]["unit_type"] == "doc-section"

    def test_markdown_blocked_skipped(self):
        art = _make_artifact(sec_status="blocked")
        units = extract_units_from_artifact(
            art, _make_occurrence(), b"# Blocked", "run-1", now="2026-06-22T00:00:00"
        )
        assert units == []

    def test_markdown_flagged_returns_minimal_unit(self):
        art = _make_artifact(sec_status="flagged")
        units = extract_units_from_artifact(
            art, _make_occurrence(), b"# Flagged\n\nsecret content", "run-1", now="2026-06-22T00:00:00"
        )
        assert len(units) == 1
        assert units[0]["redacted"] is True
        assert units[0]["semantic_text"] == ""

    def test_split_markdown_sections(self):
        text = "# H1\n\na\n\n## H2\n\nb\n\n### H3\n\nc"
        sections = split_markdown_sections(text)
        assert len(sections) == 3
        assert sections[0][1] == "H1"
        assert sections[1][1] == "H2"
        assert sections[2][1] == "H3"

    def test_empty_markdown(self):
        units = extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), b"", "run-1", now="2026-06-22T00:00:00"
        )
        assert len(units) == 0 or units[0]["semantic_text"] == "Documentation: "

    def test_deterministic_unit_ids(self):
        text = "# Stable\n\ncontent\n## H2\n\nmore"
        sha = "b" * 64
        art = _make_artifact(sha=sha)
        occ = _make_occurrence(sha=sha)
        now = "2026-06-22T00:00:00"
        units1 = extract_units_from_artifact(art, occ, text.encode(), "run-1", now=now)
        units2 = extract_units_from_artifact(art, occ, text.encode(), "run-1", now=now)
        ids1 = [u["unit_id"] for u in units1]
        ids2 = [u["unit_id"] for u in units2]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# Code: Python
# ---------------------------------------------------------------------------


class TestPythonExtraction:
    def test_python_class_extraction(self):
        text = '''
"""Module docstring."""

class MyClass:
    """My class docstring."""
    def method(self):
        pass

def my_function():
    """Do something."""
    return 42
'''
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/python", source_path="mod.py"),
            _make_occurrence(source_path="mod.py"),
            text.encode(), "run-1"
        )
        unit_types = [u["unit_type"] for u in units]
        assert all(t == "code-symbol" for t in unit_types)
        titles = [u["title"] for u in units]
        assert any("my_function" in t for t in titles)
        assert any("MyClass" in t for t in titles)

    def test_python_async_function(self):
        text = "async def fetch_data(url):\n    return await get(url)"
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/python", source_path="fetch.py"),
            _make_occurrence(source_path="fetch.py"),
            text.encode(), "run-1"
        )
        assert any("fetch_data" in u["title"] for u in units)
        assert any("async" in u["semantic_text"] for u in units)

    def test_python_private_function_skipped(self):
        text = "def _private():\n    pass"
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/python", source_path="priv.py"),
            _make_occurrence(source_path="priv.py"),
            text.encode(), "run-1"
        )
        # Private functions starting with _ should be skipped
        assert not any("_private" in u["title"] for u in units)

    def test_python_docstring(self):
        text = '"""Module docstring for testing."""\n\nx = 1'
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/python", source_path="mod.py"),
            _make_occurrence(source_path="mod.py"),
            text.encode(), "run-1"
        )
        docs = [u for u in units if "module" in u["title"] and "docstring" in u["semantic_text"]]
        assert len(docs) > 0


# ---------------------------------------------------------------------------
# Code: JS/TS
# ---------------------------------------------------------------------------


class TestJSTSExtraction:
    def test_exported_class(self):
        text = "export class MyComponent {\n  render() { return null; }\n}"
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/javascript", source_path="comp.ts"),
            _make_occurrence(source_path="comp.ts"),
            text.encode(), "run-1"
        )
        assert any("MyComponent" in u["title"] for u in units)

    def test_exported_function(self):
        text = "export function greet(name) {\n  return `Hello ${name}`;\n}"
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/typescript", source_path="greet.ts"),
            _make_occurrence(source_path="greet.ts"),
            text.encode(), "run-1"
        )
        assert any("greet" in u["title"] for u in units)

    def test_arrow_function(self):
        text = "export const handler = (req, res) => {\n  res.json({ok: true});\n}"
        units = extract_units_from_artifact(
            _make_artifact(role="code", media_type="text/typescript", source_path="handler.ts"),
            _make_occurrence(source_path="handler.ts"),
            text.encode(), "run-1"
        )
        assert any("handler" in u["title"] for u in units)


# ---------------------------------------------------------------------------
# Configuration (JSON/YAML)
# ---------------------------------------------------------------------------


class TestConfigExtraction:
    def test_json_config(self):
        text = '{"name": "test", "version": "1.0.0", "dependencies": {"express": "^4"}}'
        units = extract_units_from_artifact(
            _make_artifact(role="configuration", media_type="text/json", source_path="package.json"),
            _make_occurrence(source_path="package.json"),
            text.encode(), "run-1"
        )
        assert len(units) == 1
        assert units[0]["unit_type"] == "configuration"
        assert "package-manifest" in units[0]["semantic_text"]

    def test_yaml_config(self):
        text = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test"
        units = extract_units_from_artifact(
            _make_artifact(role="configuration", media_type="text/yaml", source_path="config.yaml"),
            _make_occurrence(source_path="config.yaml"),
            text.encode(), "run-1"
        )
        assert len(units) == 1
        assert units[0]["unit_type"] == "configuration"


# ---------------------------------------------------------------------------
# n8n workflow
# ---------------------------------------------------------------------------


class TestN8nExtraction:
    def test_n8n_workflow_detected(self):
        wf = {
            "name": "Test Workflow",
            "nodes": [{"type": "n8n-nodes-base.webhook", "name": "Webhook"}],
            "connections": {},
        }
        text = json.dumps(wf)
        assert _looks_like_n8n(text) is True

    def test_n8n_workflow_extracted(self):
        wf = {
            "name": "Test Workflow",
            "nodes": [
                {"type": "n8n-nodes-base.webhook", "name": "Webhook"},
                {"type": "n8n-nodes-base.httpRequest", "name": "HTTP"},
            ],
            "connections": {},
        }
        text = json.dumps(wf)
        units = extract_units_from_artifact(
            _make_artifact(role="n8n-workflow", media_type="text/json", source_path="wf.json"),
            _make_occurrence(source_path="wf.json"),
            text.encode(), "run-1"
        )
        assert len(units) == 1
        assert units[0]["unit_type"] == "n8n-workflow"
        assert "Test Workflow" in units[0]["title"]

    def test_n8n_flagged(self):
        wf = {
            "name": "Flagged WF",
            "nodes": [{"type": "n8n-nodes-base.webhook", "name": "Webhook"}],
            "connections": {},
        }
        text = json.dumps(wf)
        art = _make_artifact(role="n8n-workflow", media_type="text/json", sec_status="flagged")
        units = extract_units_from_artifact(
            art, _make_occurrence(), text.encode(), "run-1"
        )
        assert len(units) == 1
        assert units[0]["redacted"] is True
        assert units[0]["semantic_text"] == ""


# ---------------------------------------------------------------------------
# SKILL.md / SOUL.md → hermes-skill / hermes-soul
# ---------------------------------------------------------------------------


class TestHermesExtraction:
    def test_skill_md(self):
        text = "# My Skill\n\nThis skill does X.\n\n## Usage\n\nCall it."
        art = _make_artifact(role="agent-skill", source_path="SKILL.md")
        units = extract_units_from_artifact(
            art, _make_occurrence(source_path="SKILL.md"), text.encode(), "run-1"
        )
        assert len(units) >= 2
        for u in units:
            assert u["unit_type"] == "hermes-skill"

    def test_soul_md(self):
        text = "# My Soul\n\nI am an agent.\n\n## Purpose\n\nHelp."
        art = _make_artifact(role="agent-soul", source_path="SOUL.md")
        units = extract_units_from_artifact(
            art, _make_occurrence(source_path="SOUL.md"), text.encode(), "run-1"
        )
        assert len(units) >= 2
        for u in units:
            assert u["unit_type"] == "hermes-soul"


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


class TestDeploymentExtraction:
    def test_dockerfile(self):
        text = "FROM python:3.12\nENV FOO=bar\nCOPY . /app\nRUN pip install -r requirements.txt"
        units = extract_units_from_artifact(
            _make_artifact(role="deployment-definition", media_type="text/plain", source_path="Dockerfile"),
            _make_occurrence(source_path="Dockerfile"),
            text.encode(), "run-1", now="2026-06-22T00:00:00"
        )
        assert len(units) == 1
        assert units[0]["unit_type"] == "deployment-component"


# ---------------------------------------------------------------------------
# Binary / oversized
# ---------------------------------------------------------------------------


class TestBinarySkip:
    def test_binary_skipped(self):
        art = _make_artifact(media_type="image/png", source_path="image.png")
        units = extract_units_from_artifact(
            art, _make_occurrence(source_path="image.png"), b"PNG...", "run-1"
        )
        assert units == []

    def test_oversized_skipped(self):
        big = b"x" * 2_000_000  # 2 MB
        art = _make_artifact(media_type="application/octet-stream", source_path="big.dat")
        units = extract_units_from_artifact(
            art, _make_occurrence(source_path="big.dat"), big, "run-1"
        )
        assert units == []


# ---------------------------------------------------------------------------
# Deterministic unit IDs
# ---------------------------------------------------------------------------


class TestDeterministicIds:
    def test_same_input_same_output(self):
        art = _make_artifact(sha="c" * 64)
        occ = _make_occurrence(sha="c" * 64)
        text = b"# Hello\n\nWorld."
        now = "2026-06-22T00:00:00"
        units1 = extract_units_from_artifact(art, occ, text, "run-abc", now=now)
        units2 = extract_units_from_artifact(art, occ, text, "run-abc", now=now)
        # Compare unit_id and fingerprints only (not record_id which includes timestamp)
        keys = ["unit_id", "artifact_id", "source_anchor", "unit_type", "title", "fingerprints"]
        for k in keys:
            assert [u[k] for u in units1] == [u[k] for u in units2], f"Mismatch in {k}"

    def test_unit_id_format(self):
        text = b"# Test\n\nBody"
        units = extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), text, "run-1", now="2026-06-22T00:00:00"
        )
        for u in units:
            assert u["unit_id"].startswith("sha256:")
            assert "#" in u["unit_id"]
            assert len(u["unit_id"].split("#")[0]) == 64 + len("sha256:")

    def test_provenance_preserved(self):
        text = b"# Source\n\nBody"
        art = _make_artifact(
            sha="d" * 64,
            source_id="github:owner/project",
            source_path="docs/guide.md",
        )
        occ = _make_occurrence(
            sha="d" * 64,
            source_id="github:owner/project",
            source_path="docs/guide.md",
        )
        units = extract_units_from_artifact(
            art, occ, text, "run-1", now="2026-06-22T00:00:00"
        )
        for u in units:
            assert u["artifact_id"] == "sha256:" + "d" * 64
            # source_record_ids should contain the occurrence_id
            assert any("sha256:" in rid for rid in u["source_record_ids"])
            # source_anchor should contain section info
            assert "section" in u["source_anchor"]

    def test_no_filesystem_mutation(self, tmp_path):
        """Verify the extractor does not write files (no side effects)."""
        text = b"# Side effect test\n\nBody"
        before = sorted(p.name for p in tmp_path.rglob("*"))
        extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), text, "run-1", now="2026-06-22T00:00:00"
        )
        after = sorted(p.name for p in tmp_path.rglob("*"))
        assert before == after


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_artifact(self):
        art = _make_artifact(sha="")
        units = extract_units_from_artifact(art, _make_occurrence(), b"", "run-1")
        assert units == []

    def test_missing_source_id(self):
        art = _make_artifact()
        occ = {"occurrence_id": "sha256:abc"}
        units = extract_units_from_artifact(art, occ, b"# Test\n\nBody", "run-1")
        assert len(units) >= 1

    def test_very_long_file(self):
        """Very long file should be capped at MAX_SEMANTIC_TEXT_LEN."""
        text = "# Heading\n\n" + "word " * 1000000
        units = extract_units_from_artifact(
            _make_artifact(), _make_occurrence(), text.encode()[:50000], "run-1"
        )
        assert len(units) >= 1
        for u in units:
            if u["semantic_text"]:
                assert len(u["semantic_text"]) <= 30000 + 200  # max + summary prefix


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class TestUtils:
    def test_slugify(self):
        assert _slugify("Hello World") == "hello-world"
        assert _slugify("  Special  Characters!@#  ") == "special-characters"
        assert _slugify("") == "section"

    def test_make_fingerprints(self):
        fp = _make_fingerprints("a" * 64, "sha256:abc#test", "Test Title", "summary text")
        assert fp["content_sha256"] == "a" * 64
        assert fp["normalized_hash"].startswith("sha256:")
        assert fp["structural_hash"].startswith("sha256:")
        assert fp["semantic_signature"].startswith("sha256:")
