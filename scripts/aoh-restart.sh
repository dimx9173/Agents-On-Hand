#!/usr/bin/env bash
# Restart the aoh (Agents-On-Hand) Telegram bot.
# aoh.service uses Restart=always, so a bare kill just respawns the process —
# the systemd user unit must be stopped first. This script stops the unit,
# sweeps stray main.py processes started outside systemd, starts the unit
# again, then checks whether aoh was actually restarted.
set -uo pipefail

# Non-login shells (cron, agents) lack the user-session bus env; without it
# systemctl --user fails with "Failed to connect to bus".
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

UNIT="aoh.service"
PROJECT_DIR="/home/brian/project/Agents-On-Hand"
PATTERN="${PROJECT_DIR}/main.py"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

list_strays() {
    # Only real python processes running main.py; never match this script's
    # own shell wrapper (comm check) or itself.
    local pid comm
    for pid in $(pgrep -f "python3 ${PATTERN}" || true); do
        [[ "${pid}" == "$$" ]] && continue
        comm=$(ps -o comm= -p "${pid}" 2>/dev/null | tr -d ' ')
        [[ "${comm}" == python* ]] && echo "${pid}"
    done
}

systemctl --user stop "${UNIT}"

strays=$(list_strays)
if [[ -n "${strays}" ]]; then
    echo "stray pids outside systemd: ${strays}"
    if [[ "${FORCE}" -eq 0 ]]; then
        kill ${strays} 2>/dev/null || true
        sleep 2
    fi
    strays=$(list_strays)
    [[ -n "${strays}" ]] && kill -9 ${strays} 2>/dev/null || true
fi

systemctl --user start "${UNIT}"
sleep 3

if systemctl --user is-active --quiet "${UNIT}"; then
    pid=$(systemctl --user show "${UNIT}" -p MainPID --value)
    if [[ "${pid}" != "0" ]] && kill -0 "${pid}" 2>/dev/null; then
        echo "aoh restarted ✓ (MainPID=${pid})"
        exit 0
    fi
fi
echo "ERROR: aoh did NOT restart" >&2
systemctl --user status "${UNIT}" --no-pager | tail -5 >&2
exit 1
