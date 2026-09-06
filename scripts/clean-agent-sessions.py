#!/usr/bin/env python3
"""Two-sided session cleanup for Agents-On-Hand (session-leak prevention).

Rule (see AGENTS.md):
  When deleting aoh/opencode sessions or clearing offline sessions, the
  prime-agent side (~/.prime) MUST be cleaned in the same pass, otherwise
  orphaned session files leak.

opencode side :  opencode session delete <ses_id>            (--opencode-ids)
agent side    :  ~/.prime/agent/sessions/<uuid>.jsonl
                 ~/.prime/agent/session-artifacts/<internal-id>/
                 (only after confirming no active session-lease references it)

Safety:
  - DRY-RUN by default; pass --force to actually delete.
  - Never deletes a prime session referenced by an active session-lease.
  - --scan-cwd lists candidate offline/prime sessions without deleting.

Usage:
  # inspect primes whose cwd is the aoh dir (dry, safe)
  python3 scripts/clean-agent-sessions.py --scan-cwd /home/brian/project/Agents-On-Hand

  # delete on both sides (opencode ids + prime file prefixes)
  python3 scripts/clean-agent-sessions.py \
      --opencode-ids ses_abc ses_def \
      --prime 01a04459-11c2 01a04459-5c3c \
      --force
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

PRIME_SESSIONS_DIR = os.path.expanduser("~/.prime/agent/sessions")
PRIME_ARTIFACTS_DIR = os.path.expanduser("~/.prime/agent/session-artifacts")
PRIME_LEASES_DIR = os.path.expanduser("~/.prime/agent/session-leases")


@dataclass
class PrimeSession:
    file_path: str
    file_prefix: str
    session_id: str
    cwd: str
    size: int
    messages: int

    @property
    def artifact_dir(self) -> str:
        return os.path.join(PRIME_ARTIFACTS_DIR, self.session_id)


def read_prime(path: str) -> PrimeSession | None:
    """Parse a prime session jsonl; returns None if it has no session header."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
        if not first:
            return None
        header = json.loads(first)
        if header.get("type") != "session":
            return None
        msgs = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "message":
                    msgs += 1
        return PrimeSession(
            file_path=path,
            file_prefix=os.path.basename(path)[:16],
            session_id=header["id"],
            cwd=header.get("cwd", ""),
            size=os.path.getsize(path),
            messages=msgs,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def list_all_primes() -> list[PrimeSession]:
    out: list[PrimeSession] = []
    for path in sorted(glob.glob(os.path.join(PRIME_SESSIONS_DIR, "*.jsonl"))):
        p = read_prime(path)
        if p:
            out.append(p)
    return out


def leases_referencing(session_ids: set[str]) -> list[str]:
    """Return lease file paths that reference any of the given session ids."""
    hits: list[str] = []
    if not os.path.isdir(PRIME_LEASES_DIR):
        return hits
    for lease_dir in glob.glob(os.path.join(PRIME_LEASES_DIR, "*/")):
        for root, _, files in os.walk(lease_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    txt = open(fp, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if any(sid in txt for sid in session_ids):
                    hits.append(fp)
    return hits


def delete_prime(p: PrimeSession, force: bool) -> None:
    lock_hits = leases_referencing({p.session_id})
    if lock_hits:
        print(f"  SKIP {p.file_prefix}  (active lease: {lock_hits[0]})")
        return
    target_files = [p.file_path]
    if os.path.isdir(p.artifact_dir):
        target_files.append(p.artifact_dir)
    for t in target_files:
        if force:
            if os.path.isdir(t):
                for root, _, fs in os.walk(t, topdown=False):
                    for fn in fs:
                        os.remove(os.path.join(root, fn))
                    os.rmdir(root)
            else:
                os.remove(t)
            print(f"  DELETED {t}")
        else:
            print(f"  [dry-run] would delete {t}")


def delete_opencode_ids(ids: list[str], force: bool) -> None:
    for sid in ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", sid or ""):
            print(f"  SKIP {sid!r} (not a valid session id)")
            continue
        if force:
            r = subprocess.run(
                ["opencode", "session", "delete", sid],
                capture_output=True,
                text=True,
            )
            ok = r.returncode == 0 and "deleted" in (r.stdout + r.stderr).lower()
            print(f"  DELETED {sid} (opencode)" if ok else f"  FAILED {sid}: {r.stderr.strip()}")
        else:
            print(f"  [dry-run] would run: opencode session delete {sid}")


def scan_cwd(cwd: str) -> None:
    candidates = [p for p in list_all_primes() if p.cwd == cwd]
    print(f"prime sessions with cwd={cwd}: {len(candidates)}")
    for p in sorted(candidates, key=lambda x: -x.size):
        lock = bool(leases_referencing({p.session_id}))
        print(
            f"  {p.file_prefix}  msgs={p.messages:3d} size={p.size:9,} "
            f"live={'YES' if lock else 'no '}  {p.session_id}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--opencode-ids", nargs="+", default=[], help="opencode session ids to delete")
    ap.add_argument(
        "--prime", nargs="+", default=[], help="prime session file prefixes (e.g. 01a04459-11c2)"
    )
    ap.add_argument("--scan-cwd", help="list prime sessions under this cwd (no deletion)")
    ap.add_argument("--force", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    if args.scan_cwd:
        scan_cwd(args.scan_cwd)
        return 0
    if not args.opencode_ids and not args.prime:
        ap.error("nothing to do: pass --opencode-ids / --prime / --scan-cwd")

    print("=== opencode side ===")
    delete_opencode_ids(args.opencode_ids, args.force)
    print("=== agent side (~/.prime) ===")
    by_prefix = {
        os.path.basename(p): p for p in glob.glob(os.path.join(PRIME_SESSIONS_DIR, "*.jsonl"))
    }
    primes = [read_prime(p) for p in by_prefix.values()]
    primes = [p for p in primes if p]
    for prefix in args.prime:
        found = [p for p in primes if os.path.basename(p.file_path).startswith(prefix)]
        if not found:
            print(f"  NOT FOUND prefix {prefix}")
            continue
        for p in found:
            delete_prime(p, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
