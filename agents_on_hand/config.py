import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")


def _parse_user_ids(raw: str) -> set[int]:
    raw = raw.strip()
    if not raw:
        return set()
    result: set[int] = set()
    bad: list[str] = []
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        if not s.isdigit():
            bad.append(s)
        else:
            result.add(int(s))
    if bad:
        raise ValueError(f"Invalid ALLOWED_TELEGRAM_USER_IDS entries (must be integers): {bad}")
    return result


_raw_user_ids: str = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "")
ALLOWED_TELEGRAM_USER_IDS: set[int] = _parse_user_ids(_raw_user_ids)

_raw_dev_allow = os.getenv("AOH_DEV_ALLOW_ALL_USERS", "0").strip().lower()
DEV_ALLOW_ALL: bool = _raw_dev_allow in ("1", "true", "yes")
# External (started-outside-AOH) sessions in the path-menu picker: hidden by
# default so saved/live sessions from other windows don't clutter the list.
_raw_show_ext = os.getenv("AOH_SHOW_EXTERNAL_SESSIONS", "0").strip().lower()
SHOW_EXTERNAL_SESSIONS: bool = _raw_show_ext in ("1", "true", "yes")

_raw_root_dirs: str = os.getenv("ALLOWED_ROOT_DIRS", os.getcwd())
if os.getenv("ALLOWED_ROOT_DIRS") is None:
    _raw_root_dirs = os.getcwd()
ALLOWED_ROOT_DIRS: list[Path] = [
    Path(p.strip()).expanduser().resolve() for p in _raw_root_dirs.split(",") if p.strip()
]

SESSION_LOG_DIR: Path = (
    Path(os.getenv("SESSION_LOG_DIR", "~/.agents-on-hand/sessions")).expanduser().resolve()
)

SESSION_STATE_FILE: Path = (
    Path(os.getenv("SESSION_STATE_FILE", str(SESSION_LOG_DIR.parent / "state.json")))
    .expanduser()
    .resolve()
)


def ensure_runtime_dirs() -> None:
    """Create SESSION_LOG_DIR / state parent on startup (idempotent).

    Kept out of module top-level so importing config never touches the
    filesystem — call once from the app entry point before sessions start.
    """
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


# Agent CLI lookup dirs outside PATH; globs keep nvm version bumps code-free.
# Resolved lazily — importing this module never mutates PATH.
_EXTRA_PATHS = (
    "~/.bun/bin",
    "~/.local/bin",
    "~/.kimi-code/bin",
    "~/.nvm/versions/node/*/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
    "/opt/homebrew/bin",
)

_extra_paths_applied = False


def _resolve_extra_paths() -> list[str]:
    """Expand _EXTRA_PATHS globs to existing absolute dirs, stable-ordered."""
    import glob as _glob

    out: list[str] = []
    for raw in _EXTRA_PATHS:
        pattern = str(Path(raw).expanduser())
        for match in sorted(_glob.glob(pattern)) + ([pattern] if "*" not in raw else []):
            p = Path(match)
            if p.is_dir() and str(p) not in out:
                out.append(str(p))
    return out


def runtime_env_with_extra_paths() -> dict[str, str]:
    """Return a copy of the runtime env with agent-CLI lookup dirs prepended."""
    env = dict(os.environ)
    cur = env.get("PATH", "").split(os.pathsep)
    for ep in reversed(_resolve_extra_paths()):
        if ep not in cur:
            cur.insert(0, ep)
    env["PATH"] = os.pathsep.join(cur)
    return env


def ensure_extra_paths(*, force: bool = False) -> None:
    """Prepend existing extra lookup dirs to os.environ["PATH"]."""
    global _extra_paths_applied
    if _extra_paths_applied and not force:
        return
    current = os.getenv("PATH", "").split(os.pathsep)
    for ep in reversed(_resolve_extra_paths()):
        if ep not in current:
            current.insert(0, ep)
    os.environ["PATH"] = os.pathsep.join(current)
    _extra_paths_applied = True


# All entries verified via live ACP `initialize` handshake (2026-09-07).
# load_session: True = restart restores context (session/load); False = fresh context.
AVAILABLE_CLI_AGENTS: dict[str, dict] = {
    "kimi": {
        "name": "Kimi Code",
        "command": "kimi acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "opencode": {
        "name": "OpenCode CLI",
        "command": "opencode acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "omp": {
        "name": "OMP (Oh My Pi)",
        "command": "omp acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "gemini": {
        "name": "Gemini CLI",
        "command": "gemini --acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "qwen": {
        "name": "Qwen Code",
        "command": "qwen --acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "hermes": {
        "name": "Hermes Agent",
        "command": "hermes acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "openclaw": {
        "name": "OpenClaw Gateway",
        "command": "openclaw acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
        "load_session": True,
    },
    "prime": {
        "name": "Prime Agent",
        "command": "prime-agent --mode acp",
        "drivers": ["acp", "pi_rpc", "pty"],
        "use_acp": True,
        "load_session": False,
    },
    "bash": {
        "name": "Bash Shell",
        "command": "bash",
        "drivers": ["pty"],
        "use_acp": False,
        "load_session": False,
    },
}


_installed_cache: dict | None = None
_installed_cache_ts: float = 0.0
_INSTALLED_TTL: float = 30.0


def get_installed_cli_agents(*, use_cache: bool = True) -> dict:
    global _installed_cache, _installed_cache_ts
    import time as _time

    ensure_extra_paths()
    now = _time.monotonic()
    if use_cache and _installed_cache is not None and (now - _installed_cache_ts) < _INSTALLED_TTL:
        return _installed_cache
    installed: dict = {}
    for key, info in AVAILABLE_CLI_AGENTS.items():
        cmd = info["command"]
        exec_name = cmd.split()[0]
        if shutil.which(exec_name) is not None:
            installed[key] = info
        elif key == "prime" and shutil.which("prime") is not None:
            inst = dict(info)
            inst["command"] = "prime --mode acp"
            installed[key] = inst
    _installed_cache = installed
    _installed_cache_ts = now
    return installed


def is_user_allowed(user_id: int) -> bool:
    if DEV_ALLOW_ALL:
        return True
    if not ALLOWED_TELEGRAM_USER_IDS:
        return False
    return user_id in ALLOWED_TELEGRAM_USER_IDS


def is_path_allowed(target_path: Path) -> bool:
    try:
        resolved = target_path.expanduser().resolve()
    except Exception:
        return False
    if not ALLOWED_ROOT_DIRS:
        return False
    for allowed_root in ALLOWED_ROOT_DIRS:
        try:
            resolved.relative_to(allowed_root)
            return True
        except ValueError:
            continue
    return False
