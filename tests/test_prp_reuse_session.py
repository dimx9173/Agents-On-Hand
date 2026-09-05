"""PRP reuse: attach to a running agent in the same directory instead of spawning an orphan."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_running_session(
    sid="sess_live",
    agent_key="bash",
    agent_name="Bash",
    user_id=1,
    workdir="/tmp/proj",
    created=100.0,
):
    from pathlib import Path

    s = MagicMock()
    s.session_id = sid
    s.user_id = user_id
    s.agent_key = agent_key
    s.agent_name = agent_name
    s.working_dir = Path(workdir)
    s.is_running = True
    s.created_at = created
    return s


def test_find_running_session_hit(tmp_path):
    from agents_on_hand.session_manager import SessionManager

    mgr = SessionManager(store_path=tmp_path / "s.json")
    live = _mock_running_session("sess_1", created=100.0)
    mgr.sessions["sess_1"] = live
    found = mgr.find_running_session(1, "bash", tmp_path / "x" if False else Path("/tmp/proj"))
    assert found is live


def test_find_running_session_miss_cases(tmp_path):
    from pathlib import Path

    from agents_on_hand.session_manager import SessionManager

    mgr = SessionManager(store_path=tmp_path / "s.json")
    live = _mock_running_session("sess_1")
    dead = _mock_running_session("sess_2")
    dead.is_running = False
    other_dir = _mock_running_session("sess_3", workdir="/tmp/other")
    other_agent = _mock_running_session("sess_4", agent_key="claude", agent_name="Claude")
    other_user = _mock_running_session("sess_5", user_id=9)
    for s in (live, dead, other_dir, other_agent, other_user):
        mgr.sessions[s.session_id] = s
    assert mgr.find_running_session(1, "bash", Path("/tmp/proj")) is live
    assert mgr.find_running_session(1, "bash", Path("/tmp/nothing")) is None
    assert mgr.find_running_session(1, "claude", Path("/tmp/proj")) is other_agent
    assert mgr.find_running_session(9, "bash", Path("/tmp/proj")) is other_user
    assert mgr.find_running_session(2, "bash", Path("/tmp/proj")) is None
    # only-dead dir → None
    mgr2 = SessionManager(store_path=tmp_path / "s2.json")
    mgr2.sessions["sess_2"] = dead
    assert mgr2.find_running_session(1, "bash", Path("/tmp/proj")) is None


def test_find_running_session_picks_newest(tmp_path):
    from pathlib import Path

    from agents_on_hand.session_manager import SessionManager

    mgr = SessionManager(store_path=tmp_path / "s.json")
    old = _mock_running_session("sess_old", created=100.0)
    new = _mock_running_session("sess_new", created=200.0)
    mgr.sessions["sess_old"] = old
    mgr.sessions["sess_new"] = new
    assert mgr.find_running_session(1, "bash", Path("/tmp/proj")) is new


def _make_start_update(agent_data="agent:start:tok:bash", user_id=1):
    q = MagicMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.data = agent_data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat_id = 123
    update = MagicMock()
    update.callback_query = q
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx, q


@pytest.mark.asyncio
async def test_start_offers_reuse_when_running(tmp_path):
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    live = _mock_running_session("sess_live")
    update, ctx, q = _make_start_update()
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.find_running_session.return_value = live
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    q.edit_message_text.assert_called_once()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"agent:reuse:{live.session_id}" in callbacks
    assert any(c.startswith("agent:force_new:") for c in callbacks)


@pytest.mark.asyncio
async def test_start_creates_when_no_reuse_candidate(tmp_path):
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update()
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        sm.find_running_session.return_value = None
        mock_sess = _mock_running_session("sess_fresh")
        sm.create_session.return_value = mock_sess
        mk.return_value = MagicMock(start=MagicMock())
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_called_once()
    assert "已啟動" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_force_new_skips_reuse_check(tmp_path):
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:force_new:tok:bash")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        mock_sess = _mock_running_session("sess_fresh2")
        sm.create_session.return_value = mock_sess
        mk.return_value = MagicMock(start=MagicMock())
        await agent_start_callback_handler(update, ctx)
        sm.find_running_session.assert_not_called()
        sm.create_session.assert_called_once()


@pytest.mark.asyncio
async def test_reuse_attaches_and_streams(tmp_path):
    from agents_on_hand.ui.directory_browser import agent_reuse_callback_handler

    live = _mock_running_session("sess_live")
    live.get_last_n_lines = MagicMock(return_value="old output")
    update, ctx, q = _make_start_update("agent:reuse:sess_live")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        sm.get_session.return_value = live
        mk.return_value = MagicMock(start=MagicMock())
        await agent_reuse_callback_handler(update, ctx)
        sm.set_active_session.assert_called_once_with(1, "sess_live")
        mk.assert_called_once()
    assert "沿用" in q.edit_message_text.call_args[0][0]
    ctx.bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_reuse_fallback_when_session_gone():
    from agents_on_hand.ui.directory_browser import agent_reuse_callback_handler

    update, ctx, q = _make_start_update("agent:reuse:sess_gone")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.get_session.return_value = None
        await agent_reuse_callback_handler(update, ctx)
        sm.set_active_session.assert_not_called()
    assert "不存在或離線" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_reuse_rejects_other_user_session():
    from agents_on_hand.ui.directory_browser import agent_reuse_callback_handler

    foreign = _mock_running_session("sess_x", user_id=9)
    update, ctx, q = _make_start_update("agent:reuse:sess_x")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.get_session.return_value = foreign
        await agent_reuse_callback_handler(update, ctx)
        sm.set_active_session.assert_not_called()
    assert "不存在或離線" in q.edit_message_text.call_args[0][0]
