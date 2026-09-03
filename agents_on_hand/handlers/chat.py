import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..callback_registry import register_restart_info
from ..config import AVAILABLE_CLI_AGENTS, get_installed_cli_agents
from ..runtime import active_streamers, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """U7: compact help — tappable command menu instead of a wall of text.

    Small screens bury long help text; buttons put the 3 core flows
    (new session / my sessions / interrupt) one tap away.
    """
    installed = get_installed_cli_agents()
    n_ok = sum(1 for key in AVAILABLE_CLI_AGENTS if key in installed)
    n_total = len(AVAILABLE_CLI_AGENTS)
    active = session_manager.get_active_session(update.effective_user.id)
    if active:
        cur = f"\n🟢 當前：*{active.agent_name}* · `{active.session_id.removeprefix('sess_')}`"
    else:
        cur = "\n⚪ 目前沒有 Active Session"
    text = (
        f"🤖 *Agents-On-Hand* · {n_ok}/{n_total} 工具已安裝{cur}\n\n"
        "直接打字即傳給 Agent；`esc` 中斷、`ctrlc` 強制停。"
    )
    reply_markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 開新 Session", callback_data="help:goto:new")],
            [InlineKeyboardButton("📋 我的 Sessions", callback_data="help:goto:sessions")],
            [
                InlineKeyboardButton("⏸️ ESC", callback_data="help:ctrl:esc"),
                InlineKeyboardButton("🛑 Ctrl+C", callback_data="help:ctrl:ctrlc"),
            ],
        ]
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def help_menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route help-menu button taps to the real flows (no new commands to memorise)."""
    from ..ui.directory_browser import new_command
    from ..ui.session_menu import sessions_command

    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    # Callbacks carry no update.message — point it at the query message so
    # downstream handlers (reply_text, effective_chat) work unchanged.
    if update.message is None and getattr(query, "message", None) is not None:
        update.message = query.message  # type: ignore[assignment]
    if parts[1] == "goto":
        if parts[2] == "new":
            await new_command(update, context)
        elif parts[2] == "sessions":
            await sessions_command(update, context)
    elif parts[1] == "ctrl":
        if parts[2] == "esc":
            await esc_command(update, context)
        elif parts[2] == "ctrlc":
            await ctrlc_command(update, context)


@restricted
async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info(f"[TG_CTRL] user={user_id} action=ESC")
    active_session = session_manager.get_active_session(user_id)
    if not active_session or not active_session.is_running:
        logger.warning(f"[TG_CTRL] user={user_id} action=ESC failed — no active session")
        await update.message.reply_text("⚠️ 目前沒有作用中的 Active Session。")
        return
    try:
        active_session.trace.event("USER_INPUT", f"control=ESC user={user_id}")
    except Exception:
        pass
    active_session.send_control_char("\x1b")
    logger.info(
        f"[TG->AGENT] user={user_id} session={active_session.session_id} control=ESC delivered"
    )
    await update.message.reply_text(
        f"⏸️ 已傳送 *ESC* 中斷訊號至 `{active_session.agent_name}` (`{active_session.session_id}`)",
        parse_mode="Markdown",
    )


@restricted
async def ctrlc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info(f"[TG_CTRL] user={user_id} action=CtrlC")
    active_session = session_manager.get_active_session(user_id)
    if not active_session or not active_session.is_running:
        logger.warning(f"[TG_CTRL] user={user_id} action=CtrlC failed — no active session")
        await update.message.reply_text("⚠️ 目前沒有作用中的 Active Session。")
        return
    try:
        active_session.trace.event("USER_INPUT", f"control=CtrlC user={user_id}")
    except Exception:
        pass
    active_session.send_control_char("\x03")
    logger.info(
        f"[TG->AGENT] user={user_id} session={active_session.session_id} control=CtrlC delivered"
    )
    await update.message.reply_text(
        f"🛑 已傳送 *Ctrl+C* 中斷訊號至 `{active_session.agent_name}` (`{active_session.session_id}`)",
        parse_mode="Markdown",
    )


@restricted
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    active_session = session_manager.get_active_session(user_id)
    if not active_session:
        await update.message.reply_text("ℹ️ 目前沒有作用中的 Active Session。")
        return
    session_id = active_session.session_id
    if session_manager.kill_session(session_id):
        if user_id in active_streamers:
            active_streamers[user_id].stop()
            del active_streamers[user_id]
        await update.message.reply_text(
            f"🛑 已結束作用中的 Session: `{active_session.agent_name}` (`{session_id}`)",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ 結束 Session 失敗。")


@restricted
async def text_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import time as _time
    import uuid as _uuid

    _route_start = _time.monotonic()
    _turn_id = _uuid.uuid4().hex[:8]
    user_id = update.effective_user.id
    # Telegram may send None for non-text updates (photos, etc.); guard gracefully
    user_text = getattr(update.message, "text", None) or ""
    chat_id = getattr(update.message, "chat_id", None) or getattr(update.effective_chat, "id", None)
    logger.info(
        f"[TG_IN] turn_id={_turn_id} user={user_id} chat={chat_id} "
        f"text_len={len(user_text)} text='{user_text[:120]}' has_active={session_manager.get_active_session(user_id) is not None}"
    )
    active_session = session_manager.get_active_session(user_id)

    # If the session is currently starting up (probing chain / initializing), wait briefly
    # so a prompt sent right after /aoh_new is not dropped. Bounded: ~3.2s max,
    # then tell the user the session is still starting instead of blocking longer.
    if active_session and getattr(active_session, "is_starting", False):
        logger.info(
            f"[WAIT] turn_id={_turn_id} session={active_session.session_id} is starting, waiting briefly..."
        )
        try:
            active_session.trace.event(
                "TURN_START",
                f"turn_id={_turn_id} state=waiting_for_start text_len={len(user_text)}",
            )
        except Exception:
            pass
        for _ in range(8):
            await asyncio.sleep(0.4)
            if active_session.is_running or not getattr(active_session, "is_starting", False):
                break
            # S4: probe/start failure sets is_running False while is_starting
            # may still be True (start() in finally). Detect a dead driver
            # early instead of waiting out the full 3.2s window.
            driver = getattr(active_session, "driver", None)
            if driver is not None and not getattr(driver, "is_running", True):
                break
        if active_session.is_starting and not active_session.is_running:
            await update.message.reply_text(
                "⏳ *Session 仍在啟動中*（探測 Driver / 初始化），請稍候幾秒再傳送訊息。",
                parse_mode="Markdown",
            )
            return

    if not active_session or not active_session.is_running:
        logger.warning(
            f"[OFFLINE] turn_id={_turn_id} user={user_id} active_id={session_manager.user_active_session.get(user_id)} "
            f"has_session={active_session is not None} is_running={getattr(active_session, 'is_running', None)} sessions={list(session_manager.sessions.keys())[-3:]}"
        )
        if active_session:
            try:
                active_session.trace.error(
                    f"turn_id={_turn_id} TG message dropped — session offline", turn_id=_turn_id
                )
            except Exception:
                pass
        reply_markup = None
        if active_session:
            rst_token = register_restart_info(active_session.agent_key, active_session.working_dir)
            reply_markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 重新啟動 Agent", callback_data=f"sess_restart:{rst_token}"
                        )
                    ]
                ]
            )
        await update.message.reply_text(
            "⚠️ *當前 Session 已離線 / 進程結束*\n請點擊下方按鈕重新啟動，或發送 `/aoh_new` 建立新 Session。",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
        return
    lower_text = user_text.strip().lower()
    if lower_text in ("!esc", "!cancel", "esc", "cancel"):
        active_session.send_control_char("\x1b")
        await update.message.reply_text(
            f"⏸️ 已發送 *ESC* 至 `{active_session.agent_name}`", parse_mode="Markdown"
        )
        return
    elif lower_text in ("!ctrlc", "!stop", "ctrlc", "ctrl+c"):
        active_session.send_control_char("\x03")
        await update.message.reply_text(
            f"🛑 已發送 *Ctrl+C* 至 `{active_session.agent_name}`", parse_mode="Markdown"
        )
        return
    logger.info(
        f"[TG->AGENT] turn_id={_turn_id} user={user_id} session={active_session.session_id} "
        f"driver={active_session.active_driver_name} text_len={len(user_text)} elapsed={(_time.monotonic() - _route_start):.3f}s"
    )
    active_session.send_input(user_text, turn_id=_turn_id)
    if (
        user_id not in active_streamers
        or active_streamers[user_id].session.session_id != active_session.session_id
        or not active_streamers[user_id]._is_active
    ):
        if user_id in active_streamers:
            active_streamers[user_id].stop()
        streamer = create_streamer_for_session(context.bot, update.message.chat_id, active_session)
        streamer.start()
        active_streamers[user_id] = streamer
    active_streamers[user_id].notify_user_input()
    logger.info(
        f"[TG_ROUTE_DONE] turn_id={_turn_id} user={user_id} session={active_session.session_id} active_streamers={len(active_streamers)}"
    )
