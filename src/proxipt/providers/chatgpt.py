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

    SEL_TEXTAREA = '#prompt-textarea'
    SEL_SEND = 'button[data-testid="send-button"], button[aria-label*="Send"]'
    SEL_RESPONSE = 'div[data-message-author-role="assistant"] .markdown, div.agent-turn .markdown'
    SEL_NEW_CHAT = 'a[href="/"], nav a:first-child, button[aria-label*="New chat"]'

    async def ensure_ready(self, page: Page) -> None:
        url = page.url
        if not url or "chatgpt.com" not in url:
            log.info("Navigating to ChatGPT")
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

        try:
            await page.wait_for_selector(
                '#prompt-textarea, div[contenteditable="true"], textarea',
                timeout=15_000,
            )
        except Exception:
            log.warning("ChatGPT input area not found — login may be needed")

    async def create_new_chat(self, page: Page) -> None:
        """Navigate to fresh chat. Prefer clicking New Chat to avoid full reload."""
        url = page.url or ""
        if "chatgpt.com" in url:
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

    async def fetch_available_models(self, page: Page) -> list[str]:
        """Scrape the available models from the dropdown."""
        models = []
        try:
            # Click the model selector dropdown
            dropdown = page.locator('div[data-testid="model-switcher"], button[aria-haspopup="menu"]').first
            if await dropdown.is_visible(timeout=5000):
                await dropdown.click()
                await asyncio.sleep(0.5)
                # Find menu items
                items = page.locator('div[role="menuitem"] .text-token-text-primary, div[role="menuitem"] span')
                count = await items.count()
                for i in range(count):
                    text = await items.nth(i).inner_text()
                    if text and text.strip():
                        # Normalize simple names (e.g. "GPT-4o" -> "gpt-4o")
                        model_id = text.strip().lower().replace(" ", "-")
                        models.append(model_id)
                
                # Close dropdown
                await page.keyboard.press("Escape")
        except Exception as e:
            log.warning("Could not fetch models for ChatGPT: %s", e)
        return models

    async def select_model(self, page: Page, model_name: str) -> None:
        """Select a model from the dropdown."""
        try:
            dropdown = page.locator('div[data-testid="model-switcher"], button[aria-haspopup="menu"]').first
            if await dropdown.is_visible(timeout=5000):
                await dropdown.click()
                await asyncio.sleep(0.5)
                
                items = page.locator('div[role="menuitem"]')
                count = await items.count()
                for i in range(count):
                    item = items.nth(i)
                    text = await item.inner_text()
                    # match normalized
                    if model_name.replace("-", " ") in text.lower():
                        await item.click()
                        await asyncio.sleep(0.5)
                        log.info("Selected model %s in ChatGPT", model_name)
                        return
                
                # If not found, close dropdown
                await page.keyboard.press("Escape")
                log.warning("Model %s not found in ChatGPT UI", model_name)
        except Exception as e:
            log.warning("Could not select model for ChatGPT: %s", e)

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
                editor = page.locator('#prompt-textarea, div[contenteditable="true"]')
                if await editor.count() == 0:
                    return "auth_expired"
            except Exception:
                return "auth_expired"
        return None

    async def _inject_prompt(self, page: Page, prompt: str) -> None:
        await asyncio.sleep(0.3)

        # Dismiss overlapping popups ("Join ChatGPT Plus", "See what's new", etc.)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)

        # ChatGPT #prompt-textarea is a <div contenteditable="true">.
        # Playwright .fill() DOES NOT WORK on contenteditable divs.
        # We must use JavaScript to inject text directly into the DOM.
        try:
            await page.wait_for_selector('#prompt-textarea', timeout=10_000)
            await page.evaluate("""(prompt) => {
                const el = document.querySelector('#prompt-textarea');
                if (!el) throw new Error('prompt-textarea not found');
                // Clear existing content
                el.innerHTML = '';
                // Create a <p> with the text (ChatGPT expects this structure)
                const p = document.createElement('p');
                p.textContent = prompt;
                el.appendChild(p);
                // Dispatch input event so React picks up the change
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""", prompt)
            log.debug("Injected prompt via JS into #prompt-textarea")
        except Exception as e:
            log.warning("ChatGPT JS injection failed: %s — trying keyboard fallback", e)
            try:
                editor = page.locator('#prompt-textarea').first
                await editor.click(force=True, timeout=5000)
                await asyncio.sleep(0.2)
                await page.keyboard.insert_text(prompt)
            except Exception as e2:
                log.error("ChatGPT keyboard fallback also failed: %s", e2)
                return

        await asyncio.sleep(0.3)

        # Click the send button
        try:
            send_btn = page.locator(self.SEL_SEND).first
            if await send_btn.is_visible(timeout=3000) and await send_btn.is_enabled():
                await send_btn.click()
                return
        except Exception:
            pass
        # Fallback: Enter key (ChatGPT uses Enter to send)
        await page.keyboard.press("Enter")
