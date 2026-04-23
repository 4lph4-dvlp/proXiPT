"""DeepSeek Chat provider — chat.deepseek.com"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.deepseek")


class DeepSeekProvider(BaseProvider):
    name = "deepseek"
    base_url = "https://chat.deepseek.com"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    # CSS selectors — these target the DeepSeek chat interface
    SEL_TEXTAREA = "textarea#chat-input, textarea[placeholder*='Message'], div.chat-input textarea, textarea"
    SEL_SEND = 'div[class*="chat-input"] button:last-of-type, button[aria-label*="Send"], button[class*="send"]'
    SEL_RESPONSE = "div.ds-markdown--block, div[class*='markdown'], div[class*='message-content']"
    SEL_NEW_CHAT = 'div[class*="new-chat"], a[href="/"], button[class*="new"]'
    SEL_STOP = 'button[class*="stop"], button[aria-label*="Stop"]'
    SEL_MODEL_SELECT = 'div[class*="model-selector"], div[class*="model-select"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "deepseek.com" not in url:
            log.info("Navigating to DeepSeek Chat")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1.5)

        # Wait for the input area to appear
        try:
            await page.wait_for_selector(
                "textarea, div[contenteditable='true']",
                timeout=15_000,
            )
        except Exception:
            log.warning("Input area not found — page may require login")

    async def create_new_chat(self, page: Page) -> None:
        """Start a new conversation."""
        try:
            new_btn = page.locator(self.SEL_NEW_CHAT).first
            if await new_btn.is_visible():
                await new_btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        # Fallback: navigate to root
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(1.5)

    async def select_model(self, page: Page, model_name: str) -> None:
        """Select DeepSeek model (V3 or R1)."""
        try:
            selector = page.locator(self.SEL_MODEL_SELECT)
            if await selector.count() > 0 and await selector.first.is_visible():
                await selector.first.click()
                await asyncio.sleep(0.5)
                # Look for the model option
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
            page,
            self.SEL_RESPONSE,
            timeout=120_000,
            poll_interval=500,
            stability_checks=4,
        )

    async def send_message_streaming(self, page: Page, prompt: str) -> AsyncGenerator[str, None]:
        await self.create_new_chat(page)
        await self._inject_prompt(page, prompt)
        async for chunk in self._stream_response(
            page,
            self.SEL_RESPONSE,
            timeout=120_000,
            poll_interval=200,
            stability_checks=5,
        ):
            yield chunk

    async def detect_error(self, page: Page) -> str | None:
        content = await page.content()
        lower = content.lower()

        if any(phrase in lower for phrase in [
            "too many requests",
            "rate limit",
            "please try again later",
            "server is busy",
            "服务器繁忙",
            "请求过于频繁",
        ]):
            return "rate_limit"

        if any(phrase in lower for phrase in [
            "captcha",
            "verify you are human",
            "验证",
        ]):
            return "captcha"

        if any(phrase in lower for phrase in [
            "please log in",
            "sign in",
            "login",
            "请登录",
        ]):
            # Check if this is the login page itself vs a logged-in state
            try:
                textarea = page.locator("textarea, div[contenteditable='true']")
                if await textarea.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"

        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        """Type prompt into the chat input and submit."""
        await asyncio.sleep(0.5)

        # Try to find the textarea
        textarea = page.locator("textarea").first
        try:
            await textarea.wait_for(timeout=10_000)
        except Exception:
            # Fallback: try contenteditable div
            textarea = page.locator("div[contenteditable='true']").first
            await textarea.wait_for(timeout=5_000)

        await textarea.click()
        await asyncio.sleep(0.3)

        # Use fill for speed, but some sites need keyboard input
        await textarea.fill(prompt)
        await asyncio.sleep(0.3)

        # Try clicking send button first
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible() and await send_btn.is_enabled():
                await send_btn.click()
                log.debug("Clicked send button")
                return
        except Exception:
            pass

        # Fallback: press Enter
        await textarea.press("Enter")
        log.debug("Pressed Enter to send")
