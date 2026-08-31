"""
Testes para o handler de eventos do sistema de arquivos.

Testa filtragem, coalescência e ignore_next_change.
"""

import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from vault_search.server.event_handler import (
    MAX_IGNORE_TOKENS,
    VaultEventHandler,
    _check_and_clear_ignore,
    _ignore_lock,
    _ignore_next_change,
    ignore_next_change,
)


class TestIgnoreNextChange:
    """Testes para funções de ignore_next_change."""

    def setup_method(self):
        """Limpa o set global antes de cada teste."""
        with _ignore_lock:
            _ignore_next_change.clear()

    def test_ignore_adds_path(self, tmp_path):
        """ignore_next_change() registra a revisão atual do arquivo."""
        note = tmp_path / "pasta" / "nota.md"
        note.parent.mkdir()
        note.write_text("original")

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            assert ignore_next_change("pasta/nota.md") is True

        with _ignore_lock:
            assert "pasta/nota.md" in _ignore_next_change

    def test_check_and_clear_returns_true_for_same_revision(self, tmp_path):
        """O evento próprio é ignorado quando a revisão ainda é idêntica."""
        note = tmp_path / "pasta" / "nota.md"
        note.parent.mkdir()
        note.write_text("original")

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("pasta/nota.md")
            result = _check_and_clear_ignore("pasta/nota.md", note)

        assert result is True
        with _ignore_lock:
            assert "pasta/nota.md" not in _ignore_next_change

    def test_check_and_clear_returns_false_if_absent(self):
        """_check_and_clear_ignore() retorna False se path não estava marcado."""
        result = _check_and_clear_ignore("pasta/outra.md")

        assert result is False

    def test_ignore_is_thread_safe(self, tmp_path):
        """Operações de ignore são thread-safe."""
        errors = []
        paths_added = []

        def add_and_check(thread_id):
            try:
                path = f"path_{thread_id}.md"
                note = tmp_path / path
                note.write_text(path)
                ignore_next_change(path)
                paths_added.append(path)
                time.sleep(0.001)  # Pequeno delay para aumentar chance de race
                _check_and_clear_ignore(path, note)
            except Exception as e:
                errors.append(e)

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            threads = [threading.Thread(target=add_and_check, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(errors) == 0
        with _ignore_lock:
            # Todos devem ter sido limpos
            assert len(_ignore_next_change) == 0

    def test_revision_posterior_nao_e_ignorada(self, tmp_path):
        """Uma edição posterior do usuário invalida o token próprio."""
        note = tmp_path / "nota.md"
        note.write_text("revisão própria")

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("nota.md")
            note.write_text("edição posterior com tamanho distinto")
            assert _check_and_clear_ignore("nota.md", note) is False

        with _ignore_lock:
            assert "nota.md" not in _ignore_next_change

    def test_evento_proprio_adiantado_nao_descarta_edicao_posterior(self, tmp_path):
        """A corrida evento, token, edição mantém a edição humana na fila."""
        note = tmp_path / "nota.md"
        note.write_text("revisão própria")
        pending = {}
        handler = VaultEventHandler(pending, threading.Lock())

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            # O watcher recebe a escrita própria antes de o token ser publicado.
            handler._enqueue(str(note))
            pending.clear()

            ignore_next_change("nota.md")
            note.write_text("edição posterior do usuário, com outra revisão")
            handler._enqueue(str(note))

        assert pending["nota.md"]["deleted"] is False
        with _ignore_lock:
            assert "nota.md" not in _ignore_next_change

    def test_tokens_expiram_e_sao_limitados(self, tmp_path):
        """A tabela purga TTL e nunca ultrapassa o limite configurado."""
        with (
            patch("vault_search.server.event_handler.VAULT_PATH", tmp_path),
            patch("vault_search.server.event_handler.MAX_IGNORE_TOKENS", 2),
            patch(
                "vault_search.server.event_handler.time.monotonic",
                side_effect=[0, 1, 2, 99],
            ),
        ):
            for index in range(3):
                path = f"{index}.md"
                (tmp_path / path).write_text(path)
                ignore_next_change(path)

            with _ignore_lock:
                assert len(_ignore_next_change) == 2
                assert "0.md" not in _ignore_next_change

            assert _check_and_clear_ignore("ausente.md", tmp_path / "ausente.md") is False

        with _ignore_lock:
            assert len(_ignore_next_change) == 0
        assert MAX_IGNORE_TOKENS >= 1


class TestVaultEventHandlerInit:
    """Testes para inicialização do VaultEventHandler."""

    def test_init_stores_references(self):
        """Inicialização armazena referências ao pending e lock."""
        pending = {}
        lock = threading.Lock()

        handler = VaultEventHandler(pending, lock)

        assert handler._pending is pending
        assert handler._lock is lock


class TestVaultEventHandlerShouldProcess:
    """Testes para _should_process()."""

    @pytest.fixture
    def handler(self):
        return VaultEventHandler({}, threading.Lock())

    def test_markdown_file(self, handler):
        """Arquivo .md deve ser processado."""
        assert handler._should_process("/vault/nota.md") is True

    def test_pdf_file(self, handler):
        """Arquivo .pdf deve ser processado."""
        assert handler._should_process("/vault/doc.pdf") is True

    def test_canvas_file(self, handler):
        """Arquivo .canvas deve ser processado."""
        assert handler._should_process("/vault/mapa.canvas") is True

    def test_txt_file(self, handler):
        """Arquivo .txt DEVE ser processado (está em INDEXABLE_EXTENSIONS)."""
        assert handler._should_process("/vault/readme.txt") is True

    def test_image_file(self, handler):
        """Arquivo de imagem NÃO deve ser processado."""
        assert handler._should_process("/vault/image.png") is False

    def test_hidden_extension(self, handler):
        """Extensão oculta NÃO deve ser processado."""
        assert handler._should_process("/vault/.hidden.md") is True  # Extensão é .md
        assert handler._should_process("/vault/.config") is False

    def test_case_insensitive_extension(self, handler):
        """Extensão case-insensitive."""
        assert handler._should_process("/vault/nota.MD") is True
        assert handler._should_process("/vault/doc.PDF") is True

    def test_trash_folder(self, handler):
        """Pasta .trash deve ser ignorada."""
        assert handler._should_process("/vault/.trash/nota.md") is False

    def test_obsidian_folder(self, handler):
        """Pasta .obsidian deve ser ignorada."""
        assert handler._should_process("/vault/.obsidian/plugins.md") is False

    def test_smart_env_folder(self, handler):
        """Pasta .smart-env deve ser ignorada."""
        assert handler._should_process("/vault/.smart-env/config.md") is False

    def test_nested_ignored_folder(self, handler):
        """Pasta ignorada aninhada deve ser ignorada."""
        assert handler._should_process("/vault/projeto/.obsidian/plugins/nota.md") is False

    def test_valid_nested_path(self, handler):
        """Pasta válida aninhada deve ser processada."""
        assert handler._should_process("/vault/projetos/docs/nota.md") is True


class TestVaultEventHandlerEnqueue:
    """Testes para _enqueue()."""

    def setup_method(self):
        """Limpa ignore set antes de cada teste."""
        with _ignore_lock:
            _ignore_next_change.clear()

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_adds_to_pending(self, handler_with_pending):
        """_enqueue() adiciona evento ao pending."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/pasta/nota.md")

        assert "pasta/nota.md" in pending
        assert pending["pasta/nota.md"]["deleted"] is False

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_deleted_flag(self, handler_with_pending):
        """_enqueue() com deleted=True marca como deletado."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/nota.md", deleted=True)

        assert pending["nota.md"]["deleted"] is True

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_coalescence(self, handler_with_pending):
        """_enqueue() coalescente - último vence."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/nota.md", deleted=False)
        time1 = pending["nota.md"]["time"]

        time.sleep(0.01)
        handler._enqueue("/vault/nota.md", deleted=True)
        time2 = pending["nota.md"]["time"]

        # Último evento sobrescreve
        assert pending["nota.md"]["deleted"] is True
        assert time2 > time1

    def test_enqueue_ignores_marked_path(self, handler_with_pending, tmp_path):
        """_enqueue() ignora path marcado com ignore_next_change."""
        handler, pending = handler_with_pending
        note = tmp_path / "nota.md"
        note.write_text("revisão própria")

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("nota.md")
            handler._enqueue(str(note))

        assert "nota.md" not in pending

    def test_enqueue_clears_ignore_after_use(self, handler_with_pending, tmp_path):
        """_enqueue() limpa ignore após usar."""
        handler, pending = handler_with_pending
        note = tmp_path / "nota.md"
        note.write_text("revisão própria")

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("nota.md")
            handler._enqueue(str(note))  # Ignora e limpa

            # Segunda vez deve ser processada porque o token foi consumido.
            handler._enqueue(str(note))
        assert "nota.md" in pending

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/other"))
    def test_enqueue_outside_vault(self, handler_with_pending):
        """_enqueue() ignora arquivos fora do vault."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/nota.md")  # VAULT_PATH é /other

        assert len(pending) == 0

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_records_time(self, handler_with_pending):
        """_enqueue() registra timestamp monotônico."""
        handler, pending = handler_with_pending

        before = time.monotonic()
        handler._enqueue("/vault/nota.md")
        after = time.monotonic()

        assert before <= pending["nota.md"]["time"] <= after

    def test_enqueue_resolves_symlink_root(self, tmp_path):
        """_enqueue() aceita evento no path real quando VAULT_PATH é symlink."""
        real_vault = tmp_path / "real_vault"
        real_vault.mkdir()
        symlink_vault = tmp_path / "vault_link"
        symlink_vault.symlink_to(real_vault, target_is_directory=True)

        pending = {}
        lock = threading.Lock()

        with patch("vault_search.server.event_handler.VAULT_PATH", symlink_vault):
            handler = VaultEventHandler(pending, lock)
            handler._enqueue(str(real_vault / "notas" / "teste.md"))

        assert "notas/teste.md" in pending


class TestVaultEventHandlerOnCreated:
    """Testes para on_created()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_file(self, handler_with_pending):
        """on_created() processa arquivo válido."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/nova.md"

        handler.on_created(event)

        assert "nova.md" in pending
        assert pending["nova.md"]["deleted"] is False

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_directory(self, handler_with_pending):
        """on_created() ignora diretórios."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = True
        event.src_path = "/vault/pasta"

        handler.on_created(event)

        assert len(pending) == 0

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_invalid_extension(self, handler_with_pending):
        """on_created() ignora extensão inválida."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/image.png"

        handler.on_created(event)

        assert len(pending) == 0


class TestVaultEventHandlerOnModified:
    """Testes para on_modified()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_modified_file(self, handler_with_pending):
        """on_modified() processa arquivo válido."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/nota.md"

        handler.on_modified(event)

        assert "nota.md" in pending
        assert pending["nota.md"]["deleted"] is False

    def test_on_modified_with_ignore(self, handler_with_pending, tmp_path):
        """on_modified() respeita ignore_next_change."""
        handler, pending = handler_with_pending
        note = tmp_path / "nota.md"
        note.write_text("revisão própria")

        event = Mock()
        event.is_directory = False
        event.src_path = str(note)

        with patch("vault_search.server.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("nota.md")
            handler.on_modified(event)

        assert "nota.md" not in pending


class TestVaultEventHandlerOnDeleted:
    """Testes para on_deleted()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_deleted_file(self, handler_with_pending):
        """on_deleted() processa arquivo válido com deleted=True."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/deletada.md"

        handler.on_deleted(event)

        assert "deletada.md" in pending
        assert pending["deletada.md"]["deleted"] is True


class TestVaultEventHandlerOnMoved:
    """Testes para on_moved()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_both_valid(self, handler_with_pending):
        """on_moved() processa src como deleted e dest como criado."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/antiga.md"
        event.dest_path = "/vault/nova.md"

        handler.on_moved(event)

        assert "antiga.md" in pending
        assert pending["antiga.md"]["deleted"] is True
        assert "nova.md" in pending
        assert pending["nova.md"]["deleted"] is False

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_src_invalid(self, handler_with_pending):
        """on_moved() ignora src inválido."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/image.png"
        event.dest_path = "/vault/nova.md"

        handler.on_moved(event)

        assert "image.png" not in pending
        assert "nova.md" in pending

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_dest_invalid(self, handler_with_pending):
        """on_moved() ignora dest inválido (extensão não indexável)."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/nota.md"
        event.dest_path = "/vault/arquivo.json"  # .json não é indexável

        handler.on_moved(event)

        assert "nota.md" in pending
        assert pending["nota.md"]["deleted"] is True
        assert "arquivo.json" not in pending

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_to_trash(self, handler_with_pending):
        """on_moved() para .trash processa src como deleted."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/nota.md"
        event.dest_path = "/vault/.trash/nota.md"

        handler.on_moved(event)

        assert "nota.md" in pending
        assert pending["nota.md"]["deleted"] is True
        assert ".trash/nota.md" not in pending

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_directory(self, handler_with_pending):
        """on_moved() ignora diretórios."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = True
        event.src_path = "/vault/pasta"
        event.dest_path = "/vault/outra"

        handler.on_moved(event)

        assert len(pending) == 0


class TestVaultEventHandlerConcurrency:
    """Testes de concorrência do handler."""

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.server.event_handler.VAULT_PATH", Path("/vault"))
    def test_concurrent_events(self):
        """Handler é thread-safe para eventos concorrentes."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        errors = []

        def emit_events(thread_id):
            try:
                for i in range(10):
                    event = Mock()
                    event.is_directory = False
                    event.src_path = f"/vault/nota_{thread_id}_{i}.md"
                    handler.on_modified(event)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_events, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # 5 threads x 10 eventos = 50 entradas
        assert len(pending) == 50
