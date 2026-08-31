"""
Pydantic models for configuration validation.

The hierarchy mirrors ``config.yaml``.
Every field has a default value so the application works without an external file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vault_search.utils.network import is_loopback_host


class _ConfigModel(BaseModel):
    """Strict base model that prevents configuration typos from being ignored."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# =============================================================================
# Paths
# =============================================================================


class PathsConfig(_ConfigModel):
    """Path and directory settings."""

    vault_path: str = Field(
        default="vaults/obsidian_vault",
        description="Path to the Obsidian vault; a symlink is recommended",
    )
    data_dir: str = Field(
        default="data",
        description="Directory for LanceDB data",
    )
    lancedb_table: str = Field(
        default="vault_chunks",
        description="LanceDB table name",
    )


# =============================================================================
# Search
# =============================================================================


class SearchConfig(_ConfigModel):
    """Vector and hybrid search settings."""

    candidates: int = Field(default=50, ge=1, description="Initial candidates")
    candidates_max: int = Field(default=500, ge=1, description="Maximum candidates")
    candidates_multiplier: int = Field(default=2, ge=1, description="Candidate multiplier")
    top_k: int = Field(default=10, ge=1, le=100, description="Default result count")
    top_k_min: int = Field(default=1, ge=1, description="Minimum top_k")
    top_k_max: int = Field(default=100, ge=1, description="Maximum top_k")
    score_precision: int = Field(default=4, ge=0, le=10, description="Decimal places")
    list_notes_default_limit: int = Field(default=500, ge=1, description="Default list_notes limit")
    list_notes_max_limit: int = Field(default=5000, ge=1, description="Maximum list_notes limit")

    @model_validator(mode="after")
    def validate_ranges(self) -> SearchConfig:
        """Reject contradictory limits before starting the server."""
        if self.candidates > self.candidates_max:
            raise ValueError("candidates cannot exceed candidates_max")
        if self.top_k_min > self.top_k_max:
            raise ValueError("top_k_min cannot exceed top_k_max")
        if not self.top_k_min <= self.top_k <= self.top_k_max:
            raise ValueError("top_k must be between top_k_min and top_k_max")
        if self.list_notes_default_limit > self.list_notes_max_limit:
            raise ValueError("list_notes_default_limit cannot exceed list_notes_max_limit")
        return self


# =============================================================================
# Indexing
# =============================================================================


class IndexingConfig(_ConfigModel):
    """Vault indexing settings."""

    batch_size: int = Field(default=500, ge=1, description="Embedding batch size")
    workers: int | None = Field(default=None, description="Workers for parallel reads")
    max_chunks_per_note: int = Field(default=1000, ge=1, description="Maximum chunks per note")
    extensions: list[str] = Field(
        default_factory=lambda: [".md", ".mdx", ".txt", ".pdf", ".canvas"],
        description="Extensions to index",
    )
    ignored_folders: list[str] = Field(
        default_factory=lambda: [".obsidian", ".smart-env", ".trash", ".git"],
        description="Folders to ignore",
    )

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, values: list[str]) -> list[str]:
        """Keep the configuration aligned with available parsers."""
        supported = {".md", ".mdx", ".txt", ".pdf", ".canvas"}
        if not values:
            raise ValueError("extensions must contain at least one extension")
        if len(values) != len(set(values)):
            raise ValueError("extensions cannot contain duplicate values")
        invalid = sorted(value for value in values if value not in supported)
        if invalid:
            raise ValueError(f"extensions contains values without a parser: {', '.join(invalid)}")
        return values

    @field_validator("ignored_folders")
    @classmethod
    def validate_ignored_folders(cls, values: list[str]) -> list[str]:
        """Accept folder names that component-wise matching can apply."""
        if len(values) != len(set(values)):
            raise ValueError("ignored_folders cannot contain duplicate values")
        invalid = [
            value
            for value in values
            if not value or value in {".", ".."} or "/" in value or "\\" in value
        ]
        if invalid:
            raise ValueError("ignored_folders accepts only simple folder names")
        return values


# =============================================================================
# FTS
# =============================================================================


class FTSConfig(_ConfigModel):
    """Full-text search settings for Tantivy."""

    language: str | None = Field(
        default=None,
        description=(
            "Language used for stemming; null disables language-specific stemming "
            "and stop-word removal"
        ),
    )


# =============================================================================
# Prewarm
# =============================================================================


class PrewarmConfig(_ConfigModel):
    """Settings for preloading indexes into RAM."""

    enabled: bool = Field(default=True, description="Enable automatic prewarming")
    max_ram_percent: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Maximum RAM percentage",
    )
    min_available_ram: int = Field(
        default=2 * 1024 * 1024 * 1024,  # 2GB
        ge=0,
        description="Minimum free RAM",
    )
    bytes_per_chunk: int = Field(default=5120, ge=1, description="Bytes per chunk")


# =============================================================================
# Embedding
# =============================================================================


class EmbeddingConfig(_ConfigModel):
    """Embedding and reranking model settings."""

    model: str = Field(default="BAAI/bge-m3", description="Embedding model")
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Cross-encoder reranking model",
    )
    use_fp16: bool | None = Field(
        default=None,
        description="Use FP16; null selects automatically for the device",
    )
    device: Literal["auto", "cpu", "cuda", "mps"] = Field(
        default="auto",
        description="Inference device; auto prioritizes CUDA, MPS, then CPU",
    )
    batch_size: int = Field(default=32, ge=1, description="Batch size")
    query_max_length: int = Field(default=512, ge=1, description="Max length queries")
    corpus_max_length: int = Field(default=1024, ge=1, description="Max length corpus")
    dimension: int = Field(default=1024, ge=1, description="Vector dimension")
    reranker_normalize: bool = Field(default=True, description="Normalize reranker scores")
    idle_timeout: int = Field(default=1800, ge=0, description="Unload timeout")


# =============================================================================
# Chunking
# =============================================================================


class ChunkingConfig(_ConfigModel):
    """Document chunking settings."""

    size: int = Field(default=2000, ge=100, description="Chunk size")
    overlap: int = Field(default=200, ge=0, description="Overlap between chunks")
    header_levels: int = Field(default=4, ge=1, le=6, description="Heading levels")
    separators: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", " "],
        description="Hierarchical separators",
    )

    @field_validator("overlap")
    @classmethod
    def overlap_less_than_size(cls, v: int, info) -> int:
        """Validate that overlap is smaller than size."""
        size = info.data.get("size", 2000)
        if v >= size:
            raise ValueError(f"overlap ({v}) must be smaller than size ({size})")
        return v


# =============================================================================
# Security
# =============================================================================


class SecurityConfig(_ConfigModel):
    """Security settings and input limits."""

    max_query_length: int = Field(default=10_000, ge=1, description="Max query length")
    max_content_size: int = Field(default=10_485_760, ge=1, description="Max content size")
    max_path_length: int = Field(default=500, ge=1, description="Max path length")
    max_frontmatter_keys: int = Field(default=100, ge=1, description="Max frontmatter keys")


# =============================================================================
# Watcher
# =============================================================================


class WatcherConfig(_ConfigModel):
    """File watcher settings."""

    debounce: float = Field(default=2.0, ge=0, description="Debounce in seconds")
    poll_factor: int = Field(default=2, ge=1, description="Polling factor")
    thread_join_timeout: int = Field(default=5, ge=1, description="Thread join timeout")


# =============================================================================
# PDF
# =============================================================================


class PDFConfig(_ConfigModel):
    """PDF and OCR processing settings."""

    ocr_enabled: bool = Field(default=True, description="Enable OCR")
    ocr_languages: str = Field(default="eng", description="Tesseract languages")
    ocr_dpi: int = Field(default=150, ge=72, le=600, description="OCR rendering DPI")


# =============================================================================
# Vector Index (ANN)
# =============================================================================


class VectorIndexConfig(_ConfigModel):
    """ANN vector-index settings."""

    min_chunks: int = Field(default=5000, ge=1, description="Minimum chunks required for an index")
    auto_create: bool = Field(default=True, description="Create the index automatically")
    index_type: Literal["IVF_PQ", "IVF_HNSW_SQ"] = Field(default="IVF_PQ", description="Index type")
    num_sub_vectors: int = Field(default=128, ge=1, description="PQ sub-vectors")
    distance_type: Literal["cosine", "l2", "dot"] = Field(
        default="cosine", description="Distance metric"
    )


# =============================================================================
# Daemon
# =============================================================================


class DaemonConfig(_ConfigModel):
    """Model daemon settings."""

    host: str = Field(default="127.0.0.1", description="Daemon host")
    port: int = Field(default=9847, ge=1, le=65535, description="Daemon port")
    timeout: float = Field(default=120.0, ge=1, description="Request timeout")
    auto_use: bool = Field(default=True, description="Use the daemon automatically when available")
    max_request_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        description="Maximum HTTP request body accepted by the daemon",
    )
    max_texts: int = Field(
        default=512,
        ge=1,
        le=10_000,
        description="Maximum text items per request",
    )
    max_text_length: int = Field(
        default=100_000,
        ge=1,
        description="Maximum characters per text item",
    )

    @field_validator("host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        """Prevent notes from being sent over HTTP to a remote host."""
        normalized = value.strip()
        if not is_loopback_host(normalized):
            raise ValueError("daemon.host must be a loopback address")
        return normalized


# Constants for direct imports
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 9847
DAEMON_TIMEOUT = 120.0


# =============================================================================
# Navigation
# =============================================================================


class NavigationConfig(_ConfigModel):
    """Navigation settings such as ``folder_tree``."""

    folder_tree_max_depth: int = Field(default=10, ge=1, description="Default maximum depth")
    folder_tree_max_depth_limit: int = Field(default=50, ge=1, description="API depth limit")

    @model_validator(mode="after")
    def validate_depth_range(self) -> NavigationConfig:
        """Ensure the published default fits within the tool limit."""
        if self.folder_tree_max_depth > self.folder_tree_max_depth_limit:
            raise ValueError("folder_tree_max_depth cannot exceed folder_tree_max_depth_limit")
        return self


# =============================================================================
# Frontmatter Schema
# =============================================================================

# Import types from the frontmatter module to avoid duplication.
# The import occurs lazily in get_frontmatter_validator() to avoid a cycle.
ValidationMode = Literal["strict", "lenient", "warn_only"]


class FrontmatterAIConfig(_ConfigModel):
    """Settings for AI-assisted frontmatter enrichment."""

    enabled: bool = Field(default=False, description="Enable AI-assisted enrichment")
    allow_external_processing: bool = Field(
        default=False,
        description="Explicit consent to send content to an external provider",
    )
    provider: str | None = Field(
        default=None,
        description="External provider responsible for processing",
    )
    allow_defer_required_on_create: bool = Field(
        default=True,
        description="Allow create_note with missing required fields and defer to reindexing",
    )
    command: list[str] = Field(
        default_factory=list,
        description="CLI command template; supports {model} and receives content through stdin",
    )
    primary_model: str | None = Field(default=None, description="Primary provider model")
    fallback_model: str | None = Field(
        default=None,
        description="Fallback model; None disables fallback",
    )
    timeout_seconds: float = Field(default=8.0, ge=1.0, description="Timeout per CLI call")
    max_attempts: int = Field(default=2, ge=1, le=5, description="Attempts per model")
    max_note_chars: int = Field(default=12000, ge=500, description="Note character limit")

    @model_validator(mode="after")
    def validate_external_processing(self) -> FrontmatterAIConfig:
        """Require auditable consent and transport content through stdin."""
        if any("{prompt}" in argument for argument in self.command):
            raise ValueError("command cannot contain {prompt}; send content only through stdin")

        if self.enabled and not self.allow_external_processing:
            raise ValueError("allow_external_processing must be true when enrichment is enabled")

        if self.enabled and not (self.provider and self.provider.strip()):
            raise ValueError(
                "provider must identify the external processor when enrichment is enabled"
            )

        if self.enabled and not self.command:
            raise ValueError("command cannot be empty when enrichment is enabled")

        if self.command and not self.command[0].strip():
            raise ValueError("command must start with a non-empty executable")

        if self.enabled and not (self.primary_model and self.primary_model.strip()):
            raise ValueError("primary_model must be defined when enrichment is enabled")

        if self.primary_model is not None and not self.primary_model.strip():
            raise ValueError("primary_model cannot be empty")

        if self.fallback_model is not None and not self.fallback_model.strip():
            raise ValueError("fallback_model cannot be empty")

        return self


class FrontmatterConfig(_ConfigModel):
    """
    Frontmatter validation settings.

    The schema uses ``FieldSchema`` from ``vault_search.frontmatter``.
    ``get_frontmatter_validator()`` converts dictionaries into ``FieldSchema`` objects.
    """

    enabled: bool = Field(default=False, description="Enable schema validation")
    mode: ValidationMode = Field(
        default="lenient",
        description="Mode: strict blocks, lenient warns, warn_only reports",
    )
    allow_extra_fields: bool = Field(
        default=True,
        description="Allow fields not defined in the schema",
    )
    schema_fields: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Field schema converted to FieldSchema at runtime",
        alias="schema",
    )
    ai: FrontmatterAIConfig = Field(
        default_factory=FrontmatterAIConfig,
        description="Asynchronous AI-assisted enrichment settings",
    )


# =============================================================================
# Root Config
# =============================================================================


class VaultSearchConfig(_ConfigModel):
    """Root configuration for vault-search-mcp."""

    paths: PathsConfig = Field(default_factory=PathsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    fts: FTSConfig = Field(default_factory=FTSConfig)
    prewarm: PrewarmConfig = Field(default_factory=PrewarmConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    pdf: PDFConfig = Field(default_factory=PDFConfig)
    vector_index: VectorIndexConfig = Field(default_factory=VectorIndexConfig)
    navigation: NavigationConfig = Field(default_factory=NavigationConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    frontmatter: FrontmatterConfig = Field(default_factory=FrontmatterConfig)

    @model_validator(mode="after")
    def validate_vector_index(self) -> VaultSearchConfig:
        """Fail early when IVF_PQ partitioning does not fit the vector."""
        vector_index = self.vector_index
        if (
            vector_index.index_type == "IVF_PQ"
            and self.embedding.dimension % vector_index.num_sub_vectors != 0
        ):
            raise ValueError(
                "vector_index.num_sub_vectors must divide embedding.dimension for IVF_PQ"
            )
        return self

    def resolve_paths(self, project_root: Path) -> VaultSearchConfig:
        """
        Resolve relative paths to absolute paths from the project root.

        Parameters:
            project_root: Project root directory.

        Returns:
            A new instance with resolved paths.
        """

        def resolve_path(value: str) -> str:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = project_root / path
            return str(path.resolve(strict=False))

        return self.model_copy(
            update={
                "paths": PathsConfig(
                    vault_path=resolve_path(self.paths.vault_path),
                    data_dir=resolve_path(self.paths.data_dir),
                    lancedb_table=self.paths.lancedb_table,
                )
            }
        )
