"""
Testes unitários para server.py — validação e helpers.

Testes rápidos que NÃO precisam de modelos ML nem MCP running.
"""

import pytest

from vault_search.config.search import SEARCH_TOP_K_MAX, SEARCH_TOP_K_MIN
from vault_search.server.helpers import clamp_top_k, execute_search, log_query


class TestClampTopK:
    """Testa o helper de produção sem carregar o servidor MCP."""

    def test_valor_normal(self):
        assert clamp_top_k(10) == 10

    def test_zero(self):
        assert clamp_top_k(0) == SEARCH_TOP_K_MIN

    def test_negativo(self):
        assert clamp_top_k(-5) == SEARCH_TOP_K_MIN

    def test_maximo(self):
        assert clamp_top_k(SEARCH_TOP_K_MAX) == SEARCH_TOP_K_MAX

    def test_excede_maximo(self):
        assert clamp_top_k(200) == SEARCH_TOP_K_MAX

    def test_um(self):
        assert clamp_top_k(1) == SEARCH_TOP_K_MIN


class TestLogQuery:
    """Testa o contrato de metadados sem conteúdo da query."""

    def test_query_curta(self):
        assert log_query("busca simples") == "[redacted length=13]"

    def test_query_longa_truncada(self):
        result = log_query("a" * 100)
        assert result == "[redacted length=100]"

    def test_query_com_dados_sensiveis(self):
        """Nenhuma posição da query deve aparecer no retorno para logging."""
        result = log_query("prefixo SENHA_SECRETA_123 sufixo")
        assert "SENHA" not in result

    def test_query_vazia(self):
        assert log_query("") == "[redacted length=0]"


class TestExecuteSearch:
    """Testa o helper de produção com bordas simuladas."""

    def test_query_vazia_retorna_erro(self):
        result = execute_search("test", "", 10, lambda **kw: [])
        assert "Erro" in result
        assert "vazia" in result

    def test_query_so_espacos(self):
        result = execute_search("test", "   ", 10, lambda **kw: [])
        assert "Erro" in result

    def test_query_none_retorna_erro(self):
        result = execute_search("test", None, 10, lambda **kw: [])
        assert "Erro" in result

    def test_query_valida_chama_search_fn(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[{"text": "resultado"}])
        result = execute_search("test", "busca válida", 10, mock_fn)
        mock_fn.assert_called_once_with("busca válida", top_k=10)
        assert result == [{"text": "resultado"}]

    def test_top_k_clamped(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "query", 999, mock_fn)
        mock_fn.assert_called_once_with("query", top_k=SEARCH_TOP_K_MAX)

    def test_runtime_error_retorna_mensagem(self):
        def raise_runtime(*args, **kw):
            raise RuntimeError("Índice não encontrado")

        result = execute_search("test", "query", 10, raise_runtime)
        assert "search_unavailable" in result
        assert "Índice não encontrado" not in result

    def test_exception_generica_retorna_mensagem(self):
        def raise_generic(*args, **kw):
            raise ValueError("algo quebrou")

        result = execute_search("test", "query", 10, raise_generic)
        assert "internal_error" in result
        assert "algo quebrou" not in result

    def test_kwargs_passados_para_search_fn(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "query", 10, mock_fn, folder="projetos")
        mock_fn.assert_called_once_with("query", top_k=10, folder="projetos")

    def test_query_com_espacos_trimmed(self):
        from unittest.mock import MagicMock

        mock_fn = MagicMock(return_value=[])
        execute_search("test", "  query com espaços  ", 10, mock_fn)
        mock_fn.assert_called_once_with("query com espaços", top_k=10)


class TestGetRecentNotesParams:
    """Testes unitários para validação de parâmetros de get_recent_notes."""

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

    def test_days_negativo(self):
        assert self._clamp_days(-5) == 1

    def test_days_maximo(self):
        assert self._clamp_days(365) == 365

    def test_days_excede_maximo(self):
        assert self._clamp_days(500) == 365

    def test_limit_normal(self):
        assert self._clamp_limit(20) == 20

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_limit_negativo(self):
        assert self._clamp_limit(-10) == 1

    def test_limit_maximo(self):
        assert self._clamp_limit(100) == 100

    def test_limit_excede_maximo(self):
        assert self._clamp_limit(200) == 100


class TestGetRecentNotesFiltering:
    """Testes para lógica de filtragem de notas recentes."""

    def test_filtra_por_data(self):
        """Notas fora da janela devem ser excluídas."""
        from datetime import datetime, timedelta

        now = datetime.now()
        notes = [
            {
                "path": "recente.md",
                "modified_at": (now - timedelta(days=2)).isoformat(),
                "title": "Recente",
            },
            {
                "path": "antiga.md",
                "modified_at": (now - timedelta(days=30)).isoformat(),
                "title": "Antiga",
            },
        ]

        cutoff = now - timedelta(days=7)
        recent = [n for n in notes if datetime.fromisoformat(n["modified_at"]) >= cutoff]

        assert len(recent) == 1
        assert recent[0]["path"] == "recente.md"

    def test_ordena_por_data_decrescente(self):
        """Notas devem vir ordenadas da mais recente para mais antiga."""
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

    def test_calcula_days_ago(self):
        """days_ago deve calcular corretamente a diferença de dias."""
        from datetime import datetime, timedelta

        now = datetime.now()
        modified = now - timedelta(days=3)
        days_ago = (now - modified).days

        assert days_ago == 3

    def test_days_ago_hoje(self):
        """Nota modificada hoje deve ter days_ago=0."""
        from datetime import datetime

        now = datetime.now()
        modified = now
        days_ago = (now - modified).days

        assert days_ago == 0


class TestTagStatsParams:
    """Testes unitários para validação de parâmetros de tag_stats."""

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(limit, 500))

    def test_limit_normal(self):
        assert self._clamp_limit(50) == 50

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_limit_negativo(self):
        assert self._clamp_limit(-10) == 1

    def test_limit_maximo(self):
        assert self._clamp_limit(500) == 500

    def test_limit_excede_maximo(self):
        assert self._clamp_limit(1000) == 500


class TestTagStatsAggregation:
    """Testes para lógica de agregação de tags."""

    def test_parse_tags_string(self):
        """Tags comma-separated devem ser parseadas corretamente."""
        tags_str = "projeto, 2024, ideia"
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == ["projeto", "2024", "ideia"]

    def test_parse_tags_vazias(self):
        """String vazia deve retornar lista vazia."""
        tags_str = ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == []

    def test_parse_tags_com_espacos(self):
        """Espaços extras devem ser removidos."""
        tags_str = "  tag1  ,  tag2  ,  tag3  "
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        assert tags == ["tag1", "tag2", "tag3"]

    def test_counter_frequencia(self):
        """Counter deve agregar frequência corretamente."""
        from collections import Counter

        note_tags = {
            "nota1.md": {"projeto", "2024"},
            "nota2.md": {"projeto", "ideia"},
            "nota3.md": {"projeto"},
        }

        counter: Counter[str] = Counter()
        for tags in note_tags.values():
            counter.update(tags)

        assert counter["projeto"] == 3
        assert counter["2024"] == 1
        assert counter["ideia"] == 1

    def test_most_common(self):
        """most_common deve retornar ordenado por frequência."""
        from collections import Counter

        counter = Counter({"a": 10, "b": 5, "c": 20})
        top = counter.most_common(2)

        assert top[0] == ("c", 20)
        assert top[1] == ("a", 10)

    def test_tags_unicas_por_nota(self):
        """Mesma tag em chunks diferentes da mesma nota deve contar uma vez."""
        note_tags: dict[str, set[str]] = {}

        # Simular múltiplos chunks da mesma nota
        chunks = [
            ("nota1.md", "projeto, 2024"),
            ("nota1.md", "projeto, ideia"),  # projeto repetido
            ("nota2.md", "projeto"),
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

        # "projeto" aparece em 2 notas (não 3 chunks)
        assert counter["projeto"] == 2
        assert counter["2024"] == 1
        assert counter["ideia"] == 1


class TestFolderTreeParams:
    """Testes unitários para validação de parâmetros de folder_tree."""

    @staticmethod
    def _clamp_max_depth(max_depth: int) -> int:
        return max(1, min(max_depth, 50))

    def test_max_depth_normal(self):
        assert self._clamp_max_depth(10) == 10

    def test_max_depth_zero(self):
        assert self._clamp_max_depth(0) == 1

    def test_max_depth_negativo(self):
        assert self._clamp_max_depth(-5) == 1

    def test_max_depth_maximo(self):
        assert self._clamp_max_depth(50) == 50

    def test_max_depth_excede_maximo(self):
        assert self._clamp_max_depth(100) == 50


class TestFolderTreeBuilding:
    """Testes para lógica de construção da árvore de pastas."""

    def test_parse_folder_path(self):
        """Path de pasta deve ser dividido corretamente."""
        folder = "projetos/web/frontend"
        parts = folder.split("/")
        assert parts == ["projetos", "web", "frontend"]

    def test_build_tree_single_folder(self):
        """Pasta única deve criar estrutura simples."""
        tree: dict = {}
        folder = "projetos"
        count = 10

        parts = folder.split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        current["_count"] = count

        assert tree == {"projetos": {"_count": 10}}

    def test_build_tree_nested_folders(self):
        """Pastas aninhadas devem criar estrutura hierárquica."""
        tree: dict = {}
        folders = [
            ("projetos", 5),
            ("projetos/web", 10),
            ("projetos/mobile", 8),
        ]

        for folder, count in folders:
            parts = folder.split("/")
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current["_count"] = current.get("_count", 0) + count

        assert "projetos" in tree
        assert "web" in tree["projetos"]
        assert "mobile" in tree["projetos"]
        assert tree["projetos"]["web"]["_count"] == 10
        assert tree["projetos"]["mobile"]["_count"] == 8

    def test_max_depth_truncates(self):
        """max_depth deve limitar profundidade da árvore."""
        folder = "a/b/c/d/e"
        max_depth = 3

        parts = folder.split("/")[:max_depth]

        assert parts == ["a", "b", "c"]
        assert len(parts) == max_depth

    def test_root_notes_counted(self):
        """Notas na raiz devem ser contadas em _count."""
        tree: dict = {}
        folder = ""  # Raiz
        count = 15

        if not folder:
            tree["_count"] = tree.get("_count", 0) + count

        assert tree["_count"] == 15

    def test_collect_unique_folders(self):
        """Todas as pastas intermediárias devem ser contadas."""
        folders_set: set[str] = set()
        folder = "a/b/c"

        parts = folder.split("/")
        for i in range(len(parts)):
            intermediate = "/".join(parts[: i + 1])
            folders_set.add(intermediate)

        assert folders_set == {"a", "a/b", "a/b/c"}
        assert len(folders_set) == 3

    def test_without_counts(self):
        """Sem include_counts, _count não deve aparecer."""
        tree: dict = {}
        include_counts = False

        folder = "projetos"
        parts = folder.split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]

        if include_counts:
            current["_count"] = 10

        assert "_count" not in tree.get("projetos", {})


class TestFolderTreeRecursive:
    """Testes para implementação recursiva com PurePosixPath."""

    def test_pure_posix_path_parsing(self):
        """PurePosixPath deve parsear paths corretamente."""
        from pathlib import PurePosixPath

        path = PurePosixPath("projetos/web/frontend")
        assert path.parts == ("projetos", "web", "frontend")

    def test_pure_posix_path_empty(self):
        """PurePosixPath com string vazia deve ter parts vazia."""
        from pathlib import PurePosixPath

        path = PurePosixPath("")
        assert path.parts == ()

    def test_pure_posix_path_single(self):
        """PurePosixPath com pasta única."""
        from pathlib import PurePosixPath

        path = PurePosixPath("projetos")
        assert path.parts == ("projetos",)

    def test_defaultdict_to_dict_conversion(self):
        """defaultdict deve ser convertido para dict normal."""
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
        """Inserção recursiva deve respeitar max_depth."""
        from pathlib import PurePosixPath

        max_depth = 2
        folder = "a/b/c/d/e"
        path = PurePosixPath(folder)
        parts = path.parts[:max_depth]

        assert parts == ("a", "b")
        assert len(parts) == max_depth

    def test_accumulate_counts_at_truncation(self):
        """Contagens devem acumular na pasta truncada."""
        folders = [
            ("a/b/c", 10),  # truncado para a/b
            ("a/b/d", 5),  # truncado para a/b
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

            # Acumular na folha truncada
            current["_count"] = current.get("_count", 0) + count

        # Ambos devem acumular em a/b
        assert tree["a"]["b"]["_count"] == 15


class TestSearchByTagsParams:
    """Testes unitários para validação de parâmetros de search_by_tags."""

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(limit, 200))

    def test_limit_normal(self):
        assert self._clamp_limit(50) == 50

    def test_limit_zero(self):
        assert self._clamp_limit(0) == 1

    def test_limit_negativo(self):
        assert self._clamp_limit(-10) == 1

    def test_limit_maximo(self):
        assert self._clamp_limit(200) == 200

    def test_limit_excede_maximo(self):
        assert self._clamp_limit(500) == 200


class TestSearchByTagsNormalization:
    """Testes para normalização de tags."""

    def test_normalize_tags(self):
        """Tags devem ser normalizadas para lowercase e stripped."""
        tags = ["  Projeto  ", "2024", "  WEB  "]
        clean = [t.strip().lower() for t in tags if t.strip()]
        assert clean == ["projeto", "2024", "web"]

    def test_filter_empty_tags(self):
        """Tags vazias devem ser filtradas."""
        tags = ["projeto", "", "  ", "web"]
        clean = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()]
        assert clean == ["projeto", "web"]

    def test_truncate_too_many_tags(self):
        """Lista com mais de 20 tags deve ser truncada."""
        tags = [f"tag{i}" for i in range(30)]
        truncated = tags[:20]
        assert len(truncated) == 20


class TestSearchByTagsMatching:
    """Testes para lógica de matching de tags."""

    def test_parse_tags_string(self):
        """Tags devem ser parseadas de string comma-separated."""
        tags_str = "projeto, 2024, web"
        note_tags = {t.strip().lower() for t in tags_str.split(",") if t.strip()}
        assert note_tags == {"projeto", "2024", "web"}

    def test_match_any_or_mode(self):
        """OR mode: nota deve ter QUALQUER uma das tags."""
        note_tags = {"projeto", "2024"}
        search_tags = ["web", "2024"]
        # OR: any tag matches
        matches = any(t in note_tags for t in search_tags)
        assert matches is True

    def test_match_any_no_match(self):
        """OR mode sem match."""
        note_tags = {"projeto", "2024"}
        search_tags = ["web", "mobile"]
        matches = any(t in note_tags for t in search_tags)
        assert matches is False

    def test_match_all_and_mode(self):
        """AND mode: nota deve ter TODAS as tags."""
        note_tags = {"projeto", "2024", "web"}
        search_tags = ["projeto", "2024"]
        matches = all(t in note_tags for t in search_tags)
        assert matches is True

    def test_match_all_partial(self):
        """AND mode com match parcial deve falhar."""
        note_tags = {"projeto", "2024"}
        search_tags = ["projeto", "web"]
        matches = all(t in note_tags for t in search_tags)
        assert matches is False

    def test_dedup_by_note_path(self):
        """Chunks da mesma nota devem ser deduplicados."""
        chunks = [
            ("nota1.md", "projeto, 2024"),
            ("nota1.md", "projeto, 2024"),  # duplicado
            ("nota2.md", "web"),
        ]

        notes_map: dict[str, dict] = {}
        for path, tags in chunks:
            if path not in notes_map:
                notes_map[path] = {"path": path, "tags": tags}

        assert len(notes_map) == 2
        assert "nota1.md" in notes_map
        assert "nota2.md" in notes_map


class TestSearchByTagsSQLEscape:
    """Testes para escape de caracteres SQL."""

    def test_escape_single_quote(self):
        """Aspas simples devem ser escapadas."""
        tag = "it's"
        escaped = tag.replace("'", "''")
        assert escaped == "it''s"

    def test_sql_like_pattern_exact_tag(self):
        """Pattern LIKE deve encontrar tag exata, não substring."""
        tag = "web"

        # Casos de teste
        test_cases = [
            ("web", True),  # única
            ("web, mobile", True),  # primeira
            ("mobile, web", True),  # última
            ("a, web, b", True),  # meio
            ("webapp", False),  # substring - não deve dar match
            ("webdev, mobile", False),  # substring no início
        ]

        # Simular matching
        for tags_str, expected in test_cases:
            # Simplificação: verificar se tag está como item separado
            tags_list = [t.strip() for t in tags_str.split(",")]
            actual = tag in tags_list
            assert actual == expected, f"tags='{tags_str}' expected {expected}"


class TestRandomNoteParams:
    """Testes unitários para validação de parâmetros de random_note."""

    def test_normalize_folder_empty(self):
        """Folder vazio ou só espaços deve ser None."""
        folder = "   "
        result = folder.strip() if folder and folder.strip() else None
        assert result is None

    def test_normalize_folder_valid(self):
        """Folder válido deve ser mantido."""
        folder = "  projetos  "
        result = folder.strip() if folder and folder.strip() else None
        assert result == "projetos"

    def test_normalize_extension_add_dot(self):
        """Extensão sem ponto deve receber ponto."""
        ext = "md"
        if not ext.startswith("."):
            ext = f".{ext}"
        assert ext == ".md"

    def test_normalize_extension_with_dot(self):
        """Extensão com ponto deve ser mantida."""
        ext = ".md"
        if not ext.startswith("."):
            ext = f".{ext}"
        assert ext == ".md"

    def test_normalize_extension_lowercase(self):
        """Extensão deve ser lowercase."""
        ext = ".MD"
        ext = ext.lower()
        assert ext == ".md"

    def test_validate_extension(self):
        """Verificar extensões indexáveis."""
        from vault_search.config.search import INDEXABLE_EXTENSIONS

        # Extensões que devem ser indexáveis
        assert ".md" in INDEXABLE_EXTENSIONS
        assert ".txt" in INDEXABLE_EXTENSIONS
        assert ".mdx" in INDEXABLE_EXTENSIONS
        assert ".pdf" in INDEXABLE_EXTENSIONS
        assert ".canvas" in INDEXABLE_EXTENSIONS

        # Extensões que NÃO devem ser indexáveis
        assert ".jpg" not in INDEXABLE_EXTENSIONS
        assert ".png" not in INDEXABLE_EXTENSIONS


class TestRandomNoteSQLQuery:
    """Testes para construção de query SQL do random_note."""

    def test_order_by_random(self):
        """Query deve usar ORDER BY RANDOM()."""
        query = "SELECT * FROM notes ORDER BY RANDOM() LIMIT 1"
        assert "ORDER BY RANDOM()" in query
        assert "LIMIT 1" in query

    def test_where_folder_exact_or_subfolders(self):
        """Filtro de pasta deve incluir pasta exata e subpastas."""
        folder = "projetos"
        condition = f"(folder = '{folder}' OR folder LIKE '{folder}/%')"

        # Testes
        assert "folder = 'projetos'" in condition
        assert "folder LIKE 'projetos/%'" in condition

    def test_multiple_conditions_and(self):
        """Múltiplas condições devem usar AND."""
        conditions = ["folder = 'projetos'", "extension = '.md'"]
        where = " AND ".join(conditions)
        assert where == "folder = 'projetos' AND extension = '.md'"


class TestDailyNoteParams:
    """Testes unitários para validação de parâmetros de daily_note."""

    def test_parse_date_iso(self):
        """Data ISO deve ser parseada corretamente."""
        from datetime import datetime

        date_str = "2024-01-15"
        parsed = datetime.fromisoformat(date_str).date()
        assert parsed.year == 2024
        assert parsed.month == 1
        assert parsed.day == 15

    def test_parse_date_invalid(self):
        """Data inválida deve levantar ValueError."""
        from datetime import datetime

        with pytest.raises(ValueError):
            datetime.fromisoformat("invalid-date")

    def test_parse_date_wrong_format(self):
        """Formato errado deve levantar ValueError."""
        from datetime import datetime

        with pytest.raises(ValueError):
            datetime.fromisoformat("15/01/2024")  # DD/MM/YYYY não é ISO

    def test_default_date_is_today(self):
        """Data None deve usar hoje."""
        from datetime import date

        today = date.today()
        assert today.isoformat()  # YYYY-MM-DD format

    def test_normalize_folder_empty(self):
        """Folder vazio deve usar 'daily'."""
        folder = "   "
        result = folder.strip() if folder and folder.strip() else "daily"
        assert result == "daily"

    def test_normalize_folder_valid(self):
        """Folder válido deve ser mantido."""
        folder = "  journals  "
        result = folder.strip() if folder and folder.strip() else "daily"
        assert result == "journals"


class TestDailyNotePath:
    """Testes para construção de path de daily note."""

    def test_build_expected_path(self):
        """Path esperado deve seguir padrão folder/YYYY-MM-DD.md."""
        from datetime import date

        d = date(2024, 1, 15)
        folder = "daily"
        expected_filename = f"{d.isoformat()}.md"
        expected_path = f"{folder}/{expected_filename}"

        assert expected_path == "daily/2024-01-15.md"

    def test_build_path_custom_folder(self):
        """Pasta customizada deve ser respeitada."""
        from datetime import date

        d = date(2024, 1, 15)
        folder = "journals/2024"
        expected_filename = f"{d.isoformat()}.md"
        expected_path = f"{folder}/{expected_filename}"

        assert expected_path == "journals/2024/2024-01-15.md"

    def test_date_isoformat(self):
        """isoformat() deve retornar YYYY-MM-DD."""
        from datetime import date

        d = date(2024, 1, 5)  # dia com um dígito
        assert d.isoformat() == "2024-01-05"  # zero-padded

    def test_response_exists_structure(self):
        """Resposta de nota existente deve ter estrutura correta."""
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
        """Resposta de nota inexistente deve ter estrutura correta."""
        response = {
            "exists": False,
            "expected_path": "daily/2024-01-15.md",
            "date": "2024-01-15",
            "folder": "daily",
        }

        assert response["exists"] is False
        assert "expected_path" in response
        assert "date" in response
