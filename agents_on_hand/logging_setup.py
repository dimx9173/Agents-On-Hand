"""
Centralized logging configuration for Agents-On-Hand.

Features:
- Rotating file log (~/.agents-on-hand/aoh.log, max 10MB × 5 backups)
- Coloured console output
- Security sanitizer: redacts BOT_TOKEN, API keys, and similar secrets
  before any log record hits a handler
- Session-level structured trace log per session_id (with timing)
"""

import logging
import logging.handlers
import os
import re
import time
from pathlib import Path
from typing import Optional

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


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure root logger once with:
    - RotatingFileHandler → ~/.agents-on-hand/aoh.log
    - StreamHandler (console, coloured)
    Both filtered through _SanitizingFilter.
    """
    global _SETUP_DONE
    if _SETUP_DONE:
        return
    _SETUP_DONE = True

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

    # ── Console handler (INFO+, coloured) ───────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
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

    # Silence excessively noisy third-party loggers
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

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
    """

    _TRACE_LOG_DIR = _LOG_ROOT

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._start_time = time.monotonic()
        log_path = self._TRACE_LOG_DIR / f"{session_id}.trace.log"
        self._fh = open(log_path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._write("SESSION_START", f"session_id={session_id}")

    # ── public API ──────────────────────────────────────────────────────────

    def event(self, event: str, detail: str = "") -> None:
        """Record a generic event with elapsed time."""
        self._write(event, detail)

    def user_input(self, text: str) -> None:
        """Record incoming user message (truncated for safety)."""
        safe = _sanitize(text[:200])
        self._write("USER_INPUT", safe)

    def agent_first_token(self, agent: str, elapsed_s: float) -> None:
        """Record first-token timing (TTFT) for an agent response."""
        self._write("AGENT_TTFT", f"agent={agent} ttft={elapsed_s:.3f}s")

    def agent_response_done(self, agent: str, chars: int, elapsed_s: float) -> None:
        """Record completion of an agent response."""
        self._write("AGENT_DONE", f"agent={agent} chars={chars} elapsed={elapsed_s:.3f}s")

    def tool_request(self, req_id, tool_name: str, approved: Optional[bool] = None) -> None:
        """Record a tool permission request and its resolution."""
        status = "" if approved is None else f" approved={approved}"
        self._write("TOOL_REQUEST", f"req_id={req_id} tool={tool_name}{status}")

    def permission_response(self, req_id, approved: bool) -> None:
        """Record the user's approval/rejection of a tool request."""
        self._write("PERM_RESPONSE", f"req_id={req_id} approved={approved}")

    def acp_call(self, method: str, elapsed_s: float, ok: bool) -> None:
        """Record an ACP JSON-RPC call with timing."""
        status = "OK" if ok else "ERR"
        self._write("ACP_CALL", f"method={method} elapsed={elapsed_s:.3f}s status={status}")

    def error(self, msg: str) -> None:
        """Record an error."""
        self._write("ERROR", _sanitize(msg[:500]))

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
