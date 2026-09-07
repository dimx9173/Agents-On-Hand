"""Tests for two-sided session deletion (aoh side + agent side via commands).

Covers:
  - every session option row carries an attached delete button
  - sess:del_confirm / sess:delete handler flow
  - sess:delete_ext for external sessions
  - agent_session_cleaner command dispatch (opencode CLI / prime gio trash)
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_on_hand.agent_session_cleaner import PurgeResult


def _mock_session(sid="sess_1", agent_name="Bash", running=True, agent_key="bash"):
    s = MagicMock()
    s.session_id = sid
    s.agent_name = agent_name
    s.agent_key = agent_key
    s.working_dir = Path(f"/tmp/{sid}")
    s.is_running = running
    s.get_last_n_lines = MagicMock(return_value="l1\nl2")
    mock_log = MagicMock()
    mock_log.exists.return_value = False
    mock_log.__str__ = lambda _: f"/tmp/{sid}.log"
    s.log_file_path = mock_log
    return s


# ---------------------------------------------------------------------------
# UI: delete button attached to every session option row
# ---------------------------------------------------------------------------

def test_session_rows_have_delete_button():
    from agents_on_hand.ui.session_menu import _build_session_rows

    s = _mock_session("sess_abc", "OpenCode", running=True, agent_key="opencode")
    delete_btns = [
        b
        for row in _build_session_rows(s, active_session=None)
        for b in row
        if b.callback_data == "sess:del_confirm:sess_abc"
    ]
    assert len(delete_btns) == 1
    assert delete_btns[0].text == "🗑️ 刪除"
    # stop button still present alongside the delete button
    kill_btns = [
        b for row in _build_session_rows(s, None) for b in row if b.callback_data.startswith("sess:kill:")
    ]
    assert kill_btns


# ---------------------------------------------------------------------------
# handler: del_confirm asks, delete removes both sides
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_del_confirm_shows_confirmation():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    s = _mock_session("sess_abc", "OpenCode", agent_key="opencode")
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.get_session.return_value = s
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:del_confirm:sess_abc"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    q.edit_message_text.assert_called_once()
    txt = q.edit_message_text.call_args[0][0]
    assert "確定永久刪除" in txt
    btns = [
        b
        for row in q.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
        for b in row
    ]
    assert any(b.callback_data == "sess:delete:sess_abc" for b in btns)
    assert any(b.callback_data == "sess:back" for b in btns)


@pytest.mark.asyncio
async def test_sess_delete_removes_aoh_and_reports_agent_purge():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    purge_ok = PurgeResult(True, "opencode-cli", "deleted via opencode session delete", ["ses_ext"])
    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.delete_session = AsyncMock(return_value=(True, purge_ok))
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = "sess:delete:sess_abc"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    sm.delete_session.assert_awaited_once_with(1, "sess_abc")
    txt = q.edit_message_text.call_args[0][0]
    assert "已刪除 Session" in txt
    assert "opencode-cli" in txt and "✅" in txt


@pytest.mark.asyncio
async def test_sess_delete_missing_session():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    with patch("agents_on_hand.ui.session_menu.session_manager") as sm:
        sm.delete_session = AsyncMock(return_value=(False, PurgeResult(False, "unknown", "nope")))
        q = MagicMock()
        q.answer = AsyncMock()
        q.message = MagicMock()
        q.message.reply_text = AsyncMock()
        q.data = "sess:delete:sess_gone"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    q.message.reply_text.assert_called_once()
    assert "已不存在" in q.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_sess_delete_ext_purges_via_command():
    from agents_on_hand.callback_registry import register_external_info
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    token = register_external_info("ses_ext1234", "opencode", Path("/tmp/proj"))
    purge_ok = PurgeResult(True, "opencode-cli", "deleted", ["ses_ext1234"])
    with patch(
        "agents_on_hand.ui.session_menu.purge_agent_session",
        new=AsyncMock(return_value=purge_ok),
    ) as mock_purge:
        q = MagicMock()
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.data = f"sess:delete_ext:{token}"
        q.from_user = MagicMock(id=1)
        update = MagicMock()
        update.callback_query = q
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await session_action_callback_handler(update, MagicMock())
    mock_purge.assert_awaited_once()
    assert mock_purge.call_args.args[0] == "opencode"
    assert mock_purge.call_args.kwargs["external_id"] == "ses_ext1234"
    assert "🗑️" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_sess_delete_ext_expired_token():
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    q = MagicMock()
    q.answer = AsyncMock()
    q.data = "sess:delete_ext:e_expired000"
    q.from_user = MagicMock(id=1)
    update = MagicMock()
    update.callback_query = q
    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        await session_action_callback_handler(update, MagicMock())
    assert any(c.kwargs.get("show_alert") for c in q.answer.call_args_list)


# ---------------------------------------------------------------------------
# cleaner: command-only dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_opencode_success():
    from agents_on_hand import agent_session_cleaner as cleaner

    run_mock = AsyncMock(return_value=(0, "Session ses_x deleted", ""))
    with patch.object(cleaner.shutil, "which", return_value="/usr/bin/opencode"), patch.object(cleaner, "_run", new=run_mock):
        res = await cleaner.purge_opencode("ses_x")
    assert res.ok and res.method == "opencode-cli"
    assert run_mock.await_args.args[0] == ["/usr/bin/opencode", "session", "delete", "ses_x"]


@pytest.mark.asyncio
async def test_purge_opencode_unknown_id_refused():
    from agents_on_hand import agent_session_cleaner as cleaner

    with patch.object(cleaner.shutil, "which", return_value="/usr/bin/opencode"):
        res = await cleaner.purge_opencode(None)
    assert not res.ok
    assert "no session id" in res.detail


@pytest.mark.asyncio
async def test_purge_prime_via_acp_session_id_trash():
    from agents_on_hand import agent_session_cleaner as cleaner

    fid = "01a04459-11c2-74e0-8b15-fb949519c5c1"
    target = cleaner.PRIME_SESSIONS_DIR / f"{fid}.jsonl"
    with (
        patch.object(cleaner.shutil, "which", return_value="/usr/bin/gio"),
        patch.object(cleaner.Path, "exists", return_value=True),
        patch.object(cleaner, "_prime_artifact_dir", return_value=None),
        patch.object(cleaner, "_run", new=AsyncMock(return_value=(0, "", ""))) as mock_run,
    ):
        res = await cleaner.purge_prime(acp_session_id=fid)
    assert res.ok and res.method == "gio-trash"
    assert mock_run.await_args.args[0] == ["/usr/bin/gio", "trash", str(target)]
    assert Path(str(target)) in (Path(t) for t in res.targets)


@pytest.mark.asyncio
async def test_purge_prime_refuses_without_resolvable_target():
    from agents_on_hand import agent_session_cleaner as cleaner

    with patch.object(cleaner.shutil, "which", return_value="/usr/bin/gio"):
        res = await cleaner.purge_prime()
    assert not res.ok
    assert "no resolvable" in res.detail


@pytest.mark.asyncio
async def test_purge_omp_unsupported():
    from agents_on_hand import agent_session_cleaner as cleaner

    res = await cleaner.purge_agent_session("omp")
    assert not res.ok and res.method == "unsupported"


@pytest.mark.asyncio
async def test_purge_pty_agents_no_agent_store():
    from agents_on_hand import agent_session_cleaner as cleaner

    res = await cleaner.purge_agent_session("bash")
    assert not res.ok and res.method == "unsupported"
