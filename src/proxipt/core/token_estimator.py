"""Token estimation using tiktoken (for OpenAI-compatible usage stats)."""

from __future__ import annotations

import tiktoken

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def estimate_tokens(text: str) -> int:
    """Return an approximate token count for *text*."""
    if not text:
        return 0
    return len(_get_encoder().encode(text))
