"""
Unified Streamer for Agents-On-Hand Telegram Bot.
Handles standardized DriverEvent payloads (Text, Thought, Tool Requests, Exit).
"""

import asyncio
import logging
from typing import Optional, List, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter
from .drivers.base_driver import DriverEvent
from .ansi_cleaner import format_hermes_style

logger = logging.getLogger(__name__)


def split_text_into_chunks(text: str, max_chars: int = 3800) -> List[str]:
    """Split text into chunks smaller than max_chars for Telegram safe delivery."""
    if not text:
        return []
    chunks = []
    while len(text) > max_chars:
        split_idx = text.rfind('\n', 0, max_chars)
        if split_idx == -1:
            split_idx = max_chars
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks


class UnifiedStreamer:
    """
    Unified Live Streamer for all Agent Sessions and Protocols.
    Renders streaming response text, thinking sections, tool execution badges,
    and Telegram Inline Keyboards for Tool Approval requests.
    """

    def __init__(
        self,
        bot: Any,
        chat_id: int,
        session: Any,
        edit_interval: float = 1.8,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.session = session
        self.edit_interval = edit_interval

        self.current_text: str = ""
        self.current_thought: str = ""
        self.current_msg_id: Optional[int] = None

        self._is_active: bool = False
        self._last_edit_time: float = 0.0
        self._typing_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def start(self):
        """Start listening to driver events."""
        self._is_active = True
        self.session.register_listener(self._on_driver_event)

    def stop(self):
        """Stop streamer and typing indicator."""
        self._is_active = False
        self._stop_typing()
        self.session.unregister_listener(self._on_driver_event)

    def notify_user_input(self):
        """Trigger top typing indicator when user sends a prompt."""
        if self._typing_task is None or self._typing_task.done():
            self._typing_task = asyncio.create_task(self._typing_loop())

    async def _typing_loop(self):
        """Periodically send Telegram typing status every 4s while processing."""
        while self._is_active:
            try:
                await self.bot.send_chat_action(chat_id=self.chat_id, action="typing")
            except Exception as e:
                logger.debug(f"Error sending typing action: {e}")
            await asyncio.sleep(4.0)

    def _stop_typing(self):
        """Cancel typing task."""
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()

    def _on_driver_event(self, event: Any):
        """Callback when a DriverEvent arrives from the active AgentSession."""
        if isinstance(event, str):
            # Raw string fallback
            self.current_text += event
            self._stop_typing()
            asyncio.create_task(self._schedule_edit())
            return

        e_type = event.event_type if hasattr(event, "event_type") else getattr(event, "type", "")

        if e_type == DriverEvent.TEXT_DELTA:
            self.current_text += event.content
            self._stop_typing()
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.THOUGHT_DELTA:
            self.current_thought += event.content
            self._stop_typing()
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.TOOL_REQUEST:
            self._on_tool_request(event.request_id, event.tool_name, event.tool_args)

        elif e_type == DriverEvent.TOOL_RESULT:
            self.current_text += f"\n🛠️ *Tool ({event.tool_name})*: `{event.content[:100]}`\n"
            asyncio.create_task(self._schedule_edit())

    def _on_tool_request(self, req_id: Any, tool_name: str, tool_args: Any):
        """Render Inline Keyboard for Tool Approval Request."""
        detail_str = f"`{tool_name}`"
        if tool_args:
            detail_str += f"\n`{tool_args}`"

        text = f"🛡️ *Tool 執行審核請求*\nAgent 要求執行以下工具：\n{detail_str}\n\n請選擇是否授權："

        reply_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ 同意執行", callback_data=f"acp_perm:approve:{req_id}"),
                InlineKeyboardButton("❌ 拒絕執行", callback_data=f"acp_perm:reject:{req_id}"),
            ]
        ])

        asyncio.create_task(
            self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        )

    async def _schedule_edit(self):
        """Throttle and edit Telegram message."""
        async with self._lock:
            now = asyncio.get_running_loop().time()
            if now - self._last_edit_time < self.edit_interval:
                return

            self._last_edit_time = now

            full_content = self.current_text
            if self.current_thought:
                thought_block = f"💭 *Thinking...*\n```\n{self.current_thought.strip()}\n```\n\n"
                full_content = thought_block + full_content

            formatted = format_hermes_style(full_content)
            if not formatted.strip():
                return

            if self.current_msg_id is None:
                try:
                    msg = await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=formatted,
                        parse_mode="Markdown",
                    )
                    self.current_msg_id = msg.message_id
                except Exception as e:
                    logger.debug(f"Error creating initial streaming msg: {e}")
            else:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.current_msg_id,
                        text=formatted,
                        parse_mode="Markdown",
                    )
                except BadRequest:
                    pass
                except Exception as e:
                    logger.debug(f"Error editing streaming msg: {e}")


# Compatibility Aliases for bot.py and tests
DirectChatStreamer = UnifiedStreamer
ACPStreamer = UnifiedStreamer
