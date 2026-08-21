import logging
import asyncio
from pathlib import Path
from typing import Dict, Optional, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_ROOT_DIRS,
    AVAILABLE_CLI_AGENTS,
    is_user_allowed,
    is_path_allowed,
    get_installed_cli_agents,
)

from .session_manager import session_manager, CLISession
from .stream_handler import DirectChatStreamer
from .acp_streamer import ACPStreamer
from .ansi_cleaner import format_telegram_code_block

# Logging is configured centrally in main.py via agents_on_hand.logging_setup.setup_logging()
logger = logging.getLogger("AgentsOnHand")


# Active streamer instances per user_id
active_streamers: Dict[int, Any] = {}


def create_streamer_for_session(bot, chat_id: int, session: Any):
    """Create UnifiedStreamer for the session."""
    return DirectChatStreamer(
        bot=bot,
        chat_id=chat_id,
        session=session,
    )




def restricted(func):
    """Decorator to enforce Telegram User ID whitelist."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_user_allowed(user_id):
            if update.message:
                await update.message.reply_text("⛔ 存取拒絕：您未獲得控制此 Bot 的權限。")
            elif update.callback_query:
                await update.callback_query.answer("⛔ 存取拒絕：您未獲得權限。", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)

    return wrapped


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome and help message."""
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
        "• `/aoh_stop` - 結束當前作用中的 Active Session\n"
        "• `/aoh_help` - 顯示本說明檔\n\n"
        "🛠️ *系統 CLI / ACP Agent 安裝狀態*:\n"
        f"{agent_status_text}\n\n"
        "💡 *直通 Chat 對話模式*:\n"
        "所有非 `/aoh_` 開頭的訊息與指令（如 `/commit`, `/clear` 或一般文字），皆會 100% 直通傳送至您當前活躍的 CLI Agent！"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")



@restricted
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start interactive directory browser."""
    initial_dir = ALLOWED_ROOT_DIRS[0] if ALLOWED_ROOT_DIRS else Path.cwd()
    await send_directory_browser(update, context, initial_dir)


path_registry: Dict[str, Path] = {}
path_to_token: Dict[str, str] = {}


def get_path_token(path: Path) -> str:
    """Register a Path object and return a short unique token safe for Telegram callback_data (< 64 bytes)."""
    resolved = path.expanduser().resolve()
    path_str = str(resolved)
    if path_str in path_to_token:
        return path_to_token[path_str]

    token = f"p_{len(path_registry)}"
    path_registry[token] = resolved
    path_to_token[path_str] = token
    return token


def resolve_path_token(token_or_str: str) -> Path:
    """Resolve a short token or raw path string back to Path object."""
    if token_or_str in path_registry:
        return path_registry[token_or_str]
    return Path(token_or_str).expanduser().resolve()


async def send_directory_browser(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target_dir: Path, page: int = 0
):
    """Render inline directory selector keyboard with pagination."""
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
    
    # Parent directory button
    parent_dir = target_dir.parent
    if parent_dir != target_dir and is_path_allowed(parent_dir):
        parent_token = get_path_token(parent_dir)
        keyboard.append(
            [InlineKeyboardButton("⬆️ .. (上一層)", callback_data=f"dir:nav:{parent_token}:0")]
        )

    # Subdirectories with pagination (8 items per page)
    items_per_page = 8
    subdirs = []
    try:
        subdirs = sorted([d for d in target_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
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

    # Pagination navigation row if total_pages > 1
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton("◀️ 上一頁", callback_data=f"dir:nav:{target_token}:{page - 1}")
            )
        nav_row.append(
            InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton("下一頁 ▶️", callback_data=f"dir:nav:{target_token}:{page + 1}")
            )
        keyboard.append(nav_row)

    # Action: Select current directory
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
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )


@restricted
async def directory_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle navigation & directory selection callbacks."""
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
        await send_directory_browser(update, context, target_path, page=page)
    elif data.startswith("dir:select:"):
        path_token = data[len("dir:select:"):]
        target_path = resolve_path_token(path_token)
        await show_agent_selector(query, target_path)


async def show_agent_selector(query, working_dir: Path):
    """Render CLI Agent Selector Inline Keyboard for working_dir."""
    dir_token = get_path_token(working_dir)
    installed_agents = get_installed_cli_agents()

    keyboard = []
    if installed_agents:
        for key, info in installed_agents.items():
            mode_badge = " [ACP]" if info.get("use_acp") else ""
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🚀 啟動 {info['name']}{mode_badge}",
                        callback_data=f"agent:start:{dir_token}:{key}",
                    )
                ]
            )
    else:
        text_no_agents = f"⚠️ *檢測不到任何已安裝的 CLI Agent*:\n📁 `{working_dir}`\n\n請確認系統 PATH 環境變數或安裝工具。"
        keyboard.append([InlineKeyboardButton("🔙 返回目錄選擇", callback_data=f"dir:nav:{dir_token}:0")])
        await query.edit_message_text(text_no_agents, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard.append([InlineKeyboardButton("🔙 返回目錄選擇", callback_data=f"dir:nav:{dir_token}:0")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = f"⚙️ *選擇欲在下列目錄啟動的 CLI Agent* (共 {len(installed_agents)} 個已安裝工具):\n📁 `{working_dir}`"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)



@restricted
async def agent_start_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CLI agent launch callback."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    parts = data.split(":", 3)
    if len(parts) < 4:
        return
    
    path_token, agent_key = parts[2], parts[3]
    working_dir = resolve_path_token(path_token)


    # Stop any existing streamer for user
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]

    # Create & Start session
    session = session_manager.create_session(
        user_id=user_id,
        agent_key=agent_key,
        working_dir=working_dir,
    )

    # Edit menu message to confirm selection
    await query.edit_message_text(
        f"✅ *已啟動 Session: {session.agent_name}*\n📁 `{working_dir}`\n`ID: {session.session_id}`",
        parse_mode="Markdown",
    )

    # Send direct chat status message
    chat_id = query.message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"💬 *已對接 Active Session: {session.agent_name}* (`{session.session_id}`)\n"
             f"📁 `{working_dir}`\n\n"
             f"現在可直接打字或傳送指令與 Agent 對話！",
        parse_mode="Markdown",
    )

    # Start streamer for streaming output
    streamer = create_streamer_for_session(context.bot, chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer



@restricted
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active sessions and render management menu."""
    user_id = update.effective_user.id
    sessions = session_manager.list_user_sessions(user_id)
    active_session = session_manager.get_active_session(user_id)

    if not sessions:
        await update.message.reply_text(
            "ℹ️ 當前沒有任何 Session。請使用 `/aoh_new` 建立新 Session。", parse_mode="Markdown"
        )
        return

    text = "📋 *您的 CLI Agent Session 列表*:\n\n"
    keyboard = []

    for s in sessions:
        status_icon = "🟢" if s.is_running else "🔴"
        is_active = (active_session and active_session.session_id == s.session_id)
        active_badge = " ⭐ (當前 Active)" if is_active else ""
        
        text += f"{status_icon} *ID*: `{s.session_id}` | *Agent*: {s.agent_name}{active_badge}\n"
        text += f"   📁 `{s.working_dir.name}`\n\n"

        row = []
        if not is_active:
            row.append(
                InlineKeyboardButton(
                    f"▶️ 切換至 {s.session_id}", callback_data=f"sess:switch:{s.session_id}"
                )
            )
        row.append(
            InlineKeyboardButton(
                "📄 查看 Log", callback_data=f"sess:logs:{s.session_id}"
            )
        )
        row.append(
            InlineKeyboardButton(
                "🛑 刪除", callback_data=f"sess:kill:{s.session_id}"
            )
        )
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted
async def session_action_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle session management button callbacks (switch, logs, download, kill)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    parts = data.split(":")
    action = parts[1]
    session_id = parts[2] if len(parts) > 2 else ""

    session = session_manager.get_session(session_id)

    if action == "switch":
        if not session:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        
        # If previous active session is running and user is switching away, set background completion callback
        prev_session = session_manager.get_active_session(user_id)
        if prev_session and prev_session.session_id != session_id and prev_session.is_running:
            def _make_bg_done_cb(bg_sess_id: str, bg_agent_name: str, target_chat_id: int):
                def _on_bg_done(s: Any):
                    if not bot_app:
                        return
                    logs = s.get_last_n_lines(n=10).strip()
                    summary = logs[-200:] if len(logs) > 200 else logs
                    if not summary:
                        summary = "(無文字內容)"

                    alert_text = (
                        f"✅ *{bg_agent_name} 回覆完成*\n"
                        f"🆔 Session: `{bg_sess_id}`\n\n"
                        f"📝 *回覆摘要*:\n```\n{summary}\n```"
                    )
                    reply_markup = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(
                                f"🔄 切換回 {bg_agent_name}",
                                callback_data=f"sess:switch:{bg_sess_id}"
                            )
                        ]
                    ])
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
                _make_bg_done_cb(prev_session.session_id, prev_session.agent_name, query.message.chat_id)
            )

        # Clear any background completion callback on the target session
        session.set_background_completion_callback(None)

        # Switch active session pointer
        session_manager.set_active_session(user_id, session_id)

        # Stop existing streamer
        if user_id in active_streamers:
            active_streamers[user_id].stop()
            del active_streamers[user_id]

        # Send last 100 lines log and switch confirmation
        logs = session.get_last_n_lines(n=100)
        formatted_code = format_telegram_code_block(logs, max_chars=3700)
        chat_id = query.message.chat_id

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔄 *已切換並對接至 Session: {session.agent_name}* (`{session_id}`)\n"
                 f"📁 `{session.working_dir}`\n\n"
                 f"📄 *歷史紀錄 (最後 100 行)*:\n{formatted_code}",
            parse_mode="Markdown",
        )

        # Start appropriate streamer for newly active session
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

    elif action == "kill":
        if session_manager.kill_session(session_id):
            if user_id in active_streamers and active_streamers[user_id].session.session_id == session_id:
                active_streamers[user_id].stop()
                del active_streamers[user_id]
            await query.edit_message_text(f"🛑 已成功結束 Session: `{session_id}`", parse_mode="Markdown")
        else:
            await query.message.reply_text("❌ 結束 Session 失敗或不存在。")


from .acp_streamer import ACPStreamer


async def acp_permission_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Telegram Inline Keyboard button presses for ACP tool permission requests."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    if len(parts) < 3:
        return

    action = parts[1]
    if len(parts) >= 4:
        # Format: acp_perm:action:session_id:req_id
        session_id = parts[2]
        req_id_str = parts[3]
        target_session = session_manager.get_session(session_id)
    else:
        # Fallback format: acp_perm:action:req_id
        req_id_str = parts[2]
        user_id = query.from_user.id
        target_session = session_manager.get_active_session(user_id)

    if not target_session or not hasattr(target_session, "respond_permission"):
        await query.edit_message_text("⚠️ 找不到對應的 ACP Session。")
        return

    try:
        req_id = int(req_id_str)
    except ValueError:
        req_id = req_id_str

    if action == "approve":
        await target_session.respond_permission(req_id, approved=True)
        await query.edit_message_text("✅ *已授權 Agent 執行此 Tool*", parse_mode="Markdown")
    else:
        await target_session.respond_permission(req_id, approved=False)
        await query.edit_message_text("❌ *已拒絕 Agent 執行此 Tool*", parse_mode="Markdown")


@restricted
async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send ESC (\x1b) interrupt signal to active session."""
    user_id = update.effective_user.id
    active_session = session_manager.get_active_session(user_id)
    if not active_session or not active_session.is_running:
        await update.message.reply_text("⚠️ 目前沒有作用中的 Active Session。")
        return

    active_session.send_control_char("\x1b")
    await update.message.reply_text(
        f"⏸️ 已傳送 *ESC* 中斷訊號至 `{active_session.agent_name}` (`{active_session.session_id}`)",
        parse_mode="Markdown",
    )


@restricted
async def ctrlc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send Ctrl+C (\x03) interrupt signal to active session."""
    user_id = update.effective_user.id
    active_session = session_manager.get_active_session(user_id)
    if not active_session or not active_session.is_running:
        await update.message.reply_text("⚠️ 目前沒有作用中的 Active Session。")
        return

    active_session.send_control_char("\x03")
    await update.message.reply_text(
        f"🛑 已傳送 *Ctrl+C* 中斷訊號至 `{active_session.agent_name}` (`{active_session.session_id}`)",
        parse_mode="Markdown",
    )


@restricted
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop current active session."""
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
async def text_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route ALL non-/aoh_ messages directly to current active session's PTY stdin or ACP stdio."""
    user_id = update.effective_user.id
    user_text = update.message.text
    active_session = session_manager.get_active_session(user_id)

    if not active_session or not active_session.is_running:
        reply_markup = None
        if active_session:
            rst_token = register_restart_info(active_session.agent_key, active_session.working_dir)
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 重新啟動 Agent", callback_data=f"sess_restart:{rst_token}")]
            ])

        await update.message.reply_text(
            "⚠️ *當前 Session 已離線 / 進程結束*\n請點擊下方按鈕重新啟動，或發送 `/aoh_new` 建立新 Session。",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
        return



    # Quick text shortcuts for ESC / Ctrl+C interrupt
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

    logger.info(f"Routing text message from user {user_id} to session {active_session.session_id}: '{user_text}'")

    # Direct input to CLI session PTY stdin or ACP prompt
    active_session.send_input(user_text)

    # Ensure direct chat streamer is active for user
    if user_id not in active_streamers or not active_streamers[user_id]._is_active:
        streamer = create_streamer_for_session(context.bot, update.message.chat_id, active_session)
        streamer.start()
        active_streamers[user_id] = streamer


    # Trigger Telegram top-bar typing indicator
    active_streamers[user_id].notify_user_input()




import uuid

# Registry for short restart tokens to stay within Telegram 64-byte callback_data limit
restart_registry: Dict[str, Dict[str, Any]] = {}


def register_restart_info(agent_key: str, working_dir: Path) -> str:
    """Register restart info and return a short 8-char token for Telegram callback_data."""
    token = f"r_{uuid.uuid4().hex[:8]}"
    restart_registry[token] = {
        "agent_key": agent_key,
        "working_dir": working_dir,
    }
    return token


# Global reference for bot app for async notifications
bot_app: Optional[Application] = None


def on_background_session_finished(session: Any):
    """Callback triggered when a background session exits or crashes."""
    logger.info(f"Background session finished: {session.session_id} ({session.agent_name})")
    
    # Clean up active streamer if present
    user_id = session.user_id
    if user_id in active_streamers and active_streamers[user_id].session.session_id == session.session_id:
        active_streamers[user_id].stop()
        del active_streamers[user_id]

    if not bot_app:
        return

    rst_token = register_restart_info(session.agent_key, session.working_dir)
    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 重新啟動 Agent", callback_data=f"sess_restart:{rst_token}"),
            InlineKeyboardButton("📥 下載 Session Log", callback_data=f"sess:download:{session.session_id}"),
        ]
    ])
    
    alert_text = (
        f"⚠️ *CLI Agent 進程已結束*\n\n"
        f"• *Agent*: `{session.agent_name}` (`{session.session_id}`)\n"
        f"• *工作目錄*: `{session.working_dir}`\n"
        f"• *狀態*: 離線 (Exited)\n\n"
        f"如需繼續使用，請點擊下方按鈕重新啟動。"
    )

    async def _safe_send_exit_alert():
        try:
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=alert_text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except Exception as err:
            logger.warning(f"Could not send exit notification: {err}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_safe_send_exit_alert())
    except Exception as e:
        logger.warning(f"Could not schedule exit notification: {e}")


async def session_restart_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Telegram Inline Keyboard button for restarting a crashed/stopped session."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    if len(parts) < 2:
        return

    rst_token = parts[1]
    info = restart_registry.get(rst_token)
    if not info:
        agent_key = "omp"
        working_dir = Path.cwd()
    else:
        agent_key = info["agent_key"]
        working_dir = info["working_dir"]

    user_id = query.from_user.id

    session = session_manager.create_session(
        user_id=user_id,
        agent_key=agent_key,
        working_dir=working_dir,
    )

    streamer = create_streamer_for_session(context.bot, query.message.chat_id, session)
    streamer.start()
    active_streamers[user_id] = streamer


    await query.edit_message_text(
        f"🚀 *已成功重新啟動 Agent: {session.agent_name}*\n"
        f"• Session ID: `{session.session_id}`\n"
        f"• 工作目錄: `{working_dir}`\n\n"
        f"您可以直接發送對話進行操作。",
        parse_mode="Markdown",
    )



async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log exception and notify user if error occurs."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update):
        error_msg = f"❌ *系統處理時發生錯誤*:\n`{context.error}`"
        try:
            if update.callback_query:
                await update.callback_query.message.reply_text(error_msg, parse_mode="Markdown")
            elif update.message:
                await update.message.reply_text(error_msg, parse_mode="Markdown")
        except Exception:
            pass



async def post_init(application: Application):
    """Register /aoh_ command menu in Telegram UI and send startup greeting."""
    bot_commands = [
        BotCommand("aoh_new", "📂 開啟目錄選擇器與啟動 CLI Agent"),
        BotCommand("aoh_sessions", "📋 管理與切換背景 Session"),
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

    from .config import ALLOWED_TELEGRAM_USER_IDS

    if not ALLOWED_TELEGRAM_USER_IDS:
        return
        
    greeting_text = (
        "👋 *Agents-On-Hand 直通對話服務已啟動！*\n\n"
        "所有非 `/aoh_` 開頭的訊息與指令皆會 100% 直通傳送至 CLI Agent。\n"
        "• `/aoh_new` - 選擇目錄與啟動 Agent\n"
        "• `/aoh_sessions` - 查看與管理背景 Session\n"
        "• `/aoh_esc` - 發送 ESC 中斷訊號\n"
        "• `/aoh_ctrlc` - 發送 Ctrl+C 中斷訊號\n"
        "• `/aoh_stop` - 結束 Active Session\n"
        "• `/aoh_help` - 查看說明"
    )
    for user_id in ALLOWED_TELEGRAM_USER_IDS:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=greeting_text,
                parse_mode="Markdown",
            )
            logger.info(f"Startup greeting sent to user {user_id}")
        except Exception as e:
            logger.warning(f"Could not send startup greeting to user {user_id}: {e}")


def main():
    """Start Telegram Bot application."""
    global bot_app
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 錯誤：請先在 .env 或環境變數中設定 TELEGRAM_BOT_TOKEN！")
        return

    session_manager.register_on_finished_callback(on_background_session_finished)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    bot_app = app

    # Error handler
    app.add_error_handler(global_error_handler)

    # Intercept /aoh_ system commands
    app.add_handler(CommandHandler("aoh_start", help_command))
    app.add_handler(CommandHandler("aoh_help", help_command))
    app.add_handler(CommandHandler("aoh_new", new_command))
    app.add_handler(CommandHandler("aoh_sessions", sessions_command))
    app.add_handler(CommandHandler("aoh_esc", esc_command))
    app.add_handler(CommandHandler("aoh_ctrlc", ctrlc_command))
    app.add_handler(CommandHandler("aoh_stop", stop_command))

    # Inline Keyboard Callbacks
    app.add_handler(CallbackQueryHandler(directory_callback_handler, pattern=r"^dir:"))
    app.add_handler(CallbackQueryHandler(agent_start_callback_handler, pattern=r"^agent:"))
    app.add_handler(CallbackQueryHandler(session_action_callback_handler, pattern=r"^sess:"))
    app.add_handler(CallbackQueryHandler(acp_permission_callback_handler, pattern=r"^acp_perm:"))
    app.add_handler(CallbackQueryHandler(session_restart_callback_handler, pattern=r"^sess_restart:"))

    # Route ALL other messages & commands to active CLI Agent
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, text_message_router))
    # Handle non-/aoh_ slash commands as direct input to CLI Agent
    app.add_handler(MessageHandler(filters.COMMAND, text_message_router))

    print("🚀 Agents-On-Hand (Direct Chat Mode) 啟動中...")
    app.run_polling()



if __name__ == "__main__":
    main()

