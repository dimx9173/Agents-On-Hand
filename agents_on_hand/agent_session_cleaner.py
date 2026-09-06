"""Command-only purge of agent-side session records (session-leak prevention).

Deleting an AOH session must ALSO clean the agent's own session store — and
only through that agent's command surface, never by directly removing files
(see project AGENTS.md "Session cleanup" rule).

Supported surfaces (verified against installed CLIs):

  - opencode : `opencode session delete <id>`   (CLI command, permanent)
  - prime    : prime-agent <=0.9.1 has NO headless delete command (only the
               interactive `/resume` picker, which itself uses the `trash`
               CLI first). We use the same primitive prime prefers — the
               system `trash` command (`gio trash` on this box) — moving the
               session `.jsonl` + artifact dir to the trash, never unlinking.
  - omp      : no session-delete surface       -> reported unsupported.
  - others   : aoh-side only (pty agents keep no agent-visible session store)
               -> reported unsupported (no-op, not an error).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PRIME_SESSIONS_DIR = Path.home() / ".prime" / "agent" / "sessions"
PRIME_ARTIFACTS_DIR = Path.home() / ".prime" / "agent" / "session-artifacts"


@dataclass
class PurgeResult:
    ok: bool
    method: str  # e.g. "opencode-cli", "gio-trash", "unsupported"
    detail: str = ""
    targets: list[str] = field(default_factory=list)


async def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
    """Run a command asynchronously; returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return -1, "", "timeout"
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except Exception as e:  # noqa: BLE001 - surface any spawn failure
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------


async def purge_opencode(session_id: str | None) -> PurgeResult:
    """Delete an opencode session via `opencode session delete <id>`.

    ``session_id`` may be an external ses_ id or the ACP session id captured
    at spawn time. Unknown id -> refused (never guessed).
    """
    if not session_id:
        return PurgeResult(False, "opencode-cli", "no session id known")
    bin_name = shutil.which("opencode")
    if not bin_name:
        return PurgeResult(False, "opencode-cli", "opencode binary not found")
    rc, out, err = await _run([bin_name, "session", "delete", session_id])
    if rc == 0 and "deleted" in (out + err).lower():
        return PurgeResult(
            True, "opencode-cli", "deleted via opencode session delete", [session_id]
        )
    return PurgeResult(False, "opencode-cli", f"rc={rc} {err.strip() or out.strip()}")


# ---------------------------------------------------------------------------
# prime
# ---------------------------------------------------------------------------


def _prime_internal_session_id(session_file: Path) -> str | None:
    """Read the `id` from a prime jsonl header (filename != session id)."""
    try:
        with open(session_file, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
        if not first:
            return None
        header = json.loads(first)
        if header.get("type") == "session" and header.get("id"):
            return str(header["id"])
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return None


def _prime_artifact_dir(session_file: Path) -> Path | None:
    internal_id = _prime_internal_session_id(session_file)
    if not internal_id:
        return None
    candidate = PRIME_ARTIFACTS_DIR / internal_id
    return candidate if candidate.is_dir() else None


async def purge_prime(
    *,
    external_id: str | None = None,
    acp_session_id: str | None = None,
    agent_session_file: str | None = None,
    working_dir: Path | None = None,
) -> PurgeResult:
    """Purge a prime session via the `trash` command (never unlink).

    Resolves the target file in order:
      1. explicit ``agent_session_file``
      2. ``acp_session_id`` matching ~/.prime/agent/sessions/<id>.jsonl
      3. ``external_id`` matched against `prime-agent list --json`
      4. nothing resolvable -> refused (never trashes a guessed session)
    """
    trash = shutil.which("gio")  # `gio trash` — prime's own trash-first preference
    if not trash:
        return PurgeResult(False, "gio-trash", "no trash command (gio) available")

    session_file: Path | None = None
    detail = ""

    if agent_session_file:
        p = Path(agent_session_file)
        if p.exists():
            session_file = p
    elif acp_session_id:
        p = PRIME_SESSIONS_DIR / f"{acp_session_id}.jsonl"
        if p.exists():
            session_file = p
    elif external_id:
        # Ask prime itself which file owns the id (its own command output).
        bin_name = shutil.which("prime-agent") or shutil.which("prime")
        if bin_name:
            rc, out, _ = await _run([bin_name, "list", "--json"])
            if rc == 0:
                try:
                    payload = json.loads(out)
                except json.JSONDecodeError:
                    payload = {}
                sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
                for s in sessions:
                    if str(s.get("id", "")) == external_id and s.get("sessionFile"):
                        p = Path(s["sessionFile"])
                        if p.exists():
                            session_file = p
                            break
        if session_file is None:
            detail = f"prime session {external_id!r} not found in `prime-agent list --json`"
    else:
        return PurgeResult(False, "gio-trash", "no resolvable prime session file")

    if session_file is None:
        return PurgeResult(False, "gio-trash", detail or "session file does not exist")

    targets = [str(session_file)]
    art = _prime_artifact_dir(session_file)
    if art:
        targets.append(str(art))

    rc, out, err = await _run([trash, "trash", *targets])
    if rc != 0:
        return PurgeResult(False, "gio-trash", f"rc={rc} {err.strip() or out.strip()}", targets)
    return PurgeResult(True, "gio-trash", "moved to trash (restore-able)", targets)


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


async def purge_agent_session(
    agent_key: str,
    *,
    external_id: str | None = None,
    acp_session_id: str | None = None,
    agent_session_file: str | None = None,
    working_dir: Path | None = None,
) -> PurgeResult:
    """Command-based agent-side purge, dispatched by agent key.

    Only the stores aoh can reach with a command are touched; everything else
    reports ``unsupported`` so the caller can surface the gap instead of the
    cleanup silently half-finishing.
    """
    if agent_key == "opencode":
        return await purge_opencode(external_id or acp_session_id)
    if agent_key == "prime":
        return await purge_prime(
            external_id=external_id,
            acp_session_id=acp_session_id,
            agent_session_file=agent_session_file,
            working_dir=working_dir,
        )
    if agent_key == "omp":
        return PurgeResult(False, "unsupported", "omp has no session-delete command")
    return PurgeResult(False, "unsupported", f"{agent_key}: no agent-side session store to purge")
