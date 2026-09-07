import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..ansi_cleaner import format_telegram_code_block
from ..callback_registry import (
    get_path_token,
    register_external_info,
    resolve_external_info,
    resolve_path_token,
)
from ..config import (
    ALLOWED_ROOT_DIRS,
    SHOW_EXTERNAL_SESSIONS,
    get_installed_cli_agents,
    is_path_allowed,
)
from ..runtime import active_streamers, create_streamer_for_session
from ..security import restricted
from ..session_manager import session_manager

logger = logging.getLogger("AgentsOnHand")


_external_prime_cache: dict[str, tuple[float, list[dict]]] = {}
_EXTERNAL_PRIME_TTL_S = 15.0


def _filter_prime_sessions(raw_sessions: object, working_dir: Path) -> list[dict]:
    """Keep live top-level prime sessions whose cwd == working_dir.

    Pure function (no subprocess) so it is easy to unit test.
    """
    if not isinstance(raw_sessions, list):
        return []
    try:
        target = working_dir.expanduser().resolve()
    except Exception:
        return []
    out: list[dict] = []
    for s in raw_sessions:
        if not isinstance(s, dict):
            continue
        if s.get("lifecycle") != "live":
            continue
        if s.get("runtimeKind") != "top-level":
            continue
        try:
            if Path(str(s.get("cwd", ""))).expanduser().resolve() != target:
                continue
        except Exception:
            continue
        out.append(s)
    out.sort(
        key=lambda s: str(s.get("modified") or s.get("lastActivityAt") or ""),
        reverse=True,
    )
    return out


async def _run_prime_list_json() -> list[dict]:
    """Run `prime-agent list --json` and return the raw session list.

    Fail-open: any error (binary missing, daemon down, timeout) → [].
    """
    import asyncio as _asyncio
    import json as _json
    import shutil as _shutil

    from ..config import ensure_extra_paths

    try:
        ensure_extra_paths()
        bin_name = _shutil.which("prime-agent") or _shutil.which("prime")
        if not bin_name:
            return []
        proc = await _asyncio.create_subprocess_exec(
            bin_name,
            "list",
            "--json",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=10.0)
        except (_asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return []
        try:
            raw = _json.loads(stdout.decode("utf-8", "replace"))
        except Exception:
            return []
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        return sessions if isinstance(sessions, list) else []
    except Exception as e:
        logger.debug(f"prime-agent list --json failed: {e}")
        return []


async def _list_external_prime_sessions(working_dir: Path) -> list[dict]:
    """Live prime-agent daemon sessions in working_dir, incl. non-AOH ones.

    Cached 15s per directory so opening the picker stays snappy
    (`prime-agent list --json` costs ~0.5s).
    """
    import time as _time

    try:
        target_key = str(working_dir.expanduser().resolve())
    except Exception:
        return []
    now = _time.monotonic()
    cached = _external_prime_cache.get(target_key)
    if cached and (now - cached[0]) < _EXTERNAL_PRIME_TTL_S:
        return cached[1]
    sessions = _filter_prime_sessions(await _run_prime_list_json(), working_dir)
    _external_prime_cache[target_key] = (now, sessions)
    return sessions


async def _find_external_prime(ext_id: str) -> dict | None:
    """Find one live daemon session by its `list --json` id (any cwd)."""
    for s in await _run_prime_list_json():
        if not isinstance(s, dict):
            continue
        if str(s.get("id", "")) != ext_id:
            continue
        if s.get("lifecycle") != "live":
            return None
        return s
    return None


_external_opencode_cache: dict[str, tuple[float, list[dict]]] = {}


def _filter_opencode_sessions(raw_sessions: object, working_dir: Path) -> list[dict]:
    """Keep opencode sessions whose directory == working_dir (newest first).

    Pure function (no subprocess) so it is easy to unit test.
    """
    if not isinstance(raw_sessions, list):
        return []
    try:
        target = working_dir.expanduser().resolve()
    except Exception:
        return []
    out: list[dict] = []
    for s in raw_sessions:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", ""))
        if not sid:
            continue
        try:
            if Path(str(s.get("directory", ""))).expanduser().resolve() != target:
                continue
        except Exception:
            continue
        out.append(s)
    out.sort(key=lambda s: int(s.get("updated") or 0), reverse=True)
    return out


async def _run_opencode_session_list(cwd: Path | None = None) -> list[dict]:
    """Run `opencode session list --format json`, fail-open → [].

    `opencode session list` scopes results to the project inferred from
    the process cwd — pass `cwd` (e.g. the working_dir whose external
    sessions we want) so the CLI returns that project's sessions instead
    of the bot's own cwd."""
    import asyncio as _asyncio
    import json as _json
    import shutil as _shutil

    from ..config import ensure_extra_paths

    try:
        ensure_extra_paths()
        bin_name = _shutil.which("opencode")
        if not bin_name:
            return []
        proc = await _asyncio.create_subprocess_exec(
            bin_name,
            "session",
            "list",
            "--format",
            "json",
            cwd=str(cwd) if cwd is not None else None,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=15.0)
        except (_asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return []
        try:
            raw = _json.loads(stdout.decode("utf-8", "replace"))
        except Exception:
            return []
        return raw if isinstance(raw, list) else []
        try:
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=15.0)
        except (_asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return []
        try:
            raw = _json.loads(stdout.decode("utf-8", "replace"))
        except Exception:
            return []
        return raw if isinstance(raw, list) else []
    except Exception as e:
        logger.debug(f"opencode session list failed: {e}")
        return []


async def _list_external_opencode_sessions(working_dir: Path) -> list[dict]:
    """Saved opencode sessions in working_dir, incl. non-AOH ones (15s cache)."""
    import time as _time

    try:
        target_key = str(working_dir.expanduser().resolve())
    except Exception:
        return []
    now = _time.monotonic()
    cached = _external_opencode_cache.get(target_key)
    if cached and (now - cached[0]) < _EXTERNAL_PRIME_TTL_S:
        return cached[1]
    sessions = _filter_opencode_sessions(await _run_opencode_session_list(working_dir), working_dir)
    _external_opencode_cache[target_key] = (now, sessions)
    return sessions


async def _find_external_opencode(ext_id: str, working_dir: Path | None = None) -> dict | None:
    """Find one opencode session by id (any directory).

    `working_dir` is forwarded to `_run_opencode_session_list` so the CLI
    lists the project that actually owns the session."""
    for s in await _run_opencode_session_list(working_dir):
        if isinstance(s, dict) and str(s.get("id", "")) == ext_id:
            return s
    return None


_external_omp_cache: dict[str, tuple[float, list[dict]]] = {}


def _omp_sessions_root() -> Path:
    """omp session storage root (~/.omp/agent/sessions)."""
    import os as _os

    return Path(_os.path.expanduser("~/.omp/agent/sessions"))


def _parse_omp_session_file(path: Path) -> dict | None:
    """Read the title/session header lines of one omp .jsonl file."""
    import json as _json

    try:
        title = ""
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = _json.loads(line)
                except Exception:
                    continue
                if not isinstance(o, dict):
                    continue
                if o.get("type") == "title" and not title:
                    title = str(o.get("title", "") or "")
                elif o.get("type") == "session":
                    sid = str(o.get("id", "") or "")
                    if not sid:
                        return None
                    return {
                        "id": sid,
                        "title": title or str(o.get("title", "") or ""),
                        "cwd": str(o.get("cwd", "") or ""),
                        "timestamp": str(o.get("timestamp", "") or ""),
                    }
    except Exception:
        return None
    return None


def _scan_omp_sessions(working_dir: Path, limit: int = 8) -> list[dict]:
    """Scan omp session files for working_dir (newest files first).

    Sync filesystem scan (no subprocess); pure enough to unit test with
    a fake sessions root via _OMP_ROOT_OVERRIDE.
    """
    import os as _os

    root = Path(_os.environ.get("AOH_OMP_SESSIONS_ROOT", str(_omp_sessions_root())))
    try:
        target = working_dir.expanduser().resolve()
    except Exception:
        return []
    if not root.is_dir():
        return []
    # Candidate dirs: every project dir (cheap: names only), newest files
    # first by mtime so recent sessions are never buried by old ones.
    candidates: list[Path] = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except Exception:
        return []
    for d in dirs:
        try:
            files = sorted(
                [f for f in d.iterdir() if f.is_file() and f.suffix == ".jsonl"],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            continue
        candidates.extend(files[:limit])
    out: list[dict] = []
    seen: set[str] = set()
    for f in candidates:
        info = _parse_omp_session_file(f)
        if not info or info["id"] in seen:
            continue
        seen.add(info["id"])
        try:
            if Path(info["cwd"]).expanduser().resolve() != target:
                continue
        except Exception:
            continue
        out.append(info)
    out.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    return out[:limit]


async def _list_external_omp_sessions(working_dir: Path) -> list[dict]:
    """Saved omp sessions in working_dir, incl. non-AOH ones (15s cache)."""
    import asyncio as _asyncio
    import time as _time

    try:
        target_key = str(working_dir.expanduser().resolve())
    except Exception:
        return []
    now = _time.monotonic()
    cached = _external_omp_cache.get(target_key)
    if cached and (now - cached[0]) < _EXTERNAL_PRIME_TTL_S:
        return cached[1]
    sessions = await _asyncio.to_thread(_scan_omp_sessions, working_dir)
    _external_omp_cache[target_key] = (now, sessions)
    return sessions


async def _find_external_omp(ext_id: str) -> dict | None:
    """Find one omp session by id (any directory)."""
    import asyncio as _asyncio
    import os as _os

    root = Path(_os.environ.get("AOH_OMP_SESSIONS_ROOT", str(_omp_sessions_root())))
    if not root.is_dir():
        return None
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except Exception:
        return []
    for d in dirs:
        try:
            files = list(d.iterdir())
        except Exception:
            continue
        for f in files:
            if not (f.is_file() and f.suffix == ".jsonl" and ext_id in f.name):
                continue
            info = await _asyncio.to_thread(_parse_omp_session_file, f)
            if info and info["id"] == ext_id:
                return info
    return None


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
    """Start flow: directory browser → agent picker → launch.

    Shows the directory browser first so the user can confirm or change the
    working directory before selecting an agent.
    """
    initial_dir = ALLOWED_ROOT_DIRS[0] if ALLOWED_ROOT_DIRS else Path.cwd()
    initial_dir = initial_dir.expanduser().resolve()
    await send_directory_browser(update, context, initial_dir, page=0)


async def _send_agent_picker_new_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, working_dir: Path
) -> None:
    """Agent picker as a fresh message (for /aoh_new entry)."""
    try:
        picker_user_id = update.effective_user.id
    except Exception:
        picker_user_id = None
    reply_markup = _build_agent_picker_keyboard(working_dir, user_id=picker_user_id)
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


def _dir_agent_counts(user_id: int | None, working_dir: Path) -> dict[str, int]:
    """Count running sessions per agent key for user + directory (badge display)."""
    if user_id is None:
        return {}
    try:
        target = working_dir.expanduser().resolve()
    except Exception:
        return {}
    counts: dict[str, int] = {}
    try:
        sessions = session_manager.list_user_sessions(user_id)
    except Exception:
        return {}
    for s in sessions:
        if not s.is_running:
            continue
        try:
            if Path(s.working_dir).expanduser().resolve() != target:
                continue
        except Exception:
            continue
        counts[s.agent_key] = counts.get(s.agent_key, 0) + 1
    return counts


def _build_agent_picker_keyboard(
    working_dir: Path, user_id: int | None = None
) -> InlineKeyboardMarkup | None:
    """Shared compact agent picker: one row per agent + a 📂 directory row.

    When user_id is given, each agent row shows a `· N運作中` badge so the
    user sees at a glance which agents already run in this directory.
    Returns None when no agents are installed (callers render the warning).
    """
    dir_token = get_path_token(working_dir)
    installed_agents = get_installed_cli_agents()
    if not installed_agents:
        return None
    counts = _dir_agent_counts(user_id, working_dir)
    keyboard: list[list[InlineKeyboardButton]] = []
    for key, info in installed_agents.items():
        mode_badge = " [ACP]" if info.get("use_acp") else ""
        count_badge = f" · {counts[key]}運作中" if counts.get(key) else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🚀 {info['name']}{mode_badge}{count_badge}",
                    callback_data=f"agent:start:{dir_token}:{key}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(f"📂 {working_dir.name}", callback_data=f"dir:nav:{dir_token}:0")]
    )
    return InlineKeyboardMarkup(keyboard)


async def show_agent_selector(query, working_dir: Path) -> None:  # type: ignore[no-untyped-def]
    try:
        picker_user_id = query.from_user.id
    except Exception:
        picker_user_id = None
    reply_markup = _build_agent_picker_keyboard(working_dir, user_id=picker_user_id)
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


def _age_str(created_at: float) -> str:
    """Compact age label (e.g. 5m, 2h, 3d) for instance rows."""
    try:
        import time as _time

        secs = max(0, int(_time.time() - created_at))
    except Exception:
        return ""
    if secs < 60:
        return f"{secs}s前"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m前"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h前"
    return f"{hours // 24}d前"


async def _show_instance_picker(  # type: ignore[no-untyped-def]
    query, context, user_id: int, agent_key: str, agent_name: str, working_dir: Path
) -> None:
    """List running instances of agent_key in working_dir: attach or start new."""
    from ..config import AVAILABLE_CLI_AGENTS

    instances = session_manager.find_dir_agent_sessions(
        user_id=user_id, agent_key=agent_key, working_dir=working_dir
    )
    # External sessions (started outside AOH), shown as 🟣 rows:
    # - prime: shares one daemon, `prime-agent list --json` sees them.
    #   Tapping one starts an AOH session in the same cwd (daemon
    #   auto-attaches the live session).
    # - opencode: `opencode session list --format json` filtered by
    #   directory. Tapping one resumes it via ACP `session/load`.
    externals: list[dict] = []
    ext_kind = ""
    # 🟣 External (started-outside-AOH) sessions are hidden unless
    # AOH_SHOW_EXTERNAL_SESSIONS=1 — keeps the picker aoh-only by default.
    if SHOW_EXTERNAL_SESSIONS:
        if agent_key == "prime":
            try:
                externals = await _list_external_prime_sessions(working_dir)
                ext_kind = "prime"
            except Exception as e:
                logger.debug(f"external prime list failed: {e}")
                externals = []
        elif agent_key == "opencode":
            try:
                externals = await _list_external_opencode_sessions(working_dir)
                ext_kind = "opencode"
            except Exception as e:
                logger.debug(f"external opencode list failed: {e}")
                externals = []
        elif agent_key == "omp":
            try:
                externals = await _list_external_omp_sessions(working_dir)
                ext_kind = "omp"
            except Exception as e:
                logger.debug(f"external omp list failed: {e}")
                externals = []
    try:
        info = AVAILABLE_CLI_AGENTS.get(agent_key, {})
        agent_name = str(info.get("name", agent_name))
    except Exception:
        pass
    active = None
    try:
        active = session_manager.get_active_session(user_id)
    except Exception:
        active = None
    active_id = getattr(active, "session_id", None)
    dir_token = get_path_token(working_dir)
    total = len(instances) + len(externals)
    # externals for opencode/omp are saved sessions (may be idle);
    # prime externals are live daemon sessions.
    saved_note = (
        "（🟣 為外部歷史 session，點選即接回）"
        if externals and ext_kind in ("opencode", "omp")
        else ""
    )
    if total:
        lines = [
            f"🤖 *{agent_name}* · 📁 `{working_dir}`",
            f"共 {total} 個可沿用 — 選一個，或開新的：{saved_note}",
            "",
        ]
    else:
        lines = [
            f"🤖 *{agent_name}* · 📁 `{working_dir}`",
            "此目錄尚無運作中的 session — 可直接開新的：",
            "",
        ]
    keyboard: list[list[InlineKeyboardButton]] = []
    for s in instances:
        short_id = s.session_id.removeprefix("sess_")
        star = "⭐ " if s.session_id == active_id else ""
        age = _age_str(getattr(s, "created_at", 0) or 0)
        last = (getattr(s, "last_user_prompt", "") or "").strip().replace("\n", " ")
        if len(last) > 24:
            last = last[:24] + "…"
        label = f"🔁 {star}{short_id} · {age}".strip()
        lines.append(f"• {star}`{short_id}` · {age}" + (f" · {last}" if last else ""))
        keyboard.append([InlineKeyboardButton(label, callback_data=f"agent:reuse:{s.session_id}")])
    for ext in externals:
        ext_id = str(ext.get("id", ""))
        if not ext_id:
            continue
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
            lines.append(detail)
        else:
            first = str(ext.get("firstMessage", "") or "").strip().replace("\n", " ")
            if len(first) > 22:
                first = first[:22] + "…"
            activity = str(ext.get("activity", "") or "")
            act_badge = f" · {activity}" if activity and activity != "working" else ""
            name = str(ext.get("sessionName", "") or "")
            name_badge = f" · {name}" if name else ""
            label = f"🟣 {ext_id[:8]}{act_badge}{name_badge}"
            if first:
                label = f"{label} · {first}"
                lines.append(f"• 🟣 `{ext_id[:8]}`{act_badge}{name_badge} · {first}")
            else:
                lines.append(f"• 🟣 `{ext_id[:8]}`{act_badge}{name_badge}")
        # Telegram callback_data <= 64 bytes: never embed raw ext_id
        # (opencode 30 chars / prime UUID 36 chars overflow). Short token instead.
        ext_token = register_external_info(ext_id, agent_key, working_dir)
        keyboard.append(
            [
                InlineKeyboardButton(
                    label[:64],
                    callback_data=f"agent:attach_ext:{ext_token}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🆕 新增{agent_name}", callback_data=f"agent:force_new:{dir_token}:{agent_key}"
            )
        ]
    )
    keyboard.append(
        [InlineKeyboardButton("⬅️ 返回選 Agent", callback_data=f"agent:back:{dir_token}")]
    )
    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def agent_back_callback_handler(update, context):  # type: ignore[no-untyped-def]
    """Back button from the instance picker → agent type picker."""
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":", 2)
    if len(parts) < 3:
        return
    working_dir = resolve_path_token(parts[2])
    if working_dir is None or not is_path_allowed(working_dir):
        await query.edit_message_text(
            "⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown"
        )
        return
    await show_agent_selector(query, working_dir)


async def agent_start_callback_handler(update, context):  # type: ignore[no-untyped-def]
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    parts = data.split(":", 3)
    if len(parts) < 3:
        return
    subaction = parts[1]
    if subaction == "force_new":
        # User explicitly chose a fresh session from the reuse prompt.
        if len(parts) < 4:
            return
        path_token, agent_key = parts[2], parts[3]
        working_dir = resolve_path_token(path_token)
        if working_dir is None or not is_path_allowed(working_dir):
            await query.edit_message_text(
                "⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown"
            )
            return
        await _launch_new_session(query, context, user_id, agent_key, working_dir)
        return
    if subaction == "attach_ext":
        # External session (started outside AOH):
        # - prime: start an AOH session in the same cwd — prime's daemon
        #   auto-attaches the live session, so chat continues where it left off.
        # - opencode: resume the saved session via ACP `session/load`.
        # New callback: agent:attach_ext:<ext_token> (short registry token,
        # keeps callback_data <= 64 bytes).
        # Legacy callback: agent:attach_ext:<dir_token>:<agent_key>:<ext_id>
        # (ext_id may itself contain ":", so split with maxsplit=4.)
        ext_agent = ""
        ext_id = ""
        working_dir = None
        legacy_parts = data.split(":", 4)
        if len(legacy_parts) >= 5 and resolve_path_token(legacy_parts[2]) is not None:
            _, _, path_token, ext_agent, ext_id = legacy_parts
            working_dir = resolve_path_token(path_token)
        else:
            token_parts = data.split(":", 2)
            if len(token_parts) < 3:
                return
            info = resolve_external_info(token_parts[2])
            if info is None:
                await query.edit_message_text(
                    "⚠️ 該外部 session 按鈕已過期，請重新開啟選單再試。",
                    parse_mode="Markdown",
                )
                return
            ext_agent = str(info.get("agent_key", ""))
            ext_id = str(info.get("ext_id", ""))
            working_dir = info.get("working_dir")
        if working_dir is None or not is_path_allowed(working_dir):
            await query.edit_message_text(
                "⛔ *無法啟動*：工作目錄無效或超出允許範圍。", parse_mode="Markdown"
            )
            return
        if ext_agent in ("opencode", "omp"):
            if ext_agent == "opencode":
                ext = await _find_external_opencode(ext_id, working_dir)
            else:
                ext = await _find_external_omp(ext_id)
            if ext is None:
                await query.edit_message_text(
                    f"⚠️ 該外部 {ext_agent} session 已不存在，請改用 🆕 新增。",
                    parse_mode="Markdown",
                )
                return
            title = str(ext.get("title", "") or "").strip().replace("\n", " ")
            short = ext_id[4:16] if ext_id.startswith("ses_") else ext_id[:8]
            note = f"（外部 `{short}`" + (f" · {title[:40]}" if title else "") + "）"
            await _launch_resumed_session(
                query, context, user_id, ext_agent, working_dir, ext_id, note=note
            )
            return
        ext = await _find_external_prime(ext_id)
        if ext is None:
            await query.edit_message_text(
                "⚠️ 該外部 prime-agent 已結束，請改用 🆕 新增。",
                parse_mode="Markdown",
            )
            return
        first = str(ext.get("firstMessage", "") or "").strip().replace("\n", " ")
        note = f"（外部 `{ext_id[:8]}`" + (f" · {first[:40]}" if first else "") + "）"
        await _launch_new_session(query, context, user_id, "prime", working_dir, note=note)
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
    from ..config import AVAILABLE_CLI_AGENTS

    agent_name = str(AVAILABLE_CLI_AGENTS.get(agent_key, {}).get("name", agent_key))
    # Dir-agent instance picker: always list every running instance in this
    # directory (even zero) so the user picks attach-vs-new explicitly.
    await _show_instance_picker(query, context, user_id, agent_key, agent_name, working_dir)


async def _launch_new_session(
    query, context, user_id: int, agent_key: str, working_dir: Path, note: str = ""
) -> None:  # type: ignore[no-untyped-def]
    """Shared create-and-attach flow (agent:start: + agent:force_new:)."""
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    session = session_manager.create_session(
        user_id=user_id, agent_key=agent_key, working_dir=working_dir
    )
    suffix = f"\n{note}" if note else ""
    await query.edit_message_text(
        f"✅ *已啟動 Session: {session.agent_name}*\n📁 `{working_dir}`\n`ID: {session.session_id}`{suffix}",
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


async def _launch_resumed_session(
    query, context, user_id: int, agent_key: str, working_dir: Path, ext_id: str, note: str = ""
) -> None:  # type: ignore[no-untyped-def]
    """Start an AOH session resumed from an external ACP session id."""
    if user_id in active_streamers:
        active_streamers[user_id].stop()
        del active_streamers[user_id]
    session = session_manager.create_session(
        user_id=user_id,
        agent_key=agent_key,
        working_dir=working_dir,
        resume_acp_session_id=ext_id,
    )
    suffix = f"\n{note}" if note else ""
    await query.edit_message_text(
        f"✅ *已接回外部 Session: {session.agent_name}*\n📁 `{working_dir}`\n`ID: {session.session_id}`{suffix}",
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
