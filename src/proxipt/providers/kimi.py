"""Kimi Chat provider — kimi.moonshot.cn"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.kimi")


class KimiProvider(BaseProvider):
    name = "kimi"
    base_url = "https://kimi.moonshot.cn"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = 'div[contenteditable="true"], textarea, div.editor'
    SEL_SEND = 'button[class*="send"], button[aria-label*="Send"], button[type="submit"]'
    SEL_RESPONSE = 'div[class*="message"][class*="bot"] .markdown, div[class*="response"], div.markdown'
    SEL_NEW_CHAT = 'div[class*="new-chat"], button[class*="new"], a[href="/"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "kimi.moonshot.cn" not in url:
            log.info("Navigating to Kimi Chat")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)
        try:
            await page.wait_for_selector('div[contenteditable="true"], textarea', timeout=15_000)
        except Exception:
            log.warning("Kimi input not found")

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
        return await self._wait_for_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=4)

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(page, self.SEL_RESPONSE, timeout=120_000, stability_checks=5):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()
        if any(p in lower for p in ["请求过于频繁", "rate limit", "too many", "稍后再试"]):
            return "rate_limit"
        if any(p in lower for p in ["验证", "captcha"]):
            return "captcha"
        if any(p in lower for p in ["请登录", "登录", "sign in"]):
            try:
                ta = page.locator('div[contenteditable="true"], textarea')
                if await ta.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)
        editor = page.locator('div[contenteditable="true"]').first
        try:
            await editor.wait_for(timeout=10_000)
            await editor.click()
            await page.keyboard.insert_text(prompt)
        except Exception:
            textarea = page.locator("textarea").first
            await textarea.click()
            await textarea.fill(prompt)
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
