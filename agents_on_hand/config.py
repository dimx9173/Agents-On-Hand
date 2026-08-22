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

_raw_root_dirs: str = os.getenv("ALLOWED_ROOT_DIRS", os.getcwd())
if os.getenv("ALLOWED_ROOT_DIRS") is None:
    _raw_root_dirs = os.getcwd()
ALLOWED_ROOT_DIRS: list[Path] = [
    Path(p.strip()).expanduser().resolve() for p in _raw_root_dirs.split(",") if p.strip()
]

SESSION_LOG_DIR: Path = Path(os.getenv("SESSION_LOG_DIR", "~/.agents-on-hand/sessions")).expanduser().resolve()
SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)

SESSION_STATE_FILE: Path = Path(os.getenv("SESSION_STATE_FILE", str(SESSION_LOG_DIR.parent / "state.json"))).expanduser().resolve()
SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

extra_paths = [
    Path("~/.bun/bin").expanduser(),
    Path("~/.local/bin").expanduser(),
    Path("~/.nvm/versions/node/v24.14.0/bin").expanduser(),
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
]
current_path_dirs = os.getenv("PATH", "").split(os.pathsep)
for ep in extra_paths:
    if ep.exists() and str(ep) not in current_path_dirs:
        current_path_dirs.insert(0, str(ep))
os.environ["PATH"] = os.pathsep.join(current_path_dirs)


AVAILABLE_CLI_AGENTS = {
    "claude": {"name": "Claude Code", "command": "claude", "drivers": ["claude_stream", "pty"], "use_acp": False},
    "codex": {"name": "Codex CLI", "command": "codex", "drivers": ["pty"], "use_acp": False},
    "pi": {"name": "Pi Agent", "command": "pi", "drivers": ["pi_rpc", "pty"], "use_acp": False},
    "omp": {"name": "OMP (Oh My Pi)", "command": "omp acp", "drivers": ["acp", "pty"], "use_acp": True},
    "opencode": {"name": "OpenCode CLI", "command": "opencode acp", "drivers": ["acp", "pty"], "use_acp": True},
    "bash": {"name": "Bash Shell", "command": "bash", "drivers": ["pty"], "use_acp": False},
}


_installed_cache: dict | None = None
_installed_cache_ts: float = 0.0
_INSTALLED_TTL: float = 30.0


def get_installed_cli_agents(*, use_cache: bool = True) -> dict:
    global _installed_cache, _installed_cache_ts
    import time as _time

    now = _time.monotonic()
    if use_cache and _installed_cache is not None and (now - _installed_cache_ts) < _INSTALLED_TTL:
        return _installed_cache
    installed: dict = {}
    for key, info in AVAILABLE_CLI_AGENTS.items():
        cmd = info["command"]
        exec_name = cmd.split()[0]
        if shutil.which(exec_name) is not None:
            installed[key] = info
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
