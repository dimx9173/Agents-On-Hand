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
        self._trailing_flush_task: Optional[asyncio.Task] = None
        self._wait_indicator_task: Optional[asyncio.Task] = None

    def start(self):
        """Start listening to driver events."""
        self._is_active = True
        self.session.register_listener(self._on_driver_event)

    def stop(self):
        """Stop streamer and typing indicator."""
        self._is_active = False
        self._stop_typing()
        self._cancel_trailing_flush()
        self._cancel_wait_indicator()
        self.session.unregister_listener(self._on_driver_event)

    def notify_user_input(self):
        """Trigger top typing indicator when user sends a prompt.

        A new prompt starts a new Telegram message: flush the previous turn's
        accumulated text to its own message, then reset streaming state.
        """
        if self.current_msg_id is not None and (self.current_text or self.current_thought):
            self._cancel_trailing_flush()
            asyncio.create_task(
                self._flush_previous_turn(
                    self.current_text, self.current_thought, self.current_msg_id
                )
            )
        self.current_text = ""
        self.current_thought = ""
        self.current_msg_id = None
        self._cancel_wait_indicator()

        if self._typing_task is None or self._typing_task.done():
            self._typing_task = asyncio.create_task(self._typing_loop())

        self._wait_indicator_task = asyncio.create_task(self._wait_indicator_loop())

    async def _wait_indicator_loop(self):
        """Show initial waiting message if model takes >4s to respond."""
        try:
            await asyncio.sleep(4.0)
            async with self._lock:
                if not self.current_text and not self.current_thought and self._is_active:
                    waiting_msg = "⏳ *Agent 正在思考與處理中，請稍候...*"
                    self.current_msg_id = await self._deliver(waiting_msg, self.current_msg_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Error in wait indicator loop: {e}")

    def _cancel_wait_indicator(self):
        """Cancel pending waiting indicator task."""
        if self._wait_indicator_task and not self._wait_indicator_task.done():
            self._wait_indicator_task.cancel()
        self._wait_indicator_task = None

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
        self._cancel_wait_indicator()
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
                InlineKeyboardButton("✅ 同意執行", callback_data=f"acp_perm:approve:{self.session.session_id}:{req_id}"),
                InlineKeyboardButton("❌ 拒絕執行", callback_data=f"acp_perm:reject:{self.session.session_id}:{req_id}"),
            ]
        ])

        async def _safe_send_tool_req():
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Error sending tool request message: {e}")

        asyncio.create_task(_safe_send_tool_req())

    def _cancel_trailing_flush(self):
        """Cancel any pending trailing flush task."""
        if self._trailing_flush_task and not self._trailing_flush_task.done():
            self._trailing_flush_task.cancel()
        self._trailing_flush_task = None

    async def _schedule_edit(self):
        """Throttle and edit Telegram message.

        Edits arriving inside the throttle window are not dropped: a single
        trailing flush task is (re)scheduled so the final state of the
        accumulated text always reaches Telegram.
        """
        async with self._lock:
            now = asyncio.get_running_loop().time()
            remaining = self.edit_interval - (now - self._last_edit_time)
            if remaining > 0:
                if self._trailing_flush_task is None or self._trailing_flush_task.done():
                    self._trailing_flush_task = asyncio.create_task(
                        self._trailing_flush(remaining)
                    )
                return

            await self._flush_edit_locked()

    async def _trailing_flush(self, delay: float):
        """Wait out the throttle window, then push the final accumulated text."""
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                if not self._is_active:
                    return
                await self._flush_edit_locked()
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _render_content(text: str, thought: str) -> str:
        """Render accumulated text/thought into Telegram-ready Markdown."""
        full_content = text
        if thought:
            thought_block = f"💭 *Thinking...*\n```\n{thought.strip()}\n```\n\n"
            full_content = thought_block + full_content
        return format_hermes_style(full_content)

    async def _deliver(self, formatted: str, msg_id: Optional[int]) -> Optional[int]:
        """Send or edit the Telegram message with Markdown fallback.

        Returns the message_id of the streaming message, or None on failure.
        """
        if msg_id is None:
            try:
                msg = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=formatted,
                    parse_mode="Markdown",
                )
                return msg.message_id
            except BadRequest:
                # Markdown parse failure: retry as plain text
                try:
                    msg = await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=formatted,
                    )
                    return msg.message_id
                except Exception as e:
                    logger.debug(f"Error creating initial streaming msg (plain retry): {e}")
            except Exception as e:
                logger.debug(f"Error creating initial streaming msg: {e}")
            return None

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=msg_id,
                text=formatted,
                parse_mode="Markdown",
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return msg_id
            # Markdown parse failure: retry as plain text
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=msg_id,
                    text=formatted,
                )
            except BadRequest as retry_err:
                if "not modified" not in str(retry_err).lower():
                    logger.debug(f"Error editing streaming msg (plain retry): {retry_err}")
            except Exception as retry_err:
                logger.debug(f"Error editing streaming msg (plain retry): {retry_err}")
        except Exception as e:
            logger.debug(f"Error editing streaming msg: {e}")
        return msg_id

    async def _flush_previous_turn(self, text: str, thought: str, msg_id: int):
        """Best-effort final edit of the previous turn's message before reset."""
        try:
            formatted = self._render_content(text, thought)
            if formatted.strip():
                await self._deliver(formatted, msg_id)
        except Exception as e:
            logger.debug(f"Error flushing previous turn: {e}")

    async def _flush_edit_locked(self):
        """Render and send/edit the Telegram message. Caller must hold the lock."""
        self._last_edit_time = asyncio.get_running_loop().time()

        formatted = self._render_content(self.current_text, self.current_thought)
        if not formatted.strip():
            return

        self.current_msg_id = await self._deliver(formatted, self.current_msg_id)


# Compatibility Aliases for bot.py and tests
DirectChatStreamer = UnifiedStreamer
ACPStreamer = UnifiedStreamer
