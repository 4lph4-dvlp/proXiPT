"""Browser pool — manages Playwright browser instances and pages per provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from proxipt.config import get_config
from proxipt.utils.logger import get_logger

if TYPE_CHECKING:
    from proxipt.providers.base import BaseProvider

log = get_logger("browser_pool")


class BrowserPool:
    """Manages browser lifecycle, contexts, and page pools."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

        # provider_name -> list of available pages
        self._available: dict[str, asyncio.Queue[Page]] = {}
        # provider_name -> set of all pages (for cleanup)
        self._all_pages: dict[str, list[Page]] = {}
        # provider_name -> BrowserContext
        self._contexts: dict[str, BrowserContext] = {}

        self._lock = asyncio.Lock()
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the Playwright browser."""
        if self._started:
            return
        cfg = get_config().browser
        log.info("Launching %s browser (headless=%s)", cfg.browser_type, cfg.headless)

        self._pw = await async_playwright().start()
        launcher = getattr(self._pw, cfg.browser_type)
        self._browser = await launcher.launch(
            headless=cfg.headless,
            slow_mo=cfg.slow_mo,
        )
        self._started = True
        log.info("Browser started")

    async def stop(self) -> None:
        """Close everything."""
        log.info("Shutting down browser pool")
        for name, pages in self._all_pages.items():
            for page in pages:
                try:
                    await page.close()
                except Exception:
                    pass
        for ctx in self._contexts.values():
            try:
                await ctx.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._started = False

    # ------------------------------------------------------------------
    # Context & page management
    # ------------------------------------------------------------------

    async def get_context(self, provider_name: str) -> BrowserContext:
        """Get or create a browser context for *provider_name*, restoring
        saved session state if available."""
        if provider_name in self._contexts:
            return self._contexts[provider_name]

        assert self._browser is not None, "Browser not started"
        cfg = get_config().browser
        session_path = Path(cfg.sessions_dir) / f"{provider_name}_session.json"

        kwargs: dict = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if session_path.exists():
            log.info("Restoring session for %s from %s", provider_name, session_path)
            kwargs["storage_state"] = str(session_path)

        ctx = await self._browser.new_context(**kwargs)
        self._contexts[provider_name] = ctx
        return ctx

    async def save_session(self, provider_name: str) -> None:
        """Persist cookies / localStorage for a provider context."""
        ctx = self._contexts.get(provider_name)
        if ctx is None:
            return
        cfg = get_config().browser
        session_dir = Path(cfg.sessions_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / f"{provider_name}_session.json"

        state = await ctx.storage_state()
        session_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        log.info("Session saved for %s", provider_name)

    async def acquire_page(self, provider: BaseProvider) -> Page:
        """Get a free page for a provider (blocks if all pages busy)."""
        name = provider.name
        async with self._lock:
            if name not in self._available:
                self._available[name] = asyncio.Queue()
                self._all_pages[name] = []

        queue = self._available[name]

        # Try getting an existing idle page
        if not queue.empty():
            page = await queue.get()
            if not page.is_closed():
                return page

        # Check if we can create more pages
        cfg = get_config().browser
        if len(self._all_pages[name]) < cfg.max_pages_per_provider:
            ctx = await self.get_context(name)
            page = await ctx.new_page()
            self._all_pages[name].append(page)
            log.info("Created new page for %s (total: %d)", name, len(self._all_pages[name]))
            return page

        # All pages busy — wait for one to be released
        log.debug("Waiting for free page for %s", name)
        page = await queue.get()
        return page

    async def release_page(self, provider: BaseProvider, page: Page) -> None:
        """Return a page to the available pool."""
        name = provider.name
        if name in self._available and not page.is_closed():
            await self._available[name].put(page)

    # ------------------------------------------------------------------
    # GUI mode helpers
    # ------------------------------------------------------------------

    async def open_gui(self, provider_name: str, url: str) -> Page:
        """Open a *visible* browser window for login / CAPTCHA resolution.

        This creates a separate non-headless browser for the provider,
        navigates to the URL, and returns the page so the caller can wait
        for the user to finish.
        """
        assert self._pw is not None
        cfg = get_config().browser
        launcher = getattr(self._pw, cfg.browser_type)
        gui_browser = await launcher.launch(headless=False, slow_mo=0)

        session_path = Path(cfg.sessions_dir) / f"{provider_name}_session.json"
        kwargs: dict = {"viewport": {"width": 1280, "height": 900}}
        if session_path.exists():
            kwargs["storage_state"] = str(session_path)

        ctx = await gui_browser.new_context(**kwargs)
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        log.info("GUI browser opened for %s at %s", provider_name, url)
        return page

    async def close_gui_and_save(self, provider_name: str, page: Page) -> None:
        """Save session from GUI page, close GUI browser, and reload headless context."""
        ctx = page.context
        browser = ctx.browser

        # Save session from GUI context
        cfg = get_config().browser
        session_dir = Path(cfg.sessions_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / f"{provider_name}_session.json"

        state = await ctx.storage_state()
        session_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        log.info("GUI session saved for %s", provider_name)

        await page.close()
        await ctx.close()
        if browser:
            await browser.close()

        # Discard old headless context so next acquire_page picks up new session
        old_ctx = self._contexts.pop(provider_name, None)
        if old_ctx:
            # Close old pages
            for p in self._all_pages.pop(provider_name, []):
                try:
                    await p.close()
                except Exception:
                    pass
            if provider_name in self._available:
                # Drain the queue
                q = self._available.pop(provider_name)
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            try:
                await old_ctx.close()
            except Exception:
                pass

        log.info("Headless context reset for %s — will reload session on next request", provider_name)


# Singleton
pool = BrowserPool()
