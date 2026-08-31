"""
Utilitários de segurança para queries e validação de paths.
"""

from pathlib import Path


def escape_sql_string(value: str) -> str:
    """
    Escapa string para uso seguro em queries SQL/LanceDB.

    Previne SQL injection escapando aspas simples.

    Parâmetros:
        value: string a ser escapada

    Retorna:
        String com aspas simples duplicadas.

    Exemplo:
        "O'Brien" -> "O''Brien"
    """
    if not value:
        return value
    # Escapar aspas simples duplicando-as (padrão SQL)
    return value.replace("'", "''")


def escape_like_pattern(value: str) -> str:
    """
    Escapa caracteres especiais em patterns LIKE.

    Previne matching indesejado de wildcards.

    Parâmetros:
        value: pattern a ser escapado

    Retorna:
        String com %, _ e \\ escapados.
    """
    if not value:
        return value
    # Escapar caracteres especiais de LIKE
    value = value.replace("\\", "\\\\")
    value = value.replace("%", "\\%")
    value = value.replace("_", "\\_")
    # Também escapar aspas simples
    value = value.replace("'", "''")
    return value


def validate_relative_path(relative_path: str) -> bool:
    """
    Valida que um path relativo não contém traversal.

    Previne path traversal attacks (../../etc/passwd).

    Parâmetros:
        relative_path: path relativo a ser validado

    Retorna:
        True se o path é seguro, False caso contrário.
    """
    if not relative_path:
        return False

    # Rejeitar paths absolutos
    if relative_path.startswith("/") or relative_path.startswith("\\"):
        return False

    # Normalizar e verificar componentes
    path = Path(relative_path)

    # Rejeitar .. em qualquer parte
    for part in path.parts:
        if part == "..":
            return False
        # Rejeitar caracteres nulos (null byte injection)
        if "\x00" in part:
            return False

    # Verificar se o path normalizado não escapa do diretório base
    try:
        # Resolve relativo a um diretório fictício
        base = Path("/safe/base")
        resolved = (base / relative_path).resolve()
        # Deve permanecer dentro do base
        return str(resolved).startswith(str(base))
    except ValueError, OSError:
        return False
