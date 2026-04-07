"""Custom provider — driven entirely by CSS selectors from config.yaml."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.config import CustomProviderConfig
from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.custom")


class CustomProvider(BaseProvider):
    """A generic provider configured via YAML selectors.

    Users can add any chat website by specifying CSS selectors for
    input, send button, response container, etc.
    """

    supports_streaming = True
    supports_images = False
    supports_system_prompt = False

    def __init__(self, cfg: CustomProviderConfig) -> None:
        self.name = cfg.name.lower().replace(" ", "_")
        self.base_url = cfg.base_url
        self._selectors = cfg.selectors

    @property
    def sel(self):
        return self._selectors

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or self.base_url not in url:
            log.info("Navigating to custom provider: %s", self.name)
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
        try:
            await page.wait_for_selector(self.sel.input_textarea, timeout=15_000)
        except Exception:
            log.warning("Custom provider %s: input not found", self.name)

    async def create_new_chat(self, page: Page) -> None:
        if self.sel.new_chat_button:
            try:
                btn = page.locator(self.sel.new_chat_button).first
                if await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
                    return
            except Exception:
                pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def send_message(self, page: Page, prompt: str) -> str:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        return await self._wait_for_response(
            page, self.sel.response_container, timeout=120_000, stability_checks=4,
        )

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(
            page, self.sel.response_container, timeout=120_000, stability_checks=5,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["rate limit", "too many", "try again", "error"]):
            return "rate_limit"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        textarea = page.locator(self.sel.input_textarea).first
        await textarea.wait_for(timeout=10_000)
        await textarea.click()
        await textarea.fill(prompt)
        await asyncio.sleep(0.3)

        if self.sel.send_button:
            try:
                btn = page.locator(self.sel.send_button).first
                if await btn.is_visible():
                    await btn.click()
                    return
            except Exception:
                pass
        await textarea.press("Enter")
