"""
Comprehensive tests for Telegram-Friendly Response Formatting:
1. Markdown to Telegram HTML converter
2. Expandable blockquotes (<blockquote expandable>) for Thinking & Tool logs
3. Tag-Stack Aware Splitter (no code block breakages across >3800 char limits)
4. Streaming HTML Tag Auto-balancer
5. Incremental ANSI stream cleaner
6. UnifiedStreamer HTML delivery, turn metadata footer & retry action buttons
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

from agents_on_hand.ansi_cleaner import (
    IncrementalAnsiCleaner,
    balance_telegram_html_tags,
    escape_html,
    format_hermes_html,
    markdown_to_telegram_html,
    split_html_into_chunks,
)
from agents_on_hand.drivers.base_driver import DriverEvent
from agents_on_hand.stream_handler import UnifiedStreamer


def test_escape_html():
    assert escape_html("x < 5 & y > 10") == "x &lt; 5 &amp; y &gt; 10"
    assert escape_html("") == ""
    assert escape_html(None) == ""


def test_markdown_to_telegram_html_inline():
    # Bold, italic, strike, code, links
    md = "**bold** and *italic* and `code` and ~~strike~~ and [Doc](https://example.com)"
    res = markdown_to_telegram_html(md)
    assert "<b>bold</b>" in res
    assert "<i>italic</i>" in res
    assert "<code>code</code>" in res
    assert "<s>strike</s>" in res
    assert '<a href="https://example.com">Doc</a>' in res


def test_markdown_to_telegram_html_headers_and_quotes():
    md = "# Main Title\n## Section 2\n### Sub 3\n> Important quote\n- item 1\n- item 2"
    res = markdown_to_telegram_html(md)
    assert "📌 <b>Main Title</b>" in res
    assert "🔹 <b>Section 2</b>" in res
    assert "▪️ <b>Sub 3</b>" in res
    assert "<blockquote>Important quote</blockquote>" in res
    assert "• item 1" in res
    assert "• item 2" in res


def test_convert_table_to_bullets_2_columns():
    table_md = (
        "| 層 | 實作 |\n|---|---|\n| 語言/環境 | Python 3.13 |\n| 逐字稿 | yt-dlp + Whisper |\n"
    )
    res = markdown_to_telegram_html(table_md)
    assert "• <b>語言/環境</b>: Python 3.13" in res
    assert "• <b>逐字稿</b>: yt-dlp + Whisper" in res
    assert "|" not in res


def test_convert_table_to_bullets_multi_columns():
    table_md = (
        "| 幣種 | 價格 | 漲跌 |\n"
        "|---|---|---|\n"
        "| BTC | $65,000 | +3.5% |\n"
        "| ETH | $3,500 | -1.2% |\n"
    )
    res = markdown_to_telegram_html(table_md)
    assert "<b>BTC</b>" in res
    assert "• 價格: $65,000" in res
    assert "• 漲跌: +3.5%" in res


def test_markdown_to_telegram_html_code_blocks():
    md = "```python\ndef hello():\n    return 'world'\n```"
    res = markdown_to_telegram_html(md)
    assert (
        "<pre><code class=\"language-python\">def hello():\n    return 'world'</code></pre>" in res
    )

    # Unclosed code block at stream end
    unclosed = "```python\ndef in_progress():"
    res_unclosed = markdown_to_telegram_html(unclosed)
    assert '<pre><code class="language-python">def in_progress():</code></pre>' in res_unclosed

    # Tables inside code fences must NOT be converted
    table_in_code = "```markdown\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
    res_code_table = markdown_to_telegram_html(table_in_code)
    assert "| A | B |" in res_code_table


def test_balance_telegram_html_tags():
    assert (
        balance_telegram_html_tags("<blockquote expandable><b>💭 思考中")
        == "<blockquote expandable><b>💭 思考中</b></blockquote>"
    )
    assert (
        balance_telegram_html_tags('<pre><code class="language-py">print(1)')
        == '<pre><code class="language-py">print(1)</code></pre>'
    )
    assert balance_telegram_html_tags("Normal text without tags") == "Normal text without tags"
    # Incomplete trailing tag
    assert balance_telegram_html_tags("Hello <pre") == "Hello"
    assert balance_telegram_html_tags("Hello &am") == "Hello"


def test_split_html_into_chunks_preserves_tags():
    # Create an HTML text with a long code block inside that exceeds 3800 chars
    long_code = "print('hello')\n" * 300
    html_input = f'<blockquote expandable><b>Header</b>\n<pre><code class="language-python">{long_code}</code></pre></blockquote>'

    chunks = split_html_into_chunks(html_input, max_chars=1000)
    assert len(chunks) > 1

    # Check each chunk is valid balanced HTML
    for _i, chunk in enumerate(chunks):
        assert chunk.startswith("<") or "print" in chunk
        # If chunk opens <pre>, it must close </pre>
        if "<pre" in chunk:
            assert "</pre>" in chunk
        if "<blockquote" in chunk:
            assert "</blockquote>" in chunk


def test_format_hermes_html():
    res = format_hermes_html(
        text="Here is the solution:\n```python\nx = 10\n```",
        thought="Let me think carefully about this problem.",
        tool_results=[{"tool_name": "bash", "preview": "pytest -q", "content": "5 passed in 0.2s"}],
        is_final=True,
        duration=2.45,
        agent_name="Claude Code",
        tool_count=1,
    )
    assert "<blockquote expandable><b>💭 思考過程</b>" in res
    assert "Let me think carefully about this problem." in res
    assert "<blockquote expandable>🛠️ <b>Tool (bash)</b>" in res
    assert "pytest -q" in res
    assert "<pre><code>5 passed in 0.2s</code></pre>" in res
    assert "⚡ <code>2.5s</code>" in res
    assert "🤖 <b>Claude Code</b>" in res
    assert "🛠️ <b>1 工具</b>" in res


def test_incremental_ansi_cleaner():
    cleaner = IncrementalAnsiCleaner()
    # Feed first part of an escape sequence
    c1 = cleaner.feed("Hello \x1b[3")
    assert c1 == "Hello "
    # Feed remaining part of escape sequence
    c2 = cleaner.feed("1mRed World\x1b[0m")
    assert c2 == "Red World"
    # Flush
    assert cleaner.flush() == ""


@pytest.mark.asyncio
async def test_unified_streamer_html_and_final_actions():
    bot = MagicMock()
    msg = MagicMock(message_id=999)
    bot.send_message = AsyncMock(return_value=msg)
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()

    sess = MagicMock()
    sess.session_id = "test_stream_sess"
    sess.agent_name = "OMP Agent"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()

    streamer = UnifiedStreamer(bot=bot, chat_id=123, session=sess, edit_interval=0.01)
    streamer.start()

    # Emit thought, text, tool result, and turn end
    streamer._on_driver_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content="Analyzing input..."))
    streamer._on_driver_event(DriverEvent(DriverEvent.TOOL_RESULT, tool_name="bash", content="OK"))
    streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="All tasks completed!"))
    streamer._on_driver_event(DriverEvent(DriverEvent.TURN_END))

    await streamer._schedule_edit()
    await asyncio.sleep(0.05)

    streamer.stop()

    # Check bot was called with parse_mode='HTML'
    assert bot.send_message.called or bot.edit_message_text.called
    call_args = (
        bot.send_message.call_args_list[0]
        if bot.send_message.called
        else bot.edit_message_text.call_args_list[0]
    )
    assert call_args.kwargs.get("parse_mode") == "HTML"
    text_sent = call_args.kwargs.get("text", "")
    assert "All tasks completed!" in text_sent
    assert "<blockquote expandable>" in text_sent
    # Final reply_markup should contain retry and stop buttons
    markup = call_args.kwargs.get("reply_markup")
    assert markup is not None
    button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "🔄 重試此輪" in button_texts
    assert "🛑 結束 Session" in button_texts


@pytest.mark.asyncio
async def test_unified_streamer_html_badrequest_plain_fallback():
    bot = MagicMock()
    ok_msg = MagicMock(message_id=100)
    # Reject HTML parse mode and accept plain text
    bot.send_message = AsyncMock(side_effect=[BadRequest("Can't parse entities"), ok_msg])
    sess = MagicMock(session_id="sess_err", agent_name="Bash")
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()

    streamer = UnifiedStreamer(bot=bot, chat_id=123, session=sess, edit_interval=0.01)
    streamer.start()

    streamer._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content="Test plain retry"))
    await streamer._schedule_edit()
    await asyncio.sleep(0.05)
    streamer.stop()

    assert bot.send_message.call_count == 2
    fallback_call = bot.send_message.call_args_list[1]
    assert fallback_call.kwargs.get("parse_mode") is None
    assert "Test plain retry" in fallback_call.kwargs.get("text")


@pytest.mark.asyncio
async def test_session_action_retry_callback():
    from agents_on_hand.session_manager import session_manager
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    # Mock session with last_user_prompt
    mock_session = MagicMock()
    mock_session.session_id = "sess_retry_test"
    mock_session.agent_name = "Claude"
    mock_session.is_running = True
    mock_session.last_user_prompt = "git status"

    query = MagicMock()
    query.answer = AsyncMock()
    query.from_user = MagicMock(id=42)
    query.message = MagicMock(chat_id=123)
    query.message.reply_text = AsyncMock()
    query.data = "sess:retry:sess_retry_test"

    update = MagicMock(callback_query=query)
    context = MagicMock()
    context.bot = MagicMock()

    with (
        patch.object(session_manager, "get_session", return_value=mock_session),
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        await session_action_callback_handler(update, context)

    mock_session.send_input.assert_called_with("git status")
    query.message.reply_text.assert_called_once()
    assert "git status" in query.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_prime_agent_configuration_and_probing(tmp_path):
    from agents_on_hand.config import AVAILABLE_CLI_AGENTS, get_installed_cli_agents
    from agents_on_hand.session_manager import SessionManager

    assert "prime" in AVAILABLE_CLI_AGENTS
    prime_info = AVAILABLE_CLI_AGENTS["prime"]
    assert prime_info["name"] == "Prime Agent"
    assert "prime-agent" in prime_info["command"]
    assert prime_info["drivers"] == ["acp", "pi_rpc", "pty"]
    assert prime_info["use_acp"] is True

    # Test discovery
    with patch(
        "shutil.which", side_effect=lambda x: "/usr/bin/prime-agent" if x == "prime-agent" else None
    ):
        installed = get_installed_cli_agents(use_cache=False)
        assert "prime" in installed

    # Test discovery when binary is named prime
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/prime" if x == "prime" else None):
        installed = get_installed_cli_agents(use_cache=False)
        assert "prime" in installed
        assert "prime" in installed["prime"]["command"]

    # Test session creation with prime agent
    with patch("agents_on_hand.session_manager.AgentSession.start", new_callable=AsyncMock):
        mgr = SessionManager(store_path=tmp_path / "prime_state.json")
        sess = mgr.create_session(user_id=10, agent_key="prime", working_dir=tmp_path)
        assert sess.agent_name == "Prime Agent"
        assert sess.agent_key == "prime"
        assert "prime" in sess.command
        sess.stop()


def test_anti_zombie_process_utils_and_shutdown(tmp_path):
    from agents_on_hand.process_utils import is_process_alive, kill_process_tree
    from agents_on_hand.session_manager import SessionManager

    # Test is_process_alive
    alive_mock = MagicMock()
    alive_mock.returncode = None
    alive_mock.poll.return_value = None
    assert is_process_alive(alive_mock) is True

    dead_mock = MagicMock()
    dead_mock.returncode = 0
    assert is_process_alive(dead_mock) is False

    # Test kill_process_tree with active mock process
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.returncode = None
    mock_proc.poll.return_value = None
    kill_process_tree(mock_proc)
    assert mock_proc.terminate.called or mock_proc.kill.called

    # Test kill_process_tree with dead process (should be ignored to prevent PID reuse issues)
    dead_proc = MagicMock()
    dead_proc.pid = 99998
    dead_proc.returncode = 0
    kill_process_tree(dead_proc)
    dead_proc.terminate.assert_not_called()

    # Test shutdown_all_sessions
    mgr = SessionManager(store_path=tmp_path / "shutdown_state.json")
    s1 = MagicMock()
    s1.is_running = True
    s2 = MagicMock()
    s2.is_running = False
    mgr.sessions["s1"] = s1
    mgr.sessions["s2"] = s2

    cleaned = mgr.shutdown_all_sessions()
    assert cleaned == 1
    s1.stop.assert_called_once()
    assert not s2.stop.called
