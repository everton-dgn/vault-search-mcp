"""Tipos compartilhados entre módulos do vault-search-mcp."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


class ParseStatus(StrEnum):
    """Resultado semântico do parsing de um arquivo."""

    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


class ReindexStatus(StrEnum):
    """Estados públicos da reindexação incremental."""

    UPDATED = "updated"
    EMPTY = "empty"
    DELETED = "deleted"
    PARSE_ERROR = "parse_error"
    ERROR_ADD_FAILED = "error_add_failed"
    REJECTED_PATH_TRAVERSAL = "rejected_path_traversal"
    REJECTED_EXTENSION = "rejected_extension"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


class FullReindexStatus(StrEnum):
    """Estados públicos da reconstrução completa do índice."""

    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ChunkRecord(TypedDict):
    """Chunk de texto com metadados, pronto para inserção no LanceDB (sem vetor)."""

    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    modified_at: str
    text: str
    # Campos opcionais do frontmatter
    id: NotRequired[str]  # UUID v7 único da nota
    created_at: NotRequired[str]  # data de criação ISO
    updated_at: NotRequired[str]  # data de última atualização ISO
    description: NotRequired[str]  # descrição/resumo da nota
    status: NotRequired[str]  # draft, review, published, archived
    note_type: NotRequired[str]  # daily, weekly, monthly, yearly, meeting, idea, task
    category: NotRequired[str]  # work, personal, reference, project
    project: NotRequired[str]  # nome do projeto associado
    source: NotRequired[str]  # URL ou referência da fonte


class LinkRecord(TypedDict):
    """Link extraído de uma nota, pronto para inserção no links_index."""

    # Origem
    from_note_path: str  # nota que contém o link
    from_note_title: str  # título da nota origem

    # Tipo e target
    link_type: str  # "wikilink" | "markdown" | "embed" | "external"
    link_target: str  # target original como escrito
    link_target_normalized: str  # normalizado para matching

    # Destino (resolvido durante indexação)
    to_note_path: NotRequired[str]  # path se existir, "" se não
    is_resolved: NotRequired[bool]  # True se nota destino existe

    # Metadados de wikilink
    alias: NotRequired[str]  # alias se [[Nota|alias]]
    heading: NotRequired[str]  # heading se [[Nota#Heading]]
    block_ref: NotRequired[str]  # block ref se [[Nota^block]]

    # Contexto
    context: str  # trecho onde o link aparece
    modified_at: str  # data da nota origem


@dataclass(slots=True)
class ParseResult:
    """Resultado tipado que separa arquivo vazio de falha de parser."""

    status: ParseStatus
    chunks: list[ChunkRecord] = field(default_factory=list)
    links: list[LinkRecord] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    error_type: str | None = None

    def __iter__(
        self,
    ) -> Iterator[list[ChunkRecord] | list[LinkRecord] | list[str]]:
        """Mantém unpacking compatível com o contrato histórico de ``parse_file``."""
        yield self.chunks
        yield self.links
        yield self.aliases


class ChunkWithVector(ChunkRecord):
    """Chunk com vetor de embedding anexado."""

    vector: list[float]


class AliasRecord(TypedDict):
    """Alias normalizado persistido na tabela auxiliar."""

    note_path: str
    alias: str
    alias_normalized: str


class SearchRow(TypedDict, total=False):
    """Linha dinâmica recebida do LanceDB durante uma busca."""

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
    """Resultado de busca retornado ao usuário."""

    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    text: str
    score: NotRequired[float]


class SimilarNoteResult(TypedDict):
    """Nota relacionada retornada pela busca de similaridade."""

    note_path: str
    note_title: str
    folder: str
    tags: str
    similarity_score: float


class DuplicateNoteResult(TypedDict):
    """Identidade pública de uma nota em um grupo duplicado."""

    note_path: str
    note_title: str
    folder: str


class DuplicateGroup(TypedDict):
    """Grupo de notas com embeddings acima do limiar de similaridade."""

    notes: list[DuplicateNoteResult]
    count: int
    avg_similarity: float


class ReindexResult(TypedDict):
    """Resultado de reindexação incremental."""

    chunks_indexed: int
    status: ReindexStatus
    links_indexed: NotRequired[int]  # links extraídos e indexados
    aliases_indexed: NotRequired[int]  # aliases do frontmatter indexados
    id_added: NotRequired[bool]  # True se UUID foi gerado
    frontmatter_enriched: NotRequired[bool]  # True se IA preencheu required
    frontmatter_fields_filled: NotRequired[int]  # Quantos campos required foram preenchidos
    auto_compacted: NotRequired[bool]  # True se compactação automática ocorreu


class IndexStats(TypedDict):
    """Estatísticas do índice atual."""

    total_chunks: int
    unique_notes: int
    last_modified: str | None


class FullReindexStats(TypedDict):
    """Estatísticas de reindexação completa."""

    total_notes: int
    total_chunks: int
    duration_seconds: float
    status: FullReindexStatus
    parse_errors: NotRequired[int]
    previous_index_preserved: NotRequired[bool]
    total_links: NotRequired[int]  # links extraídos e indexados
    total_aliases: NotRequired[int]  # aliases do frontmatter indexados
    timed_out: NotRequired[bool]  # True se reindex foi interrompido por timeout
    indices_skipped: NotRequired[bool]  # True se índices foram pulados
    vector_index_created: NotRequired[bool]  # True se índice vetorial foi criado


class FullReindexPreview(TypedDict):
    """Preview medido do vault, sem estimativas de chunks ou duração."""

    dry_run: Literal[True]
    would_index: int
    notes_by_extension: dict[str, int]
    batch_size: int


class HeaderSection(TypedDict):
    """Seção extraída do split por headers markdown."""

    headers: list[str]
    content: str
