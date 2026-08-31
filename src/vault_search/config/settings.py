"""
Modelos Pydantic para validação de configuração.

Estrutura hierárquica que espelha o config.yaml.
Todos os campos têm valores default para funcionar sem arquivo externo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vault_search.utils.network import is_loopback_host


class _ConfigModel(BaseModel):
    """Base estrita para impedir que typos de configuração sejam ignorados."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# =============================================================================
# Paths
# =============================================================================


class PathsConfig(_ConfigModel):
    """Configurações de caminhos e diretórios."""

    vault_path: str = Field(
        default="vaults/obsidian_vault",
        description="Caminho para o vault Obsidian (symlink recomendado)",
    )
    data_dir: str = Field(
        default="data",
        description="Diretório para dados do LanceDB",
    )
    lancedb_table: str = Field(
        default="vault_chunks",
        description="Nome da tabela no LanceDB",
    )


# =============================================================================
# Search
# =============================================================================


class SearchConfig(_ConfigModel):
    """Configurações de busca vetorial e híbrida."""

    candidates: int = Field(default=50, ge=1, description="Candidatos iniciais")
    candidates_max: int = Field(default=500, ge=1, description="Máximo de candidatos")
    candidates_multiplier: int = Field(default=2, ge=1, description="Multiplicador")
    top_k: int = Field(default=10, ge=1, le=100, description="Resultados default")
    top_k_min: int = Field(default=1, ge=1, description="Mínimo top_k")
    top_k_max: int = Field(default=100, ge=1, description="Máximo top_k")
    score_precision: int = Field(default=4, ge=0, le=10, description="Casas decimais")
    list_notes_default_limit: int = Field(
        default=500, ge=1, description="Limite default list_notes"
    )
    list_notes_max_limit: int = Field(default=5000, ge=1, description="Limite máximo list_notes")

    @model_validator(mode="after")
    def validate_ranges(self) -> SearchConfig:
        """Rejeita limites contraditórios antes de iniciar o servidor."""
        if self.candidates > self.candidates_max:
            raise ValueError("candidates não pode exceder candidates_max")
        if self.top_k_min > self.top_k_max:
            raise ValueError("top_k_min não pode exceder top_k_max")
        if not self.top_k_min <= self.top_k <= self.top_k_max:
            raise ValueError("top_k deve estar entre top_k_min e top_k_max")
        if self.list_notes_default_limit > self.list_notes_max_limit:
            raise ValueError("list_notes_default_limit não pode exceder list_notes_max_limit")
        return self


# =============================================================================
# Indexing
# =============================================================================


class IndexingConfig(_ConfigModel):
    """Configurações de indexação do vault."""

    batch_size: int = Field(default=500, ge=1, description="Batch size para embeddings")
    workers: int | None = Field(default=None, description="Workers para leitura paralela")
    max_chunks_per_note: int = Field(default=1000, ge=1, description="Máximo chunks por nota")
    extensions: list[str] = Field(
        default_factory=lambda: [".md", ".mdx", ".txt", ".pdf", ".canvas"],
        description="Extensões para indexar",
    )
    ignored_folders: list[str] = Field(
        default_factory=lambda: [".obsidian", ".smart-env", ".trash", ".git"],
        description="Pastas a ignorar",
    )

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, values: list[str]) -> list[str]:
        """Mantém a configuração alinhada aos parsers disponíveis."""
        supported = {".md", ".mdx", ".txt", ".pdf", ".canvas"}
        if not values:
            raise ValueError("extensions deve conter ao menos uma extensão")
        if len(values) != len(set(values)):
            raise ValueError("extensions não pode conter valores duplicados")
        invalid = sorted(value for value in values if value not in supported)
        if invalid:
            raise ValueError(f"extensions contém valores sem parser: {', '.join(invalid)}")
        return values

    @field_validator("ignored_folders")
    @classmethod
    def validate_ignored_folders(cls, values: list[str]) -> list[str]:
        """Aceita nomes de pasta que a comparação por componente consegue aplicar."""
        if len(values) != len(set(values)):
            raise ValueError("ignored_folders não pode conter valores duplicados")
        invalid = [
            value
            for value in values
            if not value or value in {".", ".."} or "/" in value or "\\" in value
        ]
        if invalid:
            raise ValueError("ignored_folders aceita somente nomes simples de pasta")
        return values


# =============================================================================
# FTS
# =============================================================================


class FTSConfig(_ConfigModel):
    """Configurações de Full-Text Search (Tantivy)."""

    language: str | None = Field(
        default=None,
        description="Língua para stemming (null = tokenizador neutro)",
    )


# =============================================================================
# Prewarm
# =============================================================================


class PrewarmConfig(_ConfigModel):
    """Configurações de preload de índices na RAM."""

    enabled: bool = Field(default=True, description="Habilitar prewarm automático")
    max_ram_percent: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Percentual máximo de RAM",
    )
    min_available_ram: int = Field(
        default=2 * 1024 * 1024 * 1024,  # 2GB
        ge=0,
        description="RAM mínima livre",
    )
    bytes_per_chunk: int = Field(default=5120, ge=1, description="Bytes por chunk")


# =============================================================================
# Embedding
# =============================================================================


class EmbeddingConfig(_ConfigModel):
    """Configurações de modelos de embedding e reranking."""

    model: str = Field(default="BAAI/bge-m3", description="Modelo de embedding")
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Modelo de reranking cross-encoder",
    )
    use_fp16: bool | None = Field(
        default=None,
        description="Usar FP16 (null seleciona automaticamente conforme o device)",
    )
    device: Literal["auto", "cpu", "cuda", "mps"] = Field(
        default="auto",
        description="Device de inferência (auto prioriza CUDA, MPS e CPU)",
    )
    batch_size: int = Field(default=32, ge=1, description="Batch size")
    query_max_length: int = Field(default=512, ge=1, description="Max length queries")
    corpus_max_length: int = Field(default=1024, ge=1, description="Max length corpus")
    dimension: int = Field(default=1024, ge=1, description="Dimensão do vetor")
    reranker_normalize: bool = Field(default=True, description="Normalizar reranker")
    idle_timeout: int = Field(default=1800, ge=0, description="Timeout para descarregar")


# =============================================================================
# Chunking
# =============================================================================


class ChunkingConfig(_ConfigModel):
    """Configurações de chunking de documentos."""

    size: int = Field(default=2000, ge=100, description="Tamanho do chunk")
    overlap: int = Field(default=200, ge=0, description="Overlap entre chunks")
    header_levels: int = Field(default=4, ge=1, le=6, description="Níveis de header")
    separators: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", " "],
        description="Separadores hierárquicos",
    )

    @field_validator("overlap")
    @classmethod
    def overlap_less_than_size(cls, v: int, info) -> int:
        """Valida que overlap é menor que size."""
        size = info.data.get("size", 2000)
        if v >= size:
            raise ValueError(f"overlap ({v}) deve ser menor que size ({size})")
        return v


# =============================================================================
# Security
# =============================================================================


class SecurityConfig(_ConfigModel):
    """Configurações de segurança e limites de input."""

    max_query_length: int = Field(default=10_000, ge=1, description="Max query length")
    max_content_size: int = Field(default=10_485_760, ge=1, description="Max content size")
    max_path_length: int = Field(default=500, ge=1, description="Max path length")
    max_frontmatter_keys: int = Field(default=100, ge=1, description="Max frontmatter keys")


# =============================================================================
# Watcher
# =============================================================================


class WatcherConfig(_ConfigModel):
    """Configurações do file watcher."""

    debounce: float = Field(default=2.0, ge=0, description="Debounce em segundos")
    poll_factor: int = Field(default=2, ge=1, description="Fator de polling")
    thread_join_timeout: int = Field(default=5, ge=1, description="Timeout join threads")


# =============================================================================
# PDF
# =============================================================================


class PDFConfig(_ConfigModel):
    """Configurações de processamento de PDFs e OCR."""

    ocr_enabled: bool = Field(default=True, description="Habilitar OCR")
    ocr_languages: str = Field(default="por+eng", description="Idiomas Tesseract")
    ocr_dpi: int = Field(default=150, ge=72, le=600, description="DPI para OCR")


# =============================================================================
# Vector Index (ANN)
# =============================================================================


class VectorIndexConfig(_ConfigModel):
    """Configurações de índice vetorial ANN."""

    min_chunks: int = Field(default=5000, ge=1, description="Mínimo chunks para criar índice")
    auto_create: bool = Field(default=True, description="Criar índice automaticamente")
    index_type: Literal["IVF_PQ", "IVF_HNSW_SQ"] = Field(default="IVF_PQ", description="Tipo")
    num_sub_vectors: int = Field(default=128, ge=1, description="Sub-vetores para PQ")
    distance_type: Literal["cosine", "l2", "dot"] = Field(default="cosine", description="Métrica")


# =============================================================================
# Daemon
# =============================================================================


class DaemonConfig(_ConfigModel):
    """Configurações do daemon de modelos."""

    host: str = Field(default="127.0.0.1", description="Host do daemon")
    port: int = Field(default=9847, ge=1, le=65535, description="Porta do daemon")
    timeout: float = Field(default=120.0, ge=1, description="Timeout para requests")
    auto_use: bool = Field(default=True, description="Usar daemon automaticamente se disponível")
    max_request_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        description="Tamanho máximo do corpo HTTP aceito pelo daemon",
    )
    max_texts: int = Field(
        default=512,
        ge=1,
        le=10_000,
        description="Quantidade máxima de textos por request",
    )
    max_text_length: int = Field(
        default=100_000,
        ge=1,
        description="Quantidade máxima de caracteres por texto",
    )

    @field_validator("host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        """Impede que notas sejam enviadas em HTTP para um host remoto."""
        normalized = value.strip()
        if not is_loopback_host(normalized):
            raise ValueError("daemon.host deve ser um endereço de loopback")
        return normalized


# Constantes para import direto
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 9847
DAEMON_TIMEOUT = 120.0


# =============================================================================
# Navigation
# =============================================================================


class NavigationConfig(_ConfigModel):
    """Configurações de navegação (folder_tree, etc)."""

    folder_tree_max_depth: int = Field(default=10, ge=1, description="Profundidade max")
    folder_tree_max_depth_limit: int = Field(default=50, ge=1, description="Limite API")

    @model_validator(mode="after")
    def validate_depth_range(self) -> NavigationConfig:
        """Garante que o default publicado caiba no limite da tool."""
        if self.folder_tree_max_depth > self.folder_tree_max_depth_limit:
            raise ValueError("folder_tree_max_depth não pode exceder folder_tree_max_depth_limit")
        return self


# =============================================================================
# Frontmatter Schema
# =============================================================================

# Import tipos do módulo frontmatter para evitar duplicação
# Nota: importação tardia em get_frontmatter_validator() para evitar circular import
ValidationMode = Literal["strict", "lenient", "warn_only"]


class FrontmatterAIConfig(_ConfigModel):
    """Configurações de enriquecimento de frontmatter via IA."""

    enabled: bool = Field(default=False, description="Habilitar enriquecimento por IA")
    allow_external_processing: bool = Field(
        default=False,
        description="Consentimento explícito para enviar conteúdo a um provedor externo",
    )
    provider: str | None = Field(
        default=None,
        description="Provedor externo responsável pelo processamento",
    )
    allow_defer_required_on_create: bool = Field(
        default=True,
        description="Permitir create_note com required ausente e deferir para reindex",
    )
    command: list[str] = Field(
        default_factory=list,
        description="Comando CLI template (suporta {model}; conteúdo segue por stdin)",
    )
    primary_model: str | None = Field(default=None, description="Modelo primário do provider")
    fallback_model: str | None = Field(
        default=None,
        description="Modelo fallback (None para desabilitar)",
    )
    timeout_seconds: float = Field(default=8.0, ge=1.0, description="Timeout por chamada CLI")
    max_attempts: int = Field(default=2, ge=1, le=5, description="Tentativas por modelo")
    max_note_chars: int = Field(default=12000, ge=500, description="Limite de caracteres da nota")

    @model_validator(mode="after")
    def validate_external_processing(self) -> FrontmatterAIConfig:
        """Exige consentimento auditável e transporte do conteúdo por stdin."""
        if any("{prompt}" in argument for argument in self.command):
            raise ValueError("command não pode conter {prompt}; envie o conteúdo somente por stdin")

        if self.enabled and not self.allow_external_processing:
            raise ValueError(
                "allow_external_processing deve ser true quando o enriquecimento estiver habilitado"
            )

        if self.enabled and not (self.provider and self.provider.strip()):
            raise ValueError(
                "provider deve identificar o processador externo quando o enriquecimento estiver habilitado"
            )

        if self.enabled and not self.command:
            raise ValueError(
                "command não pode ser vazio quando o enriquecimento estiver habilitado"
            )

        if self.command and not self.command[0].strip():
            raise ValueError("command deve começar com um executável não vazio")

        if self.enabled and not (self.primary_model and self.primary_model.strip()):
            raise ValueError(
                "primary_model deve ser definido quando o enriquecimento estiver habilitado"
            )

        if self.primary_model is not None and not self.primary_model.strip():
            raise ValueError("primary_model não pode ser vazio")

        if self.fallback_model is not None and not self.fallback_model.strip():
            raise ValueError("fallback_model não pode ser vazio")

        return self


class FrontmatterConfig(_ConfigModel):
    """
    Configurações de validação de frontmatter.

    O schema usa FieldSchema do módulo vault_search.frontmatter.
    A conversão de dict para FieldSchema é feita em get_frontmatter_validator().
    """

    enabled: bool = Field(default=False, description="Habilitar validação de schema")
    mode: ValidationMode = Field(
        default="lenient",
        description="Modo: strict (bloqueia), lenient (avisa), warn_only",
    )
    allow_extra_fields: bool = Field(
        default=True,
        description="Permitir campos não definidos no schema",
    )
    schema_fields: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Schema dos campos (convertido para FieldSchema em runtime)",
        alias="schema",
    )
    ai: FrontmatterAIConfig = Field(
        default_factory=FrontmatterAIConfig,
        description="Configurações de enriquecimento assíncrono por IA",
    )


# =============================================================================
# Root Config
# =============================================================================


class VaultSearchConfig(_ConfigModel):
    """Configuração raiz do vault-search-mcp."""

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
        """Falha cedo quando o particionamento IVF_PQ não cabe no vetor."""
        vector_index = self.vector_index
        if (
            vector_index.index_type == "IVF_PQ"
            and self.embedding.dimension % vector_index.num_sub_vectors != 0
        ):
            raise ValueError(
                "vector_index.num_sub_vectors deve dividir embedding.dimension para IVF_PQ"
            )
        return self

    def resolve_paths(self, project_root: Path) -> VaultSearchConfig:
        """
        Resolve paths relativos para absolutos baseados na raiz do projeto.

        Parâmetros:
            project_root: Diretório raiz do projeto

        Retorna:
            Nova instância com paths resolvidos.
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
