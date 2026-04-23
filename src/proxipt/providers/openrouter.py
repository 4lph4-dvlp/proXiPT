"""OpenRouter provider — openrouter.ai/playground"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.openrouter")


class OpenRouterProvider(BaseProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/playground"
    supports_streaming = True
    supports_images = False
    supports_system_prompt = True

    SEL_TEXTAREA = 'textarea[placeholder*="Message"], textarea, div[contenteditable="true"]'
    SEL_SEND = 'button[aria-label*="Send"], button[type="submit"], button:has-text("Send")'
    SEL_RESPONSE = 'div[class*="message"][class*="assistant"], div.prose, div[class*="response"] .markdown'
    SEL_NEW_CHAT = 'button:has-text("New Chat"), button:has-text("Clear"), a[href*="playground"]'
    SEL_SYSTEM = 'textarea[placeholder*="System"], textarea[aria-label*="System"]'
    SEL_MODEL_SELECT = 'button[class*="model"], div[class*="model-select"], select'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "openrouter.ai" not in url:
            log.info("Navigating to OpenRouter Playground")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)
        try:
            await page.wait_for_selector('textarea, div[contenteditable="true"]', timeout=15_000)
        except Exception:
            log.warning("OpenRouter input not found — login may be needed")

    async def create_new_chat(self, page: Page) -> None:
        try:
            btn = page.locator(self.SEL_NEW_CHAT).first
            if await btn.is_visible():
                await btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def set_system_prompt(self, page: Page, prompt: str) -> None:
        try:
            sys_ta = page.locator(self.SEL_SYSTEM).first
            if await sys_ta.is_visible():
                await sys_ta.click()
                await sys_ta.fill(prompt)
                log.info("System prompt set on OpenRouter")
        except Exception as e:
            log.debug("Could not set system prompt: %s", e)

    async def select_model(self, page: Page, model_name: str) -> None:
        try:
            sel = page.locator(self.SEL_MODEL_SELECT).first
            if await sel.is_visible():
                await sel.click()
                await asyncio.sleep(0.5)
                option = page.locator(f'text="{model_name}"').first
                if await option.is_visible():
                    await option.click()
                    await asyncio.sleep(0.5)
                    log.info("Selected model: %s", model_name)
        except Exception as e:
            log.debug("Model selection skipped: %s", e)

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
        if any(p in lower for p in ["rate limit", "too many", "429", "quota"]):
            return "rate_limit"
        if any(p in lower for p in ["sign in", "log in"]):
            try:
                ta = page.locator('textarea, div[contenteditable="true"]')
                if await ta.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        textarea = page.locator("textarea").last
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
