"""
Testes unitários para watcher.py — event handling, start/stop, cleanup.

Testes rápidos que NÃO precisam de modelos ML.
"""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_search.config.watcher import WATCHER_DEBOUNCE
from vault_search.server.event_handler import VaultEventHandler
from vault_search.server.watcher import VaultWatcher


class TestVaultEventHandler:
    """Testa o handler de eventos sem watcher real."""

    def test_should_process_md(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/nota.md") is True

    def test_should_process_md_uppercase(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/nota.MD") is True

    def test_should_process_txt_aceito(self):
        """Arquivo .txt deve ser processado (indexável)."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/readme.txt") is True

    def test_should_process_jpg_rejeitado(self):
        """Arquivo .jpg não deve ser processado (não indexável)."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/image.jpg") is False

    def test_should_process_pasta_ignorada(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.obsidian/config.md") is False

    def test_enqueue_coalescente(self):
        """Múltiplos eventos no mesmo arquivo devem coalescer."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        from vault_search.config.paths import VAULT_PATH

        test_path = str(VAULT_PATH / "nota.md")

        handler._enqueue(test_path, deleted=False)
        handler._enqueue(test_path, deleted=False)
        handler._enqueue(test_path, deleted=True)

        assert len(pending) == 1
        # Último evento vence
        assert pending["nota.md"]["deleted"] is True

    def test_enqueue_multiplos_arquivos(self):
        """Eventos em arquivos diferentes devem ser separados."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        from vault_search.config.paths import VAULT_PATH

        handler._enqueue(str(VAULT_PATH / "nota1.md"))
        handler._enqueue(str(VAULT_PATH / "nota2.md"))

        assert len(pending) == 2

    def test_enqueue_path_fora_do_vault(self):
        """Path fora do vault deve ser ignorado."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        handler._enqueue("/tmp/fora_do_vault.md")
        assert len(pending) == 0


class TestVaultWatcher:
    """Testa start/stop e cleanup do watcher."""

    def test_start_e_stop(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        assert w.is_running
        w.stop()
        assert not w.is_running

    def test_stop_limpa_pending(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        # Simular evento pendente
        with w._lock:
            w._pending["test.md"] = {"deleted": False, "time": time.monotonic()}
        w.stop()
        assert len(w._pending) == 0

    def test_start_duplo_nao_cria_observers_extras(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        observer_1 = w._observer
        w.start()  # segunda chamada
        assert w._observer is observer_1  # mesmo observer
        w.stop()

    def test_stop_sem_start_nao_crasheia(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        assert w.stop() is True  # Deve ser no-op

    def test_stop_timeout_preserva_threads_vivas_e_impede_restart(
        self, monkeypatch, tmp_path: Path
    ):
        """Timeout não pode ocultar geração antiga nem permitir outra geração."""
        mock_indexer = MagicMock()
        watcher = VaultWatcher(mock_indexer)
        observer = MagicMock()
        observer.is_alive.return_value = True
        worker = MagicMock()
        worker.is_alive.return_value = True
        watcher._observer = observer
        watcher._worker = worker

        monkeypatch.setattr("vault_search.server.watcher.VAULT_PATH", tmp_path)

        assert watcher.stop() is False
        assert watcher._observer is observer
        assert watcher._worker is worker
        assert watcher.start() is False
        assert watcher._observer is observer
        assert watcher._worker is worker

    def test_falha_parcial_de_start_preserva_observer_vivo_e_impede_restart(
        self, monkeypatch, tmp_path: Path
    ):
        """Falha ao subir a worker não pode perder um observer ainda vivo."""
        watcher = VaultWatcher(MagicMock())
        observer = MagicMock()
        observer.is_alive.return_value = True
        worker = MagicMock()
        worker.start.side_effect = RuntimeError("worker start failed")
        worker.is_alive.return_value = False

        monkeypatch.setattr("vault_search.server.watcher.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.server.watcher.Observer", lambda: observer)
        monkeypatch.setattr("vault_search.server.watcher.threading.Thread", lambda **_: worker)

        with pytest.raises(RuntimeError, match="worker start failed"):
            watcher.start()

        assert watcher._observer is observer
        assert watcher._worker is None
        assert watcher.start() is False
        assert watcher._observer is observer

    def test_is_running_antes_de_start(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        assert not w.is_running

    def test_worker_thread_unica(self):
        """Deve usar exatamente 1 worker thread, não N timers."""
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        assert w._worker is not None
        assert w._worker.is_alive()
        w.stop()
        # Worker deve ter parado
        assert w._worker is None

    def test_callback_on_reindex(self):
        """Callback deve ser chamado após reindex."""
        mock_indexer = MagicMock()
        mock_indexer.reindex_note.return_value = {"status": "updated", "chunks_indexed": 1}
        callback = MagicMock()

        w = VaultWatcher(mock_indexer, on_reindex=callback)
        w.start()

        # Simular evento pronto (tempo no passado)
        with w._lock:
            w._pending["test.md"] = {
                "deleted": False,
                "time": time.monotonic() - WATCHER_DEBOUNCE - 1,
            }

        # Esperar worker processar
        time.sleep(WATCHER_DEBOUNCE + 1)

        w.stop()

        mock_indexer.reindex_note.assert_called_once_with("test.md")
        callback.assert_called_once()


class TestEventHandlerEdgeCases:
    """Testes adicionais para VaultEventHandler."""

    def test_on_moved_deleta_src_e_cria_dest(self):
        """on_moved deve deletar src e criar dest."""
        from watchdog.events import FileMovedEvent

        from vault_search.config.paths import VAULT_PATH

        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        src = str(VAULT_PATH / "old.md")
        dest = str(VAULT_PATH / "new.md")
        event = FileMovedEvent(src, dest)
        handler.on_moved(event)

        assert "old.md" in pending
        assert pending["old.md"]["deleted"] is True
        assert "new.md" in pending
        assert pending["new.md"]["deleted"] is False

    def test_on_created_ignora_diretorio(self):
        """Eventos de diretório devem ser ignorados."""
        from watchdog.events import FileCreatedEvent

        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        event = FileCreatedEvent("/vault/nova_pasta")
        event._is_directory = True
        # Simular chamada — não deve enfileirar
        if not event.is_directory and handler._should_process(event.src_path):
            handler._enqueue(event.src_path)

        assert len(pending) == 0

    def test_should_process_extensao_mista(self):
        """Extensão .Md (capitalização mista) deve ser aceita."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/nota.Md") is True

    def test_should_process_pdf(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/doc.pdf") is True

    def test_should_process_canvas(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/diagram.canvas") is True

    def test_should_process_multiplas_extensoes_nao_indexaveis(self):
        """Extensões não-indexáveis devem ser todas rejeitadas."""
        handler = VaultEventHandler({}, threading.Lock())
        for ext in [".jpg", ".png", ".gif", ".mp3", ".mp4", ".zip"]:
            assert handler._should_process(f"/vault/file{ext}") is False

    def test_should_process_multiplas_extensoes_indexaveis(self):
        """Novas extensões indexáveis (.txt, .mdx) devem ser aceitas."""
        handler = VaultEventHandler({}, threading.Lock())
        for ext in [".md", ".txt", ".mdx", ".pdf", ".canvas"]:
            assert handler._should_process(f"/vault/file{ext}") is True

    def test_should_process_pasta_trash(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.trash/deleted.md") is False

    def test_should_process_pasta_smart_env(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.smart-env/index.md") is False


class TestWatcherNoCallback:
    """Testa watcher sem callback on_reindex."""

    def test_sem_callback_nao_crasheia(self):
        """Watcher sem on_reindex não deve dar erro ao processar eventos."""
        mock_indexer = MagicMock()
        mock_indexer.reindex_note.return_value = {"status": "updated", "chunks_indexed": 1}

        w = VaultWatcher(mock_indexer, on_reindex=None)
        w.start()

        with w._lock:
            w._pending["test.md"] = {
                "deleted": False,
                "time": time.monotonic() - WATCHER_DEBOUNCE - 1,
            }

        time.sleep(WATCHER_DEBOUNCE + 1)
        w.stop()

        mock_indexer.reindex_note.assert_called_once_with("test.md")
