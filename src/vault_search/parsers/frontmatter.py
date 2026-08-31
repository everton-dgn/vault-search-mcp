"""
Parser de frontmatter YAML e extração de tags.

Inclui leitura otimizada que lê apenas até encontrar
o fechamento do frontmatter (---), evitando carregar
arquivos inteiros na memória.
"""

import re
from pathlib import Path
from typing import Any

import yaml

# Limite máximo de bytes para leitura de frontmatter (64KB)
# Frontmatters maiores que isso são raros e indicam problema
FRONTMATTER_MAX_BYTES = 64 * 1024

# Tamanho do chunk para leitura incremental (4KB)
FRONTMATTER_CHUNK_SIZE = 4 * 1024

# Regex para detectar frontmatter: --- no início de linha, sozinho
_FRONTMATTER_RE = re.compile(r"^---\s*$", re.MULTILINE)

# PyYAML pode produzir escalares, datas e coleções aninhadas. O tipo aberto
# fica restrito a esta fronteira de parsing; os extratores normalizam a saída.
type Frontmatter = dict[str, Any]


def parse_frontmatter(content: str) -> tuple[Frontmatter, str]:
    """
    Extrai frontmatter YAML do início de um arquivo markdown.

    Valida que o resultado do YAML parse é um dict — valores
    escalares (string, int) ou listas retornam {}.

    Parâmetros:
        content: conteúdo completo do arquivo .md

    Retorna:
        Tupla (metadados_dict, corpo_sem_frontmatter).
        Se não houver frontmatter, retorna ({}, content).
    """
    content = content.lstrip("\ufeff")  # Remove BOM se presente

    matches = list(_FRONTMATTER_RE.finditer(content))
    if len(matches) < 2:
        return {}, content

    # Primeiro --- deve estar no início do arquivo (possivelmente após whitespace)
    first = matches[0]
    if first.start() != 0 and content[: first.start()].strip():
        return {}, content

    second = matches[1]
    yaml_text = content[first.end() : second.start()]
    body = content[second.end() :].strip()

    try:
        metadata = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        metadata = {}

    # Validar tipo: YAML pode retornar string, int, list — só dict é válido
    if not isinstance(metadata, dict):
        return {}, body

    return metadata, body


def extract_tags(metadata: Frontmatter) -> list[str]:
    """
    Extrai tags do frontmatter. Suporta formatos comuns do Obsidian.

    Parâmetros:
        metadata: dicionário do frontmatter

    Retorna:
        Lista de tags como strings.
    """
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    elif isinstance(tags, list):
        tags = [str(t).strip() for t in tags]
    else:
        tags = []
    return [t for t in tags if t]


def extract_frontmatter_fields(metadata: Frontmatter) -> dict[str, str]:
    """
    Extrai campos estruturados do frontmatter para indexação.

    Campos suportados:
    - id: UUID v7 único da nota
    - created_at / created / date: data de criação
    - updated_at / updated / modified: data de última atualização
    - description / summary / excerpt: descrição da nota
    - status: draft, review, published, archived
    - note_type / type: daily, weekly, monthly, yearly, meeting, idea, task, person
    - category / categories: work, personal, reference, project
    - project: nome do projeto
    - source / url / link: URL ou referência da fonte

    Parâmetros:
        metadata: dicionário do frontmatter

    Retorna:
        Dict com campos extraídos (apenas não-vazios).
    """
    fields: dict[str, str] = {}

    # id: UUID único da nota
    if note_id := metadata.get("id"):
        fields["id"] = str(note_id)

    # created_at: múltiplos nomes comuns
    created = metadata.get("created_at") or metadata.get("created") or metadata.get("date")
    if created:
        # Garantir string (YAML pode retornar datetime)
        fields["created_at"] = str(created)[:19]  # Truncar se necessário (ISO)

    # updated_at: múltiplos nomes comuns
    updated = metadata.get("updated_at") or metadata.get("updated") or metadata.get("modified")
    if updated:
        fields["updated_at"] = str(updated)[:19]  # Truncar se necessário (ISO)

    # description: múltiplos nomes comuns
    description = metadata.get("description") or metadata.get("summary") or metadata.get("excerpt")
    if description and isinstance(description, str):
        fields["description"] = description[:500]  # Limitar tamanho

    # status
    status = metadata.get("status")
    if status and isinstance(status, str):
        fields["status"] = status.lower().strip()

    # note_type: 'type' é comum em Obsidian
    note_type = metadata.get("note_type") or metadata.get("type")
    if note_type and isinstance(note_type, str):
        fields["note_type"] = note_type.lower().strip()

    # category: pode ser string ou lista
    category = metadata.get("category") or metadata.get("categories")
    if category:
        if isinstance(category, list):
            category = ", ".join(str(c).strip() for c in category if c)
        elif isinstance(category, str):
            category = category.strip()
        else:
            category = str(category)
        if category:
            fields["category"] = category.lower()

    # project
    project = metadata.get("project")
    if project and isinstance(project, str):
        fields["project"] = project.strip()

    # source: URL ou referência
    source = metadata.get("source") or metadata.get("url") or metadata.get("link")
    if source and isinstance(source, str):
        fields["source"] = source.strip()[:500]  # Limitar tamanho de URLs

    return fields


def read_frontmatter_only(file_path: Path) -> tuple[Frontmatter, int]:
    """
    Lê apenas o frontmatter de um arquivo sem carregar todo o conteúdo.

    Usa leitura incremental por chunks, parando assim que encontra
    o fechamento do frontmatter (segundo ---). Muito mais eficiente
    para arquivos grandes onde só precisamos dos metadados.

    Parâmetros:
        file_path: caminho do arquivo .md

    Retorna:
        Tupla (frontmatter_dict, bytes_read).
        Se não houver frontmatter válido, retorna ({}, bytes_lidos).
    """
    bytes_read = 0
    buffer = ""

    try:
        with open(file_path, encoding="utf-8") as f:
            # Ler primeiro chunk
            chunk = f.read(FRONTMATTER_CHUNK_SIZE)
            if not chunk:
                return {}, 0

            buffer = chunk.lstrip("\ufeff")  # Remove BOM
            bytes_read = len(chunk.encode("utf-8"))

            # Verificar se começa com ---
            if not buffer.lstrip().startswith("---"):
                return {}, bytes_read

            # Procurar segundo --- incrementalmente
            while True:
                matches = list(_FRONTMATTER_RE.finditer(buffer))

                if len(matches) >= 2:
                    # Encontrou abertura e fechamento
                    first = matches[0]
                    second = matches[1]

                    # Validar que primeiro --- está no início
                    if first.start() != 0 and buffer[: first.start()].strip():
                        return {}, bytes_read

                    yaml_text = buffer[first.end() : second.start()]
                    try:
                        metadata = yaml.safe_load(yaml_text) or {}
                    except yaml.YAMLError:
                        return {}, bytes_read

                    if not isinstance(metadata, dict):
                        return {}, bytes_read

                    return metadata, bytes_read

                # Limite de segurança
                if bytes_read >= FRONTMATTER_MAX_BYTES:
                    return {}, bytes_read

                # Ler próximo chunk
                chunk = f.read(FRONTMATTER_CHUNK_SIZE)
                if not chunk:
                    # EOF sem encontrar segundo ---
                    return {}, bytes_read

                buffer += chunk
                bytes_read += len(chunk.encode("utf-8"))

    except OSError, UnicodeDecodeError:
        return {}, bytes_read
