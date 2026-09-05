import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..ansi_cleaner import format_telegram_code_block
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


def _build_recent_dirs_row(update: Update, target_dir: Path) -> list[InlineKeyboardButton] | None:
    """Up to 3 ⭐ buttons for the user's most recently used session dirs.

    Skips the directory currently being browsed. Returns None when there is
    nothing useful to shortcut (keeps the keyboard compact).
    """
    try:
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query
            else update.effective_user.id
        )
    except Exception:
        return None
    seen: list[Path] = []
    for s in session_manager.list_user_sessions(user_id):
        try:
            d = Path(s.working_dir)
        except Exception:
            continue
        if d == target_dir or d in seen:
            continue
        if not is_path_allowed(d) or not d.exists():
            continue
        seen.append(d)
        if len(seen) >= 3:
            break
    if not seen:
        return None
    return [
        InlineKeyboardButton(f"⭐ {d.name}", callback_data=f"dir:nav:{get_path_token(d)}:0")
        for d in seen
    ]


async def send_directory_browser(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target_dir: Path, page: int = 0
) -> None:
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
    # U4: ⭐ recent dirs (sessions you actually used) on page 1 — one tap
    # back to a working directory instead of browsing from the root.
    if page == 0:
        recent_row = _build_recent_dirs_row(update, target_dir)
        if recent_row:
            keyboard.append(recent_row)
    parent_dir = target_dir.parent
    if parent_dir != target_dir and is_path_allowed(parent_dir):
        parent_token = get_path_token(parent_dir)
        keyboard.append(
            [InlineKeyboardButton("⬆️ .. (上一層)", callback_data=f"dir:nav:{parent_token}:0")]
        )
    items_per_page = 8
    subdirs: list[Path] = []
    try:
        subdirs = sorted(
            [d for d in target_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        )
    except PermissionError:
        pass
    total_pages = max(1, (len(subdirs) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))
    page_subdirs = subdirs[page * items_per_page : (page + 1) * items_per_page]
    for sd in page_subdirs:
        sd_token = get_path_token(sd)
        keyboard.append(
            [InlineKeyboardButton(f"📁 {sd.name}", callback_data=f"dir:nav:{sd_token}:0")]
        )
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton("◀️ 上一頁", callback_data=f"dir:nav:{target_token}:{page - 1}")
            )
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton("下一頁 ▶️", callback_data=f"dir:nav:{target_token}:{page + 1}")
            )
        keyboard.append(nav_row)
    keyboard.append(
        [
            InlineKeyboardButton(
                f"✅ 選擇此目錄 [{target_dir.name or '/'}]",
                callback_data=f"dir:select:{target_token}",
            )
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"📂 *請選擇工作目錄* (共 {len(subdirs)} 個子目錄, 頁數 {page + 1}/{total_pages}):\n`{target_dir}`"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start flow: agent picker in the default dir (1 tap to launch).

    U3: the old flow forced dir-browser → agent-picker (2+ taps) on every
    /aoh_new even though most launches reuse the same directory. The agent
    picker keeps a 📂 directory row for the rare case you need to browse.
    """
    initial_dir = ALLOWED_ROOT_DIRS[0] if ALLOWED_ROOT_DIRS else Path.cwd()
    initial_dir = initial_dir.expanduser().resolve()
    if update.callback_query:
        await show_agent_selector(update.callback_query, initial_dir)
    else:
        await _send_agent_picker_new_message(update, context, initial_dir)


async def _send_agent_picker_new_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, working_dir: Path
) -> None:
    """Agent picker as a fresh message (for /aoh_new entry)."""
    reply_markup = _build_agent_picker_keyboard(working_dir)
    if reply_markup is None:
        await update.message.reply_text(
            f"⚠️ *檢測不到任何已安裝的 CLI Agent*:\n📁 `{working_dir}`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(
        f"⚙️ *選 Agent* · 📁 `{working_dir}`",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


@restricted
async def directory_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "noop":
        return
    if data.startswith("dir:nav:"):
        rest = data[len("dir:nav:") :]
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
        path_token = data[len("dir:select:") :]
        target_path = resolve_path_token(path_token)
        if target_path is None:
            await query.answer("⛔ 路徑已過期，請重新選擇。", show_alert=True)
            return
        await show_agent_selector(query, target_path)


def _build_agent_picker_keyboard(working_dir: Path) -> InlineKeyboardMarkup | None:
    """Shared compact agent picker: one row per agent + a 📂 directory row.

    Returns None when no agents are installed (callers render the warning).
    """
    dir_token = get_path_token(working_dir)
    installed_agents = get_installed_cli_agents()
    if not installed_agents:
        return None
    keyboard: list[list[InlineKeyboardButton]] = []
    for key, info in installed_agents.items():
        mode_badge = " [ACP]" if info.get("use_acp") else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🚀 {info['name']}{mode_badge}",
                    callback_data=f"agent:start:{dir_token}:{key}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(f"📂 {working_dir.name}", callback_data=f"dir:nav:{dir_token}:0")]
    )
    return InlineKeyboardMarkup(keyboard)


async def show_agent_selector(query, working_dir: Path) -> None:  # type: ignore[no-untyped-def]
    reply_markup = _build_agent_picker_keyboard(working_dir)
    if reply_markup is None:
        dir_token = get_path_token(working_dir)
        await query.edit_message_text(
            f"⚠️ *檢測不到任何已安裝的 CLI Agent*:\n📁 `{working_dir}`\n\n請確認系統 PATH 環境變數或安裝工具。",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📂 選擇目錄", callback_data=f"dir:nav:{dir_token}:0")]]
            ),
        )
        return
    await query.edit_message_text(
        f"⚙️ *選 Agent* · 📁 `{working_dir}`",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def agent_start_callback_handler(update, context):  # type: ignore[no-untyped-def]
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    parts = data.split(":", 3)
    if len(parts) < 4:
        return
    subaction = parts[1]
    if subaction == "force_new":
        # User explicitly chose a fresh session from the reuse prompt.
        path_token, agent_key = parts[2], parts[3]
        working_dir = resolve_path_token(path_token)
        if working_dir is None or not is_path_allowed(working_dir):
            await query.edit_message_text(
                "⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown"
            )
            return
        await _launch_new_session(query, context, user_id, agent_key, working_dir)
        return
    if subaction != "start":
        return
    path_token, agent_key = parts[2], parts[3]
    working_dir = resolve_path_token(path_token)
    # Defense-in-depth: re-validate the resolved path before spawning a process
    if working_dir is None or not is_path_allowed(working_dir):
        await query.edit_message_text(
            "⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown"
        )
        return
    # PRP reuse: same user + agent + directory already running → offer to
    # attach instead of stranding the old process (orphan) and splitting context.
    existing = session_manager.find_running_session(
        user_id=user_id, agent_key=agent_key, working_dir=working_dir
    )
    if existing is not None:
        short_id = existing.session_id.removeprefix("sess_")
        dir_token = get_path_token(working_dir)
        await query.edit_message_text(
            f"🔁 *此目錄已有運作中的 {existing.agent_name}*\n"
            f"📁 `{working_dir}`\n`ID: {existing.session_id}`\n\n"
            "要沿用它（保留對話上下文），還是另外開一個？",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"🔁 沿用 · `{short_id}`",
                            callback_data=f"agent:reuse:{existing.session_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🆕 另外開一個",
                            callback_data=f"agent:force_new:{dir_token}:{agent_key}",
                        )
                    ],
                ]
            ),
        )
        return
    await _launch_new_session(query, context, user_id, agent_key, working_dir)


async def _launch_new_session(
    query, context, user_id: int, agent_key: str, working_dir: Path
) -> None:  # type: ignore[no-untyped-def]
    """Shared create-and-attach flow (agent:start: + agent:force_new:)."""
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    session = session_manager.create_session(
        user_id=user_id, agent_key=agent_key, working_dir=working_dir
    )
    await query.edit_message_text(
        f"✅ *已啟動 Session: {session.agent_name}*\n📁 `{working_dir}`\n`ID: {session.session_id}`",
        parse_mode="Markdown",
    )
    chat_id = query.message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"💬 *已對接 Active Session: {session.agent_name}* (`{session.session_id}`)\n📁 `{working_dir}`\n\n現在可直接打字或傳送指令與 Agent 對話！",
        parse_mode="Markdown",
    )
    streamer = create_streamer_for_session(context.bot, chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer


async def agent_reuse_callback_handler(update, context):  # type: ignore[no-untyped-def]
    """Attach to the running session selected in the reuse prompt."""

    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    user_id = query.from_user.id
    session_id = parts[2]
    session = session_manager.get_session(session_id)
    if session is None or session.user_id != user_id or not session.is_running:
        await query.edit_message_text(
            "⚠️ 該 Session 已不存在或離線，請重新選擇。",
            parse_mode="Markdown",
        )
        return
    session_manager.set_active_session(user_id, session_id)
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    logs = session.get_last_n_lines(n=30)
    formatted_code = format_telegram_code_block(logs, max_chars=2500)
    chat_id = query.message.chat_id
    short_id = session_id.removeprefix("sess_")
    await query.edit_message_text(
        f"🔁 *已沿用 Session: {session.agent_name}* · `{short_id}`\n📁 `{session.working_dir}`",
        parse_mode="Markdown",
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"💬 *已對接 Active Session: {session.agent_name}* (`{session_id}`)\n📁 `{session.working_dir}`\n\n📄 *近 30 行*:\n{formatted_code}\n\n現在可直接打字或傳送指令與 Agent 對話！",
        parse_mode="Markdown",
    )
    streamer = create_streamer_for_session(context.bot, chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer
