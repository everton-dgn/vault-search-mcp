"""
Utilitários para geração de UUID v7.

UUID v7 (RFC 9562) é baseado em timestamp, ordenável cronologicamente
e ideal para identificadores de notas.

Python 3.13+ tem uuid.uuid7() nativo (PEP 707).
"""

import uuid


def generate_uuid7() -> str:
    """
    Gera UUID v7 como string.

    Usa o uuid.uuid7() nativo do Python 3.13+ (RFC 9562 compliant).
    - 48 bits de timestamp (ms)
    - 74 bits de random
    - Ordenável cronologicamente

    Retorna:
        UUID v7 no formato padrão (ex: '019c503c-08e7-707f-9441-f4e6c5d0dd61')
    """
    return str(uuid.uuid7())
