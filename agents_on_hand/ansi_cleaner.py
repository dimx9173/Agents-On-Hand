import re

# Regex pattern matching ANSI escape codes (colors, cursor movements, erase lines, etc.)
ANSI_ESCAPE_PATTERN = re.compile(
    r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])'
)


def strip_ansi_codes(text: str) -> str:
    """Strip all ANSI control and escape sequences from text."""
    if not text:
        return ""
    clean_text = ANSI_ESCAPE_PATTERN.sub('', text)
    
    raw_lines = clean_text.split('\n')
    processed_lines = []
    for line in raw_lines:
        if '\r' in line:
            parts = line.split('\r')
            non_empty_parts = [p for p in parts if p]
            line = non_empty_parts[-1] if non_empty_parts else ""
        processed_lines.append(line)
    
    return "\n".join(processed_lines)


def clean_cli_output(raw_text: str) -> str:
    """
    Strips ANSI codes, removes terminal TUI box decorations, cursor redraws,
    and status banners, returning clean, human-readable text.
    """
    if not raw_text:
        return ""

    clean = strip_ansi_codes(raw_text)

    # Keywords for static TUI banners in omp / claude / interactive tools
    tui_banner_keywords = [
        "omp v", "Claude Code", "Welcome back!", "for prompt actions",
        "for commands", "to run bash", "to run python", "LSP Servers",
        "No LSP servers", "Recent sessions", "Ctrl+D can be used to exit",
        "ctrl+r to search", "Executable not found in $PATH", "Connected: ",
        "connecting: ", "Failed: ", "MiniMax-M3", "Session accent",
        "Theme used when", "Tight Layout", "Closing session"
    ]

    box_char_pattern = re.compile(r'[\u2500-\u257F\u2580-\u259F╭╰│─┌┐└┘├┤┼╯┬┴\s]+')

    filtered_lines = []
    for line in clean.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
            
        # Skip pure TUI box drawing lines
        stripped_box = box_char_pattern.sub('', line_str)
        if not stripped_box:
            continue
            
        # Skip static TUI header/banner lines
        if any(kw.lower() in line_str.lower() for kw in tui_banner_keywords):
            continue
            
        # Strip TUI side borders from active lines (e.g. │  hello  │ -> hello)
        cleaned_line = re.sub(r'^[\u2500-\u257F\u2580-\u259F╭╰│─┌┐└┘├┤┼╯┬┴╘╒╓╫╪█▀▄▌▐╟╢┼\s]+|[\u2500-\u257F\u2580-\u259F╭╰│─┌┐└┘├┤┼╯┬┴╘╒╓╫╪█▀▄▌▐╟╢┼\s]+$', '', line_str).strip()
        if cleaned_line:
            # Deduplicate consecutive identical lines from terminal redraws
            if not filtered_lines or cleaned_line != filtered_lines[-1]:
                filtered_lines.append(cleaned_line)

    return "\n".join(filtered_lines)


def format_hermes_style(text: str) -> str:
    """
    Format CLI agent output into Hermes Agent Telegram style:
    - Conversational prose -> Natural Telegram Markdown (bold, lists)
    - Tool calls & tool executions -> 🛠️ Tool status badges
    - Thinking blocks -> 💭 Thinking badges
    - Code blocks -> Syntax-highlighted code blocks
    """
    clean = clean_cli_output(text)
    if not clean.strip():
        return ""

    lines = clean.splitlines()
    formatted_lines = []
    in_code_block = False

    for line in lines:
        line_str = line.strip()
        
        if line_str.startswith("```"):
            in_code_block = not in_code_block
            formatted_lines.append(line)
            continue

        if in_code_block:
            formatted_lines.append(line)
            continue

        # Tool execution pattern (e.g. bash: git status, edit: auth.py, read: config.json)
        tool_match = re.match(r'^(?:running\s+tool|executing\s+tool|tool|bash|edit|read|grep|write|glob|python)\s*[:>]\s*(.+)$', line_str, re.IGNORECASE)
        if tool_match:
            tool_detail = tool_match.group(1).strip()
            formatted_lines.append(f"🛠️ *Tool*: `{tool_detail}`")
            continue

        # Thinking block pattern
        if line_str.startswith("<thinking>") or line_str.startswith("Thinking:"):
            formatted_lines.append("💭 *Thinking...*")
            continue
        elif line_str == "</thinking>":
            continue

        formatted_lines.append(line)

    return "\n".join(formatted_lines)


def format_telegram_code_block(text: str, max_chars: int = 3800, lang: str = "") -> str:
    """Strip ANSI codes, clean text, and format inside a safe Markdown code block."""
    clean = clean_cli_output(text)
    
    if len(clean) > max_chars:
        trimmed_notice = "... [Log truncated - Showing last output] ...\n"
        clean = trimmed_notice + clean[-(max_chars - len(trimmed_notice)):]
    
    if not clean.strip():
        clean = "(No output yet...)"
        
    return f"```{lang}\n{clean}\n```"
