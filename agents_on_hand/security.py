import functools
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .config import is_user_allowed

logger = logging.getLogger("AgentsOnHand")


def restricted(func):  # type: ignore[no-untyped-def]
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):  # type: ignore[no-untyped-def]
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_user_allowed(user_id):
            if update.message:
                await update.message.reply_text("⛔ 存取拒絕：您未獲得控制此 Bot 的權限。")
            elif update.callback_query:
                await update.callback_query.answer("⛔ 存取拒絕：您未獲得權限。", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)

    return wrapped
