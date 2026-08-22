"""Facade — keeps `from agents_on_hand.bot import main` stable."""
from .app import global_error_handler, main, post_init
from .callback_registry import (
    get_path_token,
    path_registry,
    path_to_token,
    register_restart_info,
    resolve_path_token,
    restart_registry,
)
from .handlers.acp_permissions import acp_permission_callback_handler
from .handlers.chat import (
    ctrlc_command,
    esc_command,
    help_command,
    stop_command,
    text_message_router,
)
from .handlers.restart import on_background_session_finished, session_restart_callback_handler
from .runtime import active_streamers, bot_app, create_streamer_for_session
from .security import restricted
from .ui.directory_browser import (
    directory_callback_handler,
    new_command,
)
from .ui.session_menu import prune_command, session_action_callback_handler, sessions_command

__all__ = [
    "main", "post_init", "global_error_handler",
    "restricted", "active_streamers", "bot_app", "create_streamer_for_session",
    "get_path_token", "resolve_path_token", "register_restart_info", "path_registry", "path_to_token", "restart_registry",
    "help_command", "new_command", "sessions_command", "prune_command", "esc_command", "ctrlc_command", "stop_command",
    "directory_callback_handler", "session_action_callback_handler", "acp_permission_callback_handler",
    "session_restart_callback_handler", "text_message_router", "on_background_session_finished",
]
