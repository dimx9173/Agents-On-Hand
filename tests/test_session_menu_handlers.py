from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_session(sid="sess_1", agent_name="Bash", running=True, active=False, agent_key="bash"):
    s = MagicMock()
    s.session_id = sid
    s.agent_name = agent_name
    s.agent_key = agent_key
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

    s1 = _mock_session("sess_a", "Claude", True, active=False, agent_key="claude")
    s2 = _mock_session("sess_b", "Bash", False, active=False, agent_key="bash")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = [s1, s2]
        # Step 1 groups by agent_key, independent of running state.
        sm.get_active_session.return_value = None
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update, MagicMock())
        update.message.reply_text.assert_called_once()
        txt = update.message.reply_text.call_args[0][0]
        # Step 1 = agent list: display names + running/total badges, not flat sessions.
        assert "管理 Session" in txt
        assert "Claude Code" in txt and "Bash Shell" in txt
        assert "🟢1/1" in txt and "🟢0/1" in txt
        assert "sess_a" not in txt and "sess_b" not in txt
        kwargs = update.message.reply_text.call_args[1]
        markup = kwargs.get("reply_markup")
        assert markup is not None
        btns = [b for row in markup.inline_keyboard for b in row]
        agent_btns = [
            b for b in btns if b.callback_data and b.callback_data.startswith("sess:agent:")
        ]
        assert {b.callback_data for b in agent_btns} == {
            "sess:agent:claude",
            "sess:agent:bash",
        }
        # No per-session switch buttons at step 1.
        assert not any(
            b.callback_data and b.callback_data.startswith("sess:switch:") for b in btns
        )
        # Offline session exists -> global prune button present.
        assert any(b.callback_data == "sess:prune_offline" for b in btns)


@pytest.mark.asyncio
async def test_sessions_command_button_label_strips_sess_prefix():
    from agents_on_hand.ui.session_menu import sessions_command

    s = _mock_session("sess_abc12345", "Bash", True, active=False, agent_key="bash")
    s.working_dir = Path("/tmp/myproj")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = [s]
        sm.get_active_session.return_value = None
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update, MagicMock())
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        btn = [
            b
            for row in markup.inline_keyboard
            for b in row
            if b.callback_data == "sess:agent:bash"
        ][0]
        # Step 1 renders one agent button (display name + badge), not flat sessions.
        assert btn.text.startswith("🤖 ")
        assert "Bash Shell" in btn.text
        assert "🟢1/1" in btn.text
        assert btn.callback_data == "sess:agent:bash"

@pytest.mark.asyncio
async def test_sess_agent_view_shows_only_that_agent():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    claude = _mock_session("sess_claude1", "Claude", True, active=False, agent_key="claude")
    claude.working_dir = Path("/tmp/claudeproj")
    bash = _mock_session("sess_bash99", "Bash", True, active=False, agent_key="bash")
    bash.working_dir = Path("/tmp/bashproj")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = [claude, bash]
        sm.get_active_session.return_value = None
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:agent:bash"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    txt = q.edit_message_text.call_args[0][0]
    assert "bash99" in txt  # bash session shown
    assert "claude1" not in txt  # claude session absent
    kwargs = q.edit_message_text.call_args[1]
    btns = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    switch_btns = [
        b for b in btns if b.callback_data and b.callback_data.startswith("sess:switch:")
    ]
    # Step-2 regression: button label strips `sess_` prefix, callback keeps full id.
    assert switch_btns and all(
        b.callback_data.startswith("sess:switch:sess_") for b in switch_btns
    )
    assert all(not b.text.strip().startswith("sess_") for b in switch_btns)
    assert "bash99" in switch_btns[0].text
    assert "sess_bash99" not in switch_btns[0].text
    assert any(b.callback_data == "sess:back" for b in btns)


@pytest.mark.asyncio
async def test_sess_back_returns_agent_list():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    s1 = _mock_session("sess_a", "Claude", True, active=False, agent_key="claude")
    s2 = _mock_session("sess_b", "Bash", True, active=False, agent_key="bash")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.list_user_sessions.return_value = [s1, s2]
        sm.get_active_session.return_value = None
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:back"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    txt = q.edit_message_text.call_args[0][0]
    assert "管理 Session" in txt
    btns = [
        b
        for row in q.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
        for b in row
    ]
    assert any(b.callback_data == "sess:agent:claude" for b in btns)
    assert any(b.callback_data == "sess:agent:bash" for b in btns)


@pytest.mark.asyncio
async def test_sess_prune_offline_agent_scoped():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    bash_off = _mock_session("sess_bash1", "Bash", False, active=False, agent_key="bash")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.prune_offline_sessions.return_value = 1
        sm.list_user_sessions.return_value = [bash_off]
        sm.get_active_session.return_value = None
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:prune_offline:bash"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    txt = q.edit_message_text.call_args[0][0]
    assert "Bash Shell" in txt  # agent-scoped header
    assert "bash1" in txt  # this agent's session still rendered
    btns = [
        b
        for row in q.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
        for b in row
    ]
    assert any(b.callback_data == "sess:restart:sess_bash1" for b in btns)
    assert any(b.callback_data == "sess:prune_offline:bash" for b in btns)
    assert any(b.callback_data == "sess:back" for b in btns)


@pytest.mark.asyncio
async def test_sess_agent_view_includes_external_sessions():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    oc = _mock_session("sess_oc1", "OpenCode", True, active=False, agent_key="opencode")
    oc.working_dir = Path("/tmp/ocproj")
    with (
        patch("agents_on_hand.ui.session_menu.session_manager") as sm,
        patch(
            "agents_on_hand.ui.session_menu._list_external_opencode_sessions",
            new=AsyncMock(return_value=[{"id": "ses_ext1234", "title": "API 重構"}]),
        ) as mock_ext,
    ):
        sm.list_user_sessions.return_value = [oc]
        sm.get_active_session.return_value = None
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:agent:opencode"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    mock_ext.assert_awaited()
    q.edit_message_text.assert_called_once()
    txt = q.edit_message_text.call_args[0][0]
    assert "🟣" in txt
    assert "API 重構" in txt
    assert "🟣 為外部 session" in txt
    btns = [
        b
        for row in q.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
        for b in row
    ]
    attach_btns = [
        b for b in btns if b.callback_data and b.callback_data.startswith("agent:attach_ext:")
    ]
    assert attach_btns
    assert any(b.callback_data.endswith(":opencode:ses_ext1234") for b in attach_btns)
    assert any("🟣" in b.text for b in attach_btns)


@pytest.mark.asyncio
async def test_acp_perm_with_colon_in_req_id():
    from agents_on_hand.handlers.acp_permissions import acp_permission_callback_handler

    mock_session = MagicMock()
    mock_session.respond_permission = AsyncMock()
    with patch("agents_on_hand.handlers.acp_permissions.session_manager") as sm:
        sm.get_session.return_value = mock_session
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "acp_perm:approve:sess_abc12345:tool:call:123"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await acp_permission_callback_handler(update, MagicMock())
        mock_session.respond_permission.assert_called_once_with("tool:call:123", approved=True)


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

    with patch("agents_on_hand.handlers.acp_permissions.session_manager"):
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
    with (
        patch("agents_on_hand.handlers.restart.restart_registry", {}),
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        await session_restart_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    assert "過期" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_restart_path_out_of_bounds():
    """Restart with a valid token but a disallowed working dir must be refused."""
    from pathlib import Path

    from agents_on_hand.handlers.restart import session_restart_callback_handler

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
    with (
        patch("agents_on_hand.handlers.restart.restart_registry", registry),
        patch("agents_on_hand.handlers.restart.is_path_allowed", return_value=False),
        patch("agents_on_hand.handlers.restart.session_manager") as sm,
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        await session_restart_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    assert "無法重新啟動" in q.edit_message_text.call_args[0][0]
    sm.create_session.assert_not_called()
