import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import ALLOWED_TELEGRAM_USER_IDS, TELEGRAM_BOT_TOKEN, ensure_runtime_dirs
from .handlers.acp_permissions import acp_permission_callback_handler
from .handlers.chat import (
    ctrlc_command,
    esc_command,
    help_command,
    stop_command,
    text_message_router,
)
from .handlers.restart import session_restart_callback_handler
from .session_manager import session_manager
from .ui.directory_browser import directory_callback_handler, new_command
from .ui.session_menu import prune_command, session_action_callback_handler, sessions_command

logger = logging.getLogger("AgentsOnHand")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Log full details for debugging; never leak exception internals to the user
    logger.error("Exception while handling an update:", exc_info=context.error)
    # Also trace to session if resolvable
    try:
        uid = (
            getattr(getattr(update, "effective_user", None), "id", None)
            if isinstance(update, Update)
            else None
        )
        if uid:
            sess = session_manager.get_active_session(uid)
            if sess:
                sess.trace.error(f"global_error: {context.error}")
    except Exception:
        pass
    if isinstance(update, Update):
        error_msg = "❌ *系統處理時發生錯誤*，請稍後重試。詳細資訊已記錄於伺服器日誌。"
        try:
            if update.callback_query:
                await update.callback_query.message.reply_text(error_msg, parse_mode="Markdown")
            elif update.message:  # type: ignore[union-attr]
                await update.message.reply_text(error_msg, parse_mode="Markdown")  # type: ignore[union-attr]
        except Exception:
            pass


async def post_init(application: Application) -> None:
    ensure_runtime_dirs()
    bot_commands = [
        BotCommand("aoh_new", "📂 開啟目錄選擇器與啟動 CLI Agent"),
        BotCommand("aoh_sessions", "📋 管理與切換背景 Session"),
        BotCommand("aoh_prune", "🧹 清理所有已離線的 Session"),
        BotCommand("aoh_esc", "⏸️ 發送 ESC 中斷 Agent 執行/選單"),
        BotCommand("aoh_ctrlc", "🛑 發送 Ctrl+C 強制中斷"),
        BotCommand("aoh_stop", "❌ 結束當前 Active Session"),
        BotCommand("aoh_help", "💡 顯示說明與系統指令"),
    ]
    try:
        await application.bot.set_my_commands(bot_commands)
        logger.info("Registered Telegram command menu successfully.")
    except Exception as e:
        logger.warning(f"Failed to set Telegram bot commands: {e}")
    if not ALLOWED_TELEGRAM_USER_IDS:
        return
    greeting_text = (
        "👋 *Agents-On-Hand 直通對話服務已啟動！*\n\n"
        "所有非 `/aoh_` 開頭的訊息與指令皆會 100% 直通傳送至 CLI Agent。\n"
        "• `/aoh_new` - 選擇目錄與啟動 Agent\n"
        "• `/aoh_sessions` - 查看與管理背景 Session\n"
        "• `/aoh_prune` - 清理所有已離線的 Session\n"
        "• `/aoh_esc` - 發送 ESC 中斷訊號\n"
        "• `/aoh_ctrlc` - 發送 Ctrl+C 中斷訊號\n"
        "• `/aoh_stop` - 結束 Active Session\n"
        "• `/aoh_help` - 查看說明"
    )
    for user_id in ALLOWED_TELEGRAM_USER_IDS:
        try:
            await application.bot.send_message(
                chat_id=user_id, text=greeting_text, parse_mode="Markdown"
            )
            logger.info(f"Startup greeting sent to user {user_id}")
        except Exception as e:
            logger.warning(f"Could not send startup greeting to user {user_id}: {e}")


def main() -> None:
    global bot_app
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 錯誤：請先在 .env 或環境變數中設定 TELEGRAM_BOT_TOKEN！")
        return
    from .config import ALLOWED_TELEGRAM_USER_IDS, DEV_ALLOW_ALL

    if not ALLOWED_TELEGRAM_USER_IDS and not DEV_ALLOW_ALL:
        print("❌ 錯誤：ALLOWED_TELEGRAM_USER_IDS 為空且未設置 AOH_DEV_ALLOW_ALL_USERS=1。")
        print(
            "   請在 .env 中設置 ALLOWED_TELEGRAM_USER_IDS（例如 12345678）或設置 AOH_DEV_ALLOW_ALL_USERS=1 以允許所有用戶（僅開發用）。"
        )
        return
    from .handlers.restart import on_background_session_finished as _cb

    session_manager.register_on_finished_callback(_cb)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    # sync runtime.bot_app reference
    import agents_on_hand.runtime as _rt

    _rt.bot_app = app
    bot_app = app
    app.add_error_handler(global_error_handler)
    app.add_handler(CommandHandler("aoh_start", help_command))
    app.add_handler(CommandHandler("aoh_help", help_command))
    app.add_handler(CommandHandler("aoh_new", new_command))
    app.add_handler(CommandHandler("aoh_sessions", sessions_command))
    app.add_handler(CommandHandler("aoh_prune", prune_command))
    app.add_handler(CommandHandler("aoh_esc", esc_command))
    app.add_handler(CommandHandler("aoh_ctrlc", ctrlc_command))
    app.add_handler(CommandHandler("aoh_stop", stop_command))
    app.add_handler(CallbackQueryHandler(directory_callback_handler, pattern=r"^dir:"))
    from .ui.directory_browser import agent_start_callback_handler

    app.add_handler(CallbackQueryHandler(agent_start_callback_handler, pattern=r"^agent:"))
    app.add_handler(CallbackQueryHandler(session_action_callback_handler, pattern=r"^sess:"))
    app.add_handler(CallbackQueryHandler(acp_permission_callback_handler, pattern=r"^acp_perm:"))
    app.add_handler(
        CallbackQueryHandler(session_restart_callback_handler, pattern=r"^sess_restart:")
    )
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, text_message_router))
    app.add_handler(MessageHandler(filters.COMMAND, text_message_router))
    print("🚀 Agents-On-Hand (Direct Chat Mode) 啟動中...")
    app.run_polling()
