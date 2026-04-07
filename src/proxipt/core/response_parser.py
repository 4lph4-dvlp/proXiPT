"""Convert raw text responses into OpenAI-compatible JSON structures."""

from __future__ import annotations

import time
import uuid

from proxipt.api.schemas import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    ChunkChoice,
    DeltaMessage,
    UsageInfo,
)
from proxipt.core.token_estimator import estimate_tokens


def build_completion_response(
    text: str,
    model: str,
    prompt_text: str = "",
    finish_reason: str = "stop",
) -> ChatCompletionResponse:
    """Build a full (non-streaming) chat completion response."""
    prompt_tokens = estimate_tokens(prompt_text)
    completion_tokens = estimate_tokens(text)

    return ChatCompletionResponse(
        id=f"proxipt-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=model,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=text),
                finish_reason=finish_reason,
            )
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def build_chunk(
    text: str,
    model: str,
    chunk_id: str,
    finish_reason: str | None = None,
    include_role: bool = False,
) -> ChatCompletionChunk:
    """Build a single SSE chunk for streaming responses."""
    delta = DeltaMessage(content=text)
    if include_role:
        delta.role = "assistant"

    return ChatCompletionChunk(
        id=chunk_id,
        created=int(time.time()),
        model=model,
        choices=[
            ChunkChoice(
                index=0,
                delta=delta,
                finish_reason=finish_reason,
            )
        ],
    )


def build_done_chunk(
    model: str,
    chunk_id: str,
) -> ChatCompletionChunk:
    """Build the final chunk with finish_reason='stop' and empty content."""
    return ChatCompletionChunk(
        id=chunk_id,
        created=int(time.time()),
        model=model,
        choices=[
            ChunkChoice(
                index=0,
                delta=DeltaMessage(),
                finish_reason="stop",
            )
        ],
    )
