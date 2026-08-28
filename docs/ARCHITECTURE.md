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
| **`ACPDriver`** | JSON-RPC 2.0 stdio | `omp acp`, `opencode acp`, `prime-agent --mode acp` | Standard ACP protocol, token streaming, tool approval |
| **`PiRPCDriver`** | JSON Event Stream | `pi --mode rpc`, `prime-agent --mode rpc` | Native RPC event stream, tool approval |
| **`ClaudeStreamDriver`** | Stream-JSON stdio | `claude` | Claude Code `--output-format=stream-json` |
| **`PTYDriver`** | Unix Pseudo-Terminal | `codex`, `bash`, `prime-agent`, Fallback | PTY execution with `pexpect` and real-time ANSI cleaning |

---

## Unified Event System

All protocol drivers emit normalized `DriverEvent` payloads:

- `TEXT_DELTA`: Output response content.
- `THOUGHT_DELTA`: Model reasoning / thinking output (rendered into `<blockquote expandable><b>💭 思考過程</b>...</blockquote>`).
- `TOOL_REQUEST`: Tool execution permission request (rendered with `🛡️ Tool Approval` Inline Keyboard).
- `TOOL_RESULT`: Tool execution result block (rendered into `<blockquote expandable>🛠️ <b>Tool (...)</b>...</blockquote>`).
- `TURN_END`: Turn completion event, triggers metadata footer (`⚡ 2.4s · 🤖 Model · 🛠️ Tools`) and Quick Action Buttons (`[🔄 重試此輪]`, `[🛑 結束 Session]`).
- `EXIT`: Session process exit event.

---

## Telegram-Friendly Formatting & Rendering Pipeline

To maximize Telegram client compatibility across Mobile, Desktop, and Web:

1. **Telegram HTML Mode (`parse_mode="HTML"`)**: Converts Markdown to Telegram-compliant HTML, ensuring robust character escaping and zero parsing crashes on symbols like `_`, `*`, `[`, `]`.
2. **Expandable Blockquotes (`<blockquote expandable>`)**: Automatically collapses long reasoning traces (DeepSeek R1 / Claude 3.7 Thinking) and verbose tool outputs (e.g. `git diff`, test logs) into 3-line expandable previews.
3. **Tag-Stack Aware Splitter**: When responses exceed 3,800 characters, preserves and automatically balances open tags (`<pre>`, `<code>`, `<blockquote>`) across multiple sequential messages without breaking syntax highlighting.
4. **Streaming HTML Auto-Balancer**: Dynamically balances open and unclosed tags on in-flight edits to completely eliminate plain-text fallback flickering during live generation.
5. **Incremental ANSI Cleaner**: Statefully buffers split escape sequences across 4KB chunk boundaries in PTY streams to prevent terminal control fragments.

---

## Key Modules

| Module | Responsibility |
|---|---|
| `agents_on_hand/app.py` | Telegram `Application` wiring: command/callback handlers, error handler, post-init greeting (`bot.py` is a thin facade re-exporting `main`) |
| `agents_on_hand/config.py` | Configuration, `AVAILABLE_CLI_AGENTS` driver probing chains, whitelist + path sandbox validation |
| `agents_on_hand/session_manager.py` | `AgentSession` registry, Probing Chain execution, lifecycle management, last prompt tracking for retries, session persistence |
| `agents_on_hand/stream_handler.py` | `UnifiedStreamer` for Telegram HTML streaming, expandable quotes, turn summary footers, and quick action keyboards |
| `agents_on_hand/drivers/` | Multi-protocol driver package (`base_driver`, `acp_driver`, `pi_rpc_driver`, `claude_stream_driver`, `pty_driver`) |
| `agents_on_hand/handlers/` | Telegram handlers: `chat.py` (routing, interrupt commands), `restart.py` (crash alerts + one-click restart), `acp_permissions.py` (tool approval) |
| `agents_on_hand/ui/` | `directory_browser.py` (paged dir picker), `session_menu.py` (session list / switch / logs / kill / retry) |
| `agents_on_hand/runtime.py` | Runtime globals: `active_streamers` per user, `bot_app`, streamer factory |
| `agents_on_hand/callback_registry.py` | Short-token registries for path / restart info (Telegram `callback_data` 64-byte limit) |
| `agents_on_hand/session_store.py` | Atomic JSON persistence of session state (`state.json`) |
| `agents_on_hand/logging_setup.py` | Rotating file log, coloured console, secret-redaction filter, per-session structured trace log |
| `agents_on_hand/security.py` | `@restricted` decorator enforcing the Telegram user whitelist |
| `agents_on_hand/ansi_cleaner.py` | ANSI stream cleaner, Markdown to Telegram HTML converter, Tag-Stack Aware Splitter, and tag auto-balancer |

---

## Security Model

- **User ID Whitelist**: `ALLOWED_TELEGRAM_USER_IDS` restricts bot access to authorized Telegram users.
- **Directory Sandboxing**: `ALLOWED_ROOT_DIRS` limits filesystem navigation to configured paths.
- **No Secret Logging**: Bot token and user IDs are loaded from `.env` and never logged.

