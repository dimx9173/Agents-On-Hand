import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..callback_registry import register_restart_info, restart_registry
from ..runtime import active_streamers, bot_app, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


def on_background_session_finished(session: Any) -> None:
    logger.info(f"Background session finished: {session.session_id} ({session.agent_name})")
    user_id = session.user_id
    if user_id in active_streamers and active_streamers[user_id].session.session_id == session.session_id:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    if not bot_app:
        return
    rst_token = register_restart_info(session.agent_key, session.working_dir)
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 重新啟動 Agent", callback_data=f"sess_restart:{rst_token}"), InlineKeyboardButton("📥 下載 Session Log", callback_data=f"sess:download:{session.session_id}")]])
    alert_text = f"⚠️ *CLI Agent 進程已結束*\n\n• *Agent*: `{session.agent_name}` (`{session.session_id}`)\n• *工作目錄*: `{session.working_dir}`\n• *狀態*: 離線 (Exited)\n\n如需繼續使用，請點擊下方按鈕重新啟動。"
    async def _safe_send_exit_alert() -> None:
        try:
            await bot_app.bot.send_message(chat_id=user_id, text=alert_text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as err:
            logger.warning(f"Could not send exit notification: {err}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_safe_send_exit_alert())
    except Exception as e:
        logger.warning(f"Could not schedule exit notification: {e}")


@restricted
async def session_restart_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")
    if len(parts) < 2:
        return
    rst_token = parts[1]
    info = restart_registry.get(rst_token)
    if not info:
        await query.edit_message_text("⚠️ *重啟 token 已過期或無效*，請發送 `/aoh_new` 重新建立 Session。", parse_mode="Markdown")
        return
    agent_key = info["agent_key"]
    working_dir = info["working_dir"]
    user_id = query.from_user.id
    session = session_manager.create_session(user_id=user_id, agent_key=agent_key, working_dir=working_dir)
    streamer = create_streamer_for_session(context.bot, query.message.chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer
    await query.edit_message_text(f"🚀 *已成功重新啟動 Agent: {session.agent_name}*\n• Session ID: `{session.session_id}`\n• 工作目錄: `{working_dir}`\n\n您可以直接發送對話進行操作。", parse_mode="Markdown")
