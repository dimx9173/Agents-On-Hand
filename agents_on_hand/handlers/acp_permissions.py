import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


@restricted
async def acp_permission_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    if len(parts) >= 4:
        session_id = parts[2]
        req_id_str = ":".join(parts[3:])
        target_session = session_manager.get_session(session_id)
    else:
        req_id_str = parts[2]
        user_id = query.from_user.id
        target_session = session_manager.get_active_session(user_id)
    if not target_session or not hasattr(target_session, "respond_permission"):
        await query.edit_message_text("⚠️ 找不到對應的 ACP Session。")
        return
    try:
        req_id: object = int(req_id_str)
    except ValueError:
        req_id = req_id_str
    if action == "approve":
        await target_session.respond_permission(req_id, approved=True)
        await query.edit_message_text("✅ *已授權 Agent 執行此 Tool*", parse_mode="Markdown")
    else:
        await target_session.respond_permission(req_id, approved=False)
        await query.edit_message_text("❌ *已拒絕 Agent 執行此 Tool*", parse_mode="Markdown")
