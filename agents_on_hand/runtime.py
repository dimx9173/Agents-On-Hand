import logging
from typing import Any

from telegram.ext import Application

from .stream_handler import DirectChatStreamer

logger = logging.getLogger("AgentsOnHand")

active_streamers: dict[int, Any] = {}

bot_app: Application | None = None


def create_streamer_for_session(bot: Any, chat_id: int, session: Any) -> DirectChatStreamer:
    return DirectChatStreamer(bot=bot, chat_id=chat_id, session=session)
