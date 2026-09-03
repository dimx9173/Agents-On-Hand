import asyncio
import logging
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..ansi_cleaner import format_telegram_code_block
from ..runtime import active_streamers, bot_app, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

if TYPE_CHECKING:
    from ..session_manager import AgentSession

logger = logging.getLogger("AgentsOnHand")


def _session_label(s: "AgentSession", active_session: "AgentSession | None") -> str:
    """One-line label for the /aoh_sessions list: icon + agent + folder + short id."""
    icon = "🟢" if s.is_running else "🔴"
    star = " ⭐" if active_session and active_session.session_id == s.session_id else ""
    short_id = s.session_id.removeprefix("sess_")
    return f"{icon} {s.agent_name}{star} · {s.working_dir.name} · `{short_id}`"


def _build_session_rows(
    s: "AgentSession",
    active_session: "AgentSession | None",
) -> list[list[InlineKeyboardButton]]:
    """Build compact 2-line keyboard rows for one session.

    Line 1 (identity + primary action): switch/restart target as the label
    itself so the button text carries context on narrow screens.
    Line 2 (secondary): log + kill only. Destructive kill is always last.

    - Active + running  -> [⭐ <label>] / [📄 Log][🛑 刪除]
    - Running (bg)      -> [▶️ <label>] / [📄 Log][🛑 刪除]
    - Offline           -> [🔄 <label>] / [📄 Log][🛑 刪除]
    """
    short_id = s.session_id.removeprefix("sess_")
    label = f"{s.agent_name} · {s.working_dir.name} · {short_id}"
    is_active = active_session and active_session.session_id == s.session_id

    if is_active and s.is_running:
        primary = InlineKeyboardButton(f"⭐ {label}", callback_data=f"sess:logs:{s.session_id}")
    elif s.is_running:
        primary = InlineKeyboardButton(f"▶️ {label}", callback_data=f"sess:switch:{s.session_id}")
    else:
        primary = InlineKeyboardButton(f"🔄 {label}", callback_data=f"sess:restart:{s.session_id}")

    secondary = [
        InlineKeyboardButton("📄 Log", callback_data=f"sess:logs:{s.session_id}"),
        InlineKeyboardButton("🛑 刪除", callback_data=f"sess:kill:{s.session_id}"),
    ]
    return [[primary], secondary]


@restricted
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    sessions = session_manager.list_user_sessions(user_id)
    active_session = session_manager.get_active_session(user_id)
    if not sessions:
        await update.message.reply_text(
            "ℹ️ 當前沒有任何 Session。請使用 `/aoh_new` 建立新 Session。", parse_mode="Markdown"
        )
        return
    text = "📋 *Sessions* — 點 ▶️ 切換，⭐ 為當前：\n\n"
    keyboard = []
    has_offline = False
    for s in sessions:
        if not s.is_running:
            has_offline = True
        text += _session_label(s, active_session) + "\n"
        keyboard.extend(_build_session_rows(s, active_session))
    if has_offline:
        keyboard.append([InlineKeyboardButton("🧹 清理離線", callback_data="sess:prune_offline")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def prune_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    count = session_manager.prune_offline_sessions(user_id)
    if count > 0:
        await update.message.reply_text(
            f"🧹 已成功清理 {count} 個離線 Session！", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ 目前沒有任何離線 Session 需要清理。", parse_mode="Markdown"
        )


@restricted
async def session_action_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    parts = data.split(":")
    action = parts[1]
    session_id = parts[2] if len(parts) > 2 else ""
    logger.info(f"[TG_CB] user={user_id} action={action} session={session_id} data={data}")

    if action == "prune_offline":
        pruned_count = session_manager.prune_offline_sessions(user_id)
        if pruned_count > 0:
            remaining = session_manager.list_user_sessions(user_id)
            if not remaining:
                await query.edit_message_text(
                    f"🧹 *已成功清理 {pruned_count} 個離線 Session！*\n\n當前已無任何 Session，可使用 `/aoh_new` 建立新 Session。",
                    parse_mode="Markdown",
                )
            else:
                active_session = session_manager.get_active_session(user_id)
                text = f"🧹 *已清理 {pruned_count} 個！*\n\n📋 *Sessions*:\n\n"
                keyboard = []
                has_offline = False
                for s in remaining:
                    if not s.is_running:
                        has_offline = True
                    text += _session_label(s, active_session) + "\n"
                    keyboard.extend(_build_session_rows(s, active_session))
                if has_offline:
                    keyboard.append(
                        [InlineKeyboardButton("🧹 清理離線", callback_data="sess:prune_offline")]
                    )
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    text, parse_mode="Markdown", reply_markup=reply_markup
                )
        else:
            await query.answer("ℹ️ 目前沒有任何離線 Session 需要清理。", show_alert=True)
        return

    session = session_manager.get_session(session_id)
    if action == "switch":
        if not session:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        prev_session = session_manager.get_active_session(user_id)
        logger.info(
            f"[SESSION_SWITCH] user={user_id} from={getattr(prev_session, 'session_id', None)} to={session_id}"
        )
        try:
            (prev_session or session).trace.streamer_switch(
                getattr(prev_session, "session_id", None), session_id, reason="switch"
            )
        except Exception:
            pass
        if prev_session and prev_session.session_id != session_id and prev_session.is_running:

            def _make_bg_done_cb(bg_sess_id: str, bg_agent_name: str, target_chat_id: int):
                def _on_bg_done(s: Any) -> None:
                    if not bot_app:
                        return
                    logs = s.get_last_n_lines(n=10).strip()
                    summary = logs[-200:] if len(logs) > 200 else logs
                    if not summary:
                        summary = "(無文字內容)"
                    alert_text = f"✅ *{bg_agent_name} 回覆完成*\n🆔 Session: `{bg_sess_id}`\n\n📝 *回覆摘要*:\n```\n{summary}\n```"
                    reply_markup = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    f"🔄 切換回 {bg_agent_name}",
                                    callback_data=f"sess:switch:{bg_sess_id}",
                                )
                            ]
                        ]
                    )
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            bot_app.bot.send_message(
                                chat_id=target_chat_id,
                                text=alert_text,
                                reply_markup=reply_markup,
                                parse_mode="Markdown",
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Could not send bg completion notification: {e}")

                return _on_bg_done

            prev_session.set_background_completion_callback(
                _make_bg_done_cb(
                    prev_session.session_id, prev_session.agent_name, query.message.chat_id
                )
            )
        session.set_background_completion_callback(None)
        session_manager.set_active_session(user_id, session_id)
        if user_id in active_streamers:
            active_streamers[user_id].stop()
            del active_streamers[user_id]
        # U2: mobile-sized history (30 lines). Full context stays in the log
        # file; /aoh_sessions → 📄 Log shows 100 on demand.
        logs = session.get_last_n_lines(n=30)
        formatted_code = format_telegram_code_block(logs, max_chars=2500)
        chat_id = query.message.chat_id
        short_id = session_id.removeprefix("sess_")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔄 *{session.agent_name}* · `{short_id}`\n📁 `{session.working_dir}`\n\n📄 *近 30 行*:\n{formatted_code}",
            parse_mode="Markdown",
        )
        streamer = create_streamer_for_session(context.bot, chat_id, session)
        streamer.start()
        active_streamers[user_id] = streamer
    elif action == "logs":
        if not session:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        logs = session.get_last_n_lines(n=100)
        formatted_code = format_telegram_code_block(logs, max_chars=3700)
        await query.message.reply_text(
            f"📄 *Session Log (最後 100 行)* - `{session_id}`:\n{formatted_code}",
            parse_mode="Markdown",
        )
    elif action == "download":
        if not session or not session.log_file_path.exists():
            await query.message.reply_text("❌ Log 檔案不存在。")
            return
        with open(session.log_file_path, "rb") as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=f"{session.session_id}_{session.agent_key}.log",
                caption=f"📥 Log 檔案: {session.agent_name} ({session.session_id})",
            )
    elif action == "retry":
        if not session or not session.is_running:
            await query.message.reply_text("⚠️ 該 Session 已離線或不存在，無法重試。")
            return
        last_prompt = getattr(session, "last_user_prompt", "")
        if not last_prompt:
            await query.message.reply_text("ℹ️ 目前沒有可重試的上一條 Prompt。")
            return
        await query.message.reply_text(
            f"🔄 <b>重試上一條 Prompt</b>: <code>{last_prompt}</code>", parse_mode="HTML"
        )
        session.send_input(last_prompt)
        chat_id = query.message.chat_id
        if (
            user_id not in active_streamers
            or active_streamers[user_id].session.session_id != session.session_id
            or not active_streamers[user_id]._is_active
        ):
            if user_id in active_streamers:
                active_streamers[user_id].stop()
            streamer = create_streamer_for_session(context.bot, chat_id, session)
            streamer.start()
            active_streamers[user_id] = streamer
        active_streamers[user_id].notify_user_input()
    elif action == "kill":
        if session_manager.kill_session(session_id):
            if (
                user_id in active_streamers
                and active_streamers[user_id].session.session_id == session_id
            ):
                active_streamers[user_id].stop()
                del active_streamers[user_id]
            await query.edit_message_text(
                f"🛑 已成功結束 Session: `{session_id}`", parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("❌ 結束 Session 失敗或不存在。")
