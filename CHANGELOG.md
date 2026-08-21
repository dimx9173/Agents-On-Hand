# Changelog

All notable changes to Agents-On-Hand are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [1.2.0] — 2026-08-21

### Added
- **Turn completion events (`TURN_END`)**: standardized turn completion across all drivers (`ACPDriver`, `ClaudeStreamDriver`, `PiRPCDriver`, `BaseDriver`).
- **Background completion notifications on turn completion**: multi-agent background sessions notify users on turn end instead of waiting exclusively for process exit.
- **Conversation logging**: user prompts are now appended (`👤 User: ...`) to session log files alongside agent output.
- **Scoped ACP tool permission callbacks**: callback data format updated to `acp_perm:action:session_id:req_id` with backward-compatible fallback to ensure accurate session routing.

### Fixed
- **Safe background message handling**: added error handling and safety wrappers for background exit notifications and tool permission prompts to prevent task exceptions.
- **Session background callback reset**: active session switching safely clears stale background completion callbacks.

### Tests
- Added unit tests for background turn completion callback triggering.

## [1.1.0] — 2026-08-13

### Added
- **Centralized logging** (`logging_setup.py`): rotating file log (`~/.agents-on-hand/aoh.log`), colored console output, and a security sanitizer that redacts bot tokens / API keys before records reach handlers.
- **ACP stdio buffer-overrun recovery**: oversized JSON lines (`asyncio.LimitOverrunError`) are drained and skipped instead of killing the read loop.
- **Per-turn Telegram message**: each new prompt starts a fresh streaming message instead of appending to the previous one.
- **Pi RPC**: stderr drained to prevent pipe-buffer deadlock; `EXIT` now carries the real process exit code; only interactive UI methods (`confirm`, `select`, `input`, `editor`) surface as tool requests.
- **Claude Stream**: `--verbose` flag handling and parsing of assistant message content blocks.

### Fixed
- **Streaming completeness**: trailing text deltas dropped by the edit throttle are now guaranteed a final flush — Telegram no longer shows truncated agent replies (e.g. OMP showing only the first chunk).
- **Markdown fallback**: Telegram `BadRequest` parse failures on send/edit are retried as plain text instead of being silently swallowed.
- **Pi RPC event parsing**: message event parsing no longer misreads turn completion as session exit.

### Tests
- Added `test_stream_completeness.py` (trailing flush, Markdown fallback, new-message-per-prompt) and parallel multi-agent integration tests.

## [1.0.0] — 2026-08-12

### Added
- **Direct Chat Mode**: All non-`/aoh_` messages are forwarded directly to the active CLI Agent, enabling seamless chat-style interaction.
- **ACP (Agent Client Protocol) Engine**: Hybrid dual-protocol architecture — ACP agents (`omp`, `opencode`) use JSON-RPC 2.0 over stdio; non-ACP tools (`claude`, `codex`, `bash`) use PTY mode.
- **Telegram Typing Indicator**: Top-bar `sendChatAction("typing")` sent every 4s during agent generation; automatically stops on completion.
- **CLI Agent Crash Notifications**: Proactive Telegram alert when a session process exits, with `[🔄 重新啟動 Agent]` and `[📥 下載 Log]` inline buttons.
- **One-Click Session Restart**: Seamlessly restart a crashed session from the same working directory via Telegram inline button.
- **Dynamic Agent Installation Detection**: `shutil.which()` scan at menu open time — agent selector only shows locally installed tools.
- **ACP Tool Permission Approvals**: Inline keyboard `[✅ 同意執行]` / `[❌ 拒絕執行]` for `agent/request_permission` ACP requests.
- **Hermes Agent Message Formatting**: Conversational text formatted with natural Telegram Markdown; tool executions shown as `🛠️ Tool:` badges; `<thinking>` rendered as `💭 Thinking...`.
- **Interrupt Controls**: `/aoh_esc`, `/aoh_ctrlc` and text shortcuts (`esc`, `ctrlc`) send control characters to active session.
- **Pagination**: Directory browser supports multi-page navigation for directories with many subdirectories.
- **OMP (Oh My Pi) Support**: Full ACP integration with OMP replacing previous Pi Agent.
- **Standard GitHub Project Structure**: `agents_on_hand/` package, `tests/`, `docs/`, `.github/` CI.
- **11 Automated Tests**: Full test coverage across ACP, typing indicator, session lifecycle, agent verification, and formatting.

### Security
- Telegram User ID whitelist (`ALLOWED_TELEGRAM_USER_IDS`) enforced on all handlers.
- Directory navigation sandboxed to `ALLOWED_ROOT_DIRS`.
- Telegram `callback_data` limited to short tokens (< 20 bytes) to comply with Telegram 64-byte limit.
