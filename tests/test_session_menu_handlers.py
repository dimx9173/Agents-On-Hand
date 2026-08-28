import pathlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from agents_on_hand import config as cfg

def _mock_session(sid="sess_1", agent_name="Bash", running=True, active=False):
    s = MagicMock()
    s.session_id = sid
    s.agent_name = agent_name
    s.working_dir = Path(f"/tmp/{sid}")
    s.is_running = running
    s.get_last_n_lines = MagicMock(return_value="line1\nline2")
    mock_log = MagicMock()
    mock_log.exists.return_value = False
    mock_log.__str__ = lambda _: f"/tmp/{sid}.log"
    s.log_file_path = mock_log
    return s

@pytest.mark.asyncio
async def test_sessions_command_no_sessions():
    from agents_on_hand.ui.session_menu import sessions_command
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = []
        sm.get_active_session.return_value = None
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update, MagicMock())
        update.message.reply_text.assert_called_once()
        assert "沒有任何 Session" in update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_sessions_command_with_sessions():
    from agents_on_hand.ui.session_menu import sessions_command
    s1 = _mock_session("sess_a", "Claude", True, active=False)
    s2 = _mock_session("sess_b", "Bash", False, active=False)
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = [s1, s2]
        sm.get_active_session.return_value = s1
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update, MagicMock())
        update.message.reply_text.assert_called_once()
        txt = update.message.reply_text.call_args[0][0]
        assert "sess_a" in txt and "sess_b" in txt

@pytest.mark.asyncio
async def test_prune_command_zero():
    from agents_on_hand.ui.session_menu import prune_command
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.prune_offline_sessions.return_value = 0
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await prune_command(update, MagicMock())
        assert "沒有任何離線" in update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_prune_command_some():
    from agents_on_hand.ui.session_menu import prune_command
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.prune_offline_sessions.return_value = 3
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await prune_command(update, MagicMock())
        assert "3" in update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_session_action_prune_offline_none():
    from agents_on_hand.ui.session_menu import session_action_callback_handler
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.prune_offline_sessions.return_value = 0
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "sess:prune_offline"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
        q.answer.assert_called()

@pytest.mark.asyncio
async def test_acp_permission_approve():
    from agents_on_hand.handlers.acp_permissions import acp_permission_callback_handler
    mock_session = MagicMock()
    mock_session.respond_permission = AsyncMock()
    with patch("agents_on_hand.handlers.acp_permissions.session_manager") as sm:
        sm.get_session.return_value = mock_session
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "acp_perm:approve:sess_1:42"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await acp_permission_callback_handler(update, MagicMock())
        mock_session.respond_permission.assert_called_once()
        q.edit_message_text.assert_called_once()

@pytest.mark.asyncio
async def test_acp_permission_reject_short():
    from agents_on_hand.handlers.acp_permissions import acp_permission_callback_handler
    with patch("agents_on_hand.handlers.acp_permissions.session_manager") as sm:
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "acp_perm:bad"
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await acp_permission_callback_handler(update, MagicMock())
        q.answer.assert_called_once()

@pytest.mark.asyncio
async def test_restart_invalid_token():
    from agents_on_hand.handlers.restart import session_restart_callback_handler
    q = MagicMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.data = "sess_restart:invalid123"
    q.from_user = MagicMock(id=1)
    q.message = MagicMock()
    q.message.chat_id = 123
    update = MagicMock()
    update.callback_query = q
    with patch("agents_on_hand.handlers.restart.restart_registry", {}), patch("agents_on_hand.security.is_user_allowed", return_value=True):
        await session_restart_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    assert "過期" in q.edit_message_text.call_args[0][0]

@pytest.mark.asyncio
async def test_restart_path_out_of_bounds():
    """Restart with a valid token but a disallowed working dir must be refused."""
    from agents_on_hand.handlers.restart import session_restart_callback_handler
    from pathlib import Path
    q = MagicMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.data = "sess_restart:r_blocked"
    q.from_user = MagicMock(id=1)
    q.message = MagicMock()
    q.message.chat_id = 123
    update = MagicMock()
    update.callback_query = q
    registry = {"r_blocked": {"agent_key": "bash", "working_dir": Path("/etc")}}
    with patch("agents_on_hand.handlers.restart.restart_registry", registry), \
         patch("agents_on_hand.handlers.restart.is_path_allowed", return_value=False), \
         patch("agents_on_hand.handlers.restart.session_manager") as sm, \
         patch("agents_on_hand.security.is_user_allowed", return_value=True):
        await session_restart_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    assert "無法重新啟動" in q.edit_message_text.call_args[0][0]
    sm.create_session.assert_not_called()
