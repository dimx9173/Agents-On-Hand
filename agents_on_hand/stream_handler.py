"""
Unified Streamer for Agents-On-Hand Telegram Bot.
Handles standardized DriverEvent payloads (Text, Thought, Tool Requests, Exit)
with modern Telegram HTML formatting, expandable blockquotes, and turn summaries.
"""

import asyncio
import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .ansi_cleaner import (
    escape_html,
    format_hermes_html,
    split_html_into_chunks,
)
from .drivers.base_driver import DriverEvent

logger = logging.getLogger(__name__)


def split_text_into_chunks(text: str, max_chars: int = 3800) -> list[str]:
    """Split text into chunks smaller than max_chars for Telegram safe delivery (backward-compatible helper)."""
    if not text:
        return []
    chunks = []
    while len(text) > max_chars:
        split_idx = text.rfind("\n", 0, max_chars)
        if split_idx == -1:
            split_idx = max_chars
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def _strip_html_tags(text: str) -> str:
    """Helper to strip HTML tags for plain-text fallback delivery."""
    clean = re.sub(r"<[^>]+>", "", text)
    return (
        clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    )


class UnifiedStreamer:
    """
    Unified Live Streamer for all Agent Sessions and Protocols.
    Renders streaming response text, expandable thinking sections (<blockquote expandable>),
    tool execution badges, and Telegram Inline Keyboards for Tool Approval & Quick Actions.
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
        self.tool_results: list[dict[str, Any]] = []
        self._tool_count: int = 0
        self.msg_ids: list[int] = []

        self._is_active: bool = False
        self._last_edit_time: float = 0.0
        self._turn_start_time: float = 0.0
        self._is_turn_final: bool = False

        self._typing_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._trailing_flush_task: asyncio.Task | None = None
        self._wait_indicator_task: asyncio.Task | None = None
        self._pending_tool_req_ids: set[str] = set()
        # P2 render cache: _flush_edit_locked re-renders the FULL accumulated
        # text on every throttle tick (O(n) per edit). Skip the Telegram edit
        # when the rendered output is byte-identical to the last delivery.
        self._last_rendered: str = ""
        self._last_rendered_chunk0: str = ""
        # Dirty-flag: set by _on_driver_event, cleared on flush. Lets idle
        # throttle ticks skip render + split + edit entirely.
        self._dirty: bool = False
        # Monotonic counter of delivered chunks; a new chunk (pagination)
        # forces delivery even if the first chunk text is unchanged.
        self._delivered_chunks: int = 0

    @property
    def current_msg_id(self) -> int | None:
        """Backward compatibility getter for the primary stream message ID."""
        return self.msg_ids[0] if self.msg_ids else None

    @current_msg_id.setter
    def current_msg_id(self, val: int | None):
        """Backward compatibility setter for the primary stream message ID."""
        if val is None:
            self.msg_ids.clear()
        elif not self.msg_ids:
            self.msg_ids.append(val)
        else:
            self.msg_ids[0] = val

    def start(self):
        """Start listening to driver events."""
        self._is_active = True
        try:
            self.session.trace.streamer_start(self.__class__.__name__)
        except Exception:
            pass
        logger.info(
            f"[STREAMER_START] session={getattr(self.session, 'session_id', '?')} chat={self.chat_id}"
        )
        self.session.register_listener(self._on_driver_event)

    def stop(self):
        """Stop streamer and typing indicator."""
        self._is_active = False
        try:
            self.session.trace.streamer_stop(self.__class__.__name__)
        except Exception:
            pass
        logger.info(
            f"[STREAMER_STOP] session={getattr(self.session, 'session_id', '?')} chat={self.chat_id} final_text={len(self.current_text)} thought={len(self.current_thought)}"
        )
        self._stop_typing()
        self._cancel_trailing_flush()
        self._cancel_wait_indicator()
        self._pending_tool_req_ids.clear()
        try:
            self.session.unregister_listener(self._on_driver_event)
        except Exception:
            pass

    def notify_user_input(self):
        """Trigger top typing indicator when user sends a prompt.

        A new prompt starts a new Telegram message: flush the previous turn's
        accumulated text to its own message, then reset streaming state.
        """
        if self.msg_ids and (self.current_text or self.current_thought):
            self._cancel_trailing_flush()
            asyncio.create_task(
                self._flush_previous_turn(
                    self.current_text, self.current_thought, list(self.msg_ids)
                )
            )
        self.current_text = ""
        self.current_thought = ""
        self.tool_results = []
        self._tool_count = 0
        self.msg_ids.clear()
        self._last_rendered = ""
        self._last_rendered_chunk0 = ""
        self._delivered_chunks = 0
        self._dirty = False
        self._is_turn_final = False
        self._pending_tool_req_ids.clear()
        self._cancel_wait_indicator()

        try:
            self._turn_start_time = asyncio.get_running_loop().time()
        except RuntimeError:
            self._turn_start_time = 0.0

        self._stop_typing()
        self._typing_task = asyncio.create_task(self._typing_loop())
        self._wait_indicator_task = asyncio.create_task(self._wait_indicator_loop())

    async def _wait_indicator_loop(self):
        """Live status line while the model is silent (>4s): elapsed + tools.

        U6: on small screens a static "thinking..." message gives no sense of
        progress. Refresh a one-line status (⏳ 12s · 🛠️ 3) until the first
        delta arrives, then hand over to the normal stream renderer.
        """
        try:
            await asyncio.sleep(4.0)
            t0 = self._turn_start_time or 0.0
            while self._is_active and not self.current_text and not self.current_thought:
                try:
                    now = asyncio.get_running_loop().time()
                except RuntimeError:
                    now = 0.0
                elapsed = max(0, int(now - t0)) if t0 else 0
                tools = f" · 🛠️ {self._tool_count}" if self._tool_count else ""
                status = f"⏳ <b>處理中 {elapsed}s</b>{tools}"
                async with self._lock:
                    if self.msg_ids or not self._is_active:
                        break
                    first_id = await self._deliver(status, None)
                    if first_id:
                        self.msg_ids.append(first_id)
                        break
                await asyncio.sleep(4.0)
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
        """Periodically send Telegram typing status every 4s while turn is active."""
        while self._is_active and not self._is_turn_final:
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
            logger.info(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type=TEXT_RAW +{len(event)} chars total={len(self.current_text)}"
            )
            asyncio.create_task(self._schedule_edit())
            return

        e_type = event.event_type if hasattr(event, "event_type") else getattr(event, "type", "")

        if e_type == DriverEvent.TEXT_DELTA:
            self.current_text += event.content
            self._dirty = True
            logger.debug(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type=TEXT_DELTA +{len(event.content)} chars total={len(self.current_text)}"
            )
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.THOUGHT_DELTA:
            self.current_thought += event.content
            self._dirty = True
            logger.debug(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type=THOUGHT_DELTA +{len(event.content)} chars total={len(self.current_thought)}"
            )
            asyncio.create_task(self._schedule_edit())

        elif e_type == DriverEvent.TOOL_REQUEST:
            logger.info(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type=TOOL_REQUEST req_id={event.request_id} tool={event.tool_name}"
            )
            self._on_tool_request(event.request_id, event.tool_name, event.tool_args)

        elif e_type == DriverEvent.TOOL_RESULT:
            self._tool_count += 1
            self._dirty = True
            content_str = str(event.content).strip()
            preview = (content_str[:80] + "...") if len(content_str) > 80 else content_str
            self.tool_results.append(
                {
                    "tool_name": event.tool_name,
                    "content": content_str,
                    "preview": preview,
                }
            )
            logger.info(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type=TOOL_RESULT tool={event.tool_name} chars={len(content_str)}"
            )
            asyncio.create_task(self._schedule_edit())

        elif e_type in (DriverEvent.TURN_END, DriverEvent.EXIT):
            logger.info(
                f"[AGENT->TG] session={getattr(self.session, 'session_id', '?')} type={e_type} is_final=True text={len(self.current_text)} thought={len(self.current_thought)} tools={self._tool_count}"
            )
            self._is_turn_final = True
            self._dirty = True
            self._stop_typing()
            asyncio.create_task(self._schedule_edit())

    def _on_tool_request(self, req_id: Any, tool_name: str, tool_args: Any):
        """Render Inline Keyboard for Tool Approval Request with deduplication."""
        req_key = str(req_id) if req_id is not None else ""
        if req_key and req_key in self._pending_tool_req_ids:
            logger.info(f"Ignoring duplicate tool request for req_id={req_id}")
            return
        if req_key:
            self._pending_tool_req_ids.add(req_key)

        detail_str = f"<b>{escape_html(tool_name)}</b>"
        if tool_args:
            args_str = str(tool_args)
            if len(args_str) > 1500:
                args_str = args_str[:1500] + "…"
            detail_str += f"\n<pre><code>{escape_html(args_str)}</code></pre>"

        text = f"🛡️ <b>執行 {escape_html(tool_name)}？</b>\n{detail_str}"

        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ 執行",
                        callback_data=f"acp_perm:approve:{self.session.session_id}:{req_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ 拒絕",
                        callback_data=f"acp_perm:reject:{self.session.session_id}:{req_id}",
                    ),
                ]
            ]
        )

        async def _safe_send_tool_req():
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
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
            try:
                now = asyncio.get_running_loop().time()
            except RuntimeError:
                now = 0.0
            remaining = self.edit_interval - (now - self._last_edit_time)
            if remaining > 0:
                if self._trailing_flush_task is None or self._trailing_flush_task.done():
                    self._trailing_flush_task = asyncio.create_task(self._trailing_flush(remaining))
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

    def _render_content(self, text: str, thought: str, is_final: bool = False) -> str:
        """Render accumulated text/thought into modern Telegram-friendly HTML."""
        duration: float | None = None
        if is_final and self._turn_start_time > 0:
            try:
                duration = asyncio.get_running_loop().time() - self._turn_start_time
            except RuntimeError:
                duration = None

        agent_name = getattr(self.session, "agent_name", "")
        return format_hermes_html(
            text=text,
            thought=thought,
            tool_results=self.tool_results if self.tool_results else None,
            is_final=is_final,
            duration=duration,
            agent_name=agent_name,
            tool_count=self._tool_count,
        )

    async def _deliver_single(
        self, formatted: str, msg_id: int | None, reply_markup: Any = None
    ) -> int | None:
        """Send or edit a single Telegram message with HTML & Plaintext fallback.

        Returns the message_id of the streaming message, or None on failure.
        """
        is_edit = msg_id is not None
        action = "edit" if is_edit else "send"
        logger.info(
            f"[TG_DELIVER] chat={self.chat_id} action={action} msg_id={msg_id} chars={len(formatted)} is_final={self._is_turn_final}"
        )
        kwargs: dict[str, Any] = {"parse_mode": "HTML"}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup

        if msg_id is None:
            try:
                msg = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=formatted,
                    **kwargs,
                )
                logger.info(
                    f"[TG_DELIVER_OK] chat={self.chat_id} action=send new_msg_id={msg.message_id} chars={len(formatted)}"
                )
                try:
                    self.session.trace.tg_deliver(
                        msg.message_id, len(formatted), is_edit=False, is_final=self._is_turn_final
                    )
                except Exception:
                    pass
                return msg.message_id
            except BadRequest as e:
                logger.warning(
                    f"[TG_DELIVER_HTML_FAIL] chat={self.chat_id} action=send err={e} retry_plain=True"
                )
                plain_text = _strip_html_tags(formatted)
                try:
                    plain_kwargs = {"reply_markup": reply_markup} if reply_markup else {}
                    msg = await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=plain_text,
                        **plain_kwargs,
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
                **kwargs,
            )
            try:
                self.session.trace.tg_deliver(
                    msg_id, len(formatted), is_edit=True, is_final=self._is_turn_final
                )
            except Exception:
                pass
            return msg_id
        except BadRequest as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                return msg_id
            logger.warning(
                f"[TG_DELIVER_HTML_FAIL] chat={self.chat_id} msg_id={msg_id} err={e} retry_plain=True"
            )
            plain_text = _strip_html_tags(formatted)
            try:
                plain_kwargs = {"reply_markup": reply_markup} if reply_markup else {}
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=msg_id,
                    text=plain_text,
                    **plain_kwargs,
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

    async def _flush_previous_turn(self, text: str, thought: str, msg_ids: list[int]):
        """Best-effort final edit/send of the previous turn's message before reset."""
        try:
            formatted = self._render_content(text, thought, is_final=True)
            if not formatted.strip():
                return
            chunks = split_html_into_chunks(formatted, max_chars=3800)
            if not chunks:
                return
            for i, chunk in enumerate(chunks):
                target_id = msg_ids[i] if i < len(msg_ids) else None
                await self._deliver_single(chunk, target_id)
        except Exception as e:
            logger.debug(f"Error flushing previous turn: {e}")

    async def _flush_edit_locked(self):
        """Render and send/edit the Telegram message with automatic multi-message pagination."""
        try:
            self._last_edit_time = asyncio.get_running_loop().time()
        except RuntimeError:
            self._last_edit_time = 0.0

        if not self._dirty and not self._is_turn_final:
            return
        self._dirty = False

        formatted = self._render_content(
            self.current_text, self.current_thought, is_final=self._is_turn_final
        )
        if not formatted.strip():
            return

        # Skip the Telegram edit when nothing visible changed (e.g. a delta
        # that only altered whitespace stripped by the cleaner, or a trailing
        # flush racing an already-delivered state). Final turns always flush
        # so the footer + action buttons are attached.
        if not self._is_turn_final and formatted == self._last_rendered:
            return

        chunks = split_html_into_chunks(formatted, max_chars=3800)
        if not chunks:
            return

        # Same-chunk-count fast path: only the last chunk can change while
        # earlier chunks are frozen. Skip re-editing frozen prefix chunks —
        # each skipped edit saves one Telegram API call.
        if not self._is_turn_final and self._delivered_chunks == len(chunks) and len(chunks) > 1:
            first_now = chunks[0]
            first_before = self._last_rendered_chunk0
            if first_now == first_before:
                chunk = chunks[-1]
                is_last = True
                if len(self.msg_ids) >= len(chunks):
                    target_id = self.msg_ids[-1]
                    delivered_id = await self._deliver_single(chunk, target_id)
                    if delivered_id and delivered_id != target_id:
                        self.msg_ids[-1] = delivered_id
                else:
                    new_id = await self._deliver_single(chunk, None)
                    if new_id:
                        self.msg_ids.append(new_id)
                self._last_rendered = formatted
                self._last_rendered_chunk0 = first_now
                return

        action_markup = None
        if self._is_turn_final:
            action_markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 重試此輪", callback_data=f"sess:retry:{self.session.session_id}"
                        ),
                        InlineKeyboardButton(
                            "🛑 結束 Session", callback_data=f"sess:kill:{self.session.session_id}"
                        ),
                    ]
                ]
            )

        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            chunk_markup = action_markup if (is_last and self._is_turn_final) else None

            if i < len(self.msg_ids):
                target_id = self.msg_ids[i]
                delivered_id = await self._deliver_single(
                    chunk, target_id, reply_markup=chunk_markup
                )
                if delivered_id and delivered_id != target_id:
                    self.msg_ids[i] = delivered_id
            else:
                new_id = await self._deliver_single(chunk, None, reply_markup=chunk_markup)
                if new_id:
                    self.msg_ids.append(new_id)

        self._last_rendered = formatted
        self._last_rendered_chunk0 = chunks[0] if chunks else ""
        self._delivered_chunks = len(chunks)


# Compatibility Alias for bot.py and tests
DirectChatStreamer = UnifiedStreamer
