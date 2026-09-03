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
    installed = get_installed_cli_agents()
    agent_status_lines = []
    for key, info in AVAILABLE_CLI_AGENTS.items():
        icon = "🟢 已安裝" if key in installed else "⚪ 未安裝"
        acp_tag = " [ACP]" if info.get("use_acp") else ""
        agent_status_lines.append(f"• *{info['name']}*{acp_tag}: {icon}")
    agent_status_text = "\n".join(agent_status_lines)
    welcome = (
        "🤖 *Agents-On-Hand 遠端 CLI Orchestrator*\n\n"
        "⚡ *系統管理指令*:\n"
        "• `/aoh_new` - 開啟目錄選擇器與啟動 CLI Agent\n"
        "• `/aoh_sessions` - 管理、切換、查看與刪除背景 Session\n"
        "• `/aoh_prune` - 一鍵清理所有已離線的 Session\n"
        "• `/aoh_stop` - 結束當前作用中的 Active Session\n"
        "• `/aoh_help` - 顯示本說明檔\n\n"
        f"🛠️ *系統 CLI / ACP Agent 安裝狀態*:\n{agent_status_text}\n\n"
        "💡 *直通 Chat 對話模式*:\n"
        "所有非 `/aoh_` 開頭的訊息與指令（如 `/commit`, `/clear` 或一般文字），皆會 100% 直通傳送至您當前活躍的 CLI Agent！"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


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
