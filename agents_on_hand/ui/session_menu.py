import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..agent_session_cleaner import PurgeResult, purge_agent_session
from ..ansi_cleaner import format_telegram_code_block
from ..callback_registry import get_path_token, register_external_info, resolve_external_info
from ..runtime import active_streamers, bot_app, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager
from ..ui.directory_browser import (
    _list_external_omp_sessions,
    _list_external_opencode_sessions,
    _list_external_prime_sessions,
)

if TYPE_CHECKING:
    from ..session_manager import AgentSession
logger = logging.getLogger("AgentsOnHand")


def _purge_report(purge: PurgeResult) -> str:
    """One-line agent-side purge summary for Telegram replies."""
    icon = "✅" if purge.ok else "⚠️"
    targets = f" ({len(purge.targets)} 個目標)" if purge.targets else ""
    return f"agent 端（{purge.method}）: {icon} {purge.detail}{targets}"


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
    Line 2 (secondary): log + stop + delete. 🛑 刪除 stops the process;
    🗑️ 刪除 permanently removes the session on BOTH sides
    (aoh record + agent-side store via agent command).

    - Active + running  -> [⭐ <label>] / [📄 Log][🛑 刪除][🗑️ 刪除]
    - Running (bg)      -> [▶️ <label>] / [📄 Log][🛑 刪除][🗑️ 刪除]
    - Offline           -> [🔄 <label>] / [📄 Log][🛑 刪除][🗑️ 刪除]
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
        InlineKeyboardButton("🗑️ 刪除", callback_data=f"sess:del_confirm:{s.session_id}"),
    ]
    return [[primary], secondary]


def _agent_display_name(agent_key: str) -> str:
    """Display name for an agent key, falling back to the key itself."""
    from ..config import AVAILABLE_CLI_AGENTS

    return str(AVAILABLE_CLI_AGENTS.get(agent_key, {}).get("name", agent_key))


def _build_agent_list_view(
    user_id: int, note: str = "",
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Step-1 view: one button per distinct agent_key with a running/total badge.

    Returns (text, markup); markup is None when there are no sessions at all —
    callers then show the plain empty-state message.
    """
    sessions = session_manager.list_user_sessions(user_id)
    if not sessions:
        return ("ℹ️ 當前沒有任何 Session。請使用 `/aoh_new` 建立新 Session。", None)
    order: list[str] = []
    grouped: dict[str, list[AgentSession]] = {}
    for s in sessions:
        if s.agent_key not in grouped:
            grouped[s.agent_key] = []
            order.append(s.agent_key)
        grouped[s.agent_key].append(s)
    lines: list[str] = []
    if note:
        lines.append(note)
    lines.append("🎛 *管理 Session* — 選擇 Agent：")
    lines.append("")
    keyboard: list[list[InlineKeyboardButton]] = []
    has_offline = False
    for agent_key in order:
        group = grouped[agent_key]
        running = sum(1 for s in group if s.is_running)
        if running < len(group):
            has_offline = True
        name = _agent_display_name(agent_key)
        badge = f"🟢{running}/{len(group)}"
        lines.append(f"• {name} · {badge}")
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🤖 {name} · {badge}", callback_data=f"sess:agent:{agent_key}"
                )
            ]
        )
    if has_offline:
        keyboard.append([InlineKeyboardButton("🧹 清理離線", callback_data="sess:prune_offline")])
    return ("\n".join(lines), InlineKeyboardMarkup(keyboard))


async def _list_external_sessions(
    agent_key: str, agent_sessions: list["AgentSession"],
) -> list[tuple[Path, dict]]:
    """External (non-AOH) running sessions across the agent's working dirs."""
    fns: dict[str, Any] = {
        "opencode": _list_external_opencode_sessions,
        "omp": _list_external_omp_sessions,
        "prime": _list_external_prime_sessions,
    }
    list_fn = fns.get(agent_key)
    if list_fn is None:
        return []
    dirs: list[Path] = []
    seen_dirs: set[str] = set()
    for s in agent_sessions:
        key = str(s.working_dir)
        if key not in seen_dirs:
            seen_dirs.add(key)
            dirs.append(s.working_dir)
    out: list[tuple[Path, dict]] = []
    for working_dir in dirs:
        try:
            found = await list_fn(working_dir)
        except Exception as e:
            logger.debug(f"external {agent_key} session list failed: {e}")
            found = []
        for ext in found:
            if ext.get("id"):
                out.append((working_dir, ext))
    return out


def _external_row(ext_kind: str, ext: dict) -> tuple[str, str] | None:
    """Button label + text detail for one external session (mirrors _show_instance_picker)."""
    ext_id = str(ext.get("id", ""))
    if not ext_id:
        return None
    if ext_kind in ("opencode", "omp"):
        title = str(ext.get("title", "") or "").strip().replace("\n", " ")
        if len(title) > 24:
            title = title[:24] + "…"
        short = ext_id[4:12] if ext_id.startswith("ses_") else ext_id[:8]
        label = f"🟣 {short}"
        detail = f"• 🟣 `{short}`"
        if title and not title.startswith("New session"):
            label = f"{label} · {title}"
            detail = f"{detail} · {title}"
        return label, detail
    first = str(ext.get("firstMessage", "") or "").strip().replace("\n", " ")
    if len(first) > 22:
        first = first[:22] + "…"
    activity = str(ext.get("activity", "") or "")
    act_badge = f" · {activity}" if activity and activity != "working" else ""
    name = str(ext.get("sessionName", "") or "")
    name_badge = f" · {name}" if name else ""
    label = f"🟣 {ext_id[:8]}{act_badge}{name_badge}"
    detail = f"• 🟣 `{ext_id[:8]}`{act_badge}{name_badge}"
    if first:
        label = f"{label} · {first}"
        detail = f"{detail} · {first}"
    return label, detail


async def _build_agent_sessions_view(
    user_id: int, agent_key: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Step-2 view: one agent's AOH sessions + external running sessions."""
    sessions = session_manager.list_user_sessions(user_id)
    agent_sessions = [s for s in sessions if s.agent_key == agent_key]
    active_session = session_manager.get_active_session(user_id)
    agent_name = _agent_display_name(agent_key)
    ordered = [s for s in agent_sessions if s.is_running] + [
        s for s in agent_sessions if not s.is_running
    ]
    externals = await _list_external_sessions(agent_key, agent_sessions)
    lines = [f"🤖 *{agent_name}* · Sessions：", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    for s in ordered:
        lines.append(_session_label(s, active_session))
        keyboard.extend(_build_session_rows(s, active_session))
    if externals:
        lines.append("（🟣 為外部 session，點選即接回）")
        for working_dir, ext in externals:
            row = _external_row(agent_key, ext)
            if row is None:
                continue
            label, detail = row
            lines.append(detail)
            # Telegram callback_data <= 64 bytes: short registry token, not raw id.
            ext_token = register_external_info(str(ext.get("id", "")), agent_key, working_dir)
            keyboard.append(
                [
                    InlineKeyboardButton(
                        label[:58],
                        callback_data=(f"agent:attach_ext:{ext_token}"),
                    ),
                    InlineKeyboardButton(
                        "🗑️",
                        callback_data=(f"sess:delete_ext:{ext_token}"),
                    ),
                ]
            )
    has_offline = any(not s.is_running for s in agent_sessions)
    if has_offline:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🧹 清理離線", callback_data=f"sess:prune_offline:{agent_key}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ 返回 Agent 列表", callback_data="sess:back")])
    return ("\n".join(lines), InlineKeyboardMarkup(keyboard))


@restricted
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text, markup = _build_agent_list_view(user_id)
    if markup is None:
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


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
        prune_agent = parts[2] if len(parts) > 2 else ""
        pruned_count = session_manager.prune_offline_sessions(user_id)
        if pruned_count > 0:
            remaining = session_manager.list_user_sessions(user_id)
            if not remaining:
                await query.edit_message_text(
                    f"🧹 *已成功清理 {pruned_count} 個離線 Session！*\n\n當前已無任何 Session，可使用 `/aoh_new` 建立新 Session。",
                    parse_mode="Markdown",
                )
            elif prune_agent:
                text, markup = await _build_agent_sessions_view(user_id, prune_agent)
                await query.edit_message_text(
                    text, parse_mode="Markdown", reply_markup=markup
                )
            else:
                list_text, list_markup = _build_agent_list_view(
                    user_id, note=f"🧹 *已清理 {pruned_count} 個！*"
                )
                await query.edit_message_text(
                    list_text, parse_mode="Markdown", reply_markup=list_markup
                )
        else:
            await query.answer("ℹ️ 目前沒有任何離線 Session 需要清理。", show_alert=True)
        return

    if action == "agent":
        # Step 2: sessions for the chosen agent (AOH + external running ones).
        text, markup = await _build_agent_sessions_view(user_id, session_id)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        return

    if action == "back":
        list_text, list_markup = _build_agent_list_view(user_id)
        if list_markup is None:
            await query.edit_message_text(list_text, parse_mode="Markdown")
        else:
            await query.edit_message_text(
                list_text, parse_mode="Markdown", reply_markup=list_markup
            )
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

    elif action == "del_confirm":
        sess = session_manager.get_session(session_id)
        if not sess:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        await query.edit_message_text(
            f"⚠️ *確定永久刪除？*\n\n`{session_id}` · {sess.agent_name} · `{sess.working_dir}`\n"
            "同時清除 aoh 記錄與 agent 端 session（皆透過指令執行），無法復原。",

            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🗑️ 確認刪除", callback_data=f"sess:delete:{session_id}"),
                        InlineKeyboardButton("↩️ 取消", callback_data="sess:back"),
                    ]
                ]
            ),
        )
        return

    elif action == "delete":
        removed, purge = await session_manager.delete_session(user_id, session_id)
        if not removed:
            await query.message.reply_text("❌ 該 Session 已不存在。")
            return
        if (
            user_id in active_streamers
            and active_streamers[user_id].session.session_id == session_id
        ):
            active_streamers[user_id].stop()
            del active_streamers[user_id]
        await query.edit_message_text(
            f"🗑️ 已刪除 Session: `{session_id}`\n" + _purge_report(purge),
            parse_mode="Markdown",
        )
        return

    elif action == "delete_ext":
        info = resolve_external_info(session_id)
        if not info:
            await query.answer("⚠️ 該外部 session 記錄已過期，請重新開啟選單。", show_alert=True)
            return
        ext_id, agent_key = str(info.get("ext_id", "")), str(info.get("agent_key", ""))
        working_dir = info.get("working_dir")
        if not ext_id:
            await query.message.reply_text("❌ 外部 session id 無效。")
            return
        purge = await purge_agent_session(
            agent_key,
            external_id=ext_id,
            working_dir=working_dir if isinstance(working_dir, Path) else None,
        )
        await query.edit_message_text(
            f"🗑️ 外部 session `{ext_id[:12]}`（{agent_key}）\n" + _purge_report(purge),
            parse_mode="Markdown",
        )
