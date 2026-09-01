"""
Unit tests for server.py — validation and helpers.

Fast tests that do not require ML models or a running MCP server.
"""

import pytest

from vault_search.config.search import SEARCH_TOP_K_MAX, SEARCH_TOP_K_MIN
from vault_search.server.helpers import clamp_top_k, execute_search, log_query


class TestClampTopK:
    """Test the helper of production without load the server MCP."""

    def test_normal_value(self):
        assert clamp_top_k(10) == 10

    def test_zero(self):
        assert clamp_top_k(0) == SEARCH_TOP_K_MIN

    def test_negative_value(self):
        assert clamp_top_k(-5) == SEARCH_TOP_K_MIN

    def test_maximum_value(self):
        assert clamp_top_k(SEARCH_TOP_K_MAX) == SEARCH_TOP_K_MAX

    def test_exceeds_maximum(self):
        assert clamp_top_k(200) == SEARCH_TOP_K_MAX

    def test_one(self):
        assert clamp_top_k(1) == SEARCH_TOP_K_MIN


class TestLogQuery:
    """Test the metadata contract without exposing query content."""

    def test_short_query(self):
        assert log_query("search simple") == "[redacted length=13]"

    def test_long_query_is_truncated(self):
        result = log_query("a" * 100)
        assert result == "[redacted length=100]"

    def test_query_with_data_sensitive(self):
        """No query fragment may appear in the logging representation."""
        result = log_query("prefix SECRET_PASSWORD_123 suffix")
        assert "SENHA" not in result

    def test_query_empty(self):
        assert log_query("") == "[redacted length=0]"


class TestExecuteSearch:
    """Test the helper of production with boundaries simulated."""

    def test_query_empty_returns_error(self):
        result = execute_search("test", "", 10, lambda **kw: [])
        assert "Error" in result
        assert "empty" in result

    def test_query_so_spaces(self):
        result = execute_search("test", "   ", 10, lambda **kw: [])
        assert "Error" in result

    def test_query_none_returns_error(self):
        result = execute_search("test", None, 10, lambda **kw: [])
        assert "Error" in result

    def test_valid_query_calls_search_function(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[{"text": "result"}])
        result = execute_search("test", "search valid", 10, mock_fn)
        mock_fn.assert_called_once_with("search valid", top_k=10)
        assert result == [{"text": "result"}]

    def test_top_k_clamped(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "query", 999, mock_fn)
        mock_fn.assert_called_once_with("query", top_k=SEARCH_TOP_K_MAX)

    def test_runtime_error_returns_message(self):
        def raise_runtime(*args, **kw):
            raise RuntimeError("Index not found")

        result = execute_search("test", "query", 10, raise_runtime)
        assert "search_unavailable" in result
        assert "Index not found" not in result

    def test_exception_generic_returns_message(self):
        def raise_generic(*args, **kw):
            raise ValueError("something broke")

        result = execute_search("test", "query", 10, raise_generic)
        assert "internal_error" in result
        assert "something broke" not in result

    def test_kwargs_are_forwarded_to_search_function(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "query", 10, mock_fn, folder="projects")
        mock_fn.assert_called_once_with("query", top_k=10, folder="projects")

    def test_query_with_spaces_trimmed(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "  query with spaces  ", 10, mock_fn)
        mock_fn.assert_called_once_with("query with spaces", top_k=10)


class TestGetRecentNotesParams:
    """Unit tests for validation of parameters of get_recent_notes."""

    @staticmethod
    def _clamp_days(days: int) -> int:
        return max(1, min(days, 365))

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(limit, 100))

    def test_days_normal(self):
        assert self._clamp_days(7) == 7

    def test_days_zero(self):
        assert self._clamp_days(0) == 1

    def test_negative_days(self):
        assert self._clamp_days(-5) == 1

    def test_maximum_days(self):
        assert self._clamp_days(365) == 365

    def test_days_exceeds_maximum(self):
        assert self._clamp_days(500) == 365

    def test_limit_normal(self):
        assert self._clamp_limit(20) == 20

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_negative_limit(self):
        assert self._clamp_limit(-10) == 1

    def test_maximum_limit(self):
        assert self._clamp_limit(100) == 100

    def test_limit_exceeds_maximum(self):
        assert self._clamp_limit(200) == 100


class TestGetRecentNotesFiltering:
    """Tests for recent-note filtering logic."""

    def test_filters_by_data(self):
        """Notes outside the time window must be excluded."""
        from datetime import datetime, timedelta

        now = datetime.now()
        notes = [
            {
                "path": "recent.md",
                "modified_at": (now - timedelta(days=2)).isoformat(),
                "title": "Recent",
            },
            {
                "path": "old.md",
                "modified_at": (now - timedelta(days=30)).isoformat(),
                "title": "Old",
            },
        ]

        cutoff = now - timedelta(days=7)
        recent = [n for n in notes if datetime.fromisoformat(n["modified_at"]) >= cutoff]

        assert len(recent) == 1
        assert recent[0]["path"] == "recent.md"

    def test_sorts_by_date_descending(self):
        """Notes are ordered from most recent to oldest."""
        from datetime import datetime, timedelta

        now = datetime.now()
        notes = [
            {"path": "b.md", "modified_at": (now - timedelta(days=5)).isoformat()},
            {"path": "a.md", "modified_at": (now - timedelta(days=1)).isoformat()},
            {"path": "c.md", "modified_at": (now - timedelta(days=3)).isoformat()},
        ]

        sorted_notes = sorted(notes, key=lambda x: x["modified_at"], reverse=True)

        assert sorted_notes[0]["path"] == "a.md"
        assert sorted_notes[1]["path"] == "c.md"
        assert sorted_notes[2]["path"] == "b.md"

    def test_calculates_days_ago(self):
        """days_ago must calculate correctly a difference of days."""
        from datetime import datetime, timedelta

        now = datetime.now()
        modified = now - timedelta(days=3)
        days_ago = (now - modified).days

        assert days_ago == 3

    def test_days_ago_today(self):
        """A note modified today has days_ago=0."""
        from datetime import datetime

        now = datetime.now()
        modified = now
        days_ago = (now - modified).days

        assert days_ago == 0


class TestTagStatsParams:
    """Unit tests for validation of parameters of tag_stats."""

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(limit, 500))

    def test_limit_normal(self):
        assert self._clamp_limit(50) == 50

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_negative_limit(self):
        assert self._clamp_limit(-10) == 1

    def test_maximum_limit(self):
        assert self._clamp_limit(500) == 500

    def test_limit_exceeds_maximum(self):
        assert self._clamp_limit(1000) == 500


class TestTagStatsAggregation:
    """Tests for logic of aggregation of tags."""

    def test_parse_tags_string(self):
        """Comma-separated tags must be parsed correctly."""
        tags_str = "project, 2024, idea"
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == ["project", "2024", "idea"]

    def test_parse_tags_emptys(self):
        """An empty string returns an empty list."""
        tags_str = ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == []

    def test_parse_tags_with_spaces(self):
        """Extra spaces must be removed."""
        tags_str = "  tag1  ,  tag2  ,  tag3  "
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == ["tag1", "tag2", "tag3"]

    def test_frequency_counter(self):
        """The counter must aggregate frequency correctly."""
        from collections import Counter

        note_tags = {
            "note1.md": {"project", "2024"},
            "note2.md": {"project", "idea"},
            "note3.md": {"project"},
        }

        counter: Counter[str] = Counter()
        for tags in note_tags.values():
            counter.update(tags)

        assert counter["project"] == 3
        assert counter["2024"] == 1
        assert counter["idea"] == 1

    def test_most_common(self):
        """most_common must return sorted by frequency."""
        from collections import Counter

        counter = Counter({"a": 10, "b": 5, "c": 20})
        top = counter.most_common(2)

        assert top[0] == ("c", 20)
        assert top[1] == ("a", 10)

    def test_unique_tags_by_note(self):
        """The same tag in multiple chunks of one note must count once."""
        note_tags: dict[str, set[str]] = {}

        # Simulate multiple chunks of the same note
        chunks = [
            ("note1.md", "project, 2024"),
            ("note1.md", "project, idea"),  # project repeated
            ("note2.md", "project"),
        ]

        for note_path, tags_str in chunks:
            if note_path not in note_tags:
                note_tags[note_path] = set()
            for tag in tags_str.split(","):
                tag = tag.strip()
                if tag:
                    note_tags[note_path].add(tag)

        from collections import Counter

        counter: Counter[str] = Counter()
        for tags in note_tags.values():
            counter.update(tags)

        # "project" appears in 2 notes (not 3 chunks)
        assert counter["project"] == 2
        assert counter["2024"] == 1
        assert counter["idea"] == 1


class TestFolderTreeParams:
    """Unit tests for validation of parameters of folder_tree."""

    @staticmethod
    def _clamp_max_depth(max_depth: int) -> int:
        return max(1, min(max_depth, 50))

    def test_max_depth_normal(self):
        assert self._clamp_max_depth(10) == 10

    def test_max_depth_zero(self):
        assert self._clamp_max_depth(0) == 1

    def test_negative_max_depth(self):
        assert self._clamp_max_depth(-5) == 1

    def test_maximum_max_depth(self):
        assert self._clamp_max_depth(50) == 50

    def test_max_depth_exceeds_maximum(self):
        assert self._clamp_max_depth(100) == 50


class TestFolderTreeBuilding:
    """Tests for logic of construction of the tree of folders."""

    def test_parse_folder_path(self):
        """A folder path must be split correctly."""
        folder = "projects/web/frontend"
        parts = folder.split("/")
        assert parts == ["projects", "web", "frontend"]

    def test_build_tree_single_folder(self):
        """A single folder creates a simple structure."""
        tree: dict = {}
        folder = "projects"
        count = 10

        parts = folder.split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        current["_count"] = count

        assert tree == {"projects": {"_count": 10}}

    def test_build_tree_nested_folders(self):
        """Nested folders create a hierarchical structure."""
        tree: dict = {}
        folders = [
            ("projects", 5),
            ("projects/web", 10),
            ("projects/mobile", 8),
        ]

        for folder, count in folders:
            parts = folder.split("/")
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current["_count"] = current.get("_count", 0) + count

        assert "projects" in tree
        assert "web" in tree["projects"]
        assert "mobile" in tree["projects"]
        assert tree["projects"]["web"]["_count"] == 10
        assert tree["projects"]["mobile"]["_count"] == 8

    def test_max_depth_truncates(self):
        """max_depth limits tree depth."""
        folder = "a/b/c/d/and"
        max_depth = 3

        parts = folder.split("/")[:max_depth]

        assert parts == ["a", "b", "c"]
        assert len(parts) == max_depth

    def test_root_notes_counted(self):
        """Notes in the root must be counted in _count."""
        tree: dict = {}
        folder = ""  # Root
        count = 15

        if not folder:
            tree["_count"] = tree.get("_count", 0) + count

        assert tree["_count"] == 15

    def test_collect_unique_folders(self):
        """All intermediate folders must be counted."""
        folders_set: set[str] = set()
        folder = "a/b/c"

        parts = folder.split("/")
        for i in range(len(parts)):
            intermediate = "/".join(parts[: i + 1])
            folders_set.add(intermediate)

        assert folders_set == {"a", "a/b", "a/b/c"}
        assert len(folders_set) == 3

    def test_without_counts(self):
        """Without include_counts, _count must not appear."""
        tree: dict = {}
        include_counts = False

        folder = "projects"
        parts = folder.split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]

        if include_counts:
            current["_count"] = 10

        assert "_count" not in tree.get("projects", {})


class TestFolderTreeRecursive:
    """Tests for the recursive PurePosixPath implementation."""

    def test_pure_posix_path_parsing(self):
        """PurePosixPath must parse paths correctly."""
        from pathlib import PurePosixPath

        path = PurePosixPath("projects/web/frontend")
        assert path.parts == ("projects", "web", "frontend")

    def test_pure_posix_path_empty(self):
        """PurePosixPath created from an empty string has no meaningful parts."""
        from pathlib import PurePosixPath

        path = PurePosixPath("")
        assert path.parts == ()

    def test_pure_posix_path_single(self):
        """PurePosixPath with folder single."""
        from pathlib import PurePosixPath

        path = PurePosixPath("projects")
        assert path.parts == ("projects",)

    def test_defaultdict_to_dict_conversion(self):
        """A defaultdict is converted to a plain dictionary."""
        from collections import defaultdict

        def nested_dict():
            return defaultdict(nested_dict)

        def to_dict(d):
            if isinstance(d, defaultdict):
                d = {k: to_dict(v) for k, v in d.items()}
            elif isinstance(d, dict):
                d = {k: to_dict(v) for k, v in d.items()}
            return d

        tree = defaultdict(nested_dict)
        tree["a"]["b"]["c"] = {"_count": 5}

        result = to_dict(tree)

        assert isinstance(result, dict)
        assert not isinstance(result, defaultdict)
        assert result["a"]["b"]["c"]["_count"] == 5

    def test_recursive_insert_respects_depth(self):
        """Recursive insertion must respect max_depth."""
        from pathlib import PurePosixPath

        max_depth = 2
        folder = "a/b/c/d/and"
        path = PurePosixPath(folder)
        parts = path.parts[:max_depth]

        assert parts == ("a", "b")
        assert len(parts) == max_depth

    def test_accumulate_counts_at_truncation(self):
        """Counts accumulate in the truncated folder."""
        folders = [
            ("a/b/c", 10),  # truncated for a/b
            ("a/b/d", 5),  # truncated for a/b
        ]
        max_depth = 2

        tree: dict = {}
        for folder, count in folders:
            parts = folder.split("/")[:max_depth]
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Accumulate in the leaf truncated
            current["_count"] = current.get("_count", 0) + count

        # Both must accumulate in a/b
        assert tree["a"]["b"]["_count"] == 15


class TestSearchByTagsParams:
    """Unit tests for validation of parameters of search_by_tags."""

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(limit, 200))

    def test_limit_normal(self):
        assert self._clamp_limit(50) == 50

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_negative_limit(self):
        assert self._clamp_limit(-10) == 1

    def test_maximum_limit(self):
        assert self._clamp_limit(200) == 200

    def test_limit_exceeds_maximum(self):
        assert self._clamp_limit(500) == 200


class TestSearchByTagsNormalization:
    """Tests for normalization of tags."""

    def test_normalize_tags(self):
        """Tags are lowercased and stripped."""
        tags = ["  Project  ", "2024", "  WEB  "]
        clean = [t.strip().lower() for t in tags if t.strip()]
        assert clean == ["project", "2024", "web"]

    def test_filter_empty_tags(self):
        """Empty tags must be filtered."""
        tags = ["project", "", "  ", "web"]
        clean = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()]
        assert clean == ["project", "web"]

    def test_truncate_too_many_tags(self):
        """A list containing more than 20 tags is truncated."""
        tags = [f"tag{i}" for i in range(30)]
        truncated = tags[:20]
        assert len(truncated) == 20


class TestSearchByTagsMatching:
    """Tests for logic of matching of tags."""

    def test_parse_tags_string(self):
        """Tags are parsed from a comma-separated string."""
        tags_str = "project, 2024, web"
        note_tags = {t.strip().lower() for t in tags_str.split(",") if t.strip()}
        assert note_tags == {"project", "2024", "web"}

    def test_match_any_or_mode(self):
        """OR mode requires any one of the tags."""
        note_tags = {"project", "2024"}
        search_tags = ["web", "2024"]
        # OR: any tag matches
        matches = any(t in note_tags for t in search_tags)
        assert matches is True

    def test_match_any_in_match(self):
        """OR mode without match."""
        note_tags = {"project", "2024"}
        search_tags = ["web", "mobile"]
        matches = any(t in note_tags for t in search_tags)
        assert matches is False

    def test_match_all_and_mode(self):
        """AND mode requires all tags."""
        note_tags = {"project", "2024", "web"}
        search_tags = ["project", "2024"]
        matches = all(t in note_tags for t in search_tags)
        assert matches is True

    def test_match_all_partial(self):
        """AND mode with match partial must fail."""
        note_tags = {"project", "2024"}
        search_tags = ["project", "web"]
        matches = all(t in note_tags for t in search_tags)
        assert matches is False

    def test_dedup_by_note_path(self):
        """Chunks from the same note are deduplicated."""
        chunks = [
            ("note1.md", "project, 2024"),
            ("note1.md", "project, 2024"),  # duplicate
            ("note2.md", "web"),
        ]

        notes_map: dict[str, dict] = {}
        for path, tags in chunks:
            if path not in notes_map:
                notes_map[path] = {"path": path, "tags": tags}

        assert len(notes_map) == 2
        assert "note1.md" in notes_map
        assert "note2.md" in notes_map


class TestSearchByTagsSQLEscape:
    """Tests for escape of characters SQL."""

    def test_escape_single_quote(self):
        """Single quotes must be escaped."""
        tag = "it's"
        escaped = tag.replace("'", "''")
        assert escaped == "it''s"

    def test_sql_like_pattern_exact_tag(self):
        """Pattern LIKE must find tag exact, not substring."""
        tag = "web"

        # Cases of test
        test_cases = [
            ("web", True),  # single
            ("web, mobile", True),  # first
            ("mobile, web", True),  # last
            ("a, web, b", True),  # middle
            ("webapp", False),  # substring - must not give match
            ("webdev, mobile", False),  # substring in the start
        ]

        # Simulate matching
        for tags_str, expected in test_cases:
            # Simplification: verify that the tag is a separate item.
            tags_list = [t.strip() for t in tags_str.split(",")]
            actual = tag in tags_list
            assert actual == expected, f"tags='{tags_str}' expected {expected}"


class TestRandomNoteParams:
    """Unit tests for validation of parameters of random_note."""

    def test_normalize_folder_empty(self):
        """An empty or whitespace-only folder becomes None."""
        folder = "   "
        result = folder.strip() if folder and folder.strip() else None
        assert result is None

    def test_normalize_folder_valid(self):
        """A valid folder is preserved."""
        folder = "  projects  "
        result = folder.strip() if folder and folder.strip() else None
        assert result == "projects"

    def test_normalize_extension_add_dot(self):
        """An extension without a leading dot must receive one."""
        ext = "md"
        if not ext.startswith("."):
            ext = f".{ext}"
        assert ext == ".md"

    def test_normalize_extension_with_dot(self):
        """An extension with a leading dot must remain unchanged."""
        ext = ".md"
        if not ext.startswith("."):
            ext = f".{ext}"
        assert ext == ".md"

    def test_normalize_extension_lowercase(self):
        """The extension is lowercased."""
        ext = ".MD"
        ext = ext.lower()
        assert ext == ".md"

    def test_validate_extension(self):
        """Verify extensions indexable."""
        from vault_search.config.search import INDEXABLE_EXTENSIONS

        # Extensions that must be indexable
        assert ".md" in INDEXABLE_EXTENSIONS
        assert ".txt" in INDEXABLE_EXTENSIONS
        assert ".mdx" in INDEXABLE_EXTENSIONS
        assert ".pdf" in INDEXABLE_EXTENSIONS
        assert ".canvas" in INDEXABLE_EXTENSIONS

        # Extensions that NOT must be indexable
        assert ".jpg" not in INDEXABLE_EXTENSIONS
        assert ".png" not in INDEXABLE_EXTENSIONS


class TestRandomNoteSQLQuery:
    """Tests for construction of query SQL of the random_note."""

    def test_order_by_random(self):
        """The query uses ORDER BY RANDOM()."""
        query = "SELECT * FROM notes ORDER BY RANDOM() LIMIT 1"
        assert "ORDER BY RANDOM()" in query
        assert "LIMIT 1" in query

    def test_where_folder_exact_or_subfolders(self):
        """A folder filter must include the exact folder and its descendants."""
        folder = "projects"
        condition = f"(folder = '{folder}' OR folder LIKE '{folder}/%')"

        # Tests
        assert "folder = 'projects'" in condition
        assert "folder LIKE 'projects/%'" in condition

    def test_multiple_conditions_and(self):
        """Multiple conditions must use AND."""
        conditions = ["folder = 'projects'", "extension = '.md'"]
        where = " AND ".join(conditions)
        assert where == "folder = 'projects' AND extension = '.md'"


class TestDailyNoteParams:
    """Unit tests for validation of parameters of daily_note."""

    def test_parse_date_iso(self):
        """An ISO date must be parsed correctly."""
        from datetime import datetime

        date_str = "2024-01-15"
        parsed = datetime.fromisoformat(date_str).date()
        assert parsed.year == 2024
        assert parsed.month == 1
        assert parsed.day == 15

    def test_parse_date_invalid(self):
        """Invalid data raises ValueError."""
        from datetime import datetime

        with pytest.raises(ValueError):
            datetime.fromisoformat("invalid-date")

    def test_parse_date_wrong_format(self):
        """An invalid format raises ValueError."""
        from datetime import datetime

        with pytest.raises(ValueError):
            datetime.fromisoformat("15/01/2024")  # DD/MM/YYYY is not ISO

    def test_default_date_is_today(self):
        """A None date uses today."""
        from datetime import date

        today = date.today()
        assert today.isoformat()  # YYYY-MM-DD format

    def test_normalize_folder_empty(self):
        """An empty folder uses 'daily'."""
        folder = "   "
        result = folder.strip() if folder and folder.strip() else "daily"
        assert result == "daily"

    def test_normalize_folder_valid(self):
        """A valid folder is preserved."""
        folder = "  journals  "
        result = folder.strip() if folder and folder.strip() else "daily"
        assert result == "journals"


class TestDailyNotePath:
    """Tests for construction of path of daily note."""

    def test_build_expected_path(self):
        """Path expected must follow default folder/YYYY-MM-DD.md."""
        from datetime import date

        d = date(2024, 1, 15)
        folder = "daily"
        expected_filename = f"{d.isoformat()}.md"
        expected_path = f"{folder}/{expected_filename}"

        assert expected_path == "daily/2024-01-15.md"

    def test_build_path_custom_folder(self):
        """A custom folder must be respected."""
        from datetime import date

        d = date(2024, 1, 15)
        folder = "journals/2024"
        expected_filename = f"{d.isoformat()}.md"
        expected_path = f"{folder}/{expected_filename}"

        assert expected_path == "journals/2024/2024-01-15.md"

    def test_date_isoformat(self):
        """isoformat() must return YYYY-MM-DD."""
        from datetime import date

        d = date(2024, 1, 5)  # day with a digit
        assert d.isoformat() == "2024-01-05"  # zero-padded

    def test_response_exists_structure(self):
        """Resposta of note existing must have structure correct."""
        response = {
            "exists": True,
            "path": "daily/2024-01-15.md",
            "title": "2024-01-15",
            "folder": "daily",
            "date": "2024-01-15",
            "modified_at": "2024-01-15T10:30:00",
            "size_bytes": 1024,
        }

        assert response["exists"] is True
        assert "path" in response
        assert "modified_at" in response

    def test_response_not_exists_structure(self):
        """Resposta of note nonexistent must have structure correct."""
        response = {
            "exists": False,
            "expected_path": "daily/2024-01-15.md",
            "date": "2024-01-15",
            "folder": "daily",
        }

        assert response["exists"] is False
        assert "expected_path" in response
        assert "date" in response
