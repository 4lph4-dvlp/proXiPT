"""ChatGPT provider — chatgpt.com"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.chatgpt")


class ChatGPTProvider(BaseProvider):
    name = "chatgpt"
    base_url = "https://chatgpt.com"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = 'div#prompt-textarea, textarea[data-id="root"], div[contenteditable="true"]'
    SEL_SEND = 'button[data-testid="send-button"], button[aria-label*="Send"]'
    SEL_RESPONSE = 'div[data-message-author-role="assistant"] .markdown, div.agent-turn .markdown'
    SEL_NEW_CHAT = 'a[href="/"], nav a:first-child, button[aria-label*="New chat"]'
    SEL_MODEL_SELECT = 'button[aria-haspopup="menu"][class*="text"], div[class*="model-selector"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "chatgpt.com" not in url:
            log.info("Navigating to ChatGPT")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

        try:
            await page.wait_for_selector(
                'div#prompt-textarea, div[contenteditable="true"], textarea',
                timeout=15_000,
            )
        except Exception:
            log.warning("ChatGPT input area not found — login may be needed")

    async def create_new_chat(self, page: Page) -> None:
        try:
            new_btn = page.locator(self.SEL_NEW_CHAT).first
            if await new_btn.is_visible():
                await new_btn.click()
                await asyncio.sleep(1.5)
                return
        except Exception:
            pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

    async def select_model(self, page: Page, model_name: str) -> None:
        try:
            selector = page.locator(self.SEL_MODEL_SELECT).first
            if await selector.is_visible():
                await selector.click()
                await asyncio.sleep(0.5)
                option = page.locator(f'[role="menuitem"]:has-text("{model_name}")').first
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
            page, self.SEL_RESPONSE, timeout=120_000, stability_checks=6,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()

        if any(p in lower for p in [
            "you've reached the current usage cap",
            "rate limit", "too many requests",
            "please try again",
        ]):
            return "rate_limit"
        if any(p in lower for p in ["captcha", "verify you"]):
            return "captcha"
        if any(p in lower for p in ["log in", "sign up", "auth0"]):
            try:
                editor = page.locator('div#prompt-textarea, div[contenteditable="true"]')
                if await editor.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.5)

        # ChatGPT uses a contenteditable div with id prompt-textarea
        editor = page.locator('div#prompt-textarea, div[contenteditable="true"]').first
        try:
            await editor.wait_for(timeout=10_000)
            await editor.click()
            await asyncio.sleep(0.3)
            # ChatGPT's prompt-textarea works best with keyboard input
            await page.keyboard.insert_text(prompt)
        except Exception:
            textarea = page.locator("textarea").first
            await textarea.click()
            await textarea.fill(prompt)

        await asyncio.sleep(0.3)

        # Click send
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible() and await send_btn.is_enabled():
                await send_btn.click()
                return
        except Exception:
            pass
        await page.keyboard.press("Enter")
