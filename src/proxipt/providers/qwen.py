"""Qwen Chat provider — chat.qwen.ai"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.qwen")


class QwenProvider(BaseProvider):
    name = "qwen"
    base_url = "https://chat.qwen.ai"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = "textarea, div[contenteditable='true'], #chat-input"
    SEL_SEND = 'button[class*="send"], button[aria-label*="Send"], button[type="submit"]'
    SEL_RESPONSE = "div[class*='markdown'], div[class*='message-content'], div[class*='response']"
    SEL_NEW_CHAT = 'button[class*="new"], a[class*="new-chat"], div[class*="new-chat"]'
    SEL_MODEL_SELECT = 'div[class*="model-selector"], button[class*="model"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "qwen.ai" not in url:
            log.info("Navigating to Qwen Chat")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

        try:
            await page.wait_for_selector(
                "textarea, div[contenteditable='true']",
                timeout=15_000,
            )
        except Exception:
            log.warning("Input area not found on Qwen")

    async def create_new_chat(self, page: Page) -> None:
        try:
            new_btn = page.locator(self.SEL_NEW_CHAT).first
            if await new_btn.is_visible():
                await new_btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

    async def select_model(self, page: Page, model_name: str) -> None:
        try:
            selector = page.locator(self.SEL_MODEL_SELECT)
            if await selector.count() > 0 and await selector.first.is_visible():
                await selector.first.click()
                await asyncio.sleep(0.5)
                option = page.locator(f"text={model_name}").first
                if await option.is_visible():
                    await option.click()
                    await asyncio.sleep(0.5)
                    log.info("Selected model: %s", model_name)
        except Exception as e:
            log.debug("Model selection skipped: %s", e)

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

        if any(phrase in lower for phrase in [
            "too many requests", "rate limit", "请求过于频繁",
            "you have sent too many", "try again later",
        ]):
            return "rate_limit"
        if any(phrase in lower for phrase in ["captcha", "verify"]):
            return "captcha"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        textarea = page.locator("textarea").first
        try:
            await textarea.wait_for(timeout=10_000)
        except Exception:
            textarea = page.locator("div[contenteditable='true']").first
            await textarea.wait_for(timeout=5_000)

        await textarea.click()
        await asyncio.sleep(0.3)
        await textarea.fill(prompt)
        await asyncio.sleep(0.3)

        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible() and await send_btn.is_enabled():
                await send_btn.click()
                return
        except Exception:
            pass
        await textarea.press("Enter")
