"""
Operações de delete e move para notas do vault.
"""

import logging
import shutil
import uuid
from pathlib import Path

from vault_search.config.paths import VAULT_PATH
from vault_search.crud.locking import (
    advisory_path_lock,
    advisory_path_locks,
    file_revision,
    return_write_lock_timeout,
)
from vault_search.crud.types import OperationResult, error_result, success_result
from vault_search.crud.validation import (
    resolve_internal_path,
    resolve_path,
    validate_extension,
    validate_not_ignored_folder,
)

logger = logging.getLogger(__name__)


def _write_conflict(relative_path: str) -> OperationResult:
    """Retorna conflito seguro quando origem ou destino mudou."""
    return error_result(
        relative_path,
        "Conflito de escrita: a nota foi alterada durante a operação. Tente novamente.",
        error_code="write_conflict",
    )


@return_write_lock_timeout
def delete_note(relative_path: str) -> OperationResult:
    """
    Deleta uma nota, movendo-a para a lixeira (.trash/).

    Por segurança, deleção permanente não é suportada.
    Arquivos podem ser recuperados do .trash/ se necessário.

    Parâmetros:
        relative_path: caminho relativo no vault

    Retorna:
        OperationResult indicando sucesso ou falha.
    """
    validate_extension(relative_path)
    validate_not_ignored_folder(relative_path)  # Não deletar de .trash, .obsidian
    file_path = resolve_path(relative_path)
    with advisory_path_lock(file_path):
        file_path = resolve_path(relative_path)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Nota não encontrada: {relative_path}")

        trash_dir = resolve_internal_path(".trash")
        trash_dir.mkdir(exist_ok=True)
        relative = Path(relative_path)
        # Resolver também os componentes internos impede que um symlink como
        # ``.trash/pasta -> /fora/do/vault`` redirecione o destino do move.
        trash_path = resolve_internal_path(".trash", *relative.parts)
        trash_path.parent.mkdir(parents=True, exist_ok=True)

        final_trash_path = trash_path
        max_attempts = 10
        for attempt in range(max_attempts):
            if not final_trash_path.exists():
                break
            unique_id = uuid.uuid4().hex[:8]
            final_trash_path = trash_path.with_stem(f"{trash_path.stem}_{unique_id}")
            logger.debug(
                "delete_note collision attempt=%d max_attempts=%d",
                attempt + 1,
                max_attempts,
            )
        else:
            logger.error("delete_note collision_limit=%d", max_attempts)
            return error_result(
                relative_path,
                f"Não foi possível gerar nome único na lixeira após {max_attempts} tentativas",
            )

        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)
        try:
            shutil.move(str(file_path), str(final_trash_path))
        except (OSError, shutil.Error) as e:
            logger.error("delete_note_failed error_type=%s", type(e).__name__)
            return error_result(relative_path, "Erro ao mover para lixeira")

    logger.info("delete_note completed destination=trash")
    return success_result(
        relative_path,
        "Nota movida para lixeira: "
        f"{final_trash_path.relative_to(VAULT_PATH.expanduser().resolve(strict=False))}",
    )


@return_write_lock_timeout
def move_note(from_path: str, to_path: str) -> OperationResult:
    """
    Move ou renomeia uma nota.

    Parâmetros:
        from_path: caminho atual relativo no vault
        to_path: novo caminho relativo no vault (não pode ser pasta ignorada)

    Retorna:
        OperationResult indicando sucesso ou falha.
    """
    validate_extension(from_path)
    validate_extension(to_path)  # Destino deve ter extensão válida
    validate_not_ignored_folder(to_path)  # Impede mover para .trash, .obsidian
    validate_not_ignored_folder(from_path)  # Impede mover de .trash, .obsidian

    # Validar que extensão não muda (evita corrupção: .pdf → .md)
    from_ext = Path(from_path).suffix.lower()
    to_ext = Path(to_path).suffix.lower()
    if from_ext != to_ext:
        return error_result(
            from_path,
            f"Não é possível mudar extensão de '{from_ext}' para '{to_ext}'. "
            f"Isso pode corromper o arquivo.",
        )

    from_file = resolve_path(from_path)
    to_file = resolve_path(to_path)
    with advisory_path_locks(from_file, to_file):
        from_file = resolve_path(from_path)
        to_file = resolve_path(to_path)
        source_revision = file_revision(from_file)
        if source_revision is None:
            return error_result(from_path, f"Nota de origem não encontrada: {from_path}")
        if file_revision(to_file) is not None:
            return error_result(to_path, f"Destino já existe: {to_path}")

        to_file.parent.mkdir(parents=True, exist_ok=True)
        if file_revision(from_file) != source_revision or file_revision(to_file) is not None:
            return _write_conflict(from_path)
        try:
            shutil.move(str(from_file), str(to_file))
        except (OSError, shutil.Error) as e:
            logger.error("move_note_failed error_type=%s", type(e).__name__)
            return error_result(from_path, "Erro ao mover nota")

    logger.info("move_note completed")
    return success_result(to_path, f"Nota movida: {from_path} -> {to_path}")
