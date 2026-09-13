"""Six-character Crockford base32 join codes (no I, L, O, U)."""

from __future__ import annotations

import secrets
from typing import Protocol

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 8  # 8 chars over a 32-symbol alphabet is ~40 bits: resists guessing a private room's code
_AMBIGUOUS = str.maketrans({"I": "1", "L": "1", "O": "0", "-": "", " ": ""})


class _Rng(Protocol):
    def choice(self, seq: str, /) -> str: ...


def new_join_code(rng: _Rng = secrets) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(LENGTH))


def normalise_code(text: str) -> str:
    """Upper-case, strip separators, map ambiguous look-alike characters (i/l -> 1, o -> 0)."""
    return text.strip().upper().translate(_AMBIGUOUS)


def is_valid_code(text: str) -> bool:
    return len(text) == LENGTH and all(c in ALPHABET for c in text)
