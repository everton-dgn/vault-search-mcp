"""
Testes para o sistema de configuração YAML + Pydantic.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from vault_search.config import (
    VaultSearchConfig,
    get_config,
    load_config_from_dict,
    load_config_from_file,
    reload_config,
)
from vault_search.config.embedding import resolve_fp16, resolve_model_device
from vault_search.config.settings import (
    ChunkingConfig,
    EmbeddingConfig,
    FrontmatterAIConfig,
    IndexingConfig,
    NavigationConfig,
    PathsConfig,
    PrewarmConfig,
    SearchConfig,
    SecurityConfig,
    VectorIndexConfig,
)


@pytest.mark.parametrize("host", ["192.0.2.10", "example.test", "0.0.0.0"])
def test_daemon_config_rejects_remote_host(host):
    """Configuração nunca pode encaminhar notas para HTTP remoto."""
    with pytest.raises(ValueError, match="loopback"):
        load_config_from_dict({"daemon": {"host": host}})


class TestVaultSearchConfig:
    """Testes para o modelo raiz de configuração."""

    def test_default_values(self):
        """Configuração default deve ter valores sensíveis."""
        config = VaultSearchConfig()

        assert config.search.top_k == 10
        assert config.search.candidates == 50
        assert config.embedding.model == "BAAI/bge-m3"
        assert config.chunking.size == 2000
        assert config.fts.language is None
        assert config.frontmatter.ai.primary_model is None
        assert config.indexing.extensions == [".md", ".mdx", ".txt", ".pdf", ".canvas"]
        assert ".git" in config.indexing.ignored_folders

    def test_nested_config_access(self):
        """Acesso a configurações aninhadas funciona."""
        config = VaultSearchConfig()

        assert config.paths.lancedb_table == "vault_chunks"

    def test_unknown_fields_are_rejected(self):
        """Typos de configuração devem falhar cedo."""
        with pytest.raises(ValueError, match="extra_forbidden"):
            VaultSearchConfig.model_validate({"daemon": {"timout": 3}})

    def test_resolve_paths(self, tmp_path):
        """resolve_paths() converte paths relativos para absolutos."""
        config = VaultSearchConfig(
            paths=PathsConfig(
                vault_path="my_vault",
                data_dir="my_data",
            )
        )

        resolved = config.resolve_paths(tmp_path)

        assert resolved.paths.vault_path == str(tmp_path / "my_vault")
        assert resolved.paths.data_dir == str(tmp_path / "my_data")


class TestPathsConfig:
    """Testes para configuração de paths."""

    def test_default_paths(self):
        """Paths default são relativos ao projeto."""
        config = PathsConfig()

        assert config.vault_path == "vaults/obsidian_vault"
        assert config.data_dir == "data"
        assert config.lancedb_table == "vault_chunks"

    def test_custom_paths(self):
        """Paths customizados funcionam."""
        config = PathsConfig(
            vault_path="/custom/vault",
            data_dir="/custom/data",
        )

        assert config.vault_path == "/custom/vault"
        assert config.data_dir == "/custom/data"


class TestSearchConfig:
    """Testes para configuração de busca."""

    def test_default_values(self):
        """Valores default de busca."""
        config = SearchConfig()

        assert config.candidates == 50
        assert config.candidates_max == 500
        assert config.top_k == 10
        assert config.top_k_min == 1
        assert config.top_k_max == 100

    def test_validation_top_k_range(self):
        """top_k deve estar dentro dos limites."""
        # Válido
        config = SearchConfig(top_k=50)
        assert config.top_k == 50

        # Inválido - abaixo do mínimo
        with pytest.raises(ValueError):
            SearchConfig(top_k=0)

        # Inválido - acima do máximo
        with pytest.raises(ValueError):
            SearchConfig(top_k=101)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"candidates": 51, "candidates_max": 50}, "candidates"),
            ({"top_k_min": 20, "top_k": 10}, "top_k"),
            ({"top_k": 20, "top_k_max": 10}, "top_k"),
            (
                {"list_notes_default_limit": 101, "list_notes_max_limit": 100},
                "list_notes_default_limit",
            ),
        ],
    )
    def test_rejects_contradictory_ranges(self, overrides, message):
        """Limites contraditórios devem falhar na carga, antes da primeira busca."""
        with pytest.raises(ValueError, match=message):
            SearchConfig(**overrides)


class TestIndexingConfig:
    """Testes para extensões e diretórios ignorados na indexação."""

    @pytest.mark.parametrize(
        "extensions",
        [
            ["md"],
            [".MD"],
            [".md", ".md"],
            [".unknown"],
        ],
    )
    def test_rejects_extensions_that_cannot_match_runtime_suffixes(self, extensions):
        with pytest.raises(ValueError, match="extensions"):
            IndexingConfig(extensions=extensions)

    @pytest.mark.parametrize("folder", [".", "..", "nested/cache", "nested\\cache"])
    def test_rejects_ignored_folder_paths_that_runtime_cannot_match(self, folder):
        with pytest.raises(ValueError, match="ignored_folders"):
            IndexingConfig(ignored_folders=[folder])


class TestChunkingConfig:
    """Testes para configuração de chunking."""

    def test_default_values(self):
        """Valores default de chunking."""
        config = ChunkingConfig()

        assert config.size == 2000
        assert config.overlap == 200
        assert config.header_levels == 4
        assert config.separators == ["\n\n", "\n", ". ", " "]

    def test_overlap_validation(self):
        """Overlap deve ser menor que size."""
        # Válido
        config = ChunkingConfig(size=1000, overlap=100)
        assert config.overlap == 100

        # Inválido - overlap >= size
        with pytest.raises(ValueError, match="overlap.*deve ser menor"):
            ChunkingConfig(size=1000, overlap=1000)


class TestEmbeddingConfig:
    """Testes para configuração de embedding."""

    def test_default_values(self):
        """Valores default de embedding."""
        config = EmbeddingConfig()

        assert config.model == "BAAI/bge-m3"
        assert config.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
        assert config.dimension == 1024
        assert config.device == "auto"

    def test_device_validation(self):
        """Device deve ser válido."""
        # Válidos
        for device in ["auto", "cpu", "cuda", "mps"]:
            config = EmbeddingConfig(device=device)
            assert config.device == device

        # Inválido
        with pytest.raises(ValueError):
            EmbeddingConfig(device="invalid")

    def test_auto_device_prefers_cuda_then_mps_then_cpu(self):
        torch = type(
            "FakeTorch",
            (),
            {
                "cuda": type("Cuda", (), {"is_available": staticmethod(lambda: True)})(),
                "backends": type(
                    "Backends",
                    (),
                    {"mps": type("MPS", (), {"is_available": staticmethod(lambda: True)})()},
                )(),
            },
        )()
        assert resolve_model_device("auto", torch_module=torch) == "cuda"

        torch.cuda.is_available = lambda: False
        assert resolve_model_device("auto", torch_module=torch) == "mps"

        torch.backends.mps.is_available = lambda: False
        assert resolve_model_device("auto", torch_module=torch) == "cpu"

    def test_fp16_is_disabled_on_cpu(self):
        assert resolve_fp16("cpu", configured=True) is False

    def test_tilde_paths_are_expanded_before_project_resolution(self, tmp_path, monkeypatch):
        """Paths com til devem apontar para o diretório pessoal efetivo."""
        monkeypatch.setenv("HOME", str(tmp_path))
        config = VaultSearchConfig(paths=PathsConfig(vault_path="~/vault", data_dir="~/index"))

        resolved = config.resolve_paths(tmp_path / "project")

        assert resolved.paths.vault_path == str(tmp_path / "vault")
        assert resolved.paths.data_dir == str(tmp_path / "index")


class TestSecurityConfig:
    """Testes para configuração de segurança."""

    def test_default_values(self):
        """Valores default de segurança."""
        config = SecurityConfig()

        assert config.max_query_length == 10_000
        assert config.max_content_size == 10_485_760
        assert config.max_frontmatter_keys == 100

    def test_positive_values(self):
        """Valores devem ser positivos."""
        with pytest.raises(ValueError):
            SecurityConfig(max_query_length=0)

    @pytest.mark.parametrize("field", ["rate_limit", "reindex_timeout", "log_query_max_length"])
    def test_rejects_reserved_fields_without_runtime_effect(self, field):
        """A configuração pública não deve aceitar controles que não são aplicados."""
        with pytest.raises(ValueError, match="extra_forbidden"):
            SecurityConfig.model_validate({field: 1})


class TestPrewarmConfig:
    """Testes para configuração de prewarm."""

    def test_default_values(self):
        """Valores default de prewarm."""
        config = PrewarmConfig()

        assert config.enabled is True
        assert config.max_ram_percent == 0.25
        assert config.min_available_ram == 2 * 1024 * 1024 * 1024


class TestVectorIndexConfig:
    """Testes para configuração de índice vetorial."""

    def test_default_values(self):
        """Valores default de índice vetorial."""
        config = VectorIndexConfig()

        assert config.min_chunks == 5000
        assert config.auto_create is True
        assert config.index_type == "IVF_PQ"
        assert config.num_sub_vectors == 128
        assert config.distance_type == "cosine"

    def test_index_type_validation(self):
        """Tipo de índice deve ser válido."""
        # Válidos
        for index_type in ["IVF_PQ", "IVF_HNSW_SQ"]:
            config = VectorIndexConfig(index_type=index_type)
            assert config.index_type == index_type

        # Inválido
        with pytest.raises(ValueError):
            VectorIndexConfig(index_type="INVALID")

    def test_ivf_pq_subvectors_must_divide_embedding_dimension(self):
        """Configuração ANN inválida deve falhar antes de uma indexação demorada."""
        with pytest.raises(ValueError, match="num_sub_vectors"):
            VaultSearchConfig.model_validate(
                {
                    "embedding": {"dimension": 10},
                    "vector_index": {"index_type": "IVF_PQ", "num_sub_vectors": 3},
                }
            )


class TestNavigationConfig:
    """Testes para limites da árvore de pastas."""

    def test_default_depth_cannot_exceed_public_limit(self):
        with pytest.raises(ValueError, match="folder_tree_max_depth"):
            NavigationConfig(folder_tree_max_depth=11, folder_tree_max_depth_limit=10)


class TestFrontmatterAIConfig:
    """Testes para configuração de enriquecimento de frontmatter via IA."""

    def test_default_values(self):
        """Valores default de frontmatter.ai."""
        config = FrontmatterAIConfig()

        assert config.enabled is False
        assert config.allow_defer_required_on_create is True
        assert config.command == []
        assert config.primary_model is None
        assert config.fallback_model is None
        assert config.timeout_seconds == 8.0
        assert config.max_attempts == 2

    def test_external_processing_requires_explicit_consent_and_provider(self):
        with pytest.raises(ValueError, match="allow_external_processing"):
            FrontmatterAIConfig(enabled=True, provider="example")

        with pytest.raises(ValueError, match="provider"):
            FrontmatterAIConfig(enabled=True, allow_external_processing=True)

    def test_enabled_external_processing_requires_command_and_model(self):
        common = {
            "enabled": True,
            "allow_external_processing": True,
            "provider": "example-provider",
        }

        with pytest.raises(ValueError, match="command"):
            FrontmatterAIConfig(**common, primary_model="model-v1")

        with pytest.raises(ValueError, match="primary_model"):
            FrontmatterAIConfig(**common, command=["provider-cli"])

    def test_prompt_placeholder_is_rejected_to_require_stdin(self):
        with pytest.raises(ValueError, match="stdin"):
            FrontmatterAIConfig(command=["provider-cli", "{prompt}"])


class TestLoadConfigFromDict:
    """Testes para load_config_from_dict()."""

    def test_empty_dict(self):
        """Dict vazio usa defaults."""
        config = load_config_from_dict({})

        assert config.search.top_k == 10
        assert config.embedding.model == "BAAI/bge-m3"

    def test_partial_override(self):
        """Override parcial mantém outros defaults."""
        config = load_config_from_dict({"search": {"top_k": 20}})

        assert config.search.top_k == 20
        assert config.search.candidates == 50  # Default mantido

    def test_nested_override(self):
        """Override de configs aninhadas."""
        config = load_config_from_dict({"search": {"score_precision": 2}})

        assert config.search.score_precision == 2
        assert config.search.top_k == 10  # Default mantido


class TestLoadConfigFromFile:
    """Testes para load_config_from_file()."""

    def test_valid_yaml(self, tmp_path):
        """YAML válido é carregado corretamente."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "search": {"top_k": 25},
                    "fts": {"language": "English"},
                }
            )
        )

        config = load_config_from_file(config_file)

        assert config.search.top_k == 25
        assert config.fts.language == "English"

    def test_empty_yaml(self, tmp_path):
        """YAML vazio usa defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        config = load_config_from_file(config_file)

        assert config.search.top_k == 10

    def test_relative_paths_use_config_file_directory(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump({"paths": {"vault_path": "vault", "data_dir": "index"}})
        )

        config = load_config_from_file(config_file)

        assert config.paths.vault_path == str(tmp_path / "vault")
        assert config.paths.data_dir == str(tmp_path / "index")

    def test_file_not_found(self, tmp_path):
        """Arquivo inexistente levanta erro."""
        with pytest.raises(FileNotFoundError):
            load_config_from_file(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml(self, tmp_path):
        """YAML inválido levanta erro."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("search: { invalid yaml")

        with pytest.raises(yaml.YAMLError):
            load_config_from_file(config_file)


class TestGetConfig:
    """Testes para get_config()."""

    def test_returns_config(self):
        """get_config() retorna configuração válida."""
        config = get_config()

        assert isinstance(config, VaultSearchConfig)
        assert config.search.top_k > 0

    def test_cached(self):
        """get_config() retorna mesma instância (cached)."""
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_explicit_missing_config_fails_fast(self, tmp_path, monkeypatch):
        """Override inválido não pode cair silenciosamente nos defaults."""
        monkeypatch.setenv("VAULT_SEARCH_CONFIG", str(tmp_path / "missing.yaml"))
        get_config.cache_clear()
        try:
            with pytest.raises(FileNotFoundError, match="VAULT_SEARCH_CONFIG"):
                get_config()
        finally:
            monkeypatch.delenv("VAULT_SEARCH_CONFIG", raising=False)
            get_config.cache_clear()


class TestReloadConfig:
    """Testes para reload_config()."""

    def test_clears_cache(self):
        """reload_config() limpa o cache."""
        get_config()
        reload_config()
        config2 = get_config()

        # Após reload, deve ser nova instância
        # (na prática, pode ser igual se não houver mudança no arquivo)
        assert isinstance(config2, VaultSearchConfig)


class TestLegacyCompatibility:
    """Testes de compatibilidade com imports legados."""

    def test_paths_import(self):
        """Imports de paths funcionam."""
        from vault_search.config.paths import DATA_DIR, LANCEDB_TABLE, VAULT_PATH

        assert VAULT_PATH is not None
        assert DATA_DIR is not None
        assert LANCEDB_TABLE == "vault_chunks"

    def test_search_import(self):
        """Imports de search funcionam."""
        from vault_search.config.search import (
            FTS_LANGUAGE,
            SEARCH_CANDIDATES,
            SEARCH_TOP_K,
        )

        assert SEARCH_CANDIDATES == 50
        assert SEARCH_TOP_K == 10
        assert FTS_LANGUAGE is None

    def test_security_import(self):
        """Imports de security funcionam."""
        from vault_search.config.security import (
            MAX_QUERY_LENGTH,
            RiskLevel,
        )

        assert MAX_QUERY_LENGTH == 10_000
        assert RiskLevel.LOW.value == "low"

    def test_legacy_constants_use_yaml_on_initial_import(self, tmp_path):
        """Aliases legados devem refletir o YAML no primeiro import do processo."""
        config_path = tmp_path / "runtime.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "search": {
                        "candidates": 7,
                        "candidates_max": 91,
                        "candidates_multiplier": 3,
                        "top_k": 9,
                        "top_k_min": 2,
                        "top_k_max": 88,
                        "score_precision": 5,
                        "list_notes_default_limit": 33,
                        "list_notes_max_limit": 444,
                    },
                    "indexing": {
                        "batch_size": 17,
                        "workers": 2,
                        "max_chunks_per_note": 31,
                        "extensions": [".md", ".pdf"],
                        "ignored_folders": [".cache"],
                    },
                    "fts": {"language": None},
                    "prewarm": {
                        "enabled": False,
                        "max_ram_percent": 0.1,
                        "min_available_ram": 12345,
                        "bytes_per_chunk": 2345,
                    },
                    "chunking": {
                        "size": 900,
                        "overlap": 90,
                        "header_levels": 3,
                        "separators": ["\n", " "],
                    },
                    "security": {
                        "max_query_length": 321,
                        "max_content_size": 654,
                        "max_path_length": 111,
                        "max_frontmatter_keys": 7,
                    },
                    "watcher": {
                        "debounce": 0.25,
                        "poll_factor": 4,
                        "thread_join_timeout": 3,
                    },
                    "pdf": {
                        "ocr_enabled": False,
                        "ocr_languages": "eng",
                        "ocr_dpi": 300,
                    },
                    "vector_index": {
                        "min_chunks": 123,
                        "auto_create": False,
                        "index_type": "IVF_HNSW_SQ",
                        "num_sub_vectors": 32,
                        "distance_type": "l2",
                    },
                    "navigation": {
                        "folder_tree_max_depth": 4,
                        "folder_tree_max_depth_limit": 12,
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        script = """
import json
from vault_search.config import chunking, pdf, search, security, watcher

print(json.dumps({
    "chunking": [chunking.CHUNK_SIZE, chunking.CHUNK_OVERLAP,
                 chunking.MARKDOWN_HEADER_LEVELS, chunking.CHUNK_SEPARATORS],
    "pdf": [pdf.PDF_OCR_ENABLED, pdf.PDF_OCR_LANGUAGES, pdf.PDF_OCR_DPI],
    "watcher": [watcher.WATCHER_DEBOUNCE, watcher.WATCHER_POLL_FACTOR,
                watcher.THREAD_JOIN_TIMEOUT],
    "security": [security.MAX_QUERY_LENGTH, security.MAX_CONTENT_SIZE,
                 security.MAX_PATH_LENGTH, security.MAX_FRONTMATTER_KEYS],
    "search": [search.SEARCH_CANDIDATES, search.SEARCH_CANDIDATES_MAX,
               search.SEARCH_CANDIDATES_MULTIPLIER, search.SEARCH_TOP_K,
               search.SEARCH_TOP_K_MIN, search.SEARCH_TOP_K_MAX,
               search.SCORE_PRECISION, search.LIST_NOTES_DEFAULT_LIMIT,
               search.LIST_NOTES_MAX_LIMIT],
    "indexing": [search.REINDEX_BATCH_SIZE, search.REINDEX_WORKERS,
                 search.MAX_CHUNKS_PER_NOTE, sorted(search.INDEXABLE_EXTENSIONS),
                 sorted(search.IGNORED_FOLDERS)],
    "fts": search.FTS_LANGUAGE,
    "prewarm": [search.PREWARM_ENABLED, search.PREWARM_MAX_RAM_PERCENT,
                search.PREWARM_MIN_AVAILABLE_RAM, search.PREWARM_BYTES_PER_CHUNK],
    "navigation": [search.FOLDER_TREE_MAX_DEPTH,
                   search.FOLDER_TREE_MAX_DEPTH_LIMIT],
    "vector": [search.VECTOR_INDEX_MIN_CHUNKS, search.VECTOR_INDEX_AUTO_CREATE,
               search.VECTOR_INDEX_TYPE, search.VECTOR_INDEX_NUM_SUB_VECTORS,
               search.VECTOR_INDEX_DISTANCE_TYPE],
}))
"""
        env = os.environ.copy()
        env["VAULT_SEARCH_CONFIG"] = str(config_path)

        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        values = json.loads(result.stdout)

        assert values["chunking"] == [900, 90, 3, ["\n", " "]]
        assert values["pdf"] == [False, "eng", 300]
        assert values["watcher"] == [0.25, 4, 3]
        assert values["security"] == [321, 654, 111, 7]
        assert values["search"] == [7, 91, 3, 9, 2, 88, 5, 33, 444]
        assert values["indexing"] == [17, 2, 31, [".md", ".pdf"], [".cache"]]
        assert values["fts"] is None
        assert values["prewarm"] == [False, 0.1, 12345, 2345]
        assert values["navigation"] == [4, 12]
        assert values["vector"] == [123, False, "IVF_HNSW_SQ", 32, "l2"]


class TestConfigExampleFile:
    """Testes para o arquivo config.example.yaml."""

    def test_example_file_is_valid(self):
        """config.example.yaml deve ser YAML válido e gerar config válida."""
        example_path = Path(__file__).parent.parent / "config.example.yaml"

        if example_path.exists():
            with open(example_path) as f:
                data = yaml.safe_load(f)

            # Deve ser carregável como config válida
            config = VaultSearchConfig.model_validate(data)
            assert config.search.top_k > 0

    def test_example_operational_defaults_match_pydantic_defaults(self):
        """O exemplo pode adicionar schema, mas não mudar defaults operacionais em silêncio."""
        example_path = Path(__file__).parent.parent / "config.example.yaml"
        data = yaml.safe_load(example_path.read_text(encoding="utf-8"))

        example = VaultSearchConfig.model_validate(data).model_dump(mode="json", by_alias=True)
        defaults = VaultSearchConfig().model_dump(mode="json", by_alias=True)
        example["frontmatter"].pop("schema", None)
        defaults["frontmatter"].pop("schema", None)

        assert example == defaults


def test_folder_tree_tool_uses_configured_default(tmp_path):
    """O default publicado pela tool deve refletir navigation.folder_tree_max_depth."""
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "navigation": {
                    "folder_tree_max_depth": 4,
                    "folder_tree_max_depth_limit": 12,
                }
            }
        ),
        encoding="utf-8",
    )
    script = """
import inspect
from unittest.mock import MagicMock
from vault_search.server.search_tools import register_search_tools

class MCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function
        return decorator

mcp = MCP()
register_search_tools(mcp, MagicMock(), MagicMock())
print(inspect.signature(mcp.tools["folder_tree"]).parameters["max_depth"].default)
"""
    env = os.environ.copy()
    env["VAULT_SEARCH_CONFIG"] = str(config_path)

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.strip() == "4"
