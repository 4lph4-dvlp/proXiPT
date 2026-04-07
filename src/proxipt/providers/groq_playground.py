"""Groq Playground provider — console.groq.com/playground"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.groq")


class GroqProvider(BaseProvider):
    name = "groq"
    base_url = "https://console.groq.com/playground"
    supports_streaming = True
    supports_images = False
    supports_system_prompt = True

    SEL_TEXTAREA = 'textarea[placeholder*="message"], textarea[name*="content"], textarea'
    SEL_SEND = 'button:has-text("Submit"), button[type="submit"]'
    SEL_RESPONSE = 'div[class*="response"], div[class*="output"], pre, div[class*="message"][class*="assistant"]'
    SEL_SYSTEM = 'textarea[placeholder*="System"], textarea:first-of-type'
    SEL_MODEL_SELECT = 'select[class*="model"], button[class*="model"], div[class*="model-select"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "console.groq.com" not in url:
            log.info("Navigating to Groq Playground")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

        try:
            await page.wait_for_selector("textarea", timeout=15_000)
        except Exception:
            log.warning("Groq textarea not found")

    async def create_new_chat(self, page: Page) -> None:
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def set_system_prompt(self, page: Page, prompt: str) -> None:
        try:
            sys_ta = page.locator(self.SEL_SYSTEM).first
            if await sys_ta.is_visible():
                await sys_ta.click()
                await sys_ta.fill(prompt)
                log.info("System prompt set on Groq")
        except Exception as e:
            log.debug("Could not set system prompt: %s", e)

    async def send_message(self, page: Page, prompt: str) -> str:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        return await self._wait_for_response(
            page, self.SEL_RESPONSE, timeout=60_000, stability_checks=3,
        )

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(
            page, self.SEL_RESPONSE, timeout=60_000, stability_checks=4,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["rate limit", "too many requests", "429"]):
            return "rate_limit"
        if any(p in lower for p in ["sign in", "log in"]):
            try:
                ta = page.locator("textarea")
                if await ta.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        # Groq playground usually has multiple textareas — system + user
        textareas = page.locator("textarea")
        count = await textareas.count()
        # Use the last textarea (user message)
        target = textareas.last if count > 1 else textareas.first
        await target.click()
        await target.fill(prompt)
        await asyncio.sleep(0.3)

        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible():
                await send_btn.click()
                return
        except Exception:
            pass
        await target.press("Enter")
