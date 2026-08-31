"""
Operações de escrita para notas do vault.
"""

import logging
from pathlib import Path
from typing import Any

from vault_search.config import get_config
from vault_search.crud.locking import (
    advisory_path_lock,
    file_revision,
    return_write_lock_timeout,
)
from vault_search.crud.types import OperationResult, error_result, success_result
from vault_search.crud.validation import (
    get_frontmatter_validator,
    resolve_path,
    safe_read_text,
    safe_write_text,
    serialize_frontmatter,
    validate_content_size,
    validate_for_write,
    validate_frontmatter_schema,
    validate_frontmatter_schema_result,
    validate_frontmatter_size,
)
from vault_search.frontmatter import (
    FrontmatterEnrichmentConfigError,
    FrontmatterEnrichmentError,
    generate_required_fields_with_ai,
    get_required_schema_fields,
)
from vault_search.parsers.frontmatter import parse_frontmatter
from vault_search.server.event_handler import ignore_next_change
from vault_search.utils.uuid import generate_uuid7

logger = logging.getLogger(__name__)


def _write_conflict(relative_path: str) -> OperationResult:
    """Retorna conflito seguro quando a revisão observada mudou."""
    return error_result(
        relative_path,
        "Conflito de escrita: a nota foi alterada durante a operação. Tente novamente.",
        error_code="write_conflict",
    )


def _read_locked_text(
    file_path: Path,
    relative_path: str,
) -> tuple[str | None, OperationResult | None]:
    """Lê uma revisão estável enquanto escritores cooperativos aguardam."""
    with advisory_path_lock(file_path):
        revision = file_revision(file_path)
        if revision is None:
            return None, error_result(
                relative_path,
                f"Nota não encontrada: {relative_path}",
            )
        content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return None, read_error
        if file_revision(file_path) != revision:
            return None, _write_conflict(relative_path)
        return content, None


def _persist_generated_frontmatter(
    relative_path: str,
    file_path: Path,
    required_schema_fields: dict[str, Any],
    generated_fields: dict[str, Any],
    validate_schema: bool,
) -> OperationResult:
    """Recarrega, mescla e persiste o enriquecimento sob o mesmo lock."""
    with advisory_path_lock(file_path):
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Nota não encontrada: {relative_path}")

        current_content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert current_content is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        disk_fm, disk_body = parse_frontmatter(current_content)
        still_missing = [
            field_name
            for field_name in required_schema_fields
            if _is_field_missing_or_empty(disk_fm, field_name)
        ]
        if not still_missing:
            result = success_result(
                relative_path,
                "Campos preenchidos manualmente durante processamento da IA",
            )
            result["frontmatter_enriched"] = False
            result["frontmatter_fields_filled"] = 0
            return result

        new_values = {
            field_name: value
            for field_name, value in generated_fields.items()
            if field_name in still_missing
        }
        if not new_values:
            return error_result(
                relative_path,
                "IA não retornou campos obrigatórios suficientes para enriquecimento",
                error_code="required_missing",
            )

        new_fm = {**disk_fm, **new_values}
        if validate_schema:
            try:
                validated_data, _, warnings, suggestions = validate_frontmatter_schema(new_fm)
                new_fm = validated_data
            except ValueError as exc:
                error_message = str(exc)
                is_required_missing = (
                    "obrigatório" in error_message.lower()
                    or "required_missing" in error_message.lower()
                )
                if not (is_required_missing and new_values):
                    return error_result(
                        relative_path,
                        error_message,
                        error_code="required_missing" if is_required_missing else None,
                    )
                warnings = [
                    {
                        "field": "_schema",
                        "message": (
                            "Validação strict manteve campos obrigatórios faltantes; "
                            "enriquecimento parcial foi salvo"
                        ),
                        "code": "required_missing_partial",
                        "value": None,
                    }
                ]
                suggestions = []
        else:
            warnings = []
            suggestions = []

        validate_frontmatter_size(new_fm)
        new_content = serialize_frontmatter(new_fm) + disk_body
        validate_content_size(new_content)
        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    ignore_next_change(relative_path)
    result = success_result(relative_path, f"Frontmatter enriquecido: {relative_path}")
    result["frontmatter_enriched"] = True
    result["frontmatter_fields_filled"] = len(new_values)
    if warnings:
        result["_validation_warnings"] = warnings
    if suggestions:
        result["_validation_suggestions"] = suggestions
    return result


def is_ai_enrichment_enabled() -> bool:
    """Confirma schema, consentimento externo e transporte configurado."""
    frontmatter = get_config().frontmatter
    ai = frontmatter.ai
    return bool(
        frontmatter.enabled
        and ai.enabled
        and ai.allow_external_processing
        and ai.provider
        and ai.provider.strip()
        and ai.command
    )


def _is_empty_value(value: Any) -> bool:
    """Verifica se um valor de frontmatter é considerado vazio."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _is_field_missing_or_empty(frontmatter: dict[str, Any], field_name: str) -> bool:
    """Verifica se campo está ausente OU tem valor vazio."""
    if field_name not in frontmatter:
        return True
    return _is_empty_value(frontmatter[field_name])


def _format_validation_errors(errors: list[dict[str, Any]]) -> str:
    """Formata erros de validação em mensagem única."""
    return "Validação de frontmatter falhou: " + "; ".join(
        f"{error['field']}: {error['message']}" for error in errors
    )


def _can_defer_required_missing(errors: list[dict[str, Any]]) -> bool:
    """
    Retorna True se todos os erros forem `required_missing` e defer estiver habilitado.
    """
    if not errors:
        return False

    config = get_config().frontmatter
    if not config.enabled or not is_ai_enrichment_enabled():
        return False
    if not config.ai.allow_defer_required_on_create:
        return False

    return all(error.get("code") == "required_missing" for error in errors)


@return_write_lock_timeout
def create_note(
    relative_path: str,
    content: str,
    frontmatter: dict[str, Any] | None = None,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Cria uma nova nota markdown. Erro se já existir.

    Apenas .md é suportado (Canvas é JSON, não markdown).

    Parâmetros:
        relative_path: caminho relativo no vault (ex: 'pasta/nova-nota.md')
        content: conteúdo da nota (corpo, sem frontmatter)
        frontmatter: metadados YAML opcionais (ex: {"title": "Minha Nota", "tags": ["tag1"]})
        validate_schema: se True, valida frontmatter contra schema configurado

    Retorna:
        OperationResult indicando sucesso ou falha.
        Inclui _validation_warnings e _validation_suggestions se houver.
    """
    # Garantir que frontmatter é um dict mutável
    frontmatter = dict(frontmatter) if frontmatter else {}

    # Validar schema (pode gerar campos automáticos como 'id')
    validation_warnings = []
    validation_suggestions = []

    if validate_schema:
        validation_result = validate_frontmatter_schema_result(frontmatter)
        validation_warnings = validation_result["warnings"]
        validation_suggestions = validation_result["suggestions"]
        frontmatter = validation_result["validated_data"]

        if not validation_result["valid"]:
            if _can_defer_required_missing(validation_result["errors"]):
                validation_warnings.append(
                    {
                        "field": "_schema",
                        "message": "Campos obrigatórios ausentes deferidos para enriquecimento no reindex",
                        "code": "required_missing_deferred",
                        "value": None,
                    }
                )
            else:
                return error_result(
                    relative_path,
                    _format_validation_errors(validation_result["errors"]),
                )

    # Gerar UUID v7 automaticamente se não fornecido (fallback se schema não habilitado)
    if "id" not in frontmatter:
        frontmatter["id"] = generate_uuid7()

    # Montar conteúdo final
    fm_str = serialize_frontmatter(frontmatter or {})
    full_content = fm_str + content

    # Validar tamanho final (conteúdo + frontmatter serializado)
    validate_content_size(full_content)

    file_path = validate_for_write(relative_path, content, frontmatter)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content, frontmatter)
        if file_revision(file_path) is not None:
            return error_result(
                relative_path,
                f"Nota já existe: {relative_path}. Use write_note para sobrescrever.",
            )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        if write_error := safe_write_text(
            file_path,
            full_content,
            relative_path,
            expected_revision=None,
            check_revision=True,
        ):
            return write_error

    logger.info("create_note completed")
    result = success_result(relative_path, f"Nota criada: {relative_path}")

    # Adicionar warnings e suggestions ao resultado
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result


@return_write_lock_timeout
def write_note(relative_path: str, content: str) -> OperationResult:
    """
    Sobrescreve ou cria nota markdown com conteúdo completo.

    Apenas .md é suportado (Canvas é JSON, não markdown).
    Use esta função quando você já tem o conteúdo completo
    (incluindo frontmatter, se houver).

    Parâmetros:
        relative_path: caminho relativo no vault (ex: 'pasta/nota.md')
        content: conteúdo completo da nota

    Retorna:
        OperationResult indicando sucesso ou falha.
    """
    file_path = validate_for_write(relative_path, content)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content)
        revision = file_revision(file_path)
        existed = revision is not None
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if write_error := safe_write_text(
            file_path,
            content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    action = "atualizada" if existed else "criada"
    logger.info("write_note completed action=%s", action)
    return success_result(relative_path, f"Nota {action}: {relative_path}")


@return_write_lock_timeout
def append_note(
    relative_path: str,
    content: str,
    separator: str = "\n\n",
) -> OperationResult:
    """
    Adiciona conteúdo ao final de uma nota markdown existente.

    Apenas .md é suportado (Canvas é JSON, não markdown).

    Parâmetros:
        relative_path: caminho relativo no vault
        content: conteúdo a adicionar
        separator: separador entre conteúdo existente e novo (default: duas quebras de linha)

    Retorna:
        OperationResult indicando sucesso ou falha.
    """
    file_path = validate_for_write(relative_path, content)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(
                relative_path,
                f"Nota não encontrada: {relative_path}. Use create_note ou write_note.",
            )

        existing, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert existing is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        # Adicionar separador apenas se não termina com ele
        if existing.endswith(separator):
            new_content = existing + content
        else:
            new_content = existing + separator + content

        # Valida resultado final antes de escrever
        validate_content_size(new_content)

        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    logger.info("append_note completed")
    return success_result(relative_path, f"Conteúdo adicionado: {relative_path}")


@return_write_lock_timeout
def update_frontmatter(
    relative_path: str,
    metadata: dict[str, Any],
    merge: bool = True,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Atualiza frontmatter YAML de uma nota markdown existente.

    Apenas .md é suportado (Canvas é JSON, não markdown).
    IMPORTANTE: Merge é shallow (1 nível). Arrays/objetos são substituídos, não mesclados.

    Parâmetros:
        relative_path: caminho relativo no vault
        metadata: novos metadados
        merge: se True, mescla shallow com existente; se False, substitui completamente
        validate_schema: se True, valida frontmatter resultante contra schema configurado

    Retorna:
        OperationResult indicando sucesso ou falha.
        Inclui _validation_warnings e _validation_suggestions se houver.
    """
    # Validar tipo de metadata (deve ser dict)
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata deve ser um dicionário, recebido: {type(metadata).__name__}")

    file_path = validate_for_write(relative_path, frontmatter=metadata)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, frontmatter=metadata)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Nota não encontrada: {relative_path}")

        content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert content is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        existing_fm, body = parse_frontmatter(content)

        if merge:
            new_fm = {**existing_fm, **metadata}
        else:
            new_fm = metadata

        # Validar schema (pode aplicar coerções)
        validation_warnings = []
        validation_suggestions = []

        if validate_schema:
            try:
                validated_data, errors, warnings, suggestions = validate_frontmatter_schema(new_fm)
                new_fm = validated_data
                validation_warnings = warnings
                validation_suggestions = suggestions
            except ValueError as e:
                return error_result(relative_path, str(e))

        # Valida tamanho final
        validate_frontmatter_size(new_fm)

        fm_str = serialize_frontmatter(new_fm)
        new_content = fm_str + body

        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    action = "mesclado" if merge else "substituído"
    logger.info("update_frontmatter completed action=%s", action)
    result = success_result(relative_path, f"Frontmatter {action}: {relative_path}")

    # Adicionar warnings e suggestions ao resultado
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result


@return_write_lock_timeout
def enrich_note_frontmatter_required(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Enriquece campos obrigatórios ausentes do frontmatter via IA.

    Esta função é usada no fluxo assíncrono do reindex_note (watcher),
    e nunca sobrescreve campos já existentes.
    """
    if not relative_path.lower().endswith(".md"):
        return success_result(relative_path, "Skip enriquecimento: extensão não suportada")

    file_path = resolve_path(relative_path)
    content, read_error = _read_locked_text(file_path, relative_path)
    if read_error:
        return read_error
    assert content is not None

    existing_fm, body = parse_frontmatter(content)

    # Sem schema habilitado, não há conceito de required para enriquecer.
    validator = get_frontmatter_validator()
    if not validator.config.enabled:
        result = success_result(relative_path, "Schema de frontmatter desabilitado")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    required_schema_fields = get_required_schema_fields(validator.config.schema)
    if not required_schema_fields:
        result = success_result(relative_path, "Sem campos obrigatórios no schema")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    missing_required = [
        field_name
        for field_name in required_schema_fields
        if _is_field_missing_or_empty(existing_fm, field_name)
    ]
    if not missing_required:
        result = success_result(relative_path, "Frontmatter já contém campos obrigatórios")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    logger.info(
        "frontmatter_enrichment_started",
        extra={"missing_field_count": len(missing_required)},
    )

    try:
        generated_fields = generate_required_fields_with_ai(
            note_path=relative_path,
            note_body=body,
            current_frontmatter=existing_fm,
            required_schema_fields=required_schema_fields,
        )
    except (FrontmatterEnrichmentError, FrontmatterEnrichmentConfigError) as exc:
        return error_result(relative_path, str(exc))

    return _persist_generated_frontmatter(
        relative_path,
        file_path,
        required_schema_fields,
        generated_fields,
        validate_schema,
    )


@return_write_lock_timeout
def ensure_note_id(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """Garante ID sob lock por path durante toda a operação read-modify-write."""
    if not relative_path.lower().endswith(".md"):
        return error_result(relative_path, "Apenas .md é suportado")
    file_path = resolve_path(relative_path)
    with advisory_path_lock(file_path):
        return _ensure_note_id_locked(relative_path, validate_schema)


def _ensure_note_id_locked(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Garante que uma nota tenha um ID único no frontmatter.

    Se a nota já tem 'id' no frontmatter, não faz nada.
    Se não tem, gera UUID v7 e adiciona.
    Opcionalmente valida o frontmatter completo contra o schema.

    Parâmetros:
        relative_path: caminho relativo no vault
        validate_schema: se True, valida frontmatter contra schema configurado

    Retorna:
        OperationResult com 'id_added': True/False indicando se foi adicionado.
        Inclui _validation_warnings e _validation_suggestions se houver.
    """
    # Verificar extensão primeiro (antes de resolver path)
    if not relative_path.lower().endswith(".md"):
        return error_result(relative_path, "Apenas .md é suportado")

    file_path = resolve_path(relative_path)

    revision = file_revision(file_path)
    if revision is None:
        return error_result(relative_path, f"Nota não encontrada: {relative_path}")

    content, read_error = safe_read_text(file_path, relative_path)
    if read_error:
        return read_error
    assert content is not None
    if file_revision(file_path) != revision:
        return _write_conflict(relative_path)

    existing_fm, body = parse_frontmatter(content)

    # Já tem ID, não fazer nada (mas ainda pode validar schema)
    if "id" in existing_fm:
        result = success_result(relative_path, f"Nota já tem ID: {relative_path}")
        result["id_added"] = False
        result["id"] = existing_fm["id"]

        # Validar schema mesmo se já tem ID (para reportar warnings/suggestions)
        if validate_schema:
            try:
                _, _, warnings, suggestions = validate_frontmatter_schema(existing_fm)
                if warnings:
                    result["_validation_warnings"] = warnings
                if suggestions:
                    result["_validation_suggestions"] = suggestions
            except ValueError:
                pass  # Ignora erros de validação em notas existentes com ID

        return result

    # Validar schema e obter ID auto-gerado se configurado
    validation_warnings = []
    validation_suggestions = []

    if validate_schema:
        try:
            validated_data, errors, warnings, suggestions = validate_frontmatter_schema(existing_fm)
            # Se o schema gerou um ID, usar esse
            if "id" in validated_data and "id" not in existing_fm:
                new_fm = {
                    "id": validated_data["id"],
                    **{k: v for k, v in existing_fm.items() if k != "id"},
                }
                new_fm.update(
                    {k: v for k, v in validated_data.items() if k != "id" and k not in existing_fm}
                )
            else:
                # Gerar ID manualmente
                new_id = generate_uuid7()
                new_fm = {"id": new_id, **existing_fm}
            validation_warnings = warnings
            validation_suggestions = suggestions
        except ValueError as e:
            return error_result(relative_path, str(e))
    else:
        # Gerar e adicionar ID sem validação
        new_id = generate_uuid7()
        new_fm = {"id": new_id, **existing_fm}  # ID primeiro para ficar no topo

    fm_str = serialize_frontmatter(new_fm)
    new_content = fm_str + body

    if write_error := safe_write_text(
        file_path,
        new_content,
        relative_path,
        expected_revision=revision,
        check_revision=True,
    ):
        return write_error

    # Marcar para watcher ignorar esta mudança APÓS escrita bem-sucedida
    # (evita reindexação desnecessária)
    ignore_next_change(relative_path)

    logger.info("ensure_note_id completed")
    result = success_result(relative_path, f"ID adicionado: {relative_path}")
    result["id_added"] = True
    result["id"] = new_fm["id"]

    # Adicionar warnings e suggestions ao resultado
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result
