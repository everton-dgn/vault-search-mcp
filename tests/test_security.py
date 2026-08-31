"""
Testes para funções de segurança.

Testa escape de SQL, patterns LIKE e validação de paths.
"""

from vault_search.utils.security import (
    escape_like_pattern,
    escape_sql_string,
    validate_relative_path,
)


class TestEscapeSqlString:
    """Testes para escape_sql_string()."""

    def test_escapa_aspas_simples(self):
        """Deve escapar aspas simples duplicando-as."""
        assert escape_sql_string("O'Brien") == "O''Brien"

    def test_multiplas_aspas(self):
        """Deve escapar múltiplas aspas."""
        assert escape_sql_string("a'b'c") == "a''b''c"

    def test_string_normal_nao_muda(self):
        """String sem aspas não deve mudar."""
        assert escape_sql_string("normal") == "normal"

    def test_string_vazia(self):
        """String vazia retorna vazia."""
        assert escape_sql_string("") == ""

    def test_none_retorna_none(self):
        """None retorna None (falsy)."""
        assert escape_sql_string(None) is None

    def test_aspas_no_inicio(self):
        """Aspas no início da string."""
        assert escape_sql_string("'inicio") == "''inicio"

    def test_aspas_no_fim(self):
        """Aspas no fim da string."""
        assert escape_sql_string("fim'") == "fim''"

    def test_apenas_aspas(self):
        """String com apenas aspas."""
        assert escape_sql_string("'''") == "''''''"

    def test_sql_injection_attempt(self):
        """Deve neutralizar tentativa de SQL injection."""
        # Tentativa: '; DROP TABLE users; --
        malicious = "'; DROP TABLE users; --"
        escaped = escape_sql_string(malicious)
        assert escaped == "''; DROP TABLE users; --"
        # A aspa escapada previne o injection


class TestEscapeLikePattern:
    """Testes para escape_like_pattern()."""

    def test_escapa_percent(self):
        """Deve escapar % (wildcard)."""
        assert escape_like_pattern("test%") == "test\\%"

    def test_escapa_underscore(self):
        """Deve escapar _ (single char wildcard)."""
        assert escape_like_pattern("test_") == "test\\_"

    def test_escapa_backslash(self):
        """Deve escapar backslash."""
        assert escape_like_pattern("test\\path") == "test\\\\path"

    def test_escapa_aspas(self):
        """Deve escapar aspas simples."""
        assert escape_like_pattern("test's") == "test''s"

    def test_multiplos_wildcards(self):
        """Deve escapar múltiplos wildcards."""
        result = escape_like_pattern("%test_name%")
        assert "\\%" in result
        assert "\\_" in result

    def test_string_normal(self):
        """String sem caracteres especiais não muda."""
        assert escape_like_pattern("normal") == "normal"

    def test_string_vazia(self):
        """String vazia retorna vazia."""
        assert escape_like_pattern("") == ""


class TestValidateRelativePath:
    """Testes para validate_relative_path()."""

    def test_path_simples_valido(self):
        """Path simples é válido."""
        assert validate_relative_path("file.md") is True

    def test_path_com_pasta_valido(self):
        """Path com pasta é válido."""
        assert validate_relative_path("docs/file.md") is True

    def test_path_profundo_valido(self):
        """Path com múltiplas pastas é válido."""
        assert validate_relative_path("a/b/c/d/file.md") is True

    def test_path_traversal_rejeitado(self):
        """Path traversal com .. deve ser rejeitado."""
        assert validate_relative_path("../etc/passwd") is False

    def test_path_traversal_meio_rejeitado(self):
        """Path traversal no meio deve ser rejeitado."""
        assert validate_relative_path("docs/../../../etc/passwd") is False

    def test_path_absoluto_rejeitado(self):
        """Path absoluto deve ser rejeitado."""
        assert validate_relative_path("/etc/passwd") is False

    def test_path_absoluto_windows_rejeitado(self):
        """Path absoluto Windows deve ser rejeitado."""
        assert validate_relative_path("\\Windows\\System32") is False

    def test_string_vazia_rejeitada(self):
        """String vazia deve ser rejeitada."""
        assert validate_relative_path("") is False

    def test_none_rejeitado(self):
        """None deve ser rejeitado."""
        assert validate_relative_path(None) is False

    def test_null_byte_rejeitado(self):
        """Null byte injection deve ser rejeitado."""
        assert validate_relative_path("file.md\x00.txt") is False

    def test_dot_dot_escondido(self):
        """.. escondido em path deve ser rejeitado."""
        assert validate_relative_path("docs/..") is False

    def test_path_com_espacos_valido(self):
        """Path com espaços é válido."""
        assert validate_relative_path("my docs/my file.md") is True

    def test_path_com_caracteres_unicode_valido(self):
        """Path com caracteres Unicode é válido."""
        assert validate_relative_path("notas/café.md") is True
