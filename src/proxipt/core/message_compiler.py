"""Compile an OpenAI-style messages array into a single prompt string.

Web-based chatbots accept a single text input, so we need to flatten the
structured ``messages`` list into one coherent prompt that preserves:
  - system instructions
  - multi-turn conversation history
  - the latest user query
"""

from __future__ import annotations

from proxipt.api.schemas import ChatMessage


def compile_messages(messages: list[ChatMessage]) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)``.

    * ``system_prompt`` — extracted system messages (empty string if none).
    * ``user_prompt``   — everything else compiled into a single block of text
      that can be pasted into any chatbot text area.

    For providers that natively support a system-prompt field we return
    them separately.  For providers that do NOT support system prompts,
    callers should prepend the system prompt to *user_prompt*.
    """
    system_parts: list[str] = []
    history_parts: list[str] = []
    last_user_msg: str = ""

    for msg in messages:
        content = _extract_text(msg)
        if not content:
            continue

        if msg.role == "system":
            system_parts.append(content)
        elif msg.role == "user":
            # keep accumulating; the *last* user message is the real query
            if last_user_msg:
                history_parts.append(f"User: {last_user_msg}")
            last_user_msg = content
        elif msg.role == "assistant":
            history_parts.append(f"Assistant: {content}")

    system_prompt = "\n\n".join(system_parts)

    # If there is conversation history, build a structured prompt
    if history_parts:
        history_block = "\n".join(history_parts)
        user_prompt = (
            f"[Conversation History]\n{history_block}\n\n"
            f"[Current Request]\n{last_user_msg}"
        )
    else:
        user_prompt = last_user_msg

    return system_prompt, user_prompt


def compile_messages_flat(messages: list[ChatMessage]) -> str:
    """Merge *all* messages (including system) into one flat prompt.

    Use this when the target chatbot has no system-prompt field at all.
    """
    system, user = compile_messages(messages)
    if system:
        return f"[System Instructions]\n{system}\n\n{user}"
    return user


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_text(msg: ChatMessage) -> str:
    """Pull plain-text content from a ChatMessage (handles multimodal)."""
    if msg.content is None:
        return ""
    if isinstance(msg.content, str):
        return msg.content
    # multimodal list — extract text parts
    parts: list[str] = []
    for part in msg.content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "\n".join(parts)
