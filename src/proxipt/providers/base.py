"""Abstract base class for all LLM web chat providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from playwright.async_api import Page


class BaseProvider(ABC):
    """Every provider must implement this interface.

    A provider knows how to:
    * Navigate to the chat page
    * Start a new conversation
    * Inject a prompt and read the response
    * Detect errors (rate-limit, CAPTCHA, auth expiry)
    """

    # --- Class-level metadata (override in subclasses) ---
    name: str = ""
    base_url: str = ""
    supports_streaming: bool = True
    supports_images: bool = False
    supports_system_prompt: bool = False

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def ensure_ready(self, page: Page) -> None:
        """Navigate to the chat page and ensure it is ready to accept input.

        This is called once after a fresh page is created or when the page
        needs to be recycled for a new request.
        """

    @abstractmethod
    async def send_message(self, page: Page, prompt: str) -> str:
        """Send *prompt* and return the complete response text."""

    @abstractmethod
    async def send_message_streaming(
        self, page: Page, prompt: str
    ) -> AsyncGenerator[str, None]:
        """Send *prompt* and yield response text chunks as they arrive."""

    @abstractmethod
    async def detect_error(self, page: Page) -> str | None:
        """Return an error string if the page shows an error state, else None.

        Expected return values:
        * ``"rate_limit"``  — too many requests
        * ``"captcha"``     — CAPTCHA challenge
        * ``"auth_expired"``— session expired
        * ``None``          — everything is fine
        """

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    async def create_new_chat(self, page: Page) -> None:
        """Start a fresh conversation (default: navigate to base_url)."""
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)

    async def select_model(self, page: Page, model_name: str) -> None:
        """Select a model in the web UI.  No-op by default."""

    async def set_system_prompt(self, page: Page, prompt: str) -> None:
        """Set the system prompt.  No-op by default."""

    async def upload_image(self, page: Page, image_data: bytes, mime: str = "image/png") -> None:
        """Upload an image to the chat.  Raises NotImplementedError by default."""
        raise NotImplementedError(f"{self.name} does not support image uploads")

    async def is_response_complete(self, page: Page) -> bool:
        """Check whether the model has finished generating."""
        return True

    # ------------------------------------------------------------------
    # Helpers available to subclasses
    # ------------------------------------------------------------------

    async def _type_and_send(
        self,
        page: Page,
        selector: str,
        text: str,
        send_selector: str | None = None,
        press_enter: bool = True,
    ) -> None:
        """Fill a textarea and submit."""
        textarea = page.locator(selector)
        await textarea.click()
        await textarea.fill(text)
        if send_selector:
            await page.locator(send_selector).click()
        elif press_enter:
            await textarea.press("Enter")

    async def _wait_for_response(
        self,
        page: Page,
        response_selector: str,
        *,
        timeout: int = 120_000,
        poll_interval: int = 500,
        stability_checks: int = 3,
    ) -> str:
        """Poll the response container until text stabilises."""
        import asyncio

        stable_count = 0
        last_text = ""
        elapsed = 0

        while elapsed < timeout:
            await asyncio.sleep(poll_interval / 1000)
            elapsed += poll_interval

            try:
                elements = page.locator(response_selector)
                count = await elements.count()
                if count == 0:
                    continue
                current = await elements.last.inner_text()
            except Exception:
                continue

            if current and current == last_text:
                stable_count += 1
                if stable_count >= stability_checks:
                    return current.strip()
            else:
                stable_count = 0
                last_text = current

        return last_text.strip()

    async def _stream_response(
        self,
        page: Page,
        response_selector: str,
        *,
        timeout: int = 120_000,
        poll_interval: int = 200,
        stability_checks: int = 4,
    ) -> AsyncGenerator[str, None]:
        """Poll the response container and yield *new* text as it appears."""
        import asyncio

        emitted_len = 0
        stable_count = 0
        last_text = ""
        elapsed = 0

        while elapsed < timeout:
            await asyncio.sleep(poll_interval / 1000)
            elapsed += poll_interval

            try:
                elements = page.locator(response_selector)
                count = await elements.count()
                if count == 0:
                    continue
                current = await elements.last.inner_text()
            except Exception:
                continue

            if not current:
                continue

            # Yield new characters
            if len(current) > emitted_len:
                new_text = current[emitted_len:]
                emitted_len = len(current)
                stable_count = 0
                last_text = current
                yield new_text
            elif current == last_text:
                stable_count += 1
                if stable_count >= stability_checks:
                    return
            last_text = current
