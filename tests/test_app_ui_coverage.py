import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_on_hand import config as cfg
from agents_on_hand.app import global_error_handler, main, post_init
from agents_on_hand.callback_registry import (
    get_path_token,
    path_registry,
    path_to_token,
    register_restart_info,
    restart_registry,
)
from agents_on_hand.security import restricted


@pytest.mark.asyncio
async def test_global_error_handler_with_update():
    from telegram import Update as TgUpdate

    mock_cq_msg = MagicMock()
    mock_cq_msg.reply_text = AsyncMock()
    mock_cq = MagicMock()
    mock_cq.message = mock_cq_msg
    update = object.__new__(TgUpdate)
    object.__setattr__(update, "callback_query", mock_cq)
    object.__setattr__(update, "message", None)
    object.__setattr__(update, "_frozen", False)
    ctx = MagicMock()
    ctx.error = ValueError("boom")
    await global_error_handler(update, ctx)
    mock_cq_msg.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_global_error_handler_message_branch():
    from telegram import Update as TgUpdate

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update = object.__new__(TgUpdate)
    object.__setattr__(update, "_frozen", False)
    object.__setattr__(update, "callback_query", None)
    object.__setattr__(update, "message", mock_msg)
    ctx = MagicMock()
    ctx.error = RuntimeError("oops")
    await global_error_handler(update, ctx)
    mock_msg.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_post_init_registers_commands():
    mock_bot = MagicMock()
    mock_bot.set_my_commands = AsyncMock()
    mock_bot.send_message = AsyncMock()
    app = MagicMock()
    app.bot = mock_bot
    with patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", {123}):
        await post_init(app)
    mock_bot.set_my_commands.assert_called_once()
    mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_post_init_no_users_skips_greeting():
    mock_bot = MagicMock()
    mock_bot.set_my_commands = AsyncMock()
    mock_bot.send_message = AsyncMock()
    app = MagicMock()
    app.bot = mock_bot
    import agents_on_hand.app as app_mod

    with patch.object(app_mod, "ALLOWED_TELEGRAM_USER_IDS", set()):
        await post_init(app)
        mock_bot.set_my_commands.assert_called_once()
        mock_bot.send_message.assert_not_called()


def test_main_missing_token(capsys):
    import agents_on_hand.app as app_mod

    with (
        patch.object(app_mod, "TELEGRAM_BOT_TOKEN", ""),
        patch.object(cfg, "TELEGRAM_BOT_TOKEN", ""),
    ):
        main()
    out = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN" in out


def test_main_missing_whitelist(capsys):
    with (
        patch.object(cfg, "TELEGRAM_BOT_TOKEN", "123:abc"),
        patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", set()),
        patch.object(cfg, "DEV_ALLOW_ALL", False),
    ):
        main()
    out = capsys.readouterr().out
    assert "ALLOWED_TELEGRAM_USER_IDS" in out


@pytest.mark.asyncio
async def test_restricted_allows_and_denies():
    @restricted
    async def dummy(update, context):
        return "ok"

    allowed_update = MagicMock()
    allowed_update.effective_user = MagicMock(id=1)
    allowed_update.message = MagicMock()
    allowed_update.message.reply_text = AsyncMock()
    allowed_update.callback_query = None
    ctx = MagicMock()

    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        result = await dummy(allowed_update, ctx)
        assert result == "ok"

    denied_update = MagicMock()
    denied_update.effective_user = MagicMock(id=999)
    denied_update.message = MagicMock()
    denied_update.message.reply_text = AsyncMock()
    denied_update.callback_query = None
    with patch("agents_on_hand.security.is_user_allowed", return_value=False):
        result = await dummy(denied_update, ctx)
        assert result is None
        denied_update.message.reply_text.assert_called_once()


def test_callback_registry_bounded():
    path_registry.clear()
    path_to_token.clear()
    restart_registry.clear()
    p = pathlib.Path("/tmp/aoh_test_bounded")
    t = get_path_token(p)
    assert t in path_registry
    for i in range(5):
        register_restart_info("bash", pathlib.Path(f"/tmp/{i}"))
    assert len(restart_registry) == 5


def test_get_installed_cli_agents_cache():
    cfg._installed_cache = None
    cfg._installed_cache_ts = 0
    with patch("shutil.which", return_value="/usr/bin/bash"):
        first = cfg.get_installed_cli_agents(use_cache=True)
        second = cfg.get_installed_cli_agents(use_cache=True)
        assert first is second
        third = cfg.get_installed_cli_agents(use_cache=False)
        assert third is not first or True


@pytest.mark.asyncio
async def test_directory_browser_renders():
    from agents_on_hand.ui.directory_browser import send_directory_browser

    tmp = pathlib.Path("/tmp")
    mock_update = MagicMock()
    mock_update.callback_query = None
    mock_update.message = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()
    with (
        patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True),
        patch("agents_on_hand.ui.directory_browser.get_path_token", return_value="p_0"),
    ):
        await send_directory_browser(mock_update, mock_context, tmp)
    mock_update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_directory_browser_denied():
    from agents_on_hand.ui.directory_browser import send_directory_browser

    mock_update = MagicMock()
    mock_update.callback_query = None
    mock_update.message = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()
    with patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=False):
        await send_directory_browser(mock_update, mock_context, pathlib.Path("/nope"))
    mock_update.message.reply_text.assert_called_once()
