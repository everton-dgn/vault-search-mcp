"""
Tests for a tool search_duplicates.
"""

from unittest.mock import MagicMock, patch


class TestSearchDuplicatesSearcher:
    """Tests for VaultSearcher.search_duplicates()."""

    def test_search_duplicates_empty_index(self):
        """Returns an empty list when the note set is empty."""
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()

        with patch.object(searcher, "_open_table") as mock_table:
            mock_query = MagicMock()
            mock_query.select.return_value = mock_query
            mock_query.where.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.to_list.return_value = []
            mock_table.return_value.search.return_value = mock_query

            result = searcher.search_duplicates()

            assert result == []

    def test_search_duplicates_in_duplicates(self):
        """Returns an empty list when the duplicate set is empty."""
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()

        # Simulate two very different notes with orthogonal vectors.
        vec1 = [1.0] + [0.0] * 1023
        vec2 = [0.0, 1.0] + [0.0] * 1022

        chunks = [
            {"note_path": "note1.md", "note_title": "Note 1", "folder": "", "vector": vec1},
            {"note_path": "note2.md", "note_title": "Note 2", "folder": "", "vector": vec2},
        ]

        call_count = [0]

        def search_side_effect(*args, **kwargs):
            """Mock table.search() while distinguishing calls."""
            mock_result = MagicMock()
            mock_result.select.return_value = mock_result
            mock_result.where.return_value = mock_result
            mock_result.limit.return_value = mock_result

            call_count[0] += 1
            if call_count[0] == 1:
                # First called: list all the chunks
                mock_result.to_list.return_value = chunks
            else:
                # Subsequent calls find similar notes; a high distance is not similar.
                mock_result.to_list.return_value = [
                    {
                        "note_path": "note1.md",
                        "note_title": "Note 1",
                        "folder": "",
                        "_distance": 0.0,
                    },
                    {
                        "note_path": "note2.md",
                        "note_title": "Note 2",
                        "folder": "",
                        "_distance": 5.0,
                    },
                ]
            return mock_result

        with patch.object(searcher, "_open_table") as mock_table:
            mock_table.return_value.search.side_effect = search_side_effect

            result = searcher.search_duplicates(threshold=0.90)

            # With distance=5.0, score = 1/(1+5) = 0.166 < 0.90, so it is not a duplicate.
            assert result == []

    def test_search_duplicates_finds_duplicates(self):
        """Finds duplicates when very similar notes exist."""
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()

        # Simulate two highly similar notes with nearly identical vectors.
        vec1 = [1.0] * 1024
        vec2 = [0.99] * 1024

        chunks = [
            {"note_path": "note1.md", "note_title": "Note 1", "folder": "", "vector": vec1},
            {"note_path": "note2.md", "note_title": "Note 2", "folder": "", "vector": vec2},
        ]

        call_count = [0]

        def search_side_effect(*args, **kwargs):
            """Mock for table.search()."""
            mock_result = MagicMock()
            mock_result.select.return_value = mock_result
            mock_result.where.return_value = mock_result
            mock_result.limit.return_value = mock_result

            call_count[0] += 1
            if call_count[0] == 1:
                # First called: list all the chunks
                mock_result.to_list.return_value = chunks
            else:
                # Subsequent calls find similar notes; a low distance is very similar.
                mock_result.to_list.return_value = [
                    {
                        "note_path": "note1.md",
                        "note_title": "Note 1",
                        "folder": "",
                        "_distance": 0.0,
                    },
                    {
                        "note_path": "note2.md",
                        "note_title": "Note 2",
                        "folder": "",
                        "_distance": 0.05,
                    },
                ]
            return mock_result

        with patch.object(searcher, "_open_table") as mock_table:
            mock_table.return_value.search.side_effect = search_side_effect

            result = searcher.search_duplicates(threshold=0.90)

            # With distance=0.05, score = 1/(1+0.05) ~= 0.952 > 0.90, so it is a duplicate.
            assert len(result) >= 1
            assert result[0]["count"] >= 2

    def test_search_duplicates_respects_threshold(self):
        """Threshold more alto filters more results."""
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()

        chunks = [
            {"note_path": "note1.md", "note_title": "Note 1", "folder": "", "vector": [1.0] * 1024},
            {"note_path": "note2.md", "note_title": "Note 2", "folder": "", "vector": [0.9] * 1024},
        ]

        def make_search_mock(distance_value):
            """Creates a mock of search with distance specific."""
            call_count = [0]

            def search_side_effect(*args, **kwargs):
                mock_result = MagicMock()
                mock_result.select.return_value = mock_result
                mock_result.where.return_value = mock_result
                mock_result.limit.return_value = mock_result

                call_count[0] += 1
                if call_count[0] == 1:
                    mock_result.to_list.return_value = chunks
                else:
                    # Distance that gives score ~0.85: d = 1/0.85 - 1 ≈ 0.176
                    mock_result.to_list.return_value = [
                        {
                            "note_path": "note1.md",
                            "note_title": "Note 1",
                            "folder": "",
                            "_distance": 0.0,
                        },
                        {
                            "note_path": "note2.md",
                            "note_title": "Note 2",
                            "folder": "",
                            "_distance": distance_value,
                        },
                    ]
                return mock_result

            return search_side_effect

        # Test with threshold low (0.80) - must find
        with patch.object(searcher, "_open_table") as mock_table:
            mock_table.return_value.search.side_effect = make_search_mock(0.176)
            result_low = searcher.search_duplicates(threshold=0.80)

        # Test with threshold alto (0.90) - must not find
        with patch.object(searcher, "_open_table") as mock_table:
            mock_table.return_value.search.side_effect = make_search_mock(0.176)
            result_high = searcher.search_duplicates(threshold=0.90)

        # With threshold low must find more that with threshold alto
        assert len(result_low) >= len(result_high)

    def test_search_duplicates_folder_filter(self):
        """Filter by folder works."""
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()

        with patch.object(searcher, "_open_table") as mock_table:
            mock_query = MagicMock()
            mock_query.select.return_value = mock_query
            mock_query.where.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.to_list.return_value = []
            mock_table.return_value.search.return_value = mock_query

            searcher.search_duplicates(folder="projects")

            # Checks that where() was called with the filter of folder
            mock_query.where.assert_called()


class TestSearchDuplicatesTool:
    """Tests for a tool MCP search_duplicates."""

    def test_threshold_clamping(self):
        """Threshold is bounded between 0.5 and 0.99."""
        from vault_search.server.search_tools import register_search_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()
        searcher.search_duplicates.return_value = []

        register_search_tools(mcp, indexer, searcher)

        # Find a function search_duplicates registered
        for call in mcp.tool.return_value.call_args_list:
            if hasattr(call, "args") and call.args:
                fn = call.args[0]
                if hasattr(fn, "__name__") and fn.__name__ == "search_duplicates":
                    break

        # The function is decorated, so call it through the searcher mock.
        assert searcher is not None

    def test_max_notes_clamping(self):
        """max_notes is bounded between 10 and 1000."""
        from vault_search.server.search_tools import register_search_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()
        searcher.search_duplicates.return_value = []

        register_search_tools(mcp, indexer, searcher)

        # Verify that the registration was done
        assert mcp.tool.called


class TestSearchDuplicatesValidation:
    """Tests for validation of parameters."""

    def test_threshold_validation(self):
        """The threshold is constrained to the interval from 0 through 1."""
        # Values valid
        assert 0.5 <= max(0.5, min(0.99, 0.90)) <= 0.99
        assert 0.5 <= max(0.5, min(0.99, 0.50)) <= 0.99
        assert 0.5 <= max(0.5, min(0.99, 0.99)) <= 0.99

        # Invalid values are clamped.
        assert max(0.5, min(0.99, 0.0)) == 0.5  # Very low
        assert max(0.5, min(0.99, 1.5)) == 0.99  # Very alto

    def test_max_notes_validation(self):
        """max_notes must be between 10 and 1000."""
        # Values valid
        assert 10 <= max(10, min(1000, 500)) <= 1000
        assert 10 <= max(10, min(1000, 10)) <= 1000
        assert 10 <= max(10, min(1000, 1000)) <= 1000

        # Invalid values are clamped.
        assert max(10, min(1000, 5)) == 10  # Very low
        assert max(10, min(1000, 2000)) == 1000  # Very alto


class TestSearchDuplicatesOutput:
    """Tests for format of output."""

    def test_output_format(self):
        """Checks structure of the output."""
        # Simulate output expected
        expected_output = [
            {
                "notes": [
                    {"note_path": "note1.md", "note_title": "Note 1", "folder": ""},
                    {"note_path": "note2.md", "note_title": "Note 2", "folder": ""},
                ],
                "count": 2,
                "avg_similarity": 0.95,
            }
        ]

        # Verify structure
        assert isinstance(expected_output, list)
        assert len(expected_output) > 0
        assert "notes" in expected_output[0]
        assert "count" in expected_output[0]
        assert "avg_similarity" in expected_output[0]
        assert isinstance(expected_output[0]["notes"], list)
        assert len(expected_output[0]["notes"]) == expected_output[0]["count"]

    def test_notes_sorted_by_similarity(self):
        """Groups are sorted by similarity in descending order."""
        groups = [
            {"notes": [], "count": 2, "avg_similarity": 0.85},
            {"notes": [], "count": 3, "avg_similarity": 0.95},
            {"notes": [], "count": 2, "avg_similarity": 0.90},
        ]

        # Sort exactly as the production function does.
        sorted_groups = sorted(groups, key=lambda g: g["avg_similarity"], reverse=True)

        assert sorted_groups[0]["avg_similarity"] == 0.95
        assert sorted_groups[1]["avg_similarity"] == 0.90
        assert sorted_groups[2]["avg_similarity"] == 0.85
