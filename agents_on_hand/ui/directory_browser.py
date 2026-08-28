import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..callback_registry import get_path_token, resolve_path_token
from ..config import (
    ALLOWED_ROOT_DIRS,
    get_installed_cli_agents,
    is_path_allowed,
)
from ..runtime import active_streamers, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


async def send_directory_browser(update: Update, context: ContextTypes.DEFAULT_TYPE, target_dir: Path, page: int = 0) -> None:
    target_dir = target_dir.expanduser().resolve()
    if not is_path_allowed(target_dir):
        msg = f"⛔ 無法存取該目錄 `{target_dir}`（超出允許根目錄範圍）。"
        if update.callback_query:
            await update.callback_query.answer("超出允許根目錄範圍", show_alert=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return
    target_token = get_path_token(target_dir)
    keyboard = []
    parent_dir = target_dir.parent
    if parent_dir != target_dir and is_path_allowed(parent_dir):
        parent_token = get_path_token(parent_dir)
        keyboard.append([InlineKeyboardButton("⬆️ .. (上一層)", callback_data=f"dir:nav:{parent_token}:0")])
    items_per_page = 8
    subdirs: list[Path] = []
    try:
        subdirs = sorted([d for d in target_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    except PermissionError:
        pass
    total_pages = max(1, (len(subdirs) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))
    page_subdirs = subdirs[page * items_per_page : (page + 1) * items_per_page]
    for sd in page_subdirs:
        sd_token = get_path_token(sd)
        keyboard.append([InlineKeyboardButton(f"📁 {sd.name}", callback_data=f"dir:nav:{sd_token}:0")])
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ 上一頁", callback_data=f"dir:nav:{target_token}:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("下一頁 ▶️", callback_data=f"dir:nav:{target_token}:{page + 1}"))
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton(f"✅ 選擇此目錄 [{target_dir.name or '/'}]", callback_data=f"dir:select:{target_token}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"📂 *請選擇工作目錄* (共 {len(subdirs)} 個子目錄, 頁數 {page + 1}/{total_pages}):\n`{target_dir}`"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    initial_dir = ALLOWED_ROOT_DIRS[0] if ALLOWED_ROOT_DIRS else Path.cwd()
    await send_directory_browser(update, context, initial_dir)


@restricted
async def directory_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "noop":
        return
    if data.startswith("dir:nav:"):
        rest = data[len("dir:nav:"):]
        parts = rest.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            path_token, page_str = parts[0], parts[1]
            page = int(page_str)
        else:
            path_token, page = rest, 0
        target_path = resolve_path_token(path_token)
        if target_path is None:
            await query.answer("⛔ 路徑已過期，請重新選擇。", show_alert=True)
            return
        await send_directory_browser(update, context, target_path, page=page)
    elif data.startswith("dir:select:"):
        path_token = data[len("dir:select:"):]
        target_path = resolve_path_token(path_token)
        if target_path is None:
            await query.answer("⛔ 路徑已過期，請重新選擇。", show_alert=True)
            return
        await show_agent_selector(query, target_path)


async def show_agent_selector(query, working_dir: Path) -> None:  # type: ignore[no-untyped-def]
    dir_token = get_path_token(working_dir)
    installed_agents = get_installed_cli_agents()
    keyboard: list[list[InlineKeyboardButton]] = []
    if installed_agents:
        for key, info in installed_agents.items():
            mode_badge = " [ACP]" if info.get("use_acp") else ""
            keyboard.append([InlineKeyboardButton(f"🚀 啟動 {info['name']}{mode_badge}", callback_data=f"agent:start:{dir_token}:{key}")])
    else:
        text_no_agents = f"⚠️ *檢測不到任何已安裝的 CLI Agent*:\n📁 `{working_dir}`\n\n請確認系統 PATH 環境變數或安裝工具。"
        keyboard.append([InlineKeyboardButton("🔙 返回目錄選擇", callback_data=f"dir:nav:{dir_token}:0")])
        await query.edit_message_text(text_no_agents, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    keyboard.append([InlineKeyboardButton("🔙 返回目錄選擇", callback_data=f"dir:nav:{dir_token}:0")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"⚙️ *選擇欲在下列目錄啟動的 CLI Agent* (共 {len(installed_agents)} 個已安裝工具):\n📁 `{working_dir}`"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def agent_start_callback_handler(update, context):  # type: ignore[no-untyped-def]
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    parts = data.split(":", 3)
    if len(parts) < 4:
        return
    path_token, agent_key = parts[2], parts[3]
    working_dir = resolve_path_token(path_token)
    # Defense-in-depth: re-validate the resolved path before spawning a process
    if working_dir is None or not is_path_allowed(working_dir):
        await query.edit_message_text("⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown")
        return
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    session = session_manager.create_session(user_id=user_id, agent_key=agent_key, working_dir=working_dir)
    await query.edit_message_text(f"✅ *已啟動 Session: {session.agent_name}*\n📁 `{working_dir}`\n`ID: {session.session_id}`", parse_mode="Markdown")
    chat_id = query.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text=f"💬 *已對接 Active Session: {session.agent_name}* (`{session.session_id}`)\n📁 `{working_dir}`\n\n現在可直接打字或傳送指令與 Agent 對話！", parse_mode="Markdown")
    streamer = create_streamer_for_session(context.bot, chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer
