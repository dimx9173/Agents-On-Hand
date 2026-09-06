"""Callback data registries for Telegram 64-byte limit.

Both path tokens and restart tokens solve the same problem:
Telegram callback_data is limited to 64 bytes, so we store
full Path / restart info server-side and expose short tokens.
"""

import uuid
from pathlib import Path
from typing import Any

# Path token registry
path_registry: dict[str, Path] = {}
path_to_token: dict[str, str] = {}


_MAX_PATH_TOKENS: int = 800
_MAX_RESTART_TOKENS: int = 500


def get_path_token(path: Path) -> str:
    """Register a Path and return a short token safe for callback_data (<64 bytes)."""
    resolved = path.expanduser().resolve()
    path_str = str(resolved)
    if path_str in path_to_token:
        return path_to_token[path_str]
    if len(path_registry) >= _MAX_PATH_TOKENS:
        oldest = next(iter(path_registry))
        old_path = str(path_registry.pop(oldest))
        path_to_token.pop(old_path, None)
    token = f"p_{len(path_registry)}_{uuid.uuid4().hex[:4]}"
    while token in path_registry:
        token = f"p_{len(path_registry)}_{uuid.uuid4().hex[:4]}"
    path_registry[token] = resolved
    path_to_token[path_str] = token
    return token


def resolve_path_token(token_or_str: str) -> Path | None:
    """Resolve a short token back to Path. Returns None for unknown/expired tokens (fail closed)."""
    if token_or_str in path_registry:
        return path_registry[token_or_str]
    return None


# External session token registry
# Telegram callback_data <= 64 bytes, but opencode ext ids (30 chars) and
# prime sub-session UUIDs (36 chars) overflow when embedded raw:
#   agent:attach_ext:<dir_token>:<agent_key>:<ext_id>  -> 65-68 bytes
# So we store (ext_id, agent_key, working_dir) server-side and expose e_xxxxxxxx.
external_registry: dict[str, dict[str, Any]] = {}


# Restart token registry
restart_registry: dict[str, dict[str, Any]] = {}


def register_external_info(ext_id: str, agent_key: str, working_dir: Path) -> str:
    """Register an external session id and return a short token.

    Keeps ``agent:attach_ext:<token>`` under the 64-byte Telegram limit
    regardless of raw ext_id length (opencode 30 chars, prime UUID 36 chars).
    Re-registering the same (ext_id, agent_key, cwd) returns the same token.
    """
    try:
        wd = working_dir.expanduser().resolve()
    except Exception:
        wd = working_dir
    wd_str = str(wd)
    for token, info in external_registry.items():
        if (
            info.get("ext_id") == ext_id
            and info.get("agent_key") == agent_key
            and str(info.get("working_dir")) == wd_str
        ):
            return token
    if len(external_registry) >= _MAX_RESTART_TOKENS:
        oldest = next(iter(external_registry))
        external_registry.pop(oldest, None)
    token = f"e_{uuid.uuid4().hex[:8]}"
    while token in external_registry:
        token = f"e_{uuid.uuid4().hex[:8]}"
    external_registry[token] = {
        "ext_id": ext_id,
        "agent_key": agent_key,
        "working_dir": wd,
    }
    return token


def resolve_external_info(token: str) -> dict[str, Any] | None:
    """Resolve an external token back to its info. None for unknown/expired."""
    return external_registry.get(token)


def register_restart_info(agent_key: str, working_dir: Path) -> str:
    """Register restart info and return a short 8-char token."""
    if len(restart_registry) >= _MAX_RESTART_TOKENS:
        oldest = next(iter(restart_registry))
        restart_registry.pop(oldest, None)
    token = f"r_{uuid.uuid4().hex[:8]}"
    while token in restart_registry:
        token = f"r_{uuid.uuid4().hex[:8]}"
    restart_registry[token] = {
        "agent_key": agent_key,
        "working_dir": working_dir,
    }
    return token
