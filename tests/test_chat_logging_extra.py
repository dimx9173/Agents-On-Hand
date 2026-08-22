import pathlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

@pytest.mark.asyncio
async def test_text_router_no_session():
    from agents_on_hand.handlers.chat import text_message_router
    with patch("agents_on_hand.handlers.chat.session_manager") as sm:
        sm.get_active_session.return_value = None
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.text = "hello"
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await text_message_router(update, MagicMock())
        update.message.reply_text.assert_called_once()
        assert "離線" in update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_text_router_esc_shortcut():
    from agents_on_hand.handlers.chat import text_message_router
    mock_sess = MagicMock()
    mock_sess.is_running = True
    mock_sess.session_id = "sess_1"
    mock_sess.agent_name = "Bash"
    mock_sess.send_control_char = MagicMock()
    with patch("agents_on_hand.handlers.chat.session_manager") as sm:
        sm.get_active_session.return_value = mock_sess
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.text = "esc"
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await text_message_router(update, MagicMock())
        mock_sess.send_control_char.assert_called_once()
        update.message.reply_text.assert_called_once()

@pytest.mark.asyncio
async def test_stop_no_active():
    from agents_on_hand.handlers.chat import stop_command
    with patch("agents_on_hand.handlers.chat.session_manager") as sm:
        sm.get_active_session.return_value = None
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await stop_command(update, MagicMock())
        update.message.reply_text.assert_called_once()
        assert "沒有作用中" in update.message.reply_text.call_args[0][0]

def test_ansi_cleaner_format():
    from agents_on_hand.ansi_cleaner import format_hermes_style, strip_ansi_codes
    assert strip_ansi_codes("\x1b[31mhello\x1b[0m") == "hello"
    assert "Tool" in format_hermes_style("tool: ls\nhello")
