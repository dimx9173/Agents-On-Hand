import os
from pathlib import Path
from typing import List, Set
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Allowed Telegram User IDs (security whitelist)
_raw_user_ids: str = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "")
ALLOWED_TELEGRAM_USER_IDS: Set[int] = {
    int(uid.strip()) for uid in _raw_user_ids.split(",") if uid.strip().isdigit()
}

# Allowed root directories for directory browser
_raw_root_dirs: str = os.getenv("ALLOWED_ROOT_DIRS", os.getcwd())
ALLOWED_ROOT_DIRS: List[Path] = [
    Path(p.strip()).expanduser().resolve()
    for p in _raw_root_dirs.split(",")
    if p.strip()
]

# Session log directory
SESSION_LOG_DIR: Path = Path(
    os.getenv("SESSION_LOG_DIR", "~/.agents-on-hand/sessions")
).expanduser().resolve()
SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Ensure PATH includes common user binary paths (~/.bun/bin, ~/.local/bin, etc.)
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

import shutil

# Preset CLI agent commands
AVAILABLE_CLI_AGENTS = {
    "claude": {
        "name": "Claude Code",
        "command": "claude",
        "drivers": ["claude_stream", "pty"],
        "use_acp": False,
    },
    "codex": {
        "name": "Codex CLI",
        "command": "codex",
        "drivers": ["pty"],
        "use_acp": False,
    },
    "pi": {
        "name": "Pi Agent",
        "command": "pi",
        "drivers": ["pi_rpc", "pty"],
        "use_acp": False,
    },
    "omp": {
        "name": "OMP (Oh My Pi)",
        "command": "omp acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
    },
    "opencode": {
        "name": "OpenCode CLI",
        "command": "opencode acp",
        "drivers": ["acp", "pty"],
        "use_acp": True,
    },
    "bash": {
        "name": "Bash Shell",
        "command": "bash",
        "drivers": ["pty"],
        "use_acp": False,
    },
}





def get_installed_cli_agents() -> dict:
    """Dynamically scan system PATH and return only installed CLI agents."""
    installed = {}
    for key, info in AVAILABLE_CLI_AGENTS.items():
        cmd = info["command"]
        exec_name = cmd.split()[0]
        if shutil.which(exec_name) is not None:
            installed[key] = info
    return installed





def is_user_allowed(user_id: int) -> bool:
    """Check if a Telegram user ID is authorized."""
    if not ALLOWED_TELEGRAM_USER_IDS:
        # If no user IDs specified in env, allow all for dev (with warning)
        return True
    return user_id in ALLOWED_TELEGRAM_USER_IDS


def is_path_allowed(target_path: Path) -> bool:
    """Check if target_path is within any of the ALLOWED_ROOT_DIRS."""
    try:
        resolved = target_path.expanduser().resolve()
    except Exception:
        return False

    if not ALLOWED_ROOT_DIRS:
        return True

    for allowed_root in ALLOWED_ROOT_DIRS:
        try:
            resolved.relative_to(allowed_root)
            return True
        except ValueError:
            continue
    return False
