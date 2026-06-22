"""Tests for codex_vault_pipeline.utils.file_policy."""

import os
import tempfile
from pathlib import Path

from codex_vault_pipeline.utils import file_policy


class TestDetectMediaType:
    def test_markdown(self):
        assert file_policy.detect_media_type(Path("readme.md")) == "text/markdown"
        assert file_policy.detect_media_type(Path("docs/guide.mdx")) == "text/markdown"

    def test_python(self):
        assert file_policy.detect_media_type(Path("main.py")) == "text/python"
        assert file_policy.detect_media_type(Path("module.pyi")) == "text/python"

    def test_json(self):
        assert file_policy.detect_media_type(Path("data.json")) == "text/json"
        assert file_policy.detect_media_type(Path("config.jsonc")) == "text/json"

    def test_unknown_extension(self):
        assert file_policy.detect_media_type(Path("file.xyz")) == "application/octet-stream"
        assert file_policy.detect_media_type(Path("file")) == "application/octet-stream"


class TestIsBinary:
    def test_text_by_extension(self):
        assert not file_policy.is_binary(Path("readme.md"))
        assert not file_policy.is_binary(Path("main.py"))
        assert not file_policy.is_binary(Path("data.json"))
        assert not file_policy.is_binary(Path("file.txt"))
        assert not file_policy.is_binary(Path("config.yaml"))

    def test_binary_by_extension(self):
        assert file_policy.is_binary(Path("image.png"))
        assert file_policy.is_binary(Path("archive.zip"))
        assert file_policy.is_binary(Path("data.pdf"))
        assert file_policy.is_binary(Path("model.pth"))
        assert file_policy.is_binary(Path("library.so"))
        assert file_policy.is_binary(Path("db.sqlite3"))


class TestClassifyRole:
    def test_agent_skill(self):
        assert file_policy.classify_role(Path("SKILL.md")) == "agent-skill"
        assert file_policy.classify_role(Path("some/deep/path/SKILL.md")) == "agent-skill"

    def test_agent_soul(self):
        assert file_policy.classify_role(Path("SOUL.md")) == "agent-soul"

    def test_configuration(self):
        assert file_policy.classify_role(Path("pyproject.toml")) == "configuration"
        assert file_policy.classify_role(Path("package.json")) == "configuration"
        assert file_policy.classify_role(Path("Cargo.toml")) == "configuration"
        assert file_policy.classify_role(Path("go.mod")) == "configuration"
        assert file_policy.classify_role(Path("requirements.txt")) == "configuration"

    def test_deployment(self):
        assert file_policy.classify_role(Path("Dockerfile")) == "deployment-definition"
        assert file_policy.classify_role(Path("docker-compose.yml")) == "deployment-definition"
        assert file_policy.classify_role(Path("compose.yaml")) == "deployment-definition"

    def test_documentation_top_level(self):
        assert file_policy.classify_role(Path("readme.md")) == "documentation"
        assert file_policy.classify_role(Path("CHANGELOG.rst")) == "documentation"

    def test_docs_directory(self):
        assert file_policy.classify_role(Path("docs/getting-started.md")) == "documentation"
        assert file_policy.classify_role(Path("documentation/guide.md")) == "documentation"

    def test_reference_directory(self):
        assert file_policy.classify_role(Path("test/test_main.py")) == "reference"
        assert file_policy.classify_role(Path("tests/unit/test_foo.py")) == "reference"
        assert file_policy.classify_role(Path("__tests__/foo.test.js")) == "reference"

    def test_scripts_directory(self):
        assert file_policy.classify_role(Path("scripts/build.sh")) == "executable-script"
        assert file_policy.classify_role(Path("tools/format.py")) == "executable-script"
        assert file_policy.classify_role(Path("bin/run.sh")) == "executable-script"

    def test_unknown(self):
        assert file_policy.classify_role(Path("src/main.py")) == "unknown"
        assert file_policy.classify_role(Path("data.csv")) == "unknown"


class TestScanSecrets:
    def test_does_not_crash_on_clean_file(self):
        """scan_secrets must not raise on a temporary clean file.
        It may return 'not-scanned' when detect-secrets isn't installed."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# hello world\nprint('ok')\n")
            tmp = f.name
        try:
            status, count = file_policy.scan_secrets(Path(tmp))
            # Accept any valid status tuple — the important thing
            # is that the call does not crash.
            assert isinstance(status, str)
            assert isinstance(count, int)
            assert status in ("clean", "flagged", "blocked", "not-scanned")
            assert count >= 0
        finally:
            os.unlink(tmp)
