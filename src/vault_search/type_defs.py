"""Types shared across vault-search-mcp modules."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


class ParseStatus(StrEnum):
    """Semantic result of parsing a file."""

    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


class ReindexStatus(StrEnum):
    """Public states for incremental reindexing."""

    UPDATED = "updated"
    EMPTY = "empty"
    DELETED = "deleted"
    PARSE_ERROR = "parse_error"
    ERROR_ADD_FAILED = "error_add_failed"
    REJECTED_PATH_TRAVERSAL = "rejected_path_traversal"
    REJECTED_EXTENSION = "rejected_extension"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


class FullReindexStatus(StrEnum):
    """Public states for a complete index rebuild."""

    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ChunkRecord(TypedDict):
    """Text chunk with metadata, ready for insertion into LanceDB without a vector."""

    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    modified_at: str
    text: str
    # Optional frontmatter fields.
    id: NotRequired[str]  # Unique UUID v7 for the note
    created_at: NotRequired[str]  # ISO creation date
    updated_at: NotRequired[str]  # ISO last-update date
    description: NotRequired[str]  # Note description or summary
    status: NotRequired[str]  # draft, review, published, archived
    note_type: NotRequired[str]  # daily, weekly, monthly, yearly, meeting, idea, task
    category: NotRequired[str]  # work, personal, reference, project
    project: NotRequired[str]  # Associated project name.
    source: NotRequired[str]  # Source URL or reference


class LinkRecord(TypedDict):
    """Link extracted from a note, ready for insertion into ``links_index``."""

    # Source note.
    from_note_path: str  # Note containing the link
    from_note_title: str  # Source note title

    # Link type and target.
    link_type: str  # "wikilink" | "markdown" | "embed" | "external"
    link_target: str  # Original target as written.
    link_target_normalized: str  # Normalized for matching

    # Destination, resolved during indexing
    to_note_path: NotRequired[str]  # Path if it exists, otherwise an empty string
    is_resolved: NotRequired[bool]  # True when the destination note exists

    # Wikilink metadata.
    alias: NotRequired[str]  # Alias in [[Note|alias]]
    heading: NotRequired[str]  # Heading in [[Note#Heading]]
    block_ref: NotRequired[str]  # Block reference in [[Note^block]]

    # Context
    context: str  # Text around the link.
    modified_at: str  # Source note date


@dataclass(slots=True)
class ParseResult:
    """Typed result that distinguishes an empty file from a parser failure."""

    status: ParseStatus
    chunks: list[ChunkRecord] = field(default_factory=list)
    links: list[LinkRecord] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    error_type: str | None = None

    def __iter__(
        self,
    ) -> Iterator[list[ChunkRecord] | list[LinkRecord] | list[str]]:
        """Keep unpacking compatible with the historical ``parse_file`` contract."""
        yield self.chunks
        yield self.links
        yield self.aliases


class ChunkWithVector(ChunkRecord):
    """Chunk with an attached embedding vector."""

    vector: list[float]


class AliasRecord(TypedDict):
    """Normalized alias persisted in the auxiliary table."""

    note_path: str
    alias: str
    alias_normalized: str


class SearchRow(TypedDict, total=False):
    """Dynamic row received from LanceDB during a search."""

    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    modified_at: str
    text: str
    vector: list[float]
    _distance: float
    _score: float
    rerank_score: float
    _hybrid_score: float


class SearchResult(TypedDict):
    """Search result returned to the user."""

    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    text: str
    score: NotRequired[float]


class SimilarNoteResult(TypedDict):
    """Related note returned by similarity search."""

    note_path: str
    note_title: str
    folder: str
    tags: str
    similarity_score: float


class DuplicateNoteResult(TypedDict):
    """Public identity of a note in a duplicate group."""

    note_path: str
    note_title: str
    folder: str


class DuplicateGroup(TypedDict):
    """Group of notes with embeddings above the similarity threshold."""

    notes: list[DuplicateNoteResult]
    count: int
    avg_similarity: float


class ReindexResult(TypedDict):
    """Incremental reindexing result."""

    chunks_indexed: int
    status: ReindexStatus
    links_indexed: NotRequired[int]  # Links extracted and indexed
    aliases_indexed: NotRequired[int]  # Indexed frontmatter aliases.
    id_added: NotRequired[bool]  # Whether a UUID was generated.
    frontmatter_enriched: NotRequired[bool]  # Whether enrichment filled required fields.
    frontmatter_fields_filled: NotRequired[int]  # Number of required fields filled.
    auto_compacted: NotRequired[bool]  # True when automatic compaction occurred


class IndexStats(TypedDict):
    """Current index statistics."""

    total_chunks: int
    unique_notes: int
    last_modified: str | None


class FullReindexStats(TypedDict):
    """Complete reindexing statistics."""

    total_notes: int
    total_chunks: int
    duration_seconds: float
    status: FullReindexStatus
    parse_errors: NotRequired[int]
    previous_index_preserved: NotRequired[bool]
    total_links: NotRequired[int]  # Links extracted and indexed
    total_aliases: NotRequired[int]  # Indexed frontmatter aliases.
    timed_out: NotRequired[bool]  # True when reindexing stopped because of a timeout
    indices_skipped: NotRequired[bool]  # True when indexes were skipped
    vector_index_created: NotRequired[bool]  # True when the vector index was created


class FullReindexPreview(TypedDict):
    """Measured vault preview without chunk or duration estimates."""

    dry_run: Literal[True]
    would_index: int
    notes_by_extension: dict[str, int]
    batch_size: int


class HeaderSection(TypedDict):
    """Section extracted by splitting on Markdown headings."""

    headers: list[str]
    content: str
