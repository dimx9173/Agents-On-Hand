"""
Centralized logging configuration for Agents-On-Hand.

Features:
- Rotating file log (~/.agents-on-hand/aoh.log, max 10MB × 5 backups)
- Coloured console output
- Security sanitizer: redacts BOT_TOKEN, API keys, and similar secrets
  before any log record hits a handler
- Session-level structured trace log per session_id (with timing)
  Enhanced for full TG->agent->TG traceability
"""

import logging
import logging.handlers
import os
import re
import time
import uuid
from pathlib import Path

# ─────────────────────────────────────────────
# Log directories
# ─────────────────────────────────────────────
_LOG_ROOT = Path(os.getenv("SESSION_LOG_DIR", "~/.agents-on-hand/sessions")).expanduser().resolve()
_APP_LOG_DIR = _LOG_ROOT.parent  # ~/.agents-on-hand/
_APP_LOG_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG_FILE = _APP_LOG_DIR / "aoh.log"

# ─────────────────────────────────────────────
# Secrets redaction filter
# ─────────────────────────────────────────────
# Patterns that look like secrets: bot tokens, API keys, bearer tokens, etc.
_SECRET_PATTERNS = [
    # Telegram bot token  e.g. 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
    re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,50}\b"),
    # Generic "key = <value>" pairs  (api_key=xxx, token=xxx, secret=xxx, password=xxx)
    re.compile(
        r"(?i)(api[_-]?key|secret[_-]?key|password|passwd|token|bearer|auth[_-]?key)"
        r"\s*[=:]\s*['\"]?([A-Za-z0-9+/=._~\-]{8,})['\"]?",
        re.IGNORECASE,
    ),
    # sk- prefixed keys (OpenAI style)
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]

_REDACT_LABEL = "***REDACTED***"


def _sanitize(message: str) -> str:
    """Replace known secret patterns with a redaction label."""
    for pattern in _SECRET_PATTERNS:
        # For group-based patterns keep the key name, redact the value
        if pattern.groups:
            message = pattern.sub(lambda m: f"{m.group(1)}={_REDACT_LABEL}", message)
        else:
            message = pattern.sub(_REDACT_LABEL, message)
    return message


class _SanitizingFilter(logging.Filter):
    """Logging filter that redacts secrets from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = _sanitize(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _sanitize(str(v)) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(_sanitize(str(a)) for a in record.args)
        except Exception:
            pass  # Never crash the app due to logging
        return True


# ─────────────────────────────────────────────
# Console formatter (with colour on TTY)
# ─────────────────────────────────────────────
_COLOURS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
    "RESET": "\033[0m",
}


class _ColouredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelname, "")
        reset = _COLOURS["RESET"]
        record.levelname = f"{colour}{record.levelname}{reset}"
        return super().format(record)


# ─────────────────────────────────────────────
# Setup entry point
# ─────────────────────────────────────────────
_SETUP_DONE = False

LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s — %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int | None = None) -> None:
    """
    Configure root logger once with:
    - RotatingFileHandler → ~/.agents-on-hand/aoh.log
    - StreamHandler (console, coloured)
    Both filtered through _SanitizingFilter.

    Level resolution (priority):
    1. Explicit `level` argument
    2. Env `AOH_DEBUG=1` / `LOG_LEVEL=DEBUG` → DEBUG
    3. Default INFO
    """
    global _SETUP_DONE
    if _SETUP_DONE:
        return
    _SETUP_DONE = True

    # Resolve level from env if not explicitly passed
    if level is None:
        _env_debug = os.getenv("AOH_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
        _env_level = os.getenv("LOG_LEVEL", "").strip().upper()
        if _env_debug or _env_level == "DEBUG":
            level = logging.DEBUG
        elif _env_level == "INFO":
            level = logging.INFO
        else:
            level = logging.INFO

    sanitizer = _SanitizingFilter()

    # ── File handler (rotating, 10 MB × 5 backups) ──────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        APP_LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # capture everything to file
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    file_handler.addFilter(sanitizer)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    use_colour = os.isatty(1)  # only colour when stdout is a terminal
    if use_colour:
        console_handler.setFormatter(
            _ColouredFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        )
    else:
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    console_handler.addFilter(sanitizer)

    # ── Root logger ──────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers control effective levels
    # Remove any handlers already added by basicConfig
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    noisy_level = logging.DEBUG if level == logging.DEBUG else logging.WARNING
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(noisy_level)

    logging.getLogger("AgentsOnHand").info(
        f"Logging initialised — app log: {APP_LOG_FILE}"
    )


# ─────────────────────────────────────────────
# Per-session structured trace logger
# ─────────────────────────────────────────────

class SessionTraceLogger:
    """
    Writes structured, timestamped trace lines to a per-session log file.

    Log file: <SESSION_LOG_DIR>/<session_id>.trace.log

    Each line is tab-separated:
        <ISO timestamp>  <elapsed_ms>  <event>  <detail>

    Sensitive content is redacted before writing.

    Event Types for TG->agent->TG traceability:
    - SESSION_START/END          : Session lifecycle
    - SESSION_INIT               : Agent initialization details
    - USER_INPUT                 : User message from Telegram
    - STREAMER_START/STOP        : UnifiedStreamer lifecycle
    - STREAMER_SWITCH            : User switched to/from this session
    - TURN_START/END             : Conversation turn boundaries with turn_id
    - AGENT_TTFT                 : Time To First Token
    - AGENT_DONE                 : Agent response completion
    - THOUGHT_DELTA              : Agent thinking/reasoning
    - TOOL_REQUEST/RESULT        : Tool execution flow
    - PERM_REQUEST/RESPONSE      : Permission approval flow
    - ACP_CALL                   : ACP JSON-RPC call timing
    - ACP_SESSION_ID             : ACP session ID tracking
    - DRIVER_PROBE/BOUND         : Driver probing and binding
    - BG_COMPLETION              : Background turn completion
    - TG_DELIVER                 : Telegram message delivery (send/edit)
    - ERROR                      : Errors
    """

    _TRACE_LOG_DIR = _LOG_ROOT

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._start_time = time.monotonic()
        log_path = self._TRACE_LOG_DIR / f"{session_id}.trace.log"
        self._fh = open(log_path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._write("SESSION_START", f"session_id={session_id}")

        # Current turn tracking
        self._current_turn_id: str | None = None
        self._turn_start_time: float | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def event(self, event: str, detail: str = "") -> None:
        """Record a generic event with elapsed time."""
        self._write(event, detail)

    def session_init(self, agent: str, command: str, cwd: str, drivers: list[str]) -> None:
        """Record session initialization with probing chain details."""
        self._write("SESSION_INIT", f"agent={agent} command={command} cwd={cwd} drivers={drivers}")

    def driver_probe(self, driver: str, attempt: int, total: int) -> None:
        """Record driver probing attempt."""
        self._write("DRIVER_PROBE", f"driver={driver} attempt={attempt}/{total}")

    def driver_bound(self, driver: str, success: bool) -> None:
        """Record driver binding result."""
        self._write("DRIVER_BOUND", f"driver={driver} success={success}")

    def user_input(self, text: str, turn_id: str | None = None) -> None:
        """Record incoming user message from Telegram with turn tracking."""
        safe = _sanitize(text[:200])
        if turn_id is None:
            turn_id = uuid.uuid4().hex[:8]
        self._current_turn_id = turn_id
        self._turn_start_time = time.monotonic()
        self._write("USER_INPUT", f"turn_id={turn_id} text={safe}")
        self._write("TURN_START", f"turn_id={turn_id}")

    def streamer_start(self, streamer_type: str = "UnifiedStreamer") -> None:
        """Record streamer start."""
        self._write("STREAMER_START", f"type={streamer_type}")

    def streamer_stop(self, streamer_type: str = "UnifiedStreamer") -> None:
        """Record streamer stop."""
        self._write("STREAMER_STOP", f"type={streamer_type}")

    def streamer_switch(self, from_session: str | None, to_session: str, reason: str = "user_switch") -> None:
        """Record session switch event."""
        self._write("STREAMER_SWITCH", f"from={from_session or 'none'} to={to_session} reason={reason}")

    def agent_first_token(self, agent: str, elapsed_s: float, turn_id: str | None = None) -> None:
        """Record first-token timing (TTFT) for an agent response."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("AGENT_TTFT", f"turn_id={tid} agent={agent} ttft={elapsed_s:.3f}s")

    def agent_response_done(self, agent: str, chars: int, elapsed_s: float, turn_id: str | None = None) -> None:
        """Record completion of an agent response."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("AGENT_DONE", f"turn_id={tid} agent={agent} chars={chars} elapsed={elapsed_s:.3f}s")

    def thought_delta(self, chars: int, turn_id: str | None = None) -> None:
        """Record agent thinking/reasoning delta."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("THOUGHT_DELTA", f"turn_id={tid} chars={chars}")

    def tool_request(self, req_id, tool_name: str, tool_args: str = "", turn_id: str | None = None) -> None:
        """Record a tool permission request."""
        tid = turn_id or self._current_turn_id or "unknown"
        safe_args = _sanitize(str(tool_args)[:200])
        self._write("TOOL_REQUEST", f"turn_id={tid} req_id={req_id} tool={tool_name} args={safe_args}")

    def tool_result(self, req_id, tool_name: str, content_preview: str, turn_id: str | None = None) -> None:
        """Record tool execution result."""
        tid = turn_id or self._current_turn_id or "unknown"
        safe_preview = _sanitize(str(content_preview)[:200])
        self._write("TOOL_RESULT", f"turn_id={tid} req_id={req_id} tool={tool_name} preview={safe_preview}")

    def perm_request(self, req_id, tool_name: str, turn_id: str | None = None) -> None:
        """Record permission request sent to user."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("PERM_REQUEST", f"turn_id={tid} req_id={req_id} tool={tool_name}")

    def perm_response(self, req_id, approved: bool, turn_id: str | None = None) -> None:
        """Record user's approval/rejection of a tool request."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("PERM_RESPONSE", f"turn_id={tid} req_id={req_id} approved={approved}")

    def acp_call(self, method: str, elapsed_s: float, ok: bool, turn_id: str | None = None) -> None:
        """Record an ACP JSON-RPC call with timing."""
        tid = turn_id or self._current_turn_id or "unknown"
        status = "OK" if ok else "ERR"
        self._write("ACP_CALL", f"turn_id={tid} method={method} elapsed={elapsed_s:.3f}s status={status}")

    def acp_session_id(self, session_id: str) -> None:
        """Record ACP session ID for correlation."""
        self._write("ACP_SESSION_ID", f"acp_session_id={session_id}")

    def tg_deliver(self, msg_id: int | None, chars: int, is_edit: bool, is_final: bool, turn_id: str | None = None) -> None:
        """Record Telegram message delivery (send or edit)."""
        tid = turn_id or self._current_turn_id or "unknown"
        action = "edit" if is_edit else "send"
        self._write("TG_DELIVER", f"turn_id={tid} msg_id={msg_id or 'new'} chars={chars} action={action} final={is_final}")

    def bg_completion(self, turn_id: str | None = None, notification_sent: bool = False) -> None:
        """Record background completion event."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("BG_COMPLETION", f"turn_id={tid} notification_sent={notification_sent}")

    def turn_end(self, turn_id: str | None = None, driver: str = "", reason: str = "normal") -> None:
        """Record turn end with driver and reason."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("TURN_END", f"turn_id={tid} driver={driver} reason={reason}")
        if tid == self._current_turn_id:
            self._current_turn_id = None
            self._turn_start_time = None

    # Compatibility alias for older call sites
    def permission_response(self, req_id, approved: bool, turn_id: str | None = None) -> None:
        return self.perm_response(req_id, approved, turn_id)

    def error(self, msg: str, turn_id: str | None = None) -> None:
        """Record an error."""
        tid = turn_id or self._current_turn_id or "unknown"
        self._write("ERROR", f"turn_id={tid} msg={_sanitize(msg[:500])}")

    def close(self) -> None:
        """Flush and close the trace file."""
        self._write("SESSION_END", f"total_elapsed={self._elapsed_ms()}ms")
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass

    # ── internals ───────────────────────────────────────────────────────────

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_time) * 1000)

    def _write(self, event: str, detail: str = "") -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        ms = int((time.monotonic() % 1) * 1000)
        line = f"{ts}.{ms:03d}\t+{self._elapsed_ms()}ms\t{event}\t{detail}\n"
        try:
            self._fh.write(line)
        except Exception:
            pass
