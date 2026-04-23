"""Perplexity AI provider — perplexity.ai"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.perplexity")


class PerplexityProvider(BaseProvider):
    name = "perplexity"
    base_url = "https://www.perplexity.ai"
    supports_streaming = True
    supports_images = False
    supports_system_prompt = False

    SEL_TEXTAREA = 'textarea[placeholder*="Ask"], textarea, div[contenteditable="true"]'
    SEL_SEND = 'button[aria-label*="Submit"], button[aria-label*="Send"], button[type="submit"]'
    SEL_RESPONSE = 'div.prose, div[class*="answer"], div[class*="response"] .markdown'
    SEL_NEW_CHAT = 'a[href="/"], button[aria-label*="New"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "perplexity.ai" not in url:
            log.info("Navigating to Perplexity")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
        try:
            await page.wait_for_selector('textarea, div[contenteditable="true"]', timeout=15_000)
        except Exception:
            log.warning("Perplexity input not found")

    async def create_new_chat(self, page: Page) -> None:
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def send_message(self, page: Page, prompt: str) -> str:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        return await self._wait_for_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=5)

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=6):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["rate limit", "too many", "try again"]):
            return "rate_limit"
        if any(p in lower for p in ["sign in", "log in", "sign up"]):
            try:
                ta = page.locator('textarea, div[contenteditable="true"]')
                if await ta.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        textarea = page.locator("textarea").first
        try:
            await textarea.wait_for(timeout=10_000)
            await textarea.click()
            await textarea.fill(prompt)
        except Exception:
            editor = page.locator("div[contenteditable='true']").first
            await editor.click()
            await page.keyboard.insert_text(prompt)
        await asyncio.sleep(0.3)
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible():
                await send_btn.click()
                return
        except Exception:
            pass
        await page.keyboard.press("Enter")
        await __import__("asyncio").sleep(0.1)
        await page.keyboard.press("Control+Enter")
        await __import__("asyncio").sleep(0.1)
        await page.keyboard.press("Meta+Enter")
