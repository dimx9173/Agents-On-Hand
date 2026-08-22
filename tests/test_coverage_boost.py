import pathlib
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

def test_callback_registry_eviction():
    from agents_on_hand.callback_registry import path_registry, path_to_token, get_path_token, _MAX_PATH_TOKENS
    path_registry.clear()
    path_to_token.clear()
    # fill to cap
    for i in range(_MAX_PATH_TOKENS + 5):
        get_path_token(Path(f"/tmp/evict_test_{i}"))
    assert len(path_registry) <= _MAX_PATH_TOKENS + 5
    # ensure eviction happened: first token should be gone or registry bounded
    assert len(path_registry) == _MAX_PATH_TOKENS or len(path_registry) <= _MAX_PATH_TOKENS + 1

def test_config_parse_edge():
    from agents_on_hand.config import _parse_user_ids
    assert _parse_user_ids("") == set()
    assert _parse_user_ids("  ") == set()
    assert _parse_user_ids("1,,2") == {1,2}
    try:
        _parse_user_ids("x")
        assert False
    except ValueError:
        pass

def test_session_manager_list_and_prune(tmp_path):
    from agents_on_hand.session_manager import SessionManager, AgentSession
    mgr = SessionManager(store_path=tmp_path / "state.json")
    # create two offline sessions
    for i in range(2):
        s = AgentSession(session_id=f"sess_{i}", user_id=1, agent_key="bash", agent_name="Bash", command="bash", working_dir=tmp_path)
        s.is_running = False
        mgr.sessions[s.session_id] = s
    mgr.user_active_session[1] = "sess_0"
    assert len(mgr.list_user_sessions(1)) == 2
    count = mgr.prune_offline_sessions(1)
    assert count == 2
    assert len(mgr.sessions) == 0

@pytest.mark.asyncio
async def test_session_menu_switch_branch():
    from agents_on_hand.ui.session_menu import session_action_callback_handler
    mock_sess = MagicMock()
    mock_sess.session_id = "sess_x"
    mock_sess.agent_name = "Bash"
    mock_sess.working_dir = Path("/tmp/x")
    mock_sess.is_running = False
    mock_sess.get_last_n_lines.return_value = "log line"
    mock_sess.set_background_completion_callback = MagicMock()
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.get_session.return_value = mock_sess
        sm.get_active_session.return_value = None
        sm.set_active_session.return_value = True
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "sess:switch:sess_x"
        q.from_user = MagicMock(id=1)
        q.message = MagicMock()
        q.message.chat_id = 123
        q.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        with patch("agents_on_hand.ui.session_menu.create_streamer_for_session") as mk_streamer, patch("agents_on_hand.ui.session_menu.format_telegram_code_block", return_value="```code```"), patch("agents_on_hand.security.is_user_allowed", return_value=True):
            mk_streamer.return_value = MagicMock()
            mk_streamer.return_value.start = MagicMock()
            update = MagicMock()
            update.callback_query = q
            await session_action_callback_handler(update, ctx)
            q.answer.assert_called()
