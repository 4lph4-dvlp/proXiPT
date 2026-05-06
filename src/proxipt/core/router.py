"""Provider router — selects the best available provider for each request."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from proxipt.config import get_config, ProviderConfig, RoutingRule
from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("router")


@dataclass
class ProviderState:
    """Runtime health state for a single provider."""
    name: str
    provider: BaseProvider
    config: ProviderConfig
    last_request_time: float = 0.0
    request_count: int = 0
    consecutive_errors: int = 0
    cooldown_until: float = 0.0
    is_healthy: bool = True
    active_requests: int = 0
    last_error: str | None = None

    @property
    def is_available(self) -> bool:
        return (
            self.config.enabled
            and self.is_healthy
            and time.time() >= self.cooldown_until
            and self.active_requests < self.config.max_concurrent
        )


class ProviderRouter:
    """Manages provider selection, health tracking, and round-robin routing."""

    def __init__(self) -> None:
        self._states: dict[str, ProviderState] = {}
        self._lock = asyncio.Lock()
        self._total_requests: int = 0

    def register(self, provider: BaseProvider, config: ProviderConfig) -> None:
        """Register a provider with the router."""
        self._states[provider.name] = ProviderState(
            name=provider.name,
            provider=provider,
            config=config,
        )
        log.info("Registered provider: %s (%d models)", provider.name, len(config.models))

    def get_provider(self, name: str) -> BaseProvider | None:
        """Get a provider by name."""
        state = self._states.get(name)
        return state.provider if state else None

    def get_state(self, name: str) -> ProviderState | None:
        return self._states.get(name)

    @property
    def all_states(self) -> list[ProviderState]:
        return list(self._states.values())

    @property
    def total_requests(self) -> int:
        return self._total_requests

    # ------------------------------------------------------------------
    # Model → Provider resolution
    # ------------------------------------------------------------------

    async def resolve(self, model: str, preferred_provider: str | None = None) -> tuple[BaseProvider, str]:
        """Find the best provider for the requested *model*.

        Returns ``(provider, actual_model_id)`` or raises ``LookupError``.
        """
        cfg = get_config()

        # 1) Check if model is a virtual routing rule (e.g. "auto", "best-free")
        if model in cfg.routing:
            return await self._resolve_virtual(model, cfg.routing[model])

        # 2) If caller forced a specific provider
        if preferred_provider:
            state = self._states.get(preferred_provider)
            if state and state.is_available:
                return state.provider, model
            raise LookupError(f"Provider '{preferred_provider}' is not available")

        # 3) Find providers that serve this model
        candidates: list[ProviderState] = []
        for state in self._states.values():
            if not state.config.enabled:
                continue
            model_ids = [m.id for m in state.config.models]
            if model in model_ids or model.startswith(state.name):
                candidates.append(state)

        if not candidates:
            raise LookupError(f"No provider found for model '{model}'")

        # Pick the best available candidate (LRU)
        available = [c for c in candidates if c.is_available]
        if not available:
            # All on cooldown — pick the one whose cooldown ends soonest
            candidates.sort(key=lambda s: s.cooldown_until)
            wait_time = candidates[0].cooldown_until - time.time()
            if wait_time > 0:
                log.info("All providers for '%s' on cooldown, waiting %.1fs", model, wait_time)
                await asyncio.sleep(wait_time)
            return candidates[0].provider, model

        available.sort(key=lambda s: s.last_request_time)
        return available[0].provider, model

    async def _resolve_virtual(self, vmodel: str, rule: RoutingRule) -> tuple[BaseProvider, str]:
        """Resolve a virtual model by trying providers according to their strategy."""
        candidates: list[ProviderState] = []
        for pname in rule.providers:
            state = self._states.get(pname)
            if state and state.config.enabled:
                candidates.append(state)

        if not candidates:
            raise LookupError(f"No enabled providers found for virtual model '{vmodel}'")

        available = [c for c in candidates if c.is_available]
        if not available:
            candidates.sort(key=lambda s: s.cooldown_until)
            wait_time = candidates[0].cooldown_until - time.time()
            if wait_time > 0:
                log.info("All providers for '%s' on cooldown, waiting %.1fs", vmodel, wait_time)
                await asyncio.sleep(wait_time)
            best_state = candidates[0]
        else:
            if rule.strategy == "round_robin":
                available.sort(key=lambda s: s.last_request_time)
            best_state = available[0]

        default_model = next(
            (m.id for m in best_state.config.models if m.is_default),
            best_state.config.models[0].id if best_state.config.models else "default",
        )
        return best_state.provider, default_model

    # ------------------------------------------------------------------
    # Request lifecycle hooks
    # ------------------------------------------------------------------

    async def on_request_start(self, provider: BaseProvider) -> None:
        async with self._lock:
            state = self._states[provider.name]
            state.active_requests += 1
            state.last_request_time = time.time()
            self._total_requests += 1

    async def on_request_success(self, provider: BaseProvider) -> None:
        async with self._lock:
            state = self._states[provider.name]
            state.active_requests = max(0, state.active_requests - 1)
            state.consecutive_errors = 0
            state.request_count += 1

    async def on_request_error(self, provider: BaseProvider, error: str) -> None:
        async with self._lock:
            state = self._states[provider.name]
            state.active_requests = max(0, state.active_requests - 1)
            state.consecutive_errors += 1
            state.last_error = error
            log.warning("Provider %s error #%d: %s", provider.name, state.consecutive_errors, error)

            # Circuit breaker: 3 consecutive errors - cooldown
            if state.consecutive_errors >= 3:
                cooldown = state.config.cooldown_seconds * state.consecutive_errors
                state.cooldown_until = time.time() + cooldown
                state.is_healthy = False
                log.error("Provider %s circuit opened - cooldown %ds", provider.name, cooldown)

    async def on_rate_limit(self, provider: BaseProvider) -> None:
        """Called when rate-limit is detected on the web page."""
        async with self._lock:
            state = self._states[provider.name]
            state.active_requests = max(0, state.active_requests - 1)
            cooldown = max(60, state.config.cooldown_seconds * 10)
            state.cooldown_until = time.time() + cooldown
            state.last_error = "rate_limit_detected"
            log.warning("Rate limit detected for %s - cooldown %ds", provider.name, cooldown)

    async def on_captcha(self, provider: BaseProvider) -> None:
        """Called when CAPTCHA is detected - disable until manual resolution."""
        async with self._lock:
            state = self._states[provider.name]
            state.is_healthy = False
            state.last_error = "captcha_required"
            log.error("CAPTCHA detected for %s - provider disabled until resolved", provider.name)

    def mark_healthy(self, provider_name: str) -> None:
        """Manually re-enable a provider (after login / CAPTCHA fix)."""
        state = self._states.get(provider_name)
        if state:
            state.is_healthy = True
            state.cooldown_until = 0.0
            state.consecutive_errors = 0
            state.last_error = None
            log.info("Provider %s marked healthy", provider_name)


# Singleton
router = ProviderRouter()
