"""DuckDuckGo AI Chat provider — duck.ai"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.duckai")


class DuckAIProvider(BaseProvider):
    name = "duckai"
    base_url = "https://duck.ai"
    supports_streaming = True
    supports_images = False
    supports_system_prompt = False

    SEL_TEXTAREA = 'textarea, div[contenteditable="true"]'
    SEL_SEND = 'button[type="submit"], button[aria-label*="Send"]'
    SEL_RESPONSE = 'div[class*="response"], div.markdown, div[class*="message"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "duck.ai" not in url:
            log.info("Navigating to Duck.ai")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

            # Duck.ai may show a terms/model selection dialog
            try:
                accept = page.locator('button:has-text("Get Started"), button:has-text("Accept"), button:has-text("I Agree")')
                if await accept.count() > 0 and await accept.first.is_visible():
                    await accept.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        try:
            await page.wait_for_selector("textarea, div[contenteditable='true']", timeout=15_000)
        except Exception:
            log.warning("Duck.ai input not found")

    async def create_new_chat(self, page: Page) -> None:
        # Duck.ai resets per session — just reload
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)
        # Accept dialog if any
        try:
            accept = page.locator('button:has-text("Get Started"), button:has-text("Accept")')
            if await accept.count() > 0 and await accept.first.is_visible():
                await accept.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    async def send_message(self, page: Page, prompt: str) -> str:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        return await self._wait_for_response(
            page, self.SEL_RESPONSE, timeout=120_000, stability_checks=4,
        )

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(
            page, self.SEL_RESPONSE, timeout=120_000, stability_checks=5,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["rate limit", "too many", "try again"]):
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
