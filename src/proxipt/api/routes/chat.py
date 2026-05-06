"""POST /v1/chat/completions — the main OpenAI-compatible endpoint."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from proxipt.api.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorDetail,
    ErrorResponse,
)
from proxipt.core.browser_pool import pool
from proxipt.core.message_compiler import compile_messages, compile_messages_flat
from proxipt.core.response_parser import build_chunk, build_completion_response, build_done_chunk
from proxipt.core.router import router as provider_router
from proxipt.utils.logger import get_logger

log = get_logger("api.chat")
chat_router = APIRouter()


@chat_router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, raw_request: Request):
    """Handle a chat completion request (streaming or non-streaming)."""
    try:
        provider, model_id = await provider_router.resolve(
            req.model, preferred_provider=req.provider
        )
    except LookupError as e:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=str(e),
                    type="invalid_request_error",
                    code="model_not_found",
                )
            ).model_dump(),
        )

    # Compile messages
    system_prompt, user_prompt = compile_messages(req.messages)

    # For providers without system prompt support, merge everything
    if not provider.supports_system_prompt and system_prompt:
        prompt = compile_messages_flat(req.messages)
    else:
        prompt = user_prompt

    if req.stream:
        return StreamingResponse(
            _stream_response(provider, prompt, system_prompt, req.model, model_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await _complete_response(provider, prompt, system_prompt, req.model, model_id)


async def _complete_response(provider, prompt, system_prompt, requested_model, model_id):
    """Non-streaming: send prompt, wait for full response, return."""
    page = await pool.acquire_page(provider)
    try:
        await provider_router.on_request_start(provider)

        await provider.ensure_ready(page)
        
        # Check for provider errors (auth, captcha, rate limits)
        err = await provider.detect_error(page)
        if err == "rate_limit":
            await provider_router.on_rate_limit(provider)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded for {provider.name}")
        elif err == "captcha":
            await provider_router.on_captcha(provider)
            raise HTTPException(status_code=403, detail=f"CAPTCHA detected for {provider.name}")
        elif err == "auth_expired":
            await provider_router.on_request_error(provider, "auth_expired")
            raise HTTPException(status_code=401, detail=f"Authentication expired for {provider.name}")

        if hasattr(provider, "select_model") and model_id != "default":
            await provider.select_model(page, model_id)

        # Send and get response — provider handles navigation internally
        response_text = await provider.send_message(page, prompt)

        if not response_text:
            await provider_router.on_request_error(provider, "empty_response")
            raise HTTPException(status_code=502, detail="Empty response from provider")

        await provider_router.on_request_success(provider)

        # Apply stop sequences
        response_text = _apply_stop(response_text, None)  # TODO: pass stop from request

        return build_completion_response(
            text=response_text,
            model=requested_model,
            prompt_text=prompt,
        )
    except HTTPException:
        raise
    except Exception as e:
        await provider_router.on_request_error(provider, str(e))
        log.error("Error in %s: %s", provider.name, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")
    finally:
        await pool.release_page(provider, page)


async def _stream_response(provider, prompt, system_prompt, requested_model, model_id):
    """Generator that yields SSE events for streaming responses."""
    chunk_id = f"proxipt-{uuid.uuid4().hex[:12]}"
    page = await pool.acquire_page(provider)

    try:
        await provider_router.on_request_start(provider)

        await provider.ensure_ready(page)

        # Check for provider errors (auth, captcha, rate limits)
        err = await provider.detect_error(page)
        if err == "rate_limit":
            await provider_router.on_rate_limit(provider)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded for {provider.name}")
        elif err == "captcha":
            await provider_router.on_captcha(provider)
            raise HTTPException(status_code=403, detail=f"CAPTCHA detected for {provider.name}")
        elif err == "auth_expired":
            await provider_router.on_request_error(provider, "auth_expired")
            raise HTTPException(status_code=401, detail=f"Authentication expired for {provider.name}")

        if hasattr(provider, "select_model") and model_id != "default":
            await provider.select_model(page, model_id)

        # First chunk with role
        first_chunk = build_chunk("", requested_model, chunk_id, include_role=True)
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        # Stream content chunks — provider handles navigation internally
        async for text_chunk in provider.send_message_streaming(page, prompt):
            if text_chunk:
                chunk = build_chunk(text_chunk, requested_model, chunk_id)
                yield f"data: {chunk.model_dump_json()}\n\n"

        # Final chunk
        done_chunk = build_done_chunk(requested_model, chunk_id)
        yield f"data: {done_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

        await provider_router.on_request_success(provider)

    except Exception as e:
        await provider_router.on_request_error(provider, str(e))
        log.error("Stream error in %s: %s", provider.name, e, exc_info=True)
        error_data = {"error": {"message": str(e), "type": "server_error"}}
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        await pool.release_page(provider, page)


def _apply_stop(text: str, stop: str | list[str] | None) -> str:
    """Truncate response at the first occurrence of any stop sequence."""
    if not stop:
        return text
    if isinstance(stop, str):
        stop = [stop]
    for seq in stop:
        idx = text.find(seq)
        if idx != -1:
            text = text[:idx]
    return text
