# Changelog

All notable changes to Agents-On-Hand are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
