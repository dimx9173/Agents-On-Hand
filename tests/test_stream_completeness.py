"""
Tests for UnifiedStreamer message completeness.

Regression tests for the bug where OMP (ACP) responses arrived complete
at the driver layer but were truncated or lost on the way to Telegram:
1. Trailing deltas dropped by the edit throttle with no final flush.
2. Markdown BadRequest on send/edit silently swallowed.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest

from agents_on_hand.drivers.base_driver import DriverEvent
from agents_on_hand.stream_handler import UnifiedStreamer


def _make_bot():
    bot = MagicMock()
    msg = MagicMock()
    msg.message_id = 42
    bot.send_message = AsyncMock(return_value=msg)
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot


def _rendered_texts(bot):
    """All text payloads sent or edited, in call order."""
    texts = [call.kwargs.get("text") for call in bot.send_message.call_args_list] + [
        call.kwargs.get("text") for call in bot.edit_message_text.call_args_list
    ]
    return [t for t in texts if t is not None]


class TestStreamCompleteness(unittest.TestCase):
    def test_trailing_deltas_are_flushed_after_throttle(self):
        """Rapid-fire TEXT_DELTA chunks within the throttle window must all
        reach Telegram — the final edit must contain the complete text."""

        async def run_test():
            bot = _make_bot()
            session = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.3)
            streamer.start()

            chunks = ["Hi", "! How can ", "I help you today?"]
            for chunk in chunks:
                streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=chunk))
                await asyncio.sleep(0.01)

            full_text = "".join(chunks)
            # Wait well beyond the throttle window for the trailing flush
            await asyncio.sleep(1.0)

            rendered = _rendered_texts(bot)
            streamer.stop()
            await asyncio.sleep(0)

            self.assertTrue(rendered, "Nothing was sent to Telegram at all")
            self.assertIn(
                full_text,
                rendered[-1],
                f"Last rendered message missing trailing chunks: {rendered[-1]!r}",
            )

        asyncio.run(run_test())

    def test_markdown_badrequest_falls_back_to_plain_text(self):
        """If Telegram rejects Markdown parsing, the message must be retried
        as plain text instead of being silently dropped."""

        async def run_test():
            bot = _make_bot()
            ok_msg = MagicMock()
            ok_msg.message_id = 7
            bot.send_message = AsyncMock(side_effect=[BadRequest("Can't parse entities"), ok_msg])
            session = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.1)
            streamer.start()

            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="a *broken md"))
            await asyncio.sleep(0.5)

            streamer.stop()
            await asyncio.sleep(0)

            self.assertEqual(
                bot.send_message.call_count,
                2,
                "Plain-text fallback retry was not attempted",
            )
            retry = bot.send_message.call_args_list[1]
            self.assertTrue(
                retry.kwargs.get("parse_mode") is None,
                "Fallback retry should not use Markdown parse_mode",
            )
            self.assertIn("a *broken md", retry.kwargs.get("text", ""))

        asyncio.run(run_test())

    def test_edit_badrequest_not_modified_is_ignored_but_others_retried(self):
        """'Message is not modified' edits are benign, but other BadRequests
        (e.g. parse errors) must trigger a plain-text retry."""

        async def run_test():
            bot = _make_bot()
            bot.edit_message_text = AsyncMock(side_effect=BadRequest("Can't parse entities"))
            session = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.1)
            streamer.start()

            streamer._on_driver_event(
                DriverEvent(DriverEvent.TEXT_DELTA, content="first chunk `broken")
            )
            await asyncio.sleep(0.3)
            # Trigger an edit (message already exists now)
            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=" + second"))
            await asyncio.sleep(0.5)

            streamer.stop()
            await asyncio.sleep(0)

            self.assertGreaterEqual(
                bot.edit_message_text.call_count,
                2,
                "Edit failure should be retried as plain text",
            )
            last = bot.edit_message_text.call_args_list[-1]
            self.assertTrue(last.kwargs.get("parse_mode") is None)

        asyncio.run(run_test())

    def test_new_prompt_starts_new_message(self):
        """Each new user prompt must start a fresh Telegram message instead of
        appending the response onto the previous message."""

        async def run_test():
            bot = _make_bot()
            msg1 = MagicMock()
            msg1.message_id = 100
            msg2 = MagicMock()
            msg2.message_id = 200
            bot.send_message = AsyncMock(side_effect=[msg1, msg2])
            session = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.1)
            streamer.start()

            # First prompt response
            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="answer one"))
            await asyncio.sleep(0.5)

            # User sends a second prompt
            streamer.notify_user_input()
            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="answer two"))
            await asyncio.sleep(0.5)

            streamer.stop()
            await asyncio.sleep(0)

            self.assertEqual(
                bot.send_message.call_count,
                2,
                "Second prompt should open a new Telegram message",
            )
            second_text = bot.send_message.call_args_list[1].kwargs.get("text", "")
            self.assertIn("answer two", second_text)
            self.assertNotIn("answer one", second_text)

    def test_long_message_automatically_chunks_across_multiple_messages(self):
        """When text delta exceeds Telegram safe limit (3800 chars), it must automatically
        finalize the first message and create subsequent messages without dropping tokens."""

        async def run_test():
            bot = _make_bot()
            msg1 = MagicMock(message_id=101)
            msg2 = MagicMock(message_id=102)
            msg3 = MagicMock(message_id=103)
            bot.send_message = AsyncMock(side_effect=[msg1, msg2, msg3])

            session = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.1)
            streamer.start()

            # Emit ~8000 characters with newlines
            chunk_a = "Line A " * 400 + "\n"  # ~2800 chars
            chunk_b = "Line B " * 400 + "\n"  # ~2800 chars
            chunk_c = "Line C " * 400 + "\n"  # ~2800 chars

            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=chunk_a))
            await asyncio.sleep(0.2)
            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=chunk_b))
            await asyncio.sleep(0.2)
            streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=chunk_c))
            await asyncio.sleep(0.5)

            streamer.stop()
            await asyncio.sleep(0)

            # Check that multiple messages were created (bot.send_message called at least 2-3 times)
            self.assertGreaterEqual(
                bot.send_message.call_count,
                2,
                "Long streaming text should roll over into multiple Telegram messages",
            )
            rendered = _rendered_texts(bot)
            combined_rendered = "".join(rendered)
            self.assertIn("Line A", combined_rendered)
            self.assertIn("Line B", combined_rendered)
            self.assertIn("Line C", combined_rendered)

        asyncio.run(run_test())


class TestRepetitionLoopGuard(unittest.TestCase):
    """Degeneration-loop guard: detect repeated ack output and interrupt once."""

    def _loop_turn_text(self, n: int) -> str:
        ack = "收到：Session 選單改兩步，先選 Agent 再列 Session，外部執行中的也要列出。我來完成這個調整。"
        return "\n\n".join(ack for _ in range(n)) + "\n"

    def test_detector_returns_none_for_normal_output(self):
        from agents_on_hand.stream_handler import _detect_repetition_loop

        normal = "\n".join(
            f"• 完成第 {i} 步：檢查路由、注入依賴、補測試與文件。"
            for i in range(1, 9)
        )
        self.assertIsNone(_detect_repetition_loop(normal))

    def test_detector_catches_identical_repetition(self):
        from agents_on_hand.stream_handler import _detect_repetition_loop

        phrase = _detect_repetition_loop(self._loop_turn_text(8))
        self.assertIsNotNone(phrase)
        self.assertIn("我來完成這個調整", phrase)

    def test_detector_catches_tail_repetition_with_variant_wording(self):
        """prime loop 措辭略異但結尾相同 — 句尾短語計數要抓到。"""
        from agents_on_hand.stream_handler import _detect_repetition_loop

        variants = [
            "收到：Session 選單改兩步，先選 Agent 再列 Session，外部執行中的也要列出。我來完成這個調整。",
            "收到，Session 選單改成先選 Agent，再列出該 Agent 的 Session（含外部執行中的）。我來完成這個調整。",
            "收到：Session 選單改成先選 Agent，再列出該 Agent 的 Session（含外部執行中的）。我來完成這個調整。",
            "收到，Session 選單改成先選 Agent，再列出該 Agent 的 Session，外部執行中的也要一起列出。我來完成這個調整。",
            "收到：Session 選單改兩步，先選 Agent 再列 Session，外部執行中的也要列出。我來完成這個調整。",
            "收到：Session 選單改兩步，先選 Agent 再列 Session，外部執行中的也要列出。我來完成這個調整。",
            "收到，Session 選單改成先選 Agent，再列出該 Agent 的 Session。我來完成這個調整。",
            "收到：Session 選單改成先選 Agent，再列出該 Agent 的 Session。我來完成這個調整。",
        ]
        phrase = _detect_repetition_loop("\n\n".join(variants))
        self.assertIsNotNone(phrase)

    def test_streamer_sends_esc_and_alert_once_per_turn(self):
        async def run_test():
            from agents_on_hand.stream_handler import UnifiedStreamer

            bot = _make_bot()
            session = MagicMock()
            session.send_control_char = MagicMock()
            streamer = UnifiedStreamer(bot=bot, chat_id=12345, session=session, edit_interval=0.05)
            streamer.start()

            # 送出 loop 文本（分多次 delta 累積）
            for chunk in ["收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n",
                         "收到：Session 選單改兩步，先選 Agent 再列 Session。\n"]:
                streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=chunk))
                await asyncio.sleep(0.01)

            await asyncio.sleep(0.3)  # 讓 alert 送出
            streamer.stop()
            await asyncio.sleep(0)

            self.assertEqual(session.send_control_char.call_count, 1, "ESC 應只送一次")
            self.assertEqual(session.send_control_char.call_args[0][0], "\x1b")
            alerts = [
                c.kwargs.get("text")
                for c in bot.send_message.call_args_list
                if c.kwargs.get("text") and "偵測到重複輸出" in c.kwargs["text"]
            ]
            self.assertEqual(len(alerts), 1, "Loop alert 應只發一次")

        asyncio.run(run_test())

    def test_fenced_log_does_not_break_markdown_block(self):
        """A log containing triple backticks must not close the outer Markdown fence early."""
        from agents_on_hand.ansi_cleaner import format_telegram_code_block

        block = format_telegram_code_block("foo\n```\n```x```\nbar", max_chars=9999)
        inner = block[block.index("\n") + 1 : block.rindex("\n")]
        self.assertNotIn("```\n", inner)
        self.assertIn("\u200b", block)

    def test_retryafter_not_lost_schedules_retry(self):
        """A RetryAfter 429 must be swallowed (not logged as failure) and leave a retry marker."""

        async def run_test():
            from telegram.error import RetryAfter

            import agents_on_hand.stream_handler as sh
            from agents_on_hand.stream_handler import UnifiedStreamer

            sh._chat_next_slot.clear()
            sh._chat_flood_until.clear()
            bot = _make_bot()
            msg = MagicMock(message_id=99)
            bot.send_message = AsyncMock(side_effect=[RetryAfter(2), msg])
            sess = MagicMock()
            st = UnifiedStreamer(bot=bot, chat_id=99111, session=sess, edit_interval=0.05, chat_min_gap=0.0)
            st.start()
            st._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="hello"))
            await asyncio.sleep(0.6)
            st.stop()
            self.assertTrue(sh._chat_flood_until.get(99111, 0.0) > 0.0)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
