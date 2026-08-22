import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..ansi_cleaner import format_telegram_code_block
from ..runtime import active_streamers, bot_app, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


@restricted
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    sessions = session_manager.list_user_sessions(user_id)
    active_session = session_manager.get_active_session(user_id)
    if not sessions:
        await update.message.reply_text("ℹ️ 當前沒有任何 Session。請使用 `/aoh_new` 建立新 Session。", parse_mode="Markdown")
        return
    text = "📋 *您的 CLI Agent Session 列表*:\n\n"
    keyboard = []
    has_offline = False
    for s in sessions:
        if not s.is_running:
            has_offline = True
        status_icon = "🟢" if s.is_running else "🔴"
        is_active = (active_session and active_session.session_id == s.session_id)
        active_badge = " ⭐ (當前 Active)" if is_active else ""
        text += f"{status_icon} *ID*: `{s.session_id}` | *Agent*: {s.agent_name}{active_badge}\n"
        text += f"   📁 `{s.working_dir.name}`\n\n"
        row = []
        if not is_active:
            row.append(InlineKeyboardButton(f"▶️ 切換至 {s.session_id}", callback_data=f"sess:switch:{s.session_id}"))
        row.append(InlineKeyboardButton("📄 查看 Log", callback_data=f"sess:logs:{s.session_id}"))
        row.append(InlineKeyboardButton("🛑 刪除", callback_data=f"sess:kill:{s.session_id}"))
        keyboard.append(row)
    if has_offline:
        keyboard.append([InlineKeyboardButton("🧹 一鍵清理所有離線 Session", callback_data="sess:prune_offline")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def prune_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    count = session_manager.prune_offline_sessions(user_id)
    if count > 0:
        await update.message.reply_text(f"🧹 已成功清理 {count} 個離線 Session！", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ 目前沒有任何離線 Session 需要清理。", parse_mode="Markdown")


@restricted
async def session_action_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    parts = data.split(":")
    action = parts[1]
    session_id = parts[2] if len(parts) > 2 else ""

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
                text = f"🧹 *已成功清理 {pruned_count} 個離線 Session！*\n\n📋 *您的 CLI Agent Session 列表*:\n\n"
                keyboard = []
                has_offline = False
                for s in remaining:
                    if not s.is_running:
                        has_offline = True
                    status_icon = "🟢" if s.is_running else "🔴"
                    is_active = (active_session and active_session.session_id == s.session_id)
                    active_badge = " ⭐ (當前 Active)" if is_active else ""
                    text += f"{status_icon} *ID*: `{s.session_id}` | *Agent*: {s.agent_name}{active_badge}\n"
                    text += f"   📁 `{s.working_dir.name}`\n\n"
                    row = []
                    if not is_active:
                        row.append(InlineKeyboardButton(f"▶️ 切換至 {s.session_id}", callback_data=f"sess:switch:{s.session_id}"))
                    row.append(InlineKeyboardButton("📄 查看 Log", callback_data=f"sess:logs:{s.session_id}"))
                    row.append(InlineKeyboardButton("🛑 刪除", callback_data=f"sess:kill:{s.session_id}"))
                    keyboard.append(row)
                if has_offline:
                    keyboard.append([InlineKeyboardButton("🧹 一鍵清理所有離線 Session", callback_data="sess:prune_offline")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.answer("ℹ️ 目前沒有任何離線 Session 需要清理。", show_alert=True)
        return

    session = session_manager.get_session(session_id)
    if action == "switch":
        if not session:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        prev_session = session_manager.get_active_session(user_id)
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
                    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔄 切換回 {bg_agent_name}", callback_data=f"sess:switch:{bg_sess_id}")]])
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(bot_app.bot.send_message(chat_id=target_chat_id, text=alert_text, reply_markup=reply_markup, parse_mode="Markdown"))
                    except Exception as e:
                        logger.warning(f"Could not send bg completion notification: {e}")
                return _on_bg_done
            prev_session.set_background_completion_callback(_make_bg_done_cb(prev_session.session_id, prev_session.agent_name, query.message.chat_id))
        session.set_background_completion_callback(None)
        session_manager.set_active_session(user_id, session_id)
        if user_id in active_streamers:
            active_streamers[user_id].stop()
            del active_streamers[user_id]
        logs = session.get_last_n_lines(n=100)
        formatted_code = format_telegram_code_block(logs, max_chars=3700)
        chat_id = query.message.chat_id
        await context.bot.send_message(chat_id=chat_id, text=f"🔄 *已切換並對接至 Session: {session.agent_name}* (`{session_id}`)\n📁 `{session.working_dir}`\n\n📄 *歷史紀錄 (最後 100 行)*:\n{formatted_code}", parse_mode="Markdown")
        streamer = create_streamer_for_session(context.bot, chat_id, session)
        streamer.start()
        active_streamers[user_id] = streamer
    elif action == "logs":
        if not session:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        logs = session.get_last_n_lines(n=100)
        formatted_code = format_telegram_code_block(logs, max_chars=3700)
        await query.message.reply_text(f"📄 *Session Log (最後 100 行)* - `{session_id}`:\n{formatted_code}", parse_mode="Markdown")
    elif action == "download":
        if not session or not session.log_file_path.exists():
            await query.message.reply_text("❌ Log 檔案不存在。")
            return
        with open(session.log_file_path, "rb") as f:
            await context.bot.send_document(chat_id=query.message.chat_id, document=f, filename=f"{session.session_id}_{session.agent_key}.log", caption=f"📥 Log 檔案: {session.agent_name} ({session.session_id})")
    elif action == "kill":
        if session_manager.kill_session(session_id):
            if user_id in active_streamers and active_streamers[user_id].session.session_id == session_id:
                active_streamers[user_id].stop()
                del active_streamers[user_id]
            await query.edit_message_text(f"🛑 已成功結束 Session: `{session_id}`", parse_mode="Markdown")
        else:
            await query.message.reply_text("❌ 結束 Session 失敗或不存在。")
