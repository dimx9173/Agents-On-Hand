"""
Unified Streamer for Agents-On-Hand Telegram Bot.
Handles standardized DriverEvent payloads (Text, Thought, Tool Requests, Exit).
"""

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .ansi_cleaner import format_hermes_style
from .drivers.base_driver import DriverEvent

logger = logging.getLogger(__name__)


def split_text_into_chunks(text: str, max_chars: int = 3800) -> list[str]:
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
        self.current_msg_id: int | None = None

        self._is_active: bool = False
        self._last_edit_time: float = 0.0
        self._typing_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._trailing_flush_task: asyncio.Task | None = None
        self._wait_indicator_task: asyncio.Task | None = None
        self._pending_tool_req_ids: set[str] = set()

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
        self._pending_tool_req_ids.clear()
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
        self._pending_tool_req_ids.clear()
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
            self.current_text += event
            self._stop_typing()
            logger.info(f"Streamer TEXT (raw) +{len(event)} chars → total {len(self.current_text)}")
            asyncio.create_task(self._schedule_edit())
            return

        e_type = event.event_type if hasattr(event, "event_type") else getattr(event, "type", "")

        if e_type == DriverEvent.TEXT_DELTA:
            self.current_text += event.content
            self._stop_typing()
            logger.info(f"Streamer TEXT_DELTA +{len(event.content)} chars → total {len(self.current_text)} for session {self.session.session_id}")
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.THOUGHT_DELTA:
            self.current_thought += event.content
            self._stop_typing()
            logger.info(f"Streamer THOUGHT_DELTA +{len(event.content)} chars → total {len(self.current_thought)}")
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.TOOL_REQUEST:
            self._on_tool_request(event.request_id, event.tool_name, event.tool_args)

        elif e_type == DriverEvent.TOOL_RESULT:
            content_str = str(event.content).strip().replace("`", "'")
            preview = (content_str[:80] + "...") if len(content_str) > 80 else content_str
            self.current_text += f"\n🛠️ *Tool ({event.tool_name})*: `{preview}`\n"
            asyncio.create_task(self._schedule_edit())

    def _on_tool_request(self, req_id: Any, tool_name: str, tool_args: Any):
        """Render Inline Keyboard for Tool Approval Request with deduplication."""
        req_key = str(req_id) if req_id is not None else ""
        if req_key and req_key in self._pending_tool_req_ids:
            logger.info(f"Ignoring duplicate tool request for req_id={req_id}")
            return
        if req_key:
            self._pending_tool_req_ids.add(req_key)

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

    async def _deliver_single(self, formatted: str, msg_id: int | None) -> int | None:
        """Send or edit a single Telegram message with Markdown fallback.

        Returns the message_id of the streaming message, or None on failure.
        """
        logger.info(f"Streamer _deliver: msg_id={msg_id} chars={len(formatted)} to chat {self.chat_id}")
        if msg_id is None:
            try:
                msg = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=formatted,
                    parse_mode="Markdown",
                )
                logger.info(f"Streamer _deliver: sent new msg_id={msg.message_id}")
                return msg.message_id
            except BadRequest as e:
                logger.info(f"Streamer _deliver: Markdown failed, retry plain: {e}")
                # Markdown parse failure: retry as plain text
                try:
                    msg = await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=formatted,
                    )
                    return msg.message_id
                except Exception as plain_err:
                    logger.debug(f"Error creating initial streaming msg (plain retry): {plain_err}")
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
            return msg_id
        except BadRequest as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                return msg_id
            # Markdown parse failure: retry as plain text
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=msg_id,
                    text=formatted,
                )
                return msg_id
            except BadRequest as retry_err:
                if "not modified" not in str(retry_err).lower():
                    logger.debug(f"Error editing streaming msg (plain retry): {retry_err}")
            except Exception as retry_err:
                logger.debug(f"Error editing streaming msg (plain retry): {retry_err}")
        except Exception as e:
            logger.debug(f"Error editing streaming msg: {e}")
        return msg_id

    _deliver = _deliver_single

    async def _flush_previous_turn(self, text: str, thought: str, msg_id: int):
        """Best-effort final edit/send of the previous turn's message before reset."""
        try:
            formatted = self._render_content(text, thought)
            if not formatted.strip():
                return
            chunks = split_text_into_chunks(formatted, max_chars=3800)
            if not chunks:
                return
            await self._deliver_single(chunks[0], msg_id)
            for extra_chunk in chunks[1:]:
                await self._deliver_single(extra_chunk, None)
        except Exception as e:
            logger.debug(f"Error flushing previous turn: {e}")

    async def _flush_edit_locked(self):
        """Render and send/edit the Telegram message with automatic multi-message pagination."""
        self._last_edit_time = asyncio.get_running_loop().time()

        formatted = self._render_content(self.current_text, self.current_thought)
        if not formatted.strip():
            return

        # If content exceeds safe limit, finalize current message and roll over to a new message
        while len(formatted) > 3800:
            split_idx = formatted.rfind('\n', 0, 3800)
            if split_idx <= 0:
                split_idx = 3800
            head_chunk = formatted[:split_idx]
            tail_chunk = formatted[split_idx:].lstrip('\n')

            # Finalize current message with head_chunk
            await self._deliver_single(head_chunk, self.current_msg_id)

            # Advance state to new message
            self.current_msg_id = None
            self.current_thought = ""  # Thought is preserved in the head_chunk
            self.current_text = tail_chunk
            formatted = self._render_content(self.current_text, self.current_thought)

        if formatted.strip():
            self.current_msg_id = await self._deliver_single(formatted, self.current_msg_id)


# Compatibility Aliases for bot.py and tests
DirectChatStreamer = UnifiedStreamer
ACPStreamer = UnifiedStreamer
