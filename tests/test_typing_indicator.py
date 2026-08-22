import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from agents_on_hand.acp_streamer import ACPStreamer
from agents_on_hand.stream_handler import DirectChatStreamer


class TestTypingIndicator(unittest.TestCase):
    def test_direct_chat_streamer_typing_indicator(self):
        async def run_test():
            bot_mock = MagicMock()
            bot_mock.send_chat_action = AsyncMock()
            session_mock = MagicMock()

            streamer = DirectChatStreamer(bot=bot_mock, chat_id=12345, session=session_mock)
            streamer.start()

            streamer.notify_user_input()
            self.assertIsNotNone(streamer._typing_task)
            self.assertFalse(streamer._typing_task.done())

            await asyncio.sleep(0.1)
            bot_mock.send_chat_action.assert_called_with(chat_id=12345, action="typing")

            streamer.stop()
            await asyncio.sleep(0)
            self.assertTrue(streamer._typing_task.cancelled() or streamer._typing_task.done())

        asyncio.run(run_test())

    def test_acp_streamer_typing_indicator(self):
        async def run_test():
            bot_mock = MagicMock()
            bot_mock.send_chat_action = AsyncMock()
            session_mock = MagicMock()

            streamer = ACPStreamer(bot=bot_mock, chat_id=12345, session=session_mock)
            streamer.start()

            streamer.notify_user_input()
            self.assertIsNotNone(streamer._typing_task)
            self.assertFalse(streamer._typing_task.done())

            await asyncio.sleep(0.1)
            bot_mock.send_chat_action.assert_called_with(chat_id=12345, action="typing")

            streamer.stop()
            await asyncio.sleep(0)
            self.assertTrue(streamer._typing_task.cancelled() or streamer._typing_task.done())

        asyncio.run(run_test())



if __name__ == "__main__":
    unittest.main()
