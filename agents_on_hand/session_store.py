import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("AgentsOnHand")


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    user_id: int
    agent_key: str
    agent_name: str
    command: str
    working_dir: str
    created_at: float


class SessionStore(Protocol):
    def save_state(self, sessions: list[SessionRecord], active_map: dict[int, str]) -> None: ...
    def load_state(self) -> tuple[list[SessionRecord], dict[int, str]]: ...


class JSONSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save_state(self, sessions: list[SessionRecord], active_map: dict[int, str]) -> None:
        data: dict[str, Any] = {
            "version": 1,
            "sessions": [asdict(s) for s in sessions],
            "active_map": {str(k): v for k, v in active_map.items()},
        }
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state_tmp_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    def load_state(self) -> tuple[list[SessionRecord], dict[int, str]]:
        if not self.path.exists():
            return [], {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            sessions = [SessionRecord(**s) for s in data.get("sessions", [])]
            active_raw = data.get("active_map", {})
            active_map: dict[int, str] = {int(k): v for k, v in active_raw.items()}
            return sessions, active_map
        except Exception as e:
            logger.warning(f"State file corrupted ({self.path}): {e}; backing up and returning empty")
            try:
                bak = self.path.with_suffix(".bak")
                if self.path.exists():
                    self.path.rename(bak)
            except Exception:
                pass
            return [], {}
