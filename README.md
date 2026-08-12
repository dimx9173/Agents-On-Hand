# Agents-On-Hand 🤖

[![Tests](https://github.com/your-org/Agents-On-Hand/actions/workflows/test.yml/badge.svg)](https://github.com/your-org/Agents-On-Hand/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

A lightweight, security-first Telegram bot that lets you orchestrate multiple local CLI AI agents — Claude Code, OMP (Oh My Pi), OpenCode, Codex, and Bash — right from your Telegram chat. Supports the **ACP (Agent Client Protocol)** for structured JSON-RPC streaming as well as PTY-based fallback for all other tools.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Direct Chat Mode** | All non-`/aoh_` messages route directly to your active CLI Agent |
| **ACP + PTY Hybrid** | ACP agents (`omp`, `opencode`) use JSON-RPC 2.0 stdio; others use PTY |
| **Typing Indicator** | Top-bar "typing..." indicator while agent is processing |
| **Crash Notifications** | Instant alert + `[🔄 Restart]` button when agent process exits |
| **Dynamic Agent Detection** | Only shows locally installed agents in the selector menu |
| **Tool Approval Buttons** | `[✅ 同意]` / `[❌ 拒絕]` for ACP permission requests |
| **Hermes Formatting** | Clean Markdown output with `🛠️ Tool:` and `💭 Thinking` badges |
| **Multi-Session** | Run multiple agents concurrently; switch between sessions |
| **Security First** | User ID whitelist + directory sandboxing |
| **Interrupt Controls** | `/aoh_esc`, `/aoh_ctrlc`, or type `esc`/`ctrlc` in chat |

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/your-org/Agents-On-Hand.git
cd Agents-On-Hand
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
ALLOWED_TELEGRAM_USER_IDS=12345678
ALLOWED_ROOT_DIRS=/Users/yourname/projects
SESSION_LOG_DIR=~/.agents-on-hand/sessions
```

### 3. Run

```bash
python main.py
```

---

## 📱 Commands

| Command | Description |
|---|---|
| `/aoh_new` | Open directory browser → select CLI Agent |
| `/aoh_sessions` | Manage, switch, view logs, or kill sessions |
| `/aoh_stop` | Terminate current active session |
| `/aoh_esc` | Send ESC to active session |
| `/aoh_ctrlc` | Send Ctrl+C to active session |
| `/aoh_help` | Show help + agent installation status |

**Direct input**: Any other text or slash command (e.g. `/commit`, `/clear`) is forwarded directly to the active CLI Agent.

---

## 🛠️ Supported Agents

| Agent | Protocol | Install |
|---|---|---|
| [OMP (Oh My Pi)](https://omp.dev) | ACP (JSON-RPC) | `npm i -g oh-my-pi` |
| [OpenCode](https://opencode.ai) | ACP (JSON-RPC) | `npm i -g opencode-ai` |
| [Claude Code](https://claude.ai/code) | PTY | `npm i -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | PTY | `npm i -g @openai/codex` |
| Bash | PTY | Built-in |

---

## 🏗️ Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design including the ACP/PTY dual-protocol engine, session lifecycle, and security model.

---

## 🧪 Tests

```bash
python -m pytest tests/ -v
```

11 automated tests covering ACP engine, typing indicator, session exit lifecycle, agent verification, and output formatting.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 License

[Apache License 2.0](LICENSE)
