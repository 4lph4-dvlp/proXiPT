"""Microsoft Copilot provider — copilot.microsoft.com"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.copilot")


class CopilotProvider(BaseProvider):
    name = "copilot"
    base_url = "https://copilot.microsoft.com"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = 'textarea, div[contenteditable="true"], #userInput'
    SEL_SEND = 'button[aria-label*="Submit"], button[aria-label*="Send"], button[type="submit"]'
    SEL_RESPONSE = 'div[class*="response"] .markdown, div[class*="ac-textBlock"], div.content p'
    SEL_NEW_CHAT = 'button[aria-label*="New topic"], button[aria-label*="New chat"], a[title*="New"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "copilot.microsoft.com" not in url:
            log.info("Navigating to Microsoft Copilot")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)
        try:
            await page.wait_for_selector('textarea, div[contenteditable="true"]', timeout=15_000)
        except Exception:
            log.warning("Copilot input not found")

    async def create_new_chat(self, page: Page) -> None:
        try:
            btn = page.locator(self.SEL_NEW_CHAT).first
            if await btn.is_visible():
                await btn.click()
                await asyncio.sleep(1.5)
                return
        except Exception:
            pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

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
        if any(p in lower for p in ["rate limit", "too many", "try again later", "throttled"]):
            return "rate_limit"
        if "captcha" in lower:
            return "captcha"
        if any(p in lower for p in ["sign in", "log in", "login.microsoftonline"]):
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
