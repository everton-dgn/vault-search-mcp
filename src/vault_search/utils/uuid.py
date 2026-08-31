"""
Utilities for generating UUID v7 identifiers.

UUID v7 (RFC 9562) is timestamp-based, chronologically sortable,
and well suited to note identifiers.

Python 3.13+ provides native ``uuid.uuid7()`` support through PEP 707.
"""

import uuid


def generate_uuid7() -> str:
    """
    Generate a UUID v7 string.

    Use the native Python 3.13+ ``uuid.uuid7()`` implementation from RFC 9562.
    - 48 timestamp bits in milliseconds
    - 74 random bits
    - Chronologically sortable

    Returns:
        UUID v7 in standard form, for example
        ``019c503c-08e7-707f-9441-f4e6c5d0dd61``.
    """
    return str(uuid.uuid7())
