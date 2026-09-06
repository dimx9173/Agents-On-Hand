"""Phase-6 TG operation & UX regression tests (small-screen optimization).

- U1: compact 2-line session rows (primary carries context, kill always last)
- U2: switch history trimmed to 30 lines / 2500 chars
- U3: /aoh_new opens the directory browser first, then the agent picker (+ shared picker builder)
- U4: directory browser shows recent-dirs shortcuts
- U5: tool approval card shows full args with compact buttons
- U6: wait indicator is a live status line (elapsed + tools)
- U7: help is a tappable menu + callback routing
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_session(sid="sess_x", agent_name="Bash", running=True, workdir="/tmp/myproj",
                 agent_key="bash"):
    s = MagicMock()
    s.session_id = sid
    s.agent_name = agent_name
    s.agent_key = agent_key
    s.working_dir = Path(workdir)
    s.is_running = running
    s.get_last_n_lines = MagicMock(return_value="l1\nl2")
    return s


def _撐起_agent_sessions_ui(user_id=1, sessions=None, active=None, agent_key="bash"):
    """Run the Step-2 agent-session view via `sess:agent:` callback; return (text, buttons)."""
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    async def _run():
        with (
            patch("agents_on_hand.ui.session_menu.session_manager") as sm,
            patch("agents_on_hand.security.is_user_allowed", return_value=True),
        ):
            sm.list_user_sessions.return_value = sessions
            sm.get_active_session.return_value = active
            q = MagicMock()
            q.answer = AsyncMock()
            q.edit_message_text = AsyncMock()
            q.data = f"sess:agent:{agent_key}"
            q.from_user = MagicMock(id=user_id)
            update = MagicMock()
            update.callback_query = q
            await session_action_callback_handler(update, MagicMock())
            return q.edit_message_text.call_args

    call = asyncio.run(_run())
    txt = call[0][0]
    markup = call[1].get("reply_markup")
    btns = [b for row in markup.inline_keyboard for b in row] if markup else []
    return txt, btns


def test_u1_compact_rows_two_lines_per_session():
    s1 = _mock_session("sess_a", "Bash", True, agent_key="bash")
    s2 = _mock_session("sess_b", "Bash", False, agent_key="bash")
    txt, btns = _撐起_agent_sessions_ui(sessions=[s1, s2], active=None)
    # Step-2 agent view: one text line per session (short ids only in buttons)
    assert "sess_a" not in txt and "sess_b" not in txt
    assert "Bash Shell" in txt
    # 4 buttons per session (primary + log + stop + delete) + prune + back rows
    assert len(btns) == 4 * 2 + 2
    # Primary buttons carry context; kill always last in secondary row
    assert any(b.text.startswith("▶️ ") for b in btns)
    assert any(b.text.startswith("🔄 ") for b in btns)


def test_u1_active_session_primary_opens_log():
    """Active session's primary button must not be a dead sess:pause (no handler)."""
    s1 = _mock_session("sess_a", "Bash", True, agent_key="bash")
    _txt, btns = _撐起_agent_sessions_ui(sessions=[s1], active=s1)
    assert not any((b.callback_data or "").startswith("sess:pause:") for b in btns), (
        "sess:pause has no handler — must not be emitted"
    )
    active_primary = next(b for b in btns if b.text.startswith("⭐ "))
    assert active_primary.callback_data.startswith("sess:logs:")


def test_u2_switch_history_trimmed():
    from agents_on_hand.ui import session_menu as sm

    src = Path(sm.__file__).read_text(encoding="utf-8")
    assert "get_last_n_lines(n=30)" in src
    assert "max_chars=2500" in src


def test_u3_new_command_goes_to_directory_browser_first():
    from agents_on_hand.ui.directory_browser import new_command

    async def _run():
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.callback_query = None
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with (
            patch("agents_on_hand.security.is_user_allowed", return_value=True),
            patch(
                "agents_on_hand.ui.directory_browser.ALLOWED_ROOT_DIRS",
                [Path("/tmp")],
            ),
            patch(
                "agents_on_hand.ui.directory_browser.is_path_allowed",
                return_value=True,
            ),
        ):
            await new_command(update, MagicMock())
        return update.message.reply_text.call_args

    call = asyncio.run(_run())
    markup = call[1].get("reply_markup")
    btns = [b for row in markup.inline_keyboard for b in row]
    # Directory browser first: must offer ✅ select-dir, must NOT pre-select agents
    assert any((b.callback_data or "").startswith("dir:select:") for b in btns)
    assert not any((b.callback_data or "").startswith("agent:start:") for b in btns)


def test_u3_picker_builder_shared():
    from agents_on_hand.ui.directory_browser import _build_agent_picker_keyboard

    with patch(
        "agents_on_hand.ui.directory_browser.get_installed_cli_agents",
        return_value={"bash": {"name": "Bash Shell", "use_acp": False}},
    ):
        markup = _build_agent_picker_keyboard(Path("/tmp/myproj"))
    btns = [b for row in markup.inline_keyboard for b in row]
    assert any("Bash Shell" in b.text for b in btns)
    assert any(b.text.startswith("📂 ") for b in btns)
    with patch("agents_on_hand.ui.directory_browser.get_installed_cli_agents", return_value={}):
        assert _build_agent_picker_keyboard(Path("/tmp/myproj")) is None


def test_u4_recent_dirs_shortcuts():
    from agents_on_hand.ui.directory_browser import _build_recent_dirs_row

    s1 = _mock_session("sess_1", "Bash", True, "/tmp/alpha")
    s2 = _mock_session("sess_2", "Claude", True, "/tmp/beta")
    update = MagicMock()
    update.callback_query = None
    update.effective_user = MagicMock(id=7)
    with (
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("pathlib.Path.exists", return_value=True),
    ):
        sm.list_user_sessions.return_value = [s1, s2]
        row = _build_recent_dirs_row(update, Path("/tmp/other"))
    assert row is not None and len(row) == 2
    assert all(b.text.startswith("⭐ ") for b in row)
    # Current dir excluded; unknown user → None
    with (
        patch("agents_on_hand.ui.directory_browser.session_manager") as sm,
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
    ):
        sm.list_user_sessions.return_value = [s1]
        row2 = _build_recent_dirs_row(update, Path("/tmp/alpha"))
    assert row2 is None


def test_u5_tool_card_shows_args_and_compact_buttons():
    from agents_on_hand.stream_handler import UnifiedStreamer

    async def _run():
        bot = MagicMock()
        sent = {}

        async def _capture(chat_id=None, text=None, reply_markup=None, parse_mode=None):
            sent["text"] = text
            sent["markup"] = reply_markup
            return MagicMock(message_id=3)

        bot.send_message = _capture
        sess = MagicMock()
        sess.session_id = "sess_t"
        st = UnifiedStreamer(bot=bot, chat_id=1, session=sess)
        st._on_tool_request("r1", "bash", {"cmd": "ls -la /tmp"})
        await asyncio.sleep(0.05)
        return sent

    sent = asyncio.run(_run())
    assert "ls -la /tmp" in sent["text"], "full args must be visible for approval decision"
    btns = [b for row in sent["markup"].inline_keyboard for b in row]
    assert [b.text for b in btns] == ["✅ 執行", "❌ 拒絕"]


def test_u6_wait_indicator_shows_elapsed():
    from agents_on_hand.stream_handler import UnifiedStreamer

    async def _run():
        bot = MagicMock()
        texts = []

        async def _capture(*args, **kwargs):
            texts.append(args[0] if args else kwargs.get("text"))
            return 5

        sess = MagicMock()
        sess.session_id = "sess_w"
        st = UnifiedStreamer(bot=bot, chat_id=1, session=sess)
        st._is_active = True
        st._turn_start_time = 100.0
        with patch.object(st, "_deliver", side_effect=_capture):
            with patch("asyncio.sleep", new_callable=AsyncMock) as slp:
                slp.side_effect = [None, asyncio.CancelledError()]
                try:
                    await st._wait_indicator_loop()
                except asyncio.CancelledError:
                    pass
        return texts

    texts = asyncio.run(_run())
    assert texts and texts[0].startswith("⏳")


def test_u7_help_menu_buttons_and_routing():
    from agents_on_hand.handlers.chat import help_command, help_menu_callback_handler

    async def _help():
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with (
            patch(
                "agents_on_hand.handlers.chat.get_installed_cli_agents",
                return_value={"bash": {"name": "Bash"}},
            ),
            patch("agents_on_hand.handlers.chat.session_manager") as sm,
            patch("agents_on_hand.security.is_user_allowed", return_value=True),
        ):
            sm.get_active_session.return_value = None
            await help_command(update, MagicMock())
        return update.message.reply_text.call_args

    call = asyncio.run(_help())
    txt = call[0][0]
    assert len(txt) < 300, f"help must be compact, got {len(txt)} chars"
    btns = [b for row in call[1]["reply_markup"].inline_keyboard for b in row]
    callbacks = {b.callback_data for b in btns}
    assert callbacks == {"help:goto:new", "help:goto:sessions", "help:ctrl:esc", "help:ctrl:ctrlc"}

    async def _route():
        update = MagicMock()
        update.message = None
        q = MagicMock()
        q.answer = AsyncMock()
        q.data = "help:ctrl:esc"
        q.from_user = MagicMock(id=1)
        q.message = MagicMock()
        q.message.reply_text = AsyncMock()
        update.callback_query = q
        update.effective_user = MagicMock(id=1)
        with (
            patch("agents_on_hand.security.is_user_allowed", return_value=True),
            patch("agents_on_hand.handlers.chat.session_manager") as sm,
        ):
            sm.get_active_session.return_value = None
            await help_menu_callback_handler(update, MagicMock())
        return update, q

    update, q = asyncio.run(_route())
    assert update.message is q.message  # shim applied
    q.message.reply_text.assert_called_once()  # esc with no session replies
