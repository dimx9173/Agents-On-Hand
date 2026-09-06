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
    """Dir-agent instance picker: one live instance → reuse row + new + back."""
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
        sm.find_dir_agent_sessions.return_value = [live]
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    q.edit_message_text.assert_called_once()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"agent:reuse:{live.session_id}" in callbacks
    assert any(c.startswith("agent:force_new:") for c in callbacks)
    assert any(c.startswith("agent:back:") for c in callbacks)


@pytest.mark.asyncio
async def test_start_lists_multiple_instances_newest_first():
    """Two live instances → both listed, plus 🆕 and back rows."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    old = _mock_running_session("sess_old", created=100.0)
    new = _mock_running_session("sess_new", created=200.0)
    update, ctx, q = _make_start_update()
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.find_dir_agent_sessions.return_value = [new, old]
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "agent:reuse:sess_new" in callbacks
    assert "agent:reuse:sess_old" in callbacks
    assert any(c.startswith("agent:force_new:") for c in callbacks)


@pytest.mark.asyncio
async def test_back_returns_to_agent_picker():
    from agents_on_hand.ui.directory_browser import agent_back_callback_handler

    q = MagicMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.data = "agent:back:tok123"
    q.from_user = MagicMock(id=1)
    update = MagicMock()
    update.callback_query = q
    ctx = MagicMock()
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.get_installed_cli_agents",
            return_value={"bash": {"name": "Bash", "use_acp": False}},
        ),
    ):
        await agent_back_callback_handler(update, ctx)
    assert "選 Agent" in q.edit_message_text.call_args[0][0]


def test_find_dir_agent_sessions_lists_newest_first(tmp_path):
    from agents_on_hand.session_manager import SessionManager

    mgr = SessionManager(store_path=tmp_path / "s.json")
    old = _mock_running_session("sess_old", created=100.0)
    new = _mock_running_session("sess_new", created=200.0)
    dead = _mock_running_session("sess_dead", created=300.0)
    dead.is_running = False
    for s in (old, new, dead):
        mgr.sessions[s.session_id] = s
    got = mgr.find_dir_agent_sessions(1, "bash", Path("/tmp/proj"))
    assert [s.session_id for s in got] == ["sess_new", "sess_old"]
    got_all = mgr.find_dir_agent_sessions(1, "bash", Path("/tmp/proj"), running_only=False)
    assert [s.session_id for s in got_all] == ["sess_dead", "sess_new", "sess_old"]


def test_agent_picker_badges_show_running_counts(tmp_path):
    from agents_on_hand.ui.directory_browser import _build_agent_picker_keyboard

    live = _mock_running_session("sess_live", agent_key="prime", agent_name="Prime Agent")
    with (
        patch(
            "agents_on_hand.ui.directory_browser.get_installed_cli_agents",
            return_value={"prime": {"name": "Prime Agent", "use_acp": True}},
        ),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.list_user_sessions.return_value = [live]
        # working_dir matches the mock's /tmp/proj after resolve
        from pathlib import Path as _P

        with (
            patch("pathlib.Path.expanduser", lambda self: self),
            patch("pathlib.Path.resolve", lambda self: _P("/tmp/proj")),
        ):
            markup = _build_agent_picker_keyboard(_P("/tmp/proj"), user_id=1)
    btns = [b for row in markup.inline_keyboard for b in row]
    assert any("1運作中" in b.text for b in btns)


@pytest.mark.asyncio
async def test_start_lists_picker_when_no_reuse_candidate(tmp_path):
    """Zero instances → still show the instance picker with a 🆕 button (no fast path)."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update()
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
    ):
        sm.find_dir_agent_sessions.return_value = []
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    q.edit_message_text.assert_called_once()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(c.startswith("agent:force_new:") for c in callbacks)
    assert any(c.startswith("agent:back:") for c in callbacks)
    assert "尚無運作中" in q.edit_message_text.call_args[0][0]


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


def test_filter_prime_sessions_by_cwd(tmp_path):
    from agents_on_hand.ui.directory_browser import _filter_prime_sessions

    target = tmp_path / "proj"
    other = tmp_path / "other"
    raw = [
        {
            "id": "a1",
            "lifecycle": "live",
            "runtimeKind": "top-level",
            "cwd": str(target),
            "modified": "2026-09-05T10:00:00Z",
            "activity": "working",
            "firstMessage": "hi",
        },
        {
            "id": "b2",
            "lifecycle": "live",
            "runtimeKind": "subagent",
            "cwd": str(target),
            "modified": "2026-09-05T11:00:00Z",
        },
        {
            "id": "c3",
            "lifecycle": "saved",
            "runtimeKind": "top-level",
            "cwd": str(target),
            "modified": "2026-09-05T12:00:00Z",
        },
        {
            "id": "d4",
            "lifecycle": "live",
            "runtimeKind": "top-level",
            "cwd": str(other),
            "modified": "2026-09-05T13:00:00Z",
        },
        {
            "id": "e5",
            "lifecycle": "live",
            "runtimeKind": "top-level",
            "cwd": str(target),
            "modified": "2026-09-05T14:00:00Z",
        },
        "junk",
        None,
    ]
    got = _filter_prime_sessions(raw, target)
    assert [s["id"] for s in got] == ["e5", "a1"]
    assert _filter_prime_sessions(None, target) == []
    assert _filter_prime_sessions(raw, other)[0]["id"] == "d4"


@pytest.mark.asyncio
async def test_start_lists_external_prime_sessions():
    """prime picker merges daemon externals + 🆕 + back rows."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    live = _mock_running_session("sess_live", agent_key="prime", agent_name="Prime Agent")
    update, ctx, q = _make_start_update("agent:start:tok:prime")
    externals = [
        {
            "id": "39e9e50451eb",
            "lifecycle": "live",
            "runtimeKind": "top-level",
            "cwd": "/tmp/proj",
            "activity": "working",
            "firstMessage": "commit and push",
        },
    ]
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._list_external_prime_sessions",
            return_value=externals,
        ) as mock_ext,
    ):
        sm.find_dir_agent_sessions.return_value = [live]
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        mock_ext.assert_called_once()
        sm.create_session.assert_not_called()
    assert "共 2 個可沿用" in q.edit_message_text.call_args[0][0]
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"agent:reuse:{live.session_id}" in callbacks
    assert any(c.startswith("agent:attach_ext:") and ":prime:39e9e50451eb" in c for c in callbacks)
    assert any(c.startswith("agent:force_new:") for c in callbacks)


@pytest.mark.asyncio
async def test_start_no_external_lookup_for_non_prime():
    """Non-prime agents skip the daemon query entirely."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:start:tok:bash")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._list_external_prime_sessions",
        ) as mock_ext,
    ):
        sm.find_dir_agent_sessions.return_value = []
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        mock_ext.assert_not_called()


@pytest.mark.asyncio
async def test_attach_ext_launches_session_in_same_cwd():
    """Tapping an external row starts an AOH prime session (daemon auto-attaches)."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:attach_ext:tok:prime:39e9e50451eb")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._find_external_prime",
            return_value={
                "id": "39e9e50451eb",
                "lifecycle": "live",
                "firstMessage": "commit and push",
            },
        ),
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        mock_sess = _mock_running_session("sess_fresh", agent_key="prime")
        sm.create_session.return_value = mock_sess
        mk.return_value = MagicMock(start=MagicMock())
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_called_once_with(
            user_id=1, agent_key="prime", working_dir=Path("/tmp/proj")
        )
    assert "已啟動" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_attach_ext_gone_shows_hint():
    """External ended between list and tap → hint, no session created."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:attach_ext:tok:prime:deadbeef")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser._find_external_prime", return_value=None),
    ):
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    assert "已結束" in q.edit_message_text.call_args[0][0]


def test_filter_opencode_sessions_by_directory(tmp_path):
    from agents_on_hand.ui.directory_browser import _filter_opencode_sessions

    target = tmp_path / "proj"
    raw = [
        {"id": "ses_aaa", "title": "fix bug", "updated": 100, "directory": str(target)},
        {"id": "ses_bbb", "title": "New session - x", "updated": 200, "directory": str(target)},
        {"id": "ses_ccc", "title": "other", "updated": 300, "directory": str(tmp_path / "other")},
        {"id": "", "title": "noid", "updated": 400, "directory": str(target)},
        "junk",
        None,
    ]
    got = _filter_opencode_sessions(raw, target)
    assert [s["id"] for s in got] == ["ses_bbb", "ses_aaa"]
    assert _filter_opencode_sessions(None, target) == []


@pytest.mark.asyncio
async def test_start_lists_external_opencode_sessions():
    """opencode picker merges saved externals + 🆕 + back rows."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:start:tok:opencode")
    externals = [
        {"id": "ses_aaa111", "title": "fix bug", "updated": 100, "directory": "/tmp/proj"},
    ]
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._list_external_opencode_sessions",
            return_value=externals,
        ) as mock_ext,
        patch(
            "agents_on_hand.ui.directory_browser._list_external_prime_sessions",
        ) as mock_prime,
    ):
        sm.find_dir_agent_sessions.return_value = []
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        mock_ext.assert_called_once()
        mock_prime.assert_not_called()
        sm.create_session.assert_not_called()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(c.startswith("agent:attach_ext:") and ":opencode:ses_aaa111" in c for c in callbacks)
    assert any(c.startswith("agent:force_new:") for c in callbacks)


@pytest.mark.asyncio
async def test_attach_ext_opencode_resumes_session():
    """Tapping an external opencode row resumes it via ACP session/load."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:attach_ext:tok:opencode:ses_aaa111")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._find_external_opencode",
            return_value={"id": "ses_aaa111", "title": "fix bug", "directory": "/tmp/proj"},
        ),
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        mock_sess = _mock_running_session("sess_fresh", agent_key="opencode")
        sm.create_session.return_value = mock_sess
        mk.return_value = MagicMock(start=MagicMock())
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_called_once_with(
            user_id=1,
            agent_key="opencode",
            working_dir=Path("/tmp/proj"),
            resume_acp_session_id="ses_aaa111",
        )
    assert "接回" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_attach_ext_opencode_gone_shows_hint():
    """External opencode session vanished → hint, no session created."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:attach_ext:tok:opencode:ses_gone")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser._find_external_opencode", return_value=None),
    ):
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_not_called()
    assert "不存在" in q.edit_message_text.call_args[0][0]


def test_scan_omp_sessions_by_cwd(tmp_path, monkeypatch):
    import json as _json

    from agents_on_hand.ui import directory_browser as db

    root = tmp_path / "sessions"
    d1 = root / "-project-proj"
    d1.mkdir(parents=True)
    target = tmp_path / "proj"
    (d1 / "2026-09-01T00-00-00-000Z_aaa111.jsonl").write_text(
        _json.dumps({"type": "title", "title": "fix bug"})
        + "\n"
        + _json.dumps(
            {
                "type": "session",
                "id": "aaa111",
                "cwd": str(target),
                "timestamp": "2026-09-01T00:00:00.000Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (d1 / "2026-09-02T00-00-00-000Z_bbb222.jsonl").write_text(
        _json.dumps(
            {
                "type": "session",
                "id": "bbb222",
                "cwd": str(tmp_path / "other"),
                "timestamp": "2026-09-02T00:00:00.000Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AOH_OMP_SESSIONS_ROOT", str(root))
    got = db._scan_omp_sessions(target)
    assert [s["id"] for s in got] == ["aaa111"]
    assert got[0]["title"] == "fix bug"
    assert db._scan_omp_sessions(tmp_path / "other")[0]["id"] == "bbb222"


@pytest.mark.asyncio
async def test_start_lists_external_omp_sessions(tmp_path, monkeypatch):
    """omp picker merges saved externals + 🆕 + back rows."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:start:tok:omp")
    externals = [
        {
            "id": "aaa111",
            "title": "fix bug",
            "cwd": "/tmp/proj",
            "timestamp": "2026-09-01T00:00:00.000Z",
        },
    ]
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._list_external_omp_sessions",
            return_value=externals,
        ) as mock_ext,
    ):
        sm.find_dir_agent_sessions.return_value = []
        sm.get_active_session.return_value = None
        await agent_start_callback_handler(update, ctx)
        mock_ext.assert_called_once()
        sm.create_session.assert_not_called()
    markup = q.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(c.startswith("agent:attach_ext:") and ":omp:aaa111" in c for c in callbacks)
    assert any(c.startswith("agent:force_new:") for c in callbacks)


@pytest.mark.asyncio
async def test_attach_ext_omp_resumes_session():
    """Tapping an external omp row resumes it via ACP session/load."""
    from agents_on_hand.ui.directory_browser import agent_start_callback_handler

    update, ctx, q = _make_start_update("agent:attach_ext:tok:omp:aaa111")
    with (
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch(
            "agents_on_hand.ui.directory_browser.resolve_path_token", return_value=Path("/tmp/proj")
        ),
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch(
            "agents_on_hand.ui.directory_browser._find_external_omp",
            return_value={"id": "aaa111", "title": "fix bug", "cwd": "/tmp/proj"},
        ),
        patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mk,
        patch("agents_on_hand.ui.directory_browser.active_streamers", {}),
    ):
        mock_sess = _mock_running_session("sess_fresh", agent_key="omp")
        sm.create_session.return_value = mock_sess
        mk.return_value = MagicMock(start=MagicMock())
        await agent_start_callback_handler(update, ctx)
        sm.create_session.assert_called_once_with(
            user_id=1,
            agent_key="omp",
            working_dir=Path("/tmp/proj"),
            resume_acp_session_id="aaa111",
        )
    assert "接回" in q.edit_message_text.call_args[0][0]
