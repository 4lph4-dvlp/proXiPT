"""Admin API — provider setup, health status, CAPTCHA resolution."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, UploadFile, File

from proxipt.api.schemas import ProviderStatus, SystemStatus
from proxipt.config import get_config
from proxipt.core.browser_pool import pool
from proxipt.core.router import router as provider_router
from proxipt.utils.logger import get_logger

log = get_logger("api.admin")
admin_router = APIRouter(prefix="/admin", tags=["admin"])

_start_time = time.time()
# Store GUI pages for manual resolution
_gui_pages: dict[str, object] = {}


@admin_router.get("/status")
async def system_status() -> SystemStatus:
    """Overall system health & per-provider status."""
    providers: list[ProviderStatus] = []
    cfg = get_config()

    for state in provider_router.all_states:
        pcfg = cfg.providers.get(state.name)
        session_valid = True
        if pcfg and pcfg.requires_login:
            from pathlib import Path
            session_path = Path(cfg.browser.sessions_dir) / f"{state.name}_session.json"
            session_valid = session_path.exists()

        providers.append(
            ProviderStatus(
                name=state.name,
                enabled=state.config.enabled,
                healthy=state.is_healthy,
                session_valid=session_valid,
                active_requests=state.active_requests,
                cooldown_until=state.cooldown_until if state.cooldown_until > 0 else None,
                last_error=state.last_error,
                models=[m.id for m in state.config.models],
            )
        )

    return SystemStatus(
        uptime_seconds=time.time() - _start_time,
        total_requests=provider_router.total_requests,
        providers=providers,
        queue_length=0,  # TODO: implement queue tracking
    )


@admin_router.post("/setup/{provider_name}")
async def setup_provider(provider_name: str):
    """Open a visible browser window for the provider so the user can log in.

    After logging in, call ``POST /admin/close-gui/{provider_name}`` to save
    the session and switch back to headless mode.
    """
    cfg = get_config()
    pcfg = cfg.providers.get(provider_name)
    if not pcfg:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    log.info("Opening GUI browser for %s", provider_name)
    page = await pool.open_gui(provider_name, pcfg.base_url)
    _gui_pages[provider_name] = page

    return {
        "status": "gui_opened",
        "provider": provider_name,
        "message": (
            f"A browser window has been opened at {pcfg.base_url}. "
            f"Please log in and complete any setup. "
            f"When done, call POST /admin/close-gui/{provider_name}"
        ),
    }


@admin_router.post("/resolve/{provider_name}")
async def resolve_captcha(provider_name: str):
    """Open GUI browser to resolve a CAPTCHA or other interactive challenge."""
    cfg = get_config()
    pcfg = cfg.providers.get(provider_name)
    if not pcfg:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    log.info("Opening GUI browser for CAPTCHA resolution: %s", provider_name)
    page = await pool.open_gui(provider_name, pcfg.base_url)
    _gui_pages[provider_name] = page

    return {
        "status": "gui_opened",
        "provider": provider_name,
        "message": "Resolve the CAPTCHA in the opened browser, then call POST /admin/close-gui/{provider_name}",
    }


@admin_router.post("/close-gui/{provider_name}")
async def close_gui(provider_name: str):
    """Save the GUI session and close the visible browser, reloading headless."""
    page = _gui_pages.pop(provider_name, None)
    if page is None:
        raise HTTPException(
            status_code=400,
            detail=f"No GUI browser open for '{provider_name}'. Call /admin/setup first.",
        )

    await pool.close_gui_and_save(provider_name, page)
    provider_router.mark_healthy(provider_name)

    return {
        "status": "session_saved",
        "provider": provider_name,
        "message": "Session saved. Provider is now active in headless mode.",
    }


@admin_router.get("/sessions")
async def list_sessions():
    """List all stored session files."""
    from pathlib import Path
    cfg = get_config()
    session_dir = Path(cfg.browser.sessions_dir)
    if not session_dir.exists():
        return {"sessions": []}
    files = [f.stem.replace("_session", "") for f in session_dir.glob("*_session.json")]
    return {"sessions": files}


@admin_router.delete("/sessions/{provider_name}")
async def delete_session(provider_name: str):
    """Delete a stored session (forces re-login)."""
    from pathlib import Path
    cfg = get_config()
    session_path = Path(cfg.browser.sessions_dir) / f"{provider_name}_session.json"
    if session_path.exists():
        session_path.unlink()
        return {"status": "deleted", "provider": provider_name}
    raise HTTPException(status_code=404, detail="Session not found")

@admin_router.post("/sessions/upload/{provider_name}")
async def upload_session(provider_name: str, file: UploadFile = File(...)):
    """Upload a session JSON file from the dashboard."""
    from pathlib import Path
    cfg = get_config()
    session_dir = Path(cfg.browser.sessions_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / f"{provider_name}_session.json"
    
    content = await file.read()
    try:
        import json
        json.loads(content)  # Validate it's proper JSON
        session_path.write_bytes(content)
        
        # Clear existing context if any so it reloads the new session on next request
        old_ctx = pool._contexts.pop(provider_name, None)
        if old_ctx:
            asyncio.create_task(old_ctx.close())
            
        provider_router.mark_healthy(provider_name)
        log.info("Session file uploaded and applied for %s", provider_name)
        return {"status": "uploaded", "provider": provider_name}
    except Exception as e:
        log.error("Failed to parse uploaded session for %s: %s", provider_name, e)
        raise HTTPException(status_code=400, detail=f"Invalid session file: {e}")

@admin_router.post("/provider/{provider_name}/toggle")
async def toggle_provider(provider_name: str):
    """Toggle a provider's enabled state in config.yaml and router runtime."""
    import os
    from ruamel.yaml import YAML

    # Check if provider exists in runtime
    state = provider_router._states.get(provider_name)
    if not state:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found in router")

    config_path = os.environ.get("PROXIPT_CONFIG", "config.yaml")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=500, detail="config.yaml file not found")

    yaml = YAML()
    yaml.preserve_quotes = True
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.load(f)

        if "providers" in data and provider_name in data["providers"]:
            current_state = data["providers"][provider_name].get("enabled", False)
            new_state = not current_state
            data["providers"][provider_name]["enabled"] = new_state
            
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f)
            
            # Update runtime config
            state.config.enabled = new_state
            
            if new_state:
                # Force reset health state explicitly when manually re-enabling
                state.is_healthy = True
                state.last_error = None
                state.consecutive_errors = 0
                state.cooldown_until = 0.0
            
            return {
                "status": "success",
                "provider": provider_name,
                "enabled": new_state,
                "message": f"Provider {'enabled' if new_state else 'disabled'} successfully",
            }
        else:
            raise HTTPException(status_code=400, detail="Provider not found in config.yaml")

    except Exception as e:
        log.error("Failed to toggle provider config: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Error modifying config files: {str(e)}")
