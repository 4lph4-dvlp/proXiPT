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

        # provider_name -> concurrency semaphore
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # provider_name -> set of all pages (for cleanup)
        self._all_pages: dict[str, list[Page]] = {}
        # provider_name -> BrowserContext
        self._contexts: dict[str, BrowserContext] = {}
        # provider_name -> last used timestamp
        self._last_used: dict[str, float] = {}

        self._lock = asyncio.Lock()
        self._started = False
        self._cleanup_task: asyncio.Task | None = None

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
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"],
            ignore_default_args=["--enable-automation"],
        )
        self._started = True
        import time
        self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())
        log.info("Browser started")

    async def stop(self) -> None:
        """Close everything."""
        log.info("Shutting down browser pool")
        if self._cleanup_task:
            self._cleanup_task.cancel()
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

    async def _idle_cleanup_loop(self) -> None:
        """Periodically close idle contexts to save RAM (critical for GCP e2-micro)."""
        import time
        IDLE_TIMEOUT = 300  # 5 minutes
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                async with self._lock:
                    to_close = []
                    for name, ctx in list(self._contexts.items()):
                        # If there are no active pages and it's been idle for > 5 mins
                        active_pages = len(self._all_pages.get(name, []))
                        last_used = self._last_used.get(name, now)
                        if active_pages == 0 and (now - last_used) > IDLE_TIMEOUT:
                            to_close.append((name, ctx))
                    
                    for name, ctx in to_close:
                        log.info("Closing idle context for %s to save RAM", name)
                        await self.save_session(name)
                        del self._contexts[name]
                        if name in self._last_used:
                            del self._last_used[name]
                        # Run close synchronously in background to avoid blocking lock
                        asyncio.create_task(ctx.close())
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Error in idle cleanup loop: %s", e)

    # ------------------------------------------------------------------
    # Context & page management
    # ------------------------------------------------------------------

    async def get_context(self, provider_name: str) -> BrowserContext:
        """Get or create a browser context for *provider_name*, restoring
        saved session state if available."""
        import time
        self._last_used[provider_name] = time.time()
        
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
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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
        """Create a new page strictly governed by the max_pages_per_provider semaphore."""
        name = provider.name
        async with self._lock:
            if name not in self._semaphores:
                cfg = get_config().browser
                self._semaphores[name] = asyncio.Semaphore(cfg.max_pages_per_provider)
                self._all_pages[name] = []

        # Wait for a concurrency slot
        log.debug("Waiting for slot for %s", name)
        await self._semaphores[name].acquire()

        # Create a brand new page for pristine isolation
        ctx = await self.get_context(name)
        page = await ctx.new_page()
        self._all_pages[name].append(page)
        log.info("Created new page for %s", name)
        return page

    async def release_page(self, provider: BaseProvider, page: Page) -> None:
        """Release a page after use by completely closing it."""
        name = provider.name

        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass
            
        if name in self._all_pages and page in self._all_pages[name]:
            self._all_pages[name].remove(page)

        if name in self._semaphores:
            self._semaphores[name].release()

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
        gui_browser = await launcher.launch(
            headless=False, 
            slow_mo=0,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        session_path = Path(cfg.sessions_dir) / f"{provider_name}_session.json"
        kwargs: dict = {"viewport": {"width": 1280, "height": 900}}
        if session_path.exists():
            kwargs["storage_state"] = str(session_path)

        ctx = await gui_browser.new_context(**kwargs)
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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
            try:
                await old_ctx.close()
            except Exception:
                pass

        log.info("Headless context reset for %s — will reload session on next request", provider_name)


# Singleton
pool = BrowserPool()
