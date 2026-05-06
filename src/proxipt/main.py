"""ProxiPT — main application entry point.

Usage:
    proxipt                  # Start the API server
    python -m proxipt.main   # Same thing
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from proxipt.api.routes.admin import admin_router
from proxipt.api.routes.chat import chat_router
from proxipt.api.routes.models import models_router
from proxipt.config import get_config, load_config
from proxipt.core.browser_pool import pool
from proxipt.core.router import router as provider_router
from proxipt.utils.logger import get_logger, setup_logging

log = get_logger("main")


# ---------------------------------------------------------------------------
# Provider registry — maps config names to provider classes
# ---------------------------------------------------------------------------

def _build_provider_map():
    from proxipt.providers.deepseek import DeepSeekProvider
    from proxipt.providers.qwen import QwenProvider
    from proxipt.providers.gemini import GeminiProvider
    from proxipt.providers.chatgpt import ChatGPTProvider
    from proxipt.providers.aistudio import AIStudioProvider
    from proxipt.providers.groq_playground import GroqProvider
    from proxipt.providers.huggingchat import HuggingChatProvider
    from proxipt.providers.mistral_lechat import MistralProvider
    from proxipt.providers.duck_ai import DuckAIProvider
    from proxipt.providers.copilot import CopilotProvider
    from proxipt.providers.poe import PoeProvider
    from proxipt.providers.perplexity import PerplexityProvider
    from proxipt.providers.openrouter import OpenRouterProvider
    from proxipt.providers.kimi import KimiProvider
    from proxipt.providers.doubao import DoubaoProvider
    from proxipt.providers.chatglm import ChatGLMProvider
    from proxipt.providers.yichat import YiChatProvider
    from proxipt.providers.coze import CozeProvider
    from proxipt.providers.you import YouProvider
    from proxipt.providers.pi import PiProvider
    from proxipt.providers.metaai import MetaAIProvider
    from proxipt.providers.claude import ClaudeProvider

    return {
        # Tier 1
        "deepseek": DeepSeekProvider,
        "qwen": QwenProvider,
        "gemini": GeminiProvider,
        "chatgpt": ChatGPTProvider,
        "aistudio": AIStudioProvider,
        # Tier 2
        "groq": GroqProvider,
        "huggingchat": HuggingChatProvider,
        "mistral": MistralProvider,
        "duckai": DuckAIProvider,
        "copilot": CopilotProvider,
        "poe": PoeProvider,
        "perplexity": PerplexityProvider,
        "openrouter": OpenRouterProvider,
        # Tier 3
        "kimi": KimiProvider,
        "doubao": DoubaoProvider,
        "chatglm": ChatGLMProvider,
        "yichat": YiChatProvider,
        "coze": CozeProvider,
        "you": YouProvider,
        "pi": PiProvider,
        "metaai": MetaAIProvider,
        "claude": ClaudeProvider,
    }


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

async def _sync_models_background():
    """Background task to periodically scrape and update available models."""
    from proxipt.core.browser_pool import pool
    from proxipt.config import ModelEntry
    while True:
        log.info("Starting background model synchronization...")
        for state in provider_router.all_states:
            if not state.config.enabled:
                continue
            
            provider = state.provider
            try:
                page = await pool.acquire_page(provider)
                await provider.ensure_ready(page)
                
                fetched_models = await provider.fetch_available_models(page)
                if fetched_models:
                    log.info("Fetched dynamic models for %s: %s", provider.name, fetched_models)
                    # Update state config
                    existing_default = next((m for m in state.config.models if m.is_default), None)
                    default_id = existing_default.id if existing_default else fetched_models[0]
                    
                    new_entries = []
                    for m_id in fetched_models:
                        new_entries.append(
                            ModelEntry(
                                id=m_id,
                                name=m_id.replace("-", " ").title(),
                                is_default=(m_id == default_id)
                            )
                        )
                    # Merge with existing manually configured models
                    existing_ids = {m.id for m in state.config.models}
                    for m in new_entries:
                        if m.id not in existing_ids:
                            state.config.models.append(m)
            except Exception as e:
                log.warning("Failed to sync models for %s: %s", provider.name, e)
            finally:
                if 'page' in locals():
                    await pool.release_page(provider, page)
        
        # Sleep for 12 hours before next sync
        await asyncio.sleep(43200)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start browser pool and register providers on startup; cleanup on shutdown."""
    setup_logging()
    cfg = load_config()
    log.info("=" * 60)
    log.info("  ProxiPT - Free LLM Chat -> OpenAI API")
    log.info("=" * 60)
    log.info("Server: http://%s:%d", cfg.server.host, cfg.server.port)

    # Start browser
    await pool.start()

    # Register built-in providers
    provider_map = _build_provider_map()
    for pname, pcfg in cfg.providers.items():
        cls = provider_map.get(pname)
        if cls is None:
            log.warning("No implementation for provider '%s' - skipping", pname)
            continue
        provider_instance = cls()
        provider_router.register(provider_instance, pcfg)

    # Register custom providers
    if cfg.custom_providers:
        from proxipt.providers.custom import CustomProvider
        for cname, ccfg in cfg.custom_providers.items():
            from proxipt.config import ProviderConfig, ModelEntry
            custom_provider = CustomProvider(ccfg)
            # Create a ProviderConfig for the router
            pcfg = ProviderConfig(
                enabled=True,
                base_url=ccfg.base_url,
                requires_login=ccfg.requires_login,
                models=[ModelEntry(id=m.id, name=m.name, is_default=m.is_default) for m in ccfg.models],
            )
            provider_router.register(custom_provider, pcfg)

    enabled = [s.name for s in provider_router.all_states if s.config.enabled]
    log.info("Active providers: %s", ", ".join(enabled) or "(none)")
    log.info("Virtual models:   %s", ", ".join(cfg.routing.keys()) or "(none)")
    log.info("=" * 60)

    # Start background model syncer
    sync_task = asyncio.create_task(_sync_models_background())

    yield  # --- app is running ---

    log.info("Shutting down...")
    sync_task.cancel()
    await pool.stop()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ProxiPT",
    description="Free LLM Chat → OpenAI-Compatible API via Playwright",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Middleware ---

def _setup_middleware(app: FastAPI):
    cfg = get_config()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# API key auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    cfg = get_config()
    if cfg.server.api_key:
        # Skip auth for docs and admin dashboard paths
        if request.url.path in ("/docs", "/openapi.json", "/redoc") or request.url.path.startswith("/dashboard") or request.url.path.startswith("/admin"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != cfg.server.api_key:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "Invalid API key", "type": "auth_error"}},
            )
    return await call_next(request)


# --- Routes ---
import os
static_dir = os.path.join(os.path.dirname(__file__), "api", "static")
app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="static")

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def cli():
    """Console entry point."""
    setup_logging()
    cfg = load_config()
    _setup_middleware(app)
    uvicorn.run(
        "proxipt.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    cli()
