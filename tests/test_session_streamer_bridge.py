import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agents_on_hand.drivers.base_driver import BaseDriver, DriverEvent
from agents_on_hand.handlers.chat import text_message_router
from agents_on_hand.session_manager import DRIVER_MAP, AgentSession, SessionManager
from agents_on_hand.stream_handler import UnifiedStreamer


class MockDriver(BaseDriver):
    def __init__(self, command: str, working_dir: Path):
        super().__init__(command, working_dir)
        self.started = False
        self.sent_prompts: list[str] = []

    async def start(self) -> bool:
        self.started = True
        self.is_running = True
        return True

    def send_prompt(self, text: str):
        self.sent_prompts.append(text)

    def send_control_char(self, char: str):
        pass

    async def respond_permission(self, request_id, approved: bool):
        pass

    def stop(self):
        self.is_running = False


class TestSessionStreamerBridge(unittest.IsolatedAsyncioTestCase):
    async def test_listener_buffered_before_driver_starts(self):
        """Listeners registered on AgentSession before driver starts must be attached to the driver when it starts."""
        session = AgentSession(
            session_id="test_sess_1",
            user_id=1001,
            agent_key="opencode",
            agent_name="OpenCode CLI",
            command="opencode acp",
            working_dir=Path("/tmp/test_cwd"),
        )
        self.assertIsNone(session.driver)

        received_events: list[DriverEvent] = []
        session.register_listener(lambda ev: received_events.append(ev))

        driver = MockDriver("opencode acp", Path("/tmp/test_cwd"))
        with patch.dict("agents_on_hand.session_manager.DRIVER_MAP", {"acp": lambda *a, **kw: driver}, clear=False):
            started = await session.start(["acp"])
            self.assertTrue(started)
            self.assertEqual(session.driver, driver)

        ev = DriverEvent(DriverEvent.TEXT_DELTA, content="Hello from driver")
        driver.emit_event(ev)

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].content, "Hello from driver")

    async def test_streamer_receives_deltas_when_started_before_driver(self):
        """UnifiedStreamer started before driver starts must receive TEXT_DELTA and edit TG message."""
        session = AgentSession(
            session_id="test_sess_2",
            user_id=1002,
            agent_key="opencode",
            agent_name="OpenCode CLI",
            command="opencode acp",
            working_dir=Path("/tmp/test_cwd"),
        )

        bot = MagicMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 100
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()

        streamer = UnifiedStreamer(bot=bot, chat_id=1002, session=session, edit_interval=0.1)
        streamer.start()

        driver = MockDriver("opencode acp", Path("/tmp/test_cwd"))
        with patch.dict("agents_on_hand.session_manager.DRIVER_MAP", {"acp": lambda *a, **kw: driver}, clear=False):
            await session.start(["acp"])

        driver.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content="hihi 👋 — how can I help?"))

        await asyncio.sleep(0.3)

        self.assertIn("hihi 👋 — how can I help?", streamer.current_text)
        bot.send_message.assert_called_once()
        streamer.stop()

    async def test_text_message_router_waits_for_starting_session(self):
        """text_message_router should wait for a starting session instead of immediately saying offline."""
        session = AgentSession(
            session_id="test_sess_3",
            user_id=1003,
            agent_key="opencode",
            agent_name="OpenCode CLI",
            command="opencode acp",
            working_dir=Path("/tmp/test_cwd"),
        )
        session.is_starting = True
        session.is_running = False

        driver = MockDriver("opencode acp", Path("/tmp/test_cwd"))
        session.driver = driver

        async def become_ready_soon():
            await asyncio.sleep(0.2)
            session.is_running = True
            session.is_starting = False

        asyncio.create_task(become_ready_soon())

        mock_sm = MagicMock(spec=SessionManager)
        mock_sm.get_active_session.return_value = session
        mock_sm.user_active_session = {1003: "test_sess_3"}
        mock_sm.sessions = {"test_sess_3": session}

        update = MagicMock()
        update.effective_user.id = 1003
        update.message.text = "hihi"
        update.message.chat_id = 1003
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot = MagicMock()

        with patch("agents_on_hand.handlers.chat.session_manager", mock_sm), \
             patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await text_message_router(update, context)

        for call in update.message.reply_text.call_args_list:
            text = call.args[0] if call.args else call.kwargs.get("text", "")
            self.assertNotIn("已離線", text)

        self.assertEqual(driver.sent_prompts, ["hihi"])

    async def test_prune_offline_sessions(self):
        """prune_offline_sessions should remove only non-running sessions for the user."""
        sm = SessionManager(store_path=Path("/tmp/test_prune_state.json"))
        sm.sessions.clear()
        sm.user_active_session.clear()

        s_online = AgentSession("sess_online", 1005, "opencode", "OpenCode", "opencode acp", Path("/tmp"))
        s_online.is_running = True
        s_offline1 = AgentSession("sess_offline1", 1005, "opencode", "OpenCode", "opencode acp", Path("/tmp"))
        s_offline1.is_running = False
        s_offline2 = AgentSession("sess_offline2", 1005, "opencode", "OpenCode", "opencode acp", Path("/tmp"))
        s_offline2.is_running = False
        s_other_user = AgentSession("sess_other", 9999, "opencode", "OpenCode", "opencode acp", Path("/tmp"))
        s_other_user.is_running = False

        sm.sessions["sess_online"] = s_online
        sm.sessions["sess_offline1"] = s_offline1
        sm.sessions["sess_offline2"] = s_offline2
        sm.sessions["sess_other"] = s_other_user
        sm.user_active_session[1005] = "sess_offline1"

        pruned = sm.prune_offline_sessions(1005)
        self.assertEqual(pruned, 2)
        self.assertIn("sess_online", sm.sessions)
        self.assertNotIn("sess_offline1", sm.sessions)
        self.assertNotIn("sess_offline2", sm.sessions)
        self.assertIn("sess_other", sm.sessions)
        self.assertNotIn(1005, sm.user_active_session)

    async def test_session_menu_prune_offline_callback(self):
        """Callback sess:prune_offline should prune offline sessions and update UI."""
        from agents_on_hand.ui.session_menu import session_action_callback_handler

        sm = MagicMock()
        sm.prune_offline_sessions.return_value = 3
        sm.list_user_sessions.return_value = []

        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "sess:prune_offline"
        update.callback_query.from_user.id = 1006
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        context = MagicMock()

        with patch("agents_on_hand.ui.session_menu.session_manager", sm), \
             patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, context)

        sm.prune_offline_sessions.assert_called_once_with(1006)
        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        self.assertIn("已成功清理 3 個離線 Session", text)
