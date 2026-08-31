"""
Chunking hierárquico de texto com sobreposição.

Divide texto longo em chunks respeitando separadores naturais
(parágrafos > linhas > frases > palavras) com overlap controlado.
"""

from vault_search.config.chunking import CHUNK_SEPARATORS


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Divide texto em chunks com sobreposição.

    O overlap é aplicado APENAS no nível superior, evitando
    empilhamento entre níveis de recursão que causaria chunks
    maiores que chunk_size.

    Parâmetros:
        text: texto a dividir
        chunk_size: tamanho máximo de cada chunk (incluindo overlap)
        overlap: sobreposição entre chunks

    Retorna:
        Lista de chunks como strings.
    """
    if len(text) <= chunk_size:
        return [text]

    # Chunking SEM overlap — apenas split hierárquico
    chunks = _chunk_with_separators(text, chunk_size, CHUNK_SEPARATORS, 0)

    # Aplicar overlap APENAS aqui, no nível superior
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prefix = _get_overlap_prefix(chunks[i - 1], overlap)
            # Garantir que chunk + prefix não exceda chunk_size
            max_prefix = max(0, chunk_size - len(chunks[i]))
            if len(prefix) > max_prefix:
                prefix = prefix[-max_prefix:] if max_prefix > 0 else ""
            overlapped.append(prefix + chunks[i])
        return overlapped

    return chunks


def _chunk_with_separators(
    text: str, chunk_size: int, separators: list[str], sep_idx: int
) -> list[str]:
    """
    Implementação recursiva do chunking hierárquico (sem overlap).

    Divide usando o separador atual. Para partes que ainda excedem
    chunk_size, aplica recursivamente o próximo separador.

    Parâmetros:
        text: texto a dividir
        chunk_size: tamanho máximo
        separators: lista hierárquica de separadores
        sep_idx: índice do separador atual
    """
    if len(text) <= chunk_size:
        return [text]

    # Se esgotamos todos os separadores, corte duro
    if sep_idx >= len(separators):
        chunks = []
        for i in range(0, len(text), chunk_size):
            chunks.append(text[i : i + chunk_size])
        return chunks

    sep = separators[sep_idx]
    parts = text.split(sep)

    if len(parts) <= 1:
        # Este separador não funciona, tentar o próximo
        return _chunk_with_separators(text, chunk_size, separators, sep_idx + 1)

    # Agrupar parts em chunks respeitando chunk_size
    raw_chunks = []
    current = ""

    for part in parts:
        candidate = current + sep + part if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                raw_chunks.append(current)
            current = part

    if current:
        raw_chunks.append(current)

    # Recursão: qualquer raw_chunk que ainda excede chunk_size
    # é subdividido com o próximo separador
    final_chunks = []
    for rc in raw_chunks:
        if len(rc) > chunk_size:
            sub = _chunk_with_separators(rc, chunk_size, separators, sep_idx + 1)
            final_chunks.extend(sub)
        else:
            final_chunks.append(rc)

    return final_chunks


def _get_overlap_prefix(text: str, overlap: int) -> str:
    """
    Extrai os últimos ~overlap caracteres do texto, respeitando
    fronteira de palavras (não corta no meio de uma palavra).

    Parâmetros:
        text: texto do chunk anterior
        overlap: quantidade alvo de caracteres

    Retorna:
        Sufixo do texto que respeita fronteira de palavra.
    """
    if len(text) <= overlap:
        return text

    candidate = text[-overlap:]
    # Encontrar primeiro espaço para não cortar palavra
    space_idx = candidate.find(" ")
    if space_idx > 0 and space_idx < len(candidate) - 1:
        return candidate[space_idx + 1 :]
    return candidate
