"""Google Gemini provider — gemini.google.com"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.gemini")


class GeminiProvider(BaseProvider):
    name = "gemini"
    base_url = "https://gemini.google.com/app"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = 'div[contenteditable="true"], rich-textarea .ql-editor, div.input-area textarea, textarea'
    SEL_SEND = 'button[aria-label*="Send"], button.send-button, button[mattooltip*="Send"]'
    SEL_RESPONSE = "message-content .markdown, model-response .markdown, .response-container .markdown, div[class*='response'] .markdown"
    SEL_NEW_CHAT = 'button[aria-label*="New chat"], a[href*="/app"], button[mattooltip*="New chat"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "gemini.google.com" not in url:
            log.info("Navigating to Gemini")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

        try:
            await page.wait_for_selector(
                'div[contenteditable="true"], textarea',
                timeout=15_000,
            )
        except Exception:
            log.warning("Gemini input area not found — login may be needed")

    async def create_new_chat(self, page: Page) -> None:
        try:
            new_btn = page.locator(self.SEL_NEW_CHAT).first
            if await new_btn.is_visible(timeout=2000):
                await new_btn.click()
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

        if any(p in lower for p in ["too many requests", "rate limit", "try again in"]):
            return "rate_limit"
        if "captcha" in lower or "recaptcha" in lower:
            return "captcha"
        if any(p in lower for p in ["sign in", "log in", "accounts.google.com"]):
            try:
                textarea = page.locator('div[contenteditable="true"], textarea')
                if await textarea.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.3)

        # Gemini uses contenteditable div
        editor = page.locator('div[contenteditable="true"]').first
        try:
            await editor.wait_for(timeout=10_000)
            await editor.click()
            await asyncio.sleep(0.2)
            # Clear and type via keyboard for contenteditable
            await editor.fill("")
            await page.keyboard.insert_text(prompt)
        except Exception:
            # Fallback to textarea
            textarea = page.locator("textarea").first
            await textarea.click()
            await textarea.fill(prompt)

        await asyncio.sleep(0.2)

        # Click send
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible(timeout=2000):
                await send_btn.click()
                return
        except Exception:
            pass
        await page.keyboard.press("Enter")
