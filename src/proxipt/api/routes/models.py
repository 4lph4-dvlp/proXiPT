"""GET /v1/models — list available models in OpenAI format."""

from __future__ import annotations

from fastapi import APIRouter

from proxipt.api.schemas import ModelListResponse, ModelObject
from proxipt.config import get_config
from proxipt.core.router import router as provider_router

models_router = APIRouter()


@models_router.get("/v1/models")
async def list_models() -> ModelListResponse:
    """Return all available models across all enabled providers."""
    cfg = get_config()
    models: list[ModelObject] = []
    seen: set[str] = set()

    # Built-in providers
    for pname, pcfg in cfg.providers.items():
        if not pcfg.enabled:
            continue
        for m in pcfg.models:
            model_id = m.id
            if model_id not in seen:
                models.append(
                    ModelObject(id=model_id, owned_by=f"proxipt:{pname}")
                )
                seen.add(model_id)

    # Custom providers
    for cname, ccfg in cfg.custom_providers.items():
        for m in ccfg.models:
            model_id = m.id
            if model_id not in seen:
                models.append(
                    ModelObject(id=model_id, owned_by=f"proxipt:custom:{cname}")
                )
                seen.add(model_id)

    # Virtual routing models
    for vname in cfg.routing:
        if vname not in seen:
            models.append(ModelObject(id=vname, owned_by="proxipt:router"))
            seen.add(vname)

    return ModelListResponse(data=models)
