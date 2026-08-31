"""
Funções de validação para operações CRUD.

Inclui helpers para validação combinada de path, tamanho e frontmatter.
"""

import logging
import os
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from vault_search.config.paths import VAULT_PATH
from vault_search.config.search import (
    IGNORED_FOLDERS,
    INDEXABLE_EXTENSIONS,
    READABLE_TEXT_EXTENSIONS,
)
from vault_search.config.security import (
    MAX_CONTENT_SIZE,
    MAX_FRONTMATTER_KEYS,
    MAX_PATH_LENGTH,
)
from vault_search.crud.locking import FileRevision, file_revision
from vault_search.crud.types import OperationResult, error_result
from vault_search.utils.security import validate_relative_path

logger = logging.getLogger(__name__)


def resolve_internal_path(*parts: str) -> Path:
    """Resolve um path controlado pela aplicação sem permitir symlink externo."""
    vault_root = VAULT_PATH.expanduser().resolve(strict=False)
    candidate = vault_root.joinpath(*parts).resolve(strict=False)
    try:
        candidate.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError("Path interno inválido ou fora do vault.") from exc
    return candidate


def resolve_path(relative_path: str) -> Path:
    """
    Resolve path relativo para absoluto.

    Raises:
        ValueError: se path é inválido ou muito longo.
    """
    if not relative_path or not relative_path.strip():
        raise ValueError("Path não pode ser vazio.")

    relative_path = relative_path.strip()

    if len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError(
            f"Path muito longo ({len(relative_path)} chars). Máximo: {MAX_PATH_LENGTH} chars."
        )

    if not validate_relative_path(relative_path):
        raise ValueError("Path inválido ou fora do vault.")

    try:
        return resolve_internal_path(relative_path)
    except ValueError as exc:
        raise ValueError("Path inválido ou fora do vault.") from exc


def validate_content_size(content: str) -> None:
    """
    Valida que o conteúdo não excede o tamanho máximo.

    Raises:
        ValueError: se conteúdo for muito grande.
    """
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_SIZE:
        size_mb = size / 1_048_576
        max_mb = MAX_CONTENT_SIZE / 1_048_576
        raise ValueError(f"Conteúdo muito grande ({size_mb:.1f}MB). Máximo: {max_mb:.0f}MB.")


def validate_frontmatter_size(frontmatter: dict[str, Any]) -> None:
    """
    Valida que o frontmatter não tem chaves demais (proteção contra YAML bombs).

    Raises:
        ValueError: se frontmatter tiver muitas chaves.
    """
    if frontmatter and len(frontmatter) > MAX_FRONTMATTER_KEYS:
        raise ValueError(
            f"Frontmatter com muitas chaves ({len(frontmatter)}). Máximo: {MAX_FRONTMATTER_KEYS}."
        )


def get_folder(file_path: Path) -> str:
    """Extrai folder relativo do path."""
    vault_root = VAULT_PATH.expanduser().resolve(strict=False)
    folder = str(file_path.parent.resolve(strict=False).relative_to(vault_root))
    return "" if folder == "." else folder


def validate_extension(relative_path: str, allow_create: bool = False) -> None:
    """
    Valida que a extensão é suportada para CRUD.

    Parâmetros:
        relative_path: caminho do arquivo
        allow_create: se True, permite apenas .md e .canvas (não .pdf)
    """
    ext = Path(relative_path).suffix.lower()

    if allow_create:
        # PDFs são read-only (não criamos/editamos)
        writable_extensions = {".md", ".canvas"}
        if ext not in writable_extensions:
            raise ValueError(
                f"Extensão '{ext}' não suportada para escrita. "
                f"Use: {', '.join(sorted(writable_extensions))}"
            )
    else:
        if ext not in INDEXABLE_EXTENSIONS:
            raise ValueError(
                f"Extensão '{ext}' não suportada. Use: {', '.join(sorted(INDEXABLE_EXTENSIONS))}"
            )


def validate_readable_text(relative_path: str) -> None:
    """
    Valida que a extensão é texto plano legível (com frontmatter).

    PDFs são binários, Canvas é JSON - nenhum suporta read_text() + frontmatter.
    """
    ext = Path(relative_path).suffix.lower()
    if ext not in READABLE_TEXT_EXTENSIONS:
        raise ValueError(
            f"Extensão '{ext}' não suportada para leitura de texto. "
            f"Use: {', '.join(sorted(READABLE_TEXT_EXTENSIONS))}. "
            f"Para buscar em PDFs/Canvas, use search_vault."
        )


def validate_markdown_only(relative_path: str) -> None:
    """
    Valida que a extensão é .md (operações que manipulam frontmatter/markdown).

    Canvas é JSON - frontmatter YAML corrompe o formato.
    """
    ext = Path(relative_path).suffix.lower()
    if ext != ".md":
        raise ValueError(
            f"Extensão '{ext}' não suportada para esta operação. "
            f"Apenas .md é suportado (Canvas é JSON, não markdown)."
        )


def validate_not_ignored_folder(relative_path: str) -> None:
    """
    Valida que o path não está em uma pasta ignorada.

    Impede operações em .trash, .obsidian, etc.
    """
    path_parts = Path(relative_path).parts
    for ignored in IGNORED_FOLDERS:
        if ignored in path_parts:
            raise ValueError(f"Operação não permitida em pasta ignorada: {ignored}")


def serialize_frontmatter(frontmatter: dict[str, Any]) -> str:
    """Serializa frontmatter para YAML."""
    if not frontmatter:
        return ""
    yaml_str = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---\n\n"


# =============================================================================
# Helpers de I/O Seguro
# =============================================================================


def safe_read_text(
    file_path: Path,
    relative_path: str,
) -> tuple[str | None, OperationResult | None]:
    """
    Lê arquivo com tratamento padronizado de erros.

    Retorna:
        Tupla (content, error_result) - um deles é sempre None.
        - Se sucesso: (content, None)
        - Se erro: (None, OperationResult com success=False)
    """
    try:
        resolved_path = file_path.resolve(strict=True)
        vault_root = VAULT_PATH.expanduser().resolve(strict=False)
        resolved_path.relative_to(vault_root)
        content = file_path.read_text(encoding="utf-8")
        return content, None
    except OSError as e:
        logger.error("note_read_failed error_type=%s", type(e).__name__)
        return None, error_result(relative_path, "Erro ao ler arquivo")
    except ValueError:
        logger.error("note_read_rejected reason=outside_vault")
        return None, error_result(relative_path, "Path inválido ou fora do vault")
    except UnicodeDecodeError as e:
        logger.error("note_read_failed error_type=%s", type(e).__name__)
        return None, error_result(relative_path, "Erro de encoding: arquivo não é UTF-8 válido")


def safe_write_text(
    file_path: Path,
    content: str,
    relative_path: str,
    *,
    expected_revision: FileRevision | None = None,
    check_revision: bool = False,
) -> OperationResult | None:
    """
    Escreve arquivo com tratamento padronizado de erros.

    Quando ``check_revision`` está ativo, compara inode, mtime em nanossegundos
    e tamanho com ``expected_revision`` imediatamente antes do replace.

    Retorna:
        None se sucesso, OperationResult com erro se falhar.
    """
    temp_path: Path | None = None
    try:
        vault_root = VAULT_PATH.expanduser().resolve(strict=False)
        resolved_parent = file_path.parent.resolve(strict=True)
        resolved_parent.relative_to(vault_root)

        previous_mode = None
        if file_path.exists():
            resolved_file = file_path.resolve(strict=True)
            resolved_file.relative_to(vault_root)
            previous_mode = stat.S_IMODE(resolved_file.stat().st_mode)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=resolved_parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        if previous_mode is not None:
            temp_path.chmod(previous_mode)

        if check_revision and file_revision(file_path) != expected_revision:
            logger.warning("note_write_conflict")
            return error_result(
                relative_path,
                "Conflito de escrita: a nota foi alterada durante a operação. Tente novamente.",
            )

        os.replace(temp_path, file_path)
        temp_path = None

        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(resolved_parent, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            logger.debug("directory_fsync_unavailable")
        return None
    except OSError as e:
        logger.error("note_write_failed error_type=%s", type(e).__name__)
        return error_result(relative_path, "Erro ao escrever arquivo")
    except ValueError:
        logger.error("note_write_rejected reason=outside_vault")
        return error_result(relative_path, "Path inválido ou fora do vault")
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                recovery_dir = resolve_internal_path(".trash", "write-failures")
                recovery_dir.mkdir(parents=True, exist_ok=True)
                recovery_path = recovery_dir / f"{uuid.uuid4().hex}-{temp_path.name}"
                temp_path.replace(recovery_path)
            except OSError, ValueError:
                logger.error("temporary_write_recovery_failed")


def validate_for_write(
    relative_path: str,
    content: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    markdown_only: bool = True,
) -> Path:
    """
    Executa todas as validações necessárias para operação de escrita.

    Parâmetros:
        relative_path: caminho relativo no vault
        content: conteúdo a validar (opcional)
        frontmatter: frontmatter a validar (opcional)
        markdown_only: se True, aceita apenas .md

    Retorna:
        Path absoluto resolvido.

    Raises:
        ValueError: se qualquer validação falhar.
    """
    if markdown_only:
        validate_markdown_only(relative_path)
    else:
        validate_extension(relative_path, allow_create=True)
    validate_not_ignored_folder(relative_path)
    if content is not None:
        validate_content_size(content)
    if frontmatter is not None:
        validate_frontmatter_size(frontmatter)
    return resolve_path(relative_path)


# =============================================================================
# Schema Validation
# =============================================================================


def get_frontmatter_validator():
    """
    Retorna instância do FrontmatterValidator configurada.

    Lazy import para evitar circular dependencies.
    Converte dicts do config YAML para FieldSchema em runtime.
    """
    from vault_search.config import get_config
    from vault_search.frontmatter import FrontmatterValidator
    from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig

    config = get_config()
    fm_config = config.frontmatter

    # Converte dicts do YAML para FieldSchema
    schema_dict = {
        field_name: FieldSchema(**field_dict)
        for field_name, field_dict in fm_config.schema_fields.items()
    }

    schema_config = FrontmatterSchemaConfig(
        enabled=fm_config.enabled,
        mode=fm_config.mode,
        allow_extra_fields=fm_config.allow_extra_fields,
        schema=schema_dict,
    )

    return FrontmatterValidator(schema_config)


def validate_frontmatter_schema(
    frontmatter: dict[str, Any] | None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Valida frontmatter contra schema configurado.

    Parâmetros:
        frontmatter: dados do frontmatter

    Retorna:
        Tupla (validated_data, errors, warnings, suggestions).
        - validated_data: frontmatter após coerção e auto-geração
        - errors: lista de erros que bloqueiam operação
        - warnings: lista de avisos (coerções aplicadas)
        - suggestions: lista de sugestões (campos opcionais)

    Raises:
        ValueError: se validação falhar.
    """
    result = validate_frontmatter_schema_result(frontmatter)

    if not result["valid"]:
        raise ValueError(_format_frontmatter_errors(result["errors"]))

    return (
        result["validated_data"],
        result["errors"],
        result["warnings"],
        result["suggestions"],
    )


def validate_frontmatter_schema_result(
    frontmatter: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Valida frontmatter e retorna resultado bruto (sem lançar exceção).

    Útil para fluxos que precisam tratar tipos específicos de erro
    (ex: deferir campos required no create_note).
    """
    validator = get_frontmatter_validator()
    return validator.validate(frontmatter)


def _format_frontmatter_errors(errors: list[dict[str, Any]]) -> str:
    """Formata lista de erros de validação em mensagem amigável."""
    error_msgs = [f"{e['field']}: {e['message']}" for e in errors]
    return f"Validação de frontmatter falhou: {'; '.join(error_msgs)}"
