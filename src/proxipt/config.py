"""ProxiPT configuration system — loads YAML config + env overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8787
    api_key: str = ""
    cors_origins: list[str] = ["*"]


class BrowserConfig(BaseModel):
    headless: bool = True
    browser_type: str = "chromium"
    slow_mo: int = 0
    max_pages_per_provider: int = 2
    timeout: int = 60_000
    sessions_dir: str = "./sessions"


class ModelEntry(BaseModel):
    id: str
    name: str
    is_default: bool = False


class ProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str
    requires_login: bool = False
    max_concurrent: int = 2
    cooldown_seconds: int = 5
    models: list[ModelEntry] = Field(default_factory=list)


class RoutingRule(BaseModel):
    strategy: str = "round_robin"  # round_robin | priority
    providers: list[str] = Field(default_factory=list)


class CustomProviderSelectors(BaseModel):
    input_textarea: str
    send_button: str
    response_container: str
    new_chat_button: str = ""
    loading_indicator: str = ""
    model_selector: str = ""


class CustomProviderConfig(BaseModel):
    name: str
    base_url: str
    requires_login: bool = False
    selectors: CustomProviderSelectors
    models: list[ModelEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    routing: dict[str, RoutingRule] = Field(default_factory=dict)
    custom_providers: dict[str, CustomProviderConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_config: AppConfig | None = None


def _find_config_path() -> Path:
    """Walk up from CWD or use env var to locate config.yaml."""
    env = os.getenv("PROXIPT_CONFIG")
    if env:
        return Path(env)

    # Search CWD and parent dirs
    cur = Path.cwd()
    for _ in range(5):
        candidate = cur / "config.yaml"
        if candidate.exists():
            return candidate
        cur = cur.parent

    # Fallback — will be created with defaults if missing
    return Path.cwd() / "config.yaml"


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load and cache the config. Safe to call multiple times."""
    global _config
    if _config is not None:
        return _config

    cfg_path = Path(path) if path else _find_config_path()

    data: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Strip empty custom_providers (YAML may produce None)
    if not data.get("custom_providers"):
        data["custom_providers"] = {}

    _config = AppConfig(**data)
    
    # Environment overrides
    headless_env = os.getenv("PROXIPT_HEADLESS")
    if headless_env is not None:
        val = headless_env.lower()
        if val in ("0", "false", "no"):
            _config.browser.headless = False
        elif val in ("1", "true", "yes"):
            _config.browser.headless = True
            
    return _config


def get_config() -> AppConfig:
    """Return the cached config, loading if necessary."""
    if _config is None:
        return load_config()
    return _config


def reset_config() -> None:
    """Clear cached config — useful in tests."""
    global _config
    _config = None
