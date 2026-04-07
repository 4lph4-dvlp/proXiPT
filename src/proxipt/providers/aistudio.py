"""Google AI Studio provider — aistudio.google.com"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.aistudio")


class AIStudioProvider(BaseProvider):
    name = "aistudio"
    base_url = "https://aistudio.google.com/prompts/new_chat"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = True  # AI Studio has a system instruction field

    SEL_TEXTAREA = 'textarea[aria-label*="prompt"], div[contenteditable="true"], textarea'
    SEL_SEND = 'button[aria-label*="Run"], button[mattooltip*="Run"], button.run-button'
    SEL_RESPONSE = 'div.response-container .markdown, ms-text-chunk, div[class*="response"]'
    SEL_NEW_CHAT = 'a[href*="new_chat"], button[aria-label*="New"]'
    SEL_SYSTEM = 'textarea[aria-label*="System"], div[class*="system-instruction"] textarea'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "aistudio.google.com" not in url:
            log.info("Navigating to AI Studio")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

        try:
            await page.wait_for_selector(
                "textarea, div[contenteditable='true']",
                timeout=15_000,
            )
        except Exception:
            log.warning("AI Studio input not found")

    async def create_new_chat(self, page: Page) -> None:
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def set_system_prompt(self, page: Page, prompt: str) -> None:
        try:
            sys_input = page.locator(self.SEL_SYSTEM).first
            if await sys_input.is_visible():
                await sys_input.click()
                await sys_input.fill(prompt)
                log.info("System prompt set")
        except Exception as e:
            log.debug("Could not set system prompt: %s", e)

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
            page, self.SEL_RESPONSE, timeout=120_000, stability_checks=6,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["quota exceeded", "rate limit", "too many"]):
            return "rate_limit"
        if any(p in lower for p in ["sign in", "accounts.google.com"]):
            try:
                ta = page.locator("textarea, div[contenteditable='true']")
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
            run_btn = page.locator(self.SEL_SEND).first
            if await run_btn.is_visible():
                await run_btn.click()
                return
        except Exception:
            pass
        await page.keyboard.press("Enter")
