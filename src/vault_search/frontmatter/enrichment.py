"""
Enriquecimento de frontmatter obrigatório via CLI de LLM.
"""

import json
import logging
import re
import subprocess
import time
from typing import Any

from vault_search.config import get_config
from vault_search.frontmatter.schema import FieldSchema

logger = logging.getLogger(__name__)


class FrontmatterEnrichmentError(RuntimeError):
    """Erro durante enriquecimento de frontmatter."""


class FrontmatterEnrichmentConfigError(FrontmatterEnrichmentError):
    """Erro de configuração do enriquecimento de frontmatter."""


def get_required_schema_fields(schema: dict[str, FieldSchema]) -> dict[str, FieldSchema]:
    """Retorna apenas campos com `on_missing=require`."""
    return {
        field_name: field_schema
        for field_name, field_schema in schema.items()
        if field_schema.on_missing == "require"
    }


def _is_field_empty(value: Any) -> bool:
    """Verifica se valor de frontmatter é considerado vazio (None, string vazia, lista/dict vazio)."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def generate_required_fields_with_ai(
    note_path: str,
    note_body: str,
    current_frontmatter: dict[str, Any],
    required_schema_fields: dict[str, FieldSchema],
) -> dict[str, Any]:
    """
    Gera campos obrigatórios faltantes usando IA.

    Retorna apenas campos obrigatórios ausentes presentes no schema.
    """
    missing_required = [
        field_name
        for field_name in required_schema_fields
        if field_name not in current_frontmatter or _is_field_empty(current_frontmatter[field_name])
    ]
    if not missing_required:
        return {}

    config = get_config().frontmatter.ai
    if not config.enabled:
        raise FrontmatterEnrichmentConfigError(
            "Enriquecimento de frontmatter está desabilitado em frontmatter.ai.enabled"
        )
    if not config.command or len(config.command) == 0:
        raise FrontmatterEnrichmentConfigError("frontmatter.ai.command não pode ser vazio")
    if not config.primary_model:
        raise FrontmatterEnrichmentConfigError("frontmatter.ai.primary_model não pode ser vazio")

    # O conteúdo da nota deve viajar apenas por stdin, nunca pelo argv do processo.
    _validate_command_security(config.command)

    prompt = _build_prompt(
        note_path=note_path,
        note_body=note_body[: config.max_note_chars],
        current_frontmatter=current_frontmatter,
        required_schema_fields=required_schema_fields,
        missing_required=missing_required,
    )

    models = [config.primary_model]
    if config.fallback_model and config.fallback_model != config.primary_model:
        models.append(config.fallback_model)

    error_types: list[str] = []
    for model in models:
        try:
            generated = _generate_with_model(
                command_template=config.command,
                model=model,
                prompt=prompt,
                timeout_seconds=config.timeout_seconds,
                max_attempts=config.max_attempts,
            )
            # Merge seguro: retorna apenas campos faltantes.
            return {
                field_name: generated[field_name]
                for field_name in missing_required
                if field_name in generated
            }
        except FrontmatterEnrichmentConfigError:
            raise
        except Exception as exc:
            error_types.append(type(exc).__name__)
            logger.warning(
                "frontmatter_enrichment_model_failed error_type=%s",
                type(exc).__name__,
            )

    failure_count = len(error_types)
    raise FrontmatterEnrichmentError(
        f"Falha ao gerar frontmatter obrigatório via IA ({failure_count} tentativa(s))"
    )


def _generate_with_model(
    command_template: list[str],
    model: str,
    prompt: str,
    timeout_seconds: float,
    max_attempts: int,
) -> dict[str, Any]:
    """Executa CLI com retry curto e parseia saída JSON."""
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            command, stdin_data = _resolve_command(
                command_template=command_template,
                model=model,
                prompt=prompt,
            )
            output = _run_cli_command(
                command=command,
                stdin_data=stdin_data,
                timeout_seconds=timeout_seconds,
            )
            parsed = _extract_json_object(output)
            if not isinstance(parsed, dict):
                raise FrontmatterEnrichmentError("Saída JSON não é objeto")
            return parsed
        except FrontmatterEnrichmentConfigError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep_seconds = min(0.5 * (2 ** (attempt - 1)), 2.0)
                time.sleep(sleep_seconds)

    error_type = type(last_error).__name__ if last_error else "UnknownError"
    raise FrontmatterEnrichmentError(f"Falha no provider de enriquecimento ({error_type})")


def _validate_command_security(command_template: list[str]) -> None:
    """
    Valida segurança do template de comando.

    Bloqueia `{prompt}` em qualquer argumento. O argv é visível para outros
    processos locais e pode acabar em diagnósticos do sistema operacional.
    """
    if not command_template:
        return

    if any("{prompt}" in str(part) for part in command_template):
        raise FrontmatterEnrichmentConfigError(
            "Placeholder {prompt} não é permitido no comando. "
            "O prompt deve ser enviado exclusivamente por stdin."
        )


def _resolve_command(
    command_template: list[str],
    model: str,
    prompt: str,
) -> tuple[list[str], str]:
    """Resolve `{model}` e mantém o prompt exclusivamente no stdin."""
    resolved: list[str] = []

    for part in command_template:
        token = str(part)
        token = token.replace("{model}", model)
        resolved.append(token)

    return resolved, prompt


def _run_cli_command(
    command: list[str],
    stdin_data: str | None,
    timeout_seconds: float,
) -> str:
    """Executa comando CLI e retorna stdout."""
    try:
        completed = subprocess.run(
            command,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FrontmatterEnrichmentConfigError("Comando de enriquecimento não encontrado") from exc
    except subprocess.TimeoutExpired as exc:
        raise FrontmatterEnrichmentError(
            f"Timeout no enriquecimento após {timeout_seconds:g}s"
        ) from exc

    stdout = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise FrontmatterEnrichmentError(
            f"Provider de enriquecimento encerrou com código {completed.returncode}"
        )

    if not stdout:
        raise FrontmatterEnrichmentError("CLI retornou saída vazia")

    return stdout


def _extract_json_object(raw_output: str) -> dict[str, Any]:
    """Extrai primeiro objeto JSON válido da saída textual."""
    text = raw_output.strip()
    if not text:
        raise FrontmatterEnrichmentError("Saída vazia")

    # Caso comum: saída JSON pura
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Caso comum: bloco markdown ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Fallback otimizado: tentar primeiro { até último } (O(1) ao invés de O(n²))
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Último recurso: iterar posições de { (raro, só se JSON estiver fragmentado)
    decoder = json.JSONDecoder()
    for idx in range(first_brace + 1 if first_brace != -1 else 0, len(text)):
        if text[idx] != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    raise FrontmatterEnrichmentError("Não foi possível extrair JSON válido da saída")


def _build_prompt(
    note_path: str,
    note_body: str,
    current_frontmatter: dict[str, Any],
    required_schema_fields: dict[str, FieldSchema],
    missing_required: list[str],
) -> str:
    """Monta prompt para preenchimento de campos obrigatórios faltantes."""
    schema_view = {
        field_name: _field_schema_to_dict(required_schema_fields[field_name])
        for field_name in missing_required
    }

    instructions = (
        "Você é um assistente que completa frontmatter YAML.\n"
        "Retorne SOMENTE um objeto JSON válido.\n"
        "Inclua apenas os campos obrigatórios faltantes listados em missing_required.\n"
        "Não invente campos fora do schema.\n"
        "Respeite tipos, enum, limites e formato do schema.\n"
    )
    context = {
        "note_path": note_path,
        "missing_required": missing_required,
        "required_schema": schema_view,
        "current_frontmatter": current_frontmatter,
        "note_body": note_body,
    }

    return instructions + "\nContexto:\n" + json.dumps(context, ensure_ascii=False, indent=2)


def _field_schema_to_dict(field_schema: FieldSchema) -> dict[str, Any]:
    """Converte FieldSchema para representação compacta no prompt."""
    data: dict[str, Any] = {"type": field_schema.type}
    if field_schema.values is not None:
        data["values"] = field_schema.values
    if field_schema.min_length is not None:
        data["min_length"] = field_schema.min_length
    if field_schema.max_length is not None:
        data["max_length"] = field_schema.max_length
    if field_schema.pattern is not None:
        data["pattern"] = field_schema.pattern
    if field_schema.minimum is not None:
        data["minimum"] = field_schema.minimum
    if field_schema.maximum is not None:
        data["maximum"] = field_schema.maximum
    if field_schema.item_type is not None:
        data["item_type"] = field_schema.item_type
    if field_schema.min_items is not None:
        data["min_items"] = field_schema.min_items
    if field_schema.max_items is not None:
        data["max_items"] = field_schema.max_items
    return data
