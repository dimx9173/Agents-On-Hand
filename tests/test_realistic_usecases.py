"""Realistic responsible use-case tests — full user journeys."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_session(sid="sess_1", agent_name="Bash", running=True):
    s = MagicMock()
    s.session_id = sid
    s.agent_name = agent_name
    s.working_dir = Path(f"/tmp/{sid}")
    s.is_running = running
    s.get_last_n_lines = MagicMock(return_value="history line 1\nhistory line 2")
    s.log_file_path = MagicMock()
    s.log_file_path.__str__ = lambda _: f"/tmp/{sid}.log"
    s.log_file_path.exists.return_value = False
    s.set_background_completion_callback = MagicMock()
    s.send_input = MagicMock()
    s.send_control_char = MagicMock()
    s.respond_permission = AsyncMock()
    s.is_starting = False
    return s


@pytest.mark.asyncio
async def test_journey_new_session_via_directory_browser(tmp_path):
    """User: /aoh_new -> browse -> select dir -> select agent -> start session."""
    from agents_on_hand.ui.directory_browser import (
        agent_start_callback_handler,
        send_directory_browser,
        show_agent_selector,
    )

    # browse
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.callback_query = None
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    with (
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.get_path_token", return_value="p_0"),
    ):
        await send_directory_browser(update, MagicMock(), tmp_path)
    update.message.reply_text.assert_called_once()
    # show agent selector with no agents
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    with (
        patch("agents_on_hand.ui.directory_browser.get_installed_cli_agents", return_value={}),
        patch("agents_on_hand.ui.directory_browser.get_path_token", return_value="p_0"),
    ):
        await show_agent_selector(q, tmp_path)
    q.edit_message_text.assert_called_once()
    assert (
        "已安裝" in q.edit_message_text.call_args[0][0]
        or "檢測不到" in q.edit_message_text.call_args[0][0]
    )
    # start agent
    with (
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk_streamer,
        patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=tmp_path),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        mock_sess = _make_mock_session("sess_new", "Bash", True)
        sm.create_session.return_value = mock_sess
        q2 = MagicMock()
        q2.answer = AsyncMock()
        q2.edit_message_text = AsyncMock()
        q2.from_user = MagicMock(id=1)
        q2.message = MagicMock()
        q2.message.chat_id = 123
        q2.data = "agent:start:p_0:bash"
        update2 = MagicMock()
        update2.callback_query = q2
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        mk_streamer.return_value = MagicMock()
        mk_streamer.return_value.start = MagicMock()
        await agent_start_callback_handler(update2, ctx)
        sm.create_session.assert_called_once()
        q2.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_journey_chat_send_and_offline_recovery(tmp_path):
    """User sends text -> active session handles -> offline shows restart button."""
    from agents_on_hand.handlers.chat import text_message_router

    # active session running
    mock_sess = _make_mock_session("sess_chat", "Claude", True)
    mock_sess.is_running = True
    mock_sess.is_starting = False
    with (
        patch("agents_on_hand.handlers.chat.session_manager") as sm,
        patch("agents_on_hand.handlers.chat.create_streamer_for_session") as mk_streamer,
        patch("agents_on_hand.handlers.chat.register_restart_info", return_value="r_abc12345"),
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        sm.get_active_session.return_value = mock_sess
        sm.user_active_session = {1: "sess_chat"}
        sm.sessions = {"sess_chat": mock_sess}
        mk_streamer.return_value = MagicMock()
        mk_streamer.return_value.session = mock_sess
        mk_streamer.return_value._is_active = True
        mk_streamer.return_value.start = MagicMock()
        mk_streamer.return_value.stop = MagicMock()
        mk_streamer.return_value.notify_user_input = MagicMock()
        # need to ensure active_streamers dict is patched
        with patch("agents_on_hand.handlers.chat.active_streamers", {}):
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.text = "hello agent"
            update.message.chat_id = 123
            update.message.reply_text = AsyncMock()
            ctx = MagicMock()
            ctx.bot = MagicMock()
            await text_message_router(update, ctx)
            assert mock_sess.send_input.called
            assert mock_sess.send_input.call_args[0][0] == "hello agent"
            assert "turn_id" in mock_sess.send_input.call_args[1]
        # offline case
        mock_sess_off = _make_mock_session("sess_off", "Bash", False)
        mock_sess_off.is_running = False
        mock_sess_off.is_starting = False
        sm.get_active_session.return_value = mock_sess_off
        update2 = MagicMock()
        update2.effective_user = MagicMock(id=1)
        update2.message = MagicMock()
        update2.message.text = "hi"
        update2.message.reply_text = AsyncMock()
        with patch("agents_on_hand.handlers.chat.active_streamers", {}):
            await text_message_router(update2, MagicMock())
        update2.message.reply_text.assert_called_once()
        assert "離線" in update2.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_journey_tool_approval_int_and_str_req_id():
    """ACP tool approval with int and string req_id."""
    from agents_on_hand.handlers.acp_permissions import acp_permission_callback_handler

    for req_id_str, expected in [("42", 42), ("abc-xyz", "abc-xyz")]:
        mock_sess = MagicMock()
        mock_sess.respond_permission = AsyncMock()
        with patch("agents_on_hand.handlers.acp_permissions.session_manager") as sm:
            sm.get_session.return_value = mock_sess
            q = MagicMock()
            q.answer = AsyncMock()
            q.edit_message_text = AsyncMock()
            q.data = f"acp_perm:approve:sess_1:{req_id_str}"
            q.from_user = MagicMock(id=1)
            update = MagicMock()
            update.callback_query = q
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                await acp_permission_callback_handler(update, MagicMock())
            mock_sess.respond_permission.assert_called_once()
            # check int vs str
            called_id = mock_sess.respond_permission.call_args[0][0]
            assert called_id == expected


@pytest.mark.asyncio
async def test_journey_session_switch_and_logs():
    """User switches active session, views logs, kills."""
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    mock_sess = _make_mock_session("sess_sw", "Bash", True)
    with (
        patch("agents_on_hand.ui.session_menu.session_manager") as sm,
        patch("agents_on_hand.ui.session_menu.active_streamers", {}),
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        sm.get_session.return_value = mock_sess
        sm.get_active_session.return_value = None
        sm.set_active_session.return_value = True
        # switch
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "sess:switch:sess_sw"
        q.from_user = MagicMock(id=1)
        q.message = MagicMock()
        q.message.chat_id = 123
        update = MagicMock()
        update.callback_query = q
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        with (
            patch("agents_on_hand.ui.session_menu.create_streamer_for_session") as mk_streamer,
            patch(
                "agents_on_hand.ui.session_menu.format_telegram_code_block",
                return_value="```logs```",
            ),
        ):
            mk_streamer.return_value = MagicMock()
            mk_streamer.return_value.start = MagicMock()
            await session_action_callback_handler(update, ctx)
            sm.set_active_session.assert_called_once()
            ctx.bot.send_message.assert_called_once()
        # logs
        q2 = MagicMock()
        q2.answer = AsyncMock()
        q2.data = "sess:logs:sess_sw"
        q2.from_user = MagicMock(id=1)
        q2.message = MagicMock()
        q2.message.reply_text = AsyncMock()
        update2 = MagicMock()
        update2.callback_query = q2
        with patch(
            "agents_on_hand.ui.session_menu.format_telegram_code_block", return_value="```logs```"
        ):
            await session_action_callback_handler(update2, ctx)
            q2.message.reply_text.assert_called_once()
        # kill
        sm.kill_session.return_value = True
        q3 = MagicMock()
        q3.answer = AsyncMock()
        q3.data = "sess:kill:sess_sw"
        q3.from_user = MagicMock(id=1)
        q3.message = MagicMock()
        q3.edit_message_text = AsyncMock()
        update3 = MagicMock()
        update3.callback_query = q3
        await session_action_callback_handler(update3, MagicMock())
        sm.kill_session.assert_called()


@pytest.mark.asyncio
async def test_journey_background_finish_and_restart(tmp_path):
    """Agent exits in background -> notification with restart button -> restart."""
    from agents_on_hand.handlers.restart import (
        on_background_session_finished,
        session_restart_callback_handler,
    )
    from agents_on_hand.session_manager import AgentSession

    # background finish
    sess = AgentSession(
        session_id="sess_bg",
        user_id=1,
        agent_key="bash",
        agent_name="Bash",
        command="bash",
        working_dir=tmp_path,
    )
    sess.is_running = False
    with (
        patch("agents_on_hand.handlers.restart.bot_app") as bot_app,
        patch("agents_on_hand.handlers.restart.register_restart_info", return_value="r_test1234"),
        patch("agents_on_hand.handlers.restart.active_streamers", {}),
    ):
        bot_app.bot = MagicMock()
        bot_app.bot.send_message = AsyncMock()
        # need running loop
        on_background_session_finished(sess)
        await asyncio.sleep(0.1)
    # restart with valid token
    with (
        patch(
            "agents_on_hand.handlers.restart.restart_registry",
            {"r_test1234": {"agent_key": "bash", "working_dir": tmp_path}},
        ),
        patch("agents_on_hand.handlers.restart.session_manager") as sm,
        patch("agents_on_hand.handlers.restart.create_streamer_for_session") as mk_streamer,
        patch("agents_on_hand.handlers.restart.is_path_allowed", return_value=True),
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        mock_sess = _make_mock_session("sess_new2", "Bash", True)
        sm.create_session.return_value = mock_sess
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess_restart:r_test1234"
        q.from_user = MagicMock(id=1)
        q.message = MagicMock()
        q.message.chat_id = 123
        update = MagicMock()
        update.callback_query = q
        ctx = MagicMock()
        ctx.bot = MagicMock()
        mk_streamer.return_value = MagicMock()
        mk_streamer.return_value.start = MagicMock()
        with patch("agents_on_hand.handlers.restart.active_streamers", {}):
            await session_restart_callback_handler(update, ctx)
        sm.create_session.assert_called_once()
        q.edit_message_text.assert_called_once()
