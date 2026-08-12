# Architecture — Agents-On-Hand

## Overview

Agents-On-Hand is a Telegram Bot that acts as an orchestration proxy between a user's Telegram client and one or more local CLI AI agents (Claude Code, OMP, OpenCode, Pi Agent, Codex, Bash).

```
Telegram User
     │
     │ (messages / inline buttons)
     ▼
 bot.py ─────────── session_manager.py (Probing Chain)
     │                      │
     │               ┌──────┴──────────────────────────┐
     │          ACP Driver / Pi RPC / Claude Stream / PTY Driver
     │                      │
     │               UnifiedStreamer
     │                      │
     ▼                      ▼
 Telegram API (sendMessage / editMessage / sendChatAction)
```

---

## Multi-Protocol Driver Architecture

Agents-On-Hand uses a **Probing Chain (Priority: ACP > Native RPC > PTY Fallback)** to dynamically select the highest-fidelity protocol for each agent:

| Driver | Protocol | Agents | Key Features |
|---|---|---|---|
| **`ACPDriver`** | JSON-RPC 2.0 stdio | `omp acp`, `opencode acp` | Standard ACP protocol, token streaming, tool approval |
| **`PiRPCDriver`** | JSON Event Stream | `pi --mode rpc` | Pi Agent native RPC event stream, tool approval |
| **`ClaudeStreamDriver`** | Stream-JSON stdio | `claude` | Claude Code `--output-format=stream-json` |
| **`PTYDriver`** | Unix Pseudo-Terminal | `codex`, `bash`, Fallback | PTY execution with `pexpect` and real-time ANSI cleaning |

---

## Unified Event System

All protocol drivers emit normalized `DriverEvent` payloads:

- `TEXT_DELTA`: Output response content.
- `THOUGHT_DELTA`: Model reasoning / thinking output (rendered as `💭 Thinking...`).
- `TOOL_REQUEST`: Tool execution permission request (rendered with `🛡️ Tool Approval` Inline Keyboard).
- `TOOL_RESULT`: Tool execution result badge (`🛠️ Tool:`).
- `EXIT`: Session process exit event.

---

## Key Modules

| Module | Responsibility |
|---|---|
| `agents_on_hand/bot.py` | Telegram command handlers, callback queries, message routing |
| `agents_on_hand/config.py` | Configuration, `AVAILABLE_CLI_AGENTS` driver probing chains, whitelist validation |
| `agents_on_hand/session_manager.py` | `AgentSession` registry, Probing Chain execution, lifecycle management |
| `agents_on_hand/stream_handler.py` | `UnifiedStreamer` for Telegram message streaming and inline button approval |
| `agents_on_hand/drivers/` | Multi-protocol driver package (`base_driver`, `acp_driver`, `pi_rpc_driver`, `claude_stream_driver`, `pty_driver`) |
| `agents_on_hand/ansi_cleaner.py` | ANSI/TUI control code stripping, Hermes-style message formatting |

---

## Security Model

- **User ID Whitelist**: `ALLOWED_TELEGRAM_USER_IDS` restricts bot access to authorized Telegram users.
- **Directory Sandboxing**: `ALLOWED_ROOT_DIRS` limits filesystem navigation to configured paths.
- **No Secret Logging**: Bot token and user IDs are loaded from `.env` and never logged.
