"""
Tests for functions of security.

Test escape of SQL, patterns LIKE and validation of paths.
"""

from vault_search.utils.security import (
    escape_like_pattern,
    escape_sql_string,
    validate_relative_path,
)


class TestEscapeSqlString:
    """Tests for escape_sql_string()."""

    def test_escapes_single_quotes(self):
        """Single quotes must be escaped by doubling them."""
        assert escape_sql_string("The'Brien") == "The''Brien"

    def test_multiple_quotes(self):
        """Must escape multiple quotes."""
        assert escape_sql_string("a'b'c") == "a''b''c"

    def test_string_normal_not_changes(self):
        """A string without quotes remains unchanged."""
        assert escape_sql_string("normal") == "normal"

    def test_string_empty(self):
        """An empty string remains empty."""
        assert escape_sql_string("") == ""

    def test_none_returns_none(self):
        """None returns None (falsy)."""
        assert escape_sql_string(None) is None

    def test_quotes_at_start(self):
        """Quotes in the start of the string."""
        assert escape_sql_string("'start") == "''start"

    def test_quotes_at_end(self):
        """Quotes in the end of the string."""
        assert escape_sql_string("end'") == "end''"

    def test_only_quotes(self):
        """A string containing only quotes is handled safely."""
        assert escape_sql_string("'''") == "''''''"

    def test_sql_injection_attempt(self):
        """Neutralize an SQL injection attempt."""
        # Attempt: '; DROP TABLE users; --
        malicious = "'; DROP TABLE users; --"
        escaped = escape_sql_string(malicious)
        assert escaped == "''; DROP TABLE users; --"
        # A quote escaped prevents the injection


class TestEscapeLikePattern:
    """Tests for escape_like_pattern()."""

    def test_escapes_percent(self):
        """Must escape % (wildcard)."""
        assert escape_like_pattern("test%") == "test\\%"

    def test_escapes_underscore(self):
        """Must escape _ (single char wildcard)."""
        assert escape_like_pattern("test_") == "test\\_"

    def test_escapes_backslash(self):
        """Must escape backslash."""
        assert escape_like_pattern("test\\path") == "test\\\\path"

    def test_escapes_quotes(self):
        """Must escape quotes simple."""
        assert escape_like_pattern("test's") == "test''s"

    def test_multiple_wildcards(self):
        """Must escape multiple wildcards."""
        result = escape_like_pattern("%test_name%")
        assert "\\%" in result
        assert "\\_" in result

    def test_string_normal(self):
        """A string without special characters remains unchanged."""
        assert escape_like_pattern("normal") == "normal"

    def test_string_empty(self):
        """An empty string remains empty."""
        assert escape_like_pattern("") == ""


class TestValidateRelativePath:
    """Tests for validate_relative_path()."""

    def test_simple_path_is_valid(self):
        """Path simple is valid."""
        assert validate_relative_path("file.md") is True

    def test_path_with_valid_folder(self):
        """A path containing a folder is valid."""
        assert validate_relative_path("docs/file.md") is True

    def test_deep_path_is_valid(self):
        """A path containing multiple folders is valid."""
        assert validate_relative_path("a/b/c/d/file.md") is True

    def test_path_traversal_rejected(self):
        """Path traversal with .. must be rejected."""
        assert validate_relative_path("../etc/passwd") is False

    def test_path_traversal_middle_rejected(self):
        """Path traversal in the middle must be rejected."""
        assert validate_relative_path("docs/../../../etc/passwd") is False

    def test_path_absolute_rejected(self):
        """Path absolute must be rejected."""
        assert validate_relative_path("/etc/passwd") is False

    def test_path_absolute_windows_rejected(self):
        """Path absolute Windows must be rejected."""
        assert validate_relative_path("\\Windows\\System32") is False

    def test_empty_string_rejected(self):
        """An empty string is rejected."""
        assert validate_relative_path("") is False

    def test_none_rejected(self):
        """None is rejected."""
        assert validate_relative_path(None) is False

    def test_null_byte_rejected(self):
        """Null byte injection must be rejected."""
        assert validate_relative_path("file.md\x00.txt") is False

    def test_hidden_dot_dot(self):
        """A hidden .. segment in a path must be rejected."""
        assert validate_relative_path("docs/..") is False

    def test_path_with_spaces_valid(self):
        """A path containing spaces is valid."""
        assert validate_relative_path("my docs/my file.md") is True

    def test_path_with_characters_unicode_valid(self):
        """A path containing Unicode characters is valid."""
        assert validate_relative_path("notes/café.md") is True
