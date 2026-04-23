"""Pi AI provider — pi.ai"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.pi")


class PiProvider(BaseProvider):
    name = "pi"
    base_url = "https://pi.ai/talk"
    supports_streaming = True
    supports_images = False
    supports_system_prompt = False

    SEL_TEXTAREA = 'textarea, div[contenteditable="true"], input[type="text"]'
    SEL_SEND = 'button[aria-label*="Send"], button[type="submit"]'
    SEL_RESPONSE = 'div[class*="message"][class*="bot"], div[class*="response"], div[class*="ai-message"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "pi.ai" not in url:
            log.info("Navigating to Pi")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
        try:
            await page.wait_for_selector('textarea, div[contenteditable="true"], input[type="text"]', timeout=15_000)
        except Exception:
            log.warning("Pi input not found")

    async def create_new_chat(self, page: Page) -> None:
        # Pi doesn't have a traditional "new chat" — it's continuous
        # Just navigate back to start
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def send_message(self, page: Page, prompt: str) -> str:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        return await self._wait_for_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=4)

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=5):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["rate limit", "too many", "slow down"]):
            return "rate_limit"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        textarea = page.locator("textarea").first
        try:
            await textarea.wait_for(timeout=10_000)
            await textarea.click()
            await textarea.fill(prompt)
        except Exception:
            inp = page.locator('input[type="text"]').first
            await inp.click()
            await inp.fill(prompt)
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
