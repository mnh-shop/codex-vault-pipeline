"""Tests for the deterministic occurrence_id helper."""

import pytest
from codex_vault_pipeline.ingest.occurrence_identity import (
    occurrence_id,
    format_occurrence_id,
)

# ── deterministic behaviour ─────────────────────────────────────────────


class TestOccurrenceId:
    def test_basic_compute(self):
        """Simple case produces a 64-char hex string."""
        h = occurrence_id("github:owner/repo", "path/to/file.py")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        """Same input → same output."""
        h1 = occurrence_id("github:a/b", "src/main.py")
        h2 = occurrence_id("github:a/b", "src/main.py")
        assert h1 == h2

    def test_different_source(self):
        """Different source_id → different hash."""
        h1 = occurrence_id("github:a/b", "src/main.py")
        h2 = occurrence_id("github:c/d", "src/main.py")
        assert h1 != h2

    def test_different_path(self):
        """Different source_path → different hash."""
        h1 = occurrence_id("github:a/b", "src/main.py")
        h2 = occurrence_id("github:a/b", "src/utils.py")
        assert h1 != h2

    def test_null_byte_separation(self):
        """Uses null byte separator, so composite strings don't collide."""
        # "a/b\0cd" vs "a\0b/cd" should produce different hashes
        h1 = occurrence_id("a/b", "cd")
        h2 = occurrence_id("a", "b/cd")
        assert h1 != h2

    def test_empty_source_id_raises(self):
        """Empty source_id must be rejected."""
        with pytest.raises(ValueError, match="source_id"):
            occurrence_id("", "path/to/file.py")

    def test_empty_source_path_raises(self):
        """Empty source_path must be rejected."""
        with pytest.raises(ValueError, match="source_path"):
            occurrence_id("github:a/b", "")

    # ── format_occurrence_id ─────────────────────────────────────────

    def test_format_includes_prefix(self):
        """format_occurrence_id returns sha256:hex."""
        result = format_occurrence_id("github:a/b", "main.py")
        assert result.startswith("sha256:")
        hex_part = result[len("sha256:"):]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_format_deterministic(self):
        """format_occurrence_id is also deterministic."""
        r1 = format_occurrence_id("github:x/y", "a.md")
        r2 = format_occurrence_id("github:x/y", "a.md")
        assert r1 == r2

    def test_format_prefixed_hex_matches_raw(self):
        """sha256:hex matches the raw hash."""
        raw = occurrence_id("github:t/t", "test.py")
        fmt = format_occurrence_id("github:t/t", "test.py")
        assert fmt == f"sha256:{raw}"

    # ── edge cases ──────────────────────────────────────────────────────

    def test_deep_path(self):
        """Deep nested path still produces valid hash."""
        h = occurrence_id(
            "github:org/repo-name",
            "very/deep/nested/directory/structure/file.name.ext",
        )
        assert len(h) == 64

    def test_special_characters(self):
        """Special chars in source_id or path work safely."""
        h = occurrence_id("github:my-org/my_repo.2024", "file(name).ext~1")
        assert len(h) == 64

    def test_unicode_source_id(self):
        """Unicode characters in source_id are handled."""
        h = occurrence_id("github:用户/存储库", "文档.md")
        assert len(h) == 64

    def test_unicode_path(self):
        """Unicode characters in path are handled."""
        h = occurrence_id("github:a/b", "répertoire/fichier.md")
        assert len(h) == 64

    def test_no_trailing_newline_bias(self):
        """Prevents accidental trailing-newline collision."""
        h1 = occurrence_id("github:a/b", "file.py")
        h2 = occurrence_id("github:a/b", "file.py\n")
        assert h1 != h2  # would collide if we didn't encode exactly
