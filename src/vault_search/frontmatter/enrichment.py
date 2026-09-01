"""Generate required frontmatter fields through a local LLM CLI."""

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
    """Raised when frontmatter enrichment fails."""


class FrontmatterEnrichmentConfigError(FrontmatterEnrichmentError):
    """Raised when frontmatter enrichment is configured unsafely or incompletely."""


def get_required_schema_fields(schema: dict[str, FieldSchema]) -> dict[str, FieldSchema]:
    """Return fields configured with `on_missing=require`."""
    return {
        field_name: field_schema
        for field_name, field_schema in schema.items()
        if field_schema.on_missing == "require"
    }


def _is_field_empty(value: Any) -> bool:
    """Return whether a frontmatter value is empty."""
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
    Generate missing required fields with the configured LLM provider.

    Return only missing fields that are required by the schema.
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
            "Frontmatter enrichment is disabled by frontmatter.ai.enabled"
        )
    if not config.command or len(config.command) == 0:
        raise FrontmatterEnrichmentConfigError("frontmatter.ai.command cannot be empty")
    if not config.primary_model:
        raise FrontmatterEnrichmentConfigError("frontmatter.ai.primary_model cannot be empty")

    # Note content must travel through stdin, never process argv.
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
            # Keep the provider response inside the missing-field allowlist.
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
        f"Failed to generate required frontmatter ({failure_count} provider attempt(s))"
    )


def _generate_with_model(
    command_template: list[str],
    model: str,
    prompt: str,
    timeout_seconds: float,
    max_attempts: int,
) -> dict[str, Any]:
    """Run the CLI with bounded retries and parse its JSON output."""
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
                raise FrontmatterEnrichmentError("JSON output is not an object")
            return parsed
        except FrontmatterEnrichmentConfigError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep_seconds = min(0.5 * (2 ** (attempt - 1)), 2.0)
                time.sleep(sleep_seconds)

    error_type = type(last_error).__name__ if last_error else "UnknownError"
    raise FrontmatterEnrichmentError(f"Enrichment provider failed ({error_type})")


def _validate_command_security(command_template: list[str]) -> None:
    """
    Validate the command template's security contract.

    Reject `{prompt}` in every argument. Process argv can be visible to other
    local processes and operating-system diagnostics.
    """
    if not command_template:
        return

    if any("{prompt}" in str(part) for part in command_template):
        raise FrontmatterEnrichmentConfigError(
            "The {prompt} placeholder is not allowed in the command. "
            "Send the prompt exclusively through stdin."
        )


def _resolve_command(
    command_template: list[str],
    model: str,
    prompt: str,
) -> tuple[list[str], str]:
    """Resolve `{model}` while keeping the prompt exclusively on stdin."""
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
    """Run a CLI command and return stdout."""
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
        raise FrontmatterEnrichmentConfigError("Enrichment command was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise FrontmatterEnrichmentError(
            f"Enrichment timed out after {timeout_seconds:g}s"
        ) from exc

    stdout = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise FrontmatterEnrichmentError(
            f"Enrichment provider exited with code {completed.returncode}"
        )

    if not stdout:
        raise FrontmatterEnrichmentError("CLI returned empty output")

    return stdout


def _extract_json_object(raw_output: str) -> dict[str, Any]:
    """Extract the first valid JSON object from textual output."""
    text = raw_output.strip()
    if not text:
        raise FrontmatterEnrichmentError("Output is empty")

    # Common case: plain JSON output.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Common case: a fenced Markdown JSON block.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Fast fallback: parse from the first opening brace to the last closing brace.
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

    # Last resort: iterate opening braces when output contains fragmented JSON.
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

    raise FrontmatterEnrichmentError("Could not extract a valid JSON object from output")


def _build_prompt(
    note_path: str,
    note_body: str,
    current_frontmatter: dict[str, Any],
    required_schema_fields: dict[str, FieldSchema],
    missing_required: list[str],
) -> str:
    """Build the prompt for missing required fields."""
    schema_view = {
        field_name: _field_schema_to_dict(required_schema_fields[field_name])
        for field_name in missing_required
    }

    instructions = (
        "Complete the missing YAML frontmatter fields.\n"
        "Return ONLY a valid JSON object.\n"
        "Include only the required missing fields listed in missing_required.\n"
        "Do not add fields outside the schema.\n"
        "Follow every schema type, enum, constraint, and format.\n"
    )
    context = {
        "note_path": note_path,
        "missing_required": missing_required,
        "required_schema": schema_view,
        "current_frontmatter": current_frontmatter,
        "note_body": note_body,
    }

    return instructions + "\nContext:\n" + json.dumps(context, ensure_ascii=False, indent=2)


def _field_schema_to_dict(field_schema: FieldSchema) -> dict[str, Any]:
    """Convert FieldSchema to its compact prompt representation."""
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
