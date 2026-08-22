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


def resolve_path_token(token_or_str: str) -> Path:
    """Resolve a short token or raw path string back to Path."""
    if token_or_str in path_registry:
        return path_registry[token_or_str]
    return Path(token_or_str).expanduser().resolve()


# Restart token registry
restart_registry: dict[str, dict[str, Any]] = {}


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
