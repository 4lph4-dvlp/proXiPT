"""Claude.ai provider — claude.ai (Anthropic)"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from playwright.async_api import Page

from proxipt.providers.base import BaseProvider
from proxipt.utils.logger import get_logger

log = get_logger("provider.claude")


class ClaudeProvider(BaseProvider):
    name = "claude"
    base_url = "https://claude.ai/new"
    supports_streaming = True
    supports_images = True
    supports_system_prompt = False

    SEL_TEXTAREA = 'div[contenteditable="true"], div.ProseMirror, textarea'
    SEL_SEND = 'button[aria-label*="Send"], button[class*="send"]'
    SEL_RESPONSE = 'div[class*="response"] .markdown, div[class*="message"][class*="assistant"], div.prose'
    SEL_NEW_CHAT = 'a[href="/new"], button[aria-label*="New"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "claude.ai" not in url:
            log.info("Navigating to Claude")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
        try:
            await page.wait_for_selector('div[contenteditable="true"], div.ProseMirror, textarea', timeout=15_000)
        except Exception:
            log.warning("Claude input not found — login may be needed")

    async def create_new_chat(self, page: Page) -> None:
        try:
            btn = page.locator(self.SEL_NEW_CHAT).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

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
        if any(p in lower for p in ["rate limit", "usage limit", "too many", "try again"]):
            return "rate_limit"
        if "captcha" in lower:
            return "captcha"
        if any(p in lower for p in ["log in", "sign in", "sign up"]):
            try:
                ta = page.locator('div[contenteditable="true"], div.ProseMirror, textarea')
                if await ta.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.3)

        # Claude uses ProseMirror (contenteditable div).
        # Like ChatGPT, .fill() does NOT work on contenteditable.
        # Use JS injection with ProseMirror-compatible structure.
        try:
            await page.wait_for_selector('div[contenteditable="true"], div.ProseMirror', timeout=10_000)
            await page.evaluate("""(prompt) => {
                const el = document.querySelector('div.ProseMirror') || 
                           document.querySelector('div[contenteditable="true"]');
                if (!el) throw new Error('Editor not found');
                el.innerHTML = '<p>' + prompt + '</p>';
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""", prompt)
            log.debug("Injected prompt via JS into ProseMirror")
        except Exception as e:
            log.warning("Claude JS injection failed: %s — trying keyboard", e)
            try:
                editor = page.locator('div[contenteditable="true"], div.ProseMirror').first
                await editor.click()
                await page.keyboard.insert_text(prompt)
            except Exception as e2:
                log.error("Claude keyboard fallback also failed: %s", e2)
                return

        await asyncio.sleep(0.3)

        # Click send
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible(timeout=3000) and await send_btn.is_enabled():
                await send_btn.click()
                return
        except Exception:
            pass
        await page.keyboard.press("Enter")
