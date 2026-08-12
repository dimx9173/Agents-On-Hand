"""
ACP Streamer module - Aliased to UnifiedStreamer for multi-protocol compatibility.
"""

from .stream_handler import UnifiedStreamer

# ACPStreamer is unified into UnifiedStreamer for multi-protocol support
ACPStreamer = UnifiedStreamer
