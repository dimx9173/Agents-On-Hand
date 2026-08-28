"""Towards 75% — app/session_menu/acp_session remaining branches."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.mark.asyncio
async def test_app_post_init_with_users():
    from agents_on_hand.app import post_init
    mock_bot = MagicMock()
    mock_bot.set_my_commands = AsyncMock()
    mock_bot.send_message = AsyncMock()
    app = MagicMock()
    app.bot = mock_bot
    import agents_on_hand.app as app_mod
    with patch.object(app_mod, "ALLOWED_TELEGRAM_USER_IDS", {123, 456}):
        await post_init(app)
    assert mock_bot.set_my_commands.called
    assert mock_bot.send_message.call_count == 2

@pytest.mark.asyncio
async def test_app_global_error_with_empty_update():
    from agents_on_hand.app import global_error_handler
    from telegram import Update
    # non-Update object should just log and not crash
    ctx = MagicMock()
    ctx.error = ValueError("test")
    await global_error_handler(object(), ctx)
    # Update with no query and no message
    upd = object.__new__(Update)
    object.__setattr__(upd, "_frozen", False)
    object.__setattr__(upd, "callback_query", None)
    object.__setattr__(upd, "message", None)
    await global_error_handler(upd, ctx)

@pytest.mark.asyncio
async def test_session_menu_full():
    from agents_on_hand.ui.session_menu import sessions_command, session_action_callback_handler, prune_command
    # sessions with mix
    s1 = MagicMock()
    s1.session_id = "s1"
    s1.agent_name = "Claude"
    s1.working_dir = Path("/tmp/a")
    s1.is_running = True
    s2 = MagicMock()
    s2.session_id = "s2"
    s2.agent_name = "Bash"
    s2.working_dir = Path("/tmp/b")
    s2.is_running = False
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm, patch("agents_on_hand.security.is_user_allowed", return_value=True):
        sm.list_user_sessions.return_value = [s1, s2]
        sm.get_active_session.return_value = s1
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        await sessions_command(update, MagicMock())
        assert update.message.reply_text.called
        # prune
        sm.prune_offline_sessions.return_value = 1
        update2 = MagicMock()
        update2.effective_user = MagicMock(id=1)
        update2.message = MagicMock()
        update2.message.reply_text = AsyncMock()
        await prune_command(update2, MagicMock())
        assert update2.message.reply_text.called
        # action switch with background callback
        sm.get_session.return_value = s1
        sm.get_active_session.return_value = s2
        s2.is_running = True
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "sess:switch:s1"
        q.from_user = MagicMock(id=1)
        q.message = MagicMock()
        q.message.chat_id = 1
        update3 = MagicMock()
        update3.callback_query = q
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        with patch("agents_on_hand.ui.session_menu.create_streamer_for_session") as mk, patch("agents_on_hand.ui.session_menu.format_telegram_code_block", return_value="```x```"), patch("agents_on_hand.ui.session_menu.bot_app", MagicMock()):
            mk.return_value = MagicMock()
            mk.return_value.start = MagicMock()
            await session_action_callback_handler(update3, ctx)
            assert q.answer.called

def test_acp_session_extract_edge2():
    from agents_on_hand.drivers.acp_driver import extract_acp_text_delta
    assert extract_acp_text_delta({"content": {"text": "hi"}}) == "hi"
    assert extract_acp_text_delta({"update": {"delta": "d"}}) == "d"
    assert extract_acp_text_delta({"content": 123}) == ""
    assert extract_acp_text_delta({"content": None}) == ""
    assert extract_acp_text_delta({}) == ""

def test_bot_facade_imports():
    from agents_on_hand.bot import main, help_command, new_command
    assert callable(main)
    assert callable(help_command)
    assert callable(new_command)
