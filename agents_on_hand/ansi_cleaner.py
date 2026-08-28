import html
import re
from typing import Any

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


class IncrementalAnsiCleaner:
    """
    Incremental ANSI stream parser for PTY/chunked streams.
    Buffers trailing incomplete escape sequences (e.g. `\\x1b[3` across chunk boundaries)
    so control code fragments never leak into output.
    """

    def __init__(self):
        self._buffer: str = ""

    def feed(self, chunk: str) -> str:
        """Feed a new chunk, return stripped text, and retain any incomplete trailing escape code."""
        if not chunk:
            return ""
        combined = self._buffer + chunk
        self._buffer = ""

        # Check if text ends with an incomplete escape sequence
        # Match \\x1b followed by incomplete CSI or escape sequence
        trailing_esc = re.search(r'\x1B(?:\[[0-?]*[ -/]*|\([A-Z0-9]?|\][^\x07\x1b]*)$', combined)
        if trailing_esc:
            split_point = trailing_esc.start()
            self._buffer = combined[split_point:]
            clean_part = combined[:split_point]
        else:
            clean_part = combined

        return strip_ansi_codes(clean_part)

    def flush(self) -> str:
        """Flush remaining buffer."""
        res = strip_ansi_codes(self._buffer)
        self._buffer = ""
        return res


def clean_cli_output(raw_text: str) -> str:
    """
    Strips ANSI codes, removes terminal TUI box decorations, cursor redraws,
    and status banners, returning clean, human-readable text.
    """
    if not raw_text:
        return ""

    clean = strip_ansi_codes(raw_text)

    # Keywords for static TUI banners in omp / claude / prime / interactive tools
    tui_banner_keywords = [
        "omp v", "Claude Code", "Prime Agent", "prime-agent", "Welcome back!", "for prompt actions",
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

        # Skip raw internal JSON hook dumps or transcript diagnostics
        if (line_str.startswith('{"session_id":') and ("hook_event_name" in line_str or "transcript_path" in line_str)) or line_str.startswith("[{'type': 'content'"):
            continue

        # Strip TUI side borders from active lines (e.g. │  hello  │ -> hello)
        cleaned_line = re.sub(r'^[\u2500-\u257F\u2580-\u259F╭╰│─┌┐└┘├┤┼╯┬┴╘╒╓╫╪█▀▄▌▐╟╢┼\s]+|[\u2500-\u257F\u2580-\u259F╭╰│─┌┐└┘├┤┼╯┬┴╘╒╓╫╪█▀▄▌▐╟╢┼\s]+$', '', line_str).strip()
        if cleaned_line:
            # Deduplicate consecutive identical lines from terminal redraws
            if not filtered_lines or cleaned_line != filtered_lines[-1]:
                filtered_lines.append(cleaned_line)

    return "\n".join(filtered_lines)



def escape_html(text: str) -> str:
    """Escape &, <, > for safe Telegram HTML parsing."""
    if not text:
        return ""
    return html.escape(text, quote=False)


TABLE_SEPARATOR_PATTERN = re.compile(
    r'^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$'
)


def split_markdown_table_row(row_str: str) -> list[str]:
    """Split a markdown table row by pipe '|' into stripped cell strings."""
    stripped = row_str.strip()
    if stripped.startswith('|'):
        stripped = stripped[1:]
    if stripped.endswith('|'):
        stripped = stripped[:-1]
    cells = [c.strip() for c in re.split(r'(?<!\\)\|', stripped)]
    return cells


def is_table_row(line: str) -> bool:
    """Return True if line is a valid markdown table row."""
    stripped = line.strip()
    if not stripped or stripped.startswith('```'):
        return False
    return '|' in stripped


def _render_table_block(table_block: list[str]) -> str:
    """
    Render GFM table into mobile-friendly bullet cards (for 2-column or simple tables)
    or structured card groups for multi-column tables.
    """
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    data_rows = [split_markdown_table_row(r) for r in table_block[2:]]
    if not data_rows:
        return "\n".join(table_block)

    num_cols = len(headers)

    # 1. Two-column table (Most common, e.g. | 項目 | 狀態 |)
    # Output clean card bullets: • **Key**: Value
    if num_cols == 2:
        rendered_lines = []
        for row in data_rows:
            if not row or not any(row):
                continue
            key = row[0] if len(row) > 0 else ""
            val = row[1] if len(row) > 1 else ""
            if key and val:
                rendered_lines.append(f"• **{key}**: {val}")
            elif key:
                rendered_lines.append(f"• **{key}**")
            elif val:
                rendered_lines.append(f"• {val}")
        return "\n".join(rendered_lines)

    # 2. Multi-column table (3+ columns)
    # Output bold card heading + sub-bullets
    rendered_groups = []
    for idx, row in enumerate(data_rows, start=1):
        if not row or not any(row):
            continue
        heading = row[0] if row[0] else f"項目 {idx}"
        sub_bullets = []
        for h, v in zip(headers[1:], row[1:]):
            if v:
                sub_bullets.append(f"  • {h}: {v}")
        if sub_bullets:
            rendered_groups.append(f"**{heading}**\n" + "\n".join(sub_bullets))
        else:
            rendered_groups.append(f"**{heading}**")

    return "\n\n".join(rendered_groups)


def convert_table_to_bullets(text: str) -> str:
    """
    Hermes-style Table Rewriter:
    Rewrites GFM pipe tables into mobile-friendly cards/bullets.
    Code blocks (```...```) are preserved without modification.
    """
    if not text or '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence:
            out.append(line)
            i += 1
            continue

        if (
            '|' in line
            and i + 1 < len(lines)
            and TABLE_SEPARATOR_PATTERN.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]) and not TABLE_SEPARATOR_PATTERN.match(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


def markdown_to_telegram_html(md_text: str) -> str:
    """
    Convert Markdown formatted text into Telegram-supported HTML tags:
    - Tables -> Mobile-friendly Bullet Cards (Hermes style)
    - Code blocks ```lang\\n...``` -> <pre><code class="language-lang">...</code></pre>
    - Inline code `...` -> <code>...</code>
    - Bold **...** or __...__ -> <b>...</b>
    - Italic *...* or _..._ -> <i>...</i>
    - Strikethrough ~...~ or ~~...~~ -> <s>...</s>
    - Blockquotes > ... -> <blockquote>...</blockquote>
    - Links [text](url) -> <a href="url">text</a>
    - Headers # ... -> 📌 <b>...</b> / 🔹 <b>...</b> / ▪️ <b>...</b>
    """
    if not md_text:
        return ""

    # Pre-process GFM tables into mobile-friendly bullet cards
    text_with_tables = convert_table_to_bullets(md_text)

    lines = text_with_tables.split("\n")
    html_lines = []
    in_code_block = False
    code_block_lang = ""
    code_block_lines = []

    for line in lines:
        stripped = line.strip()

        # Fenced code block delimiter
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_lang = stripped[3:].strip()
                code_block_lines = []
            else:
                in_code_block = False
                escaped_code = escape_html("\n".join(code_block_lines))
                if code_block_lang:
                    html_lines.append(f'<pre><code class="language-{escape_html(code_block_lang)}">{escaped_code}</code></pre>')
                else:
                    html_lines.append(f'<pre><code>{escaped_code}</code></pre>')
                code_block_lines = []
                code_block_lang = ""
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        # Header check: # Header -> 📌 <b>Header</b>, ## Header -> 🔹 <b>Header</b>, ### Header -> ▪️ <b>Header</b>
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if header_match:
            level = len(header_match.group(1))
            header_text = header_match.group(2).strip()
            # Strip redundant outer bold markers in header
            header_text = re.sub(r'^\*\*(.+?)\*\*$', r'\1', header_text)
            prefix = "📌 " if level == 1 else ("🔹 " if level == 2 else "▪️ ")
            html_lines.append(f"{prefix}<b>{_format_inline_markdown(header_text)}</b>")
            continue

        # Standard blockquote: > Quote -> <blockquote>...</blockquote>
        if line.startswith("> ") or line == ">":
            quote_content = line[2:] if line.startswith("> ") else ""
            html_lines.append(f"<blockquote>{_format_inline_markdown(quote_content)}</blockquote>")
            continue

        # Unordered list: - item or * item -> • item
        list_match = re.match(r'^(\s*)[-*+]\s+(.+)$', line)
        if list_match:
            indent = list_match.group(1)
            item_text = list_match.group(2)
            html_lines.append(f"{indent}• {_format_inline_markdown(item_text)}")
            continue

        html_lines.append(_format_inline_markdown(line))

    # If stream ended with unclosed code block, close it cleanly
    if in_code_block:
        escaped_code = escape_html("\n".join(code_block_lines))
        if code_block_lang:
            html_lines.append(f'<pre><code class="language-{escape_html(code_block_lang)}">{escaped_code}</code></pre>')
        else:
            html_lines.append(f'<pre><code>{escaped_code}</code></pre>')

    return "\n".join(html_lines)



def _format_inline_markdown(text: str) -> str:
    """Format inline markdown elements (inline code, bold, italic, links, strike) with HTML escaping."""
    if not text:
        return ""

    # Split text into inline code segments and non-code segments
    # Pattern to match inline code: `code`
    parts = re.split(r'(`[^`\n]+`)', text)
    formatted_parts = []

    for part in parts:
        if part.startswith('`') and part.endswith('`') and len(part) >= 2:
            code_content = part[1:-1]
            formatted_parts.append(f"<code>{escape_html(code_content)}</code>")
        else:
            # Escape HTML first in regular text
            escaped = escape_html(part)

            # Markdown links: [text](url) -> <a href="url">text</a>
            escaped = re.sub(
                r'\[([^\]]+)\]\((https?://[^\s\)]+)\)',
                r'<a href="\2">\1</a>',
                escaped
            )

            # Bold: **text** or __text__ -> <b>text</b>
            escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
            escaped = re.sub(r'__(.+?)__', r'<b>\1</b>', escaped)

            # Strikethrough: ~~text~~ or ~text~ -> <s>text</s>
            escaped = re.sub(r'~~(.+?)~~', r'<s>\1</s>', escaped)
            escaped = re.sub(r'~([^~\s]+?)~', r'<s>\1</s>', escaped)

            # Italic: *text* or _text_ (excluding inside words like file_name_test)
            escaped = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'<i>\1</i>', escaped)
            escaped = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', escaped)

            formatted_parts.append(escaped)

    return "".join(formatted_parts)


def balance_telegram_html_tags(html_text: str) -> str:
    """
    Auto-balances open/unclosed HTML tags for streaming Telegram updates.
    Handles <blockquote expandable>, <blockquote>, <pre>, <code>, <b>, <i>, <s>, <a>.
    """
    if not html_text:
        return ""

    # Remove incomplete trailing tag at the very end of stream (e.g. "<pre" or "<b" or "&am")
    cleaned = re.sub(r'\s*<[a-zA-Z0-9_\-\s="]*$', '', html_text)
    cleaned = re.sub(r'\s*&[a-zA-Z0-9#]*$', '', cleaned)

    # Find all opening and closing tags
    tag_pattern = re.compile(r'<(/)?([a-zA-Z0-9_\-]+)(?:\s+[^>]*)?>')
    open_stack = []

    for match in tag_pattern.finditer(cleaned):
        is_closing = bool(match.group(1))
        tag_name = match.group(2).lower()

        # Void tags / self-closing tags (if any)
        if tag_name in ("br", "hr", "img"):
            continue

        if not is_closing:
            open_stack.append(tag_name)
        else:
            # Pop corresponding open tag
            if tag_name in open_stack:
                # Remove last occurrence
                idx = len(open_stack) - 1 - open_stack[::-1].index(tag_name)
                open_stack.pop(idx)

    # Append closing tags in reverse order
    closing_tags = "".join(f"</{tag}>" for tag in reversed(open_stack))
    return cleaned + closing_tags


def split_html_into_chunks(html_text: str, max_chars: int = 3800) -> list[str]:
    """
    Tag-Stack Aware Splitter: Splits HTML text into chunks smaller than max_chars.
    Preserves open tags across chunk boundaries (auto-closes at end of chunk, re-opens at start of next chunk).
    """
    if not html_text:
        return []

    if len(html_text) <= max_chars:
        return [balance_telegram_html_tags(html_text)]

    chunks = []
    remaining = html_text
    tag_regex = re.compile(r'<(/)?([a-zA-Z0-9_\-]+)(\s+[^>]*)?>')

    while len(remaining) > max_chars:
        # Search for safe split point before max_chars
        # Priority: newline after paragraph > any newline > tag boundary > max_chars
        split_idx = remaining.rfind('\n\n', 0, max_chars)
        if split_idx == -1 or split_idx < max_chars // 2:
            split_idx = remaining.rfind('\n', 0, max_chars)
        if split_idx == -1 or split_idx < max_chars // 2:
            split_idx = remaining.rfind('>', 0, max_chars)
            if split_idx != -1:
                split_idx += 1  # include closing '>'
        if split_idx == -1 or split_idx <= 0:
            split_idx = max_chars

        head = remaining[:split_idx]
        tail = remaining[split_idx:].lstrip('\n')

        # Analyze active open tags up to split_idx
        open_tags_with_attrs: list[str] = []
        open_tags_names: list[str] = []

        for match in tag_regex.finditer(head):
            is_closing = bool(match.group(1))
            tag_name = match.group(2).lower()
            full_open_tag = match.group(0)

            if tag_name in ("br", "hr", "img"):
                continue

            if not is_closing:
                open_tags_names.append(tag_name)
                open_tags_with_attrs.append(full_open_tag)
            else:
                if tag_name in open_tags_names:
                    idx = len(open_tags_names) - 1 - open_tags_names[::-1].index(tag_name)
                    open_tags_names.pop(idx)
                    open_tags_with_attrs.pop(idx)

        # Close all open tags in head
        head_closing = "".join(f"</{tag}>" for tag in reversed(open_tags_names))
        chunk_to_add = balance_telegram_html_tags(head + head_closing)
        if chunk_to_add.strip():
            chunks.append(chunk_to_add)

        # Reopen all active tags at the beginning of tail
        tail_opening = "".join(open_tags_with_attrs)
        remaining = tail_opening + tail

    if remaining.strip():
        chunks.append(balance_telegram_html_tags(remaining))

    return chunks


def format_hermes_html(
    text: str,
    thought: str = "",
    tool_results: list[dict[str, Any]] | None = None,
    is_final: bool = False,
    duration: float | None = None,
    agent_name: str = "",
    tool_count: int = 0,
) -> str:
    """
    Format CLI agent output into modern Telegram-friendly HTML:
    - Thinking -> <blockquote expandable><b>💭 思考過程</b>\\n...</blockquote>
    - Tool Results -> <blockquote expandable>🛠️ <b>Tool (name)</b>: <code>args</code>\\n<pre><code>result</code></pre></blockquote>
    - Main Text -> Rich Telegram HTML (bold, lists, syntax-highlighted code blocks)
    - Metadata Footer -> ⚡ duration · 🤖 agent · 🛠️ tool_count (at turn end)
    """
    clean_text = clean_cli_output(text)
    clean_thought = clean_cli_output(thought)

    sections = []

    # 1. Expandable Thinking Block
    if clean_thought.strip():
        escaped_thought = escape_html(clean_thought.strip())
        thought_html = f"<blockquote expandable><b>💭 思考過程</b>\n{escaped_thought}</blockquote>"
        sections.append(thought_html)

    # 2. Tool Executions & Results (if any)
    if tool_results:
        for tool in tool_results:
            name = escape_html(str(tool.get("tool_name", "Tool")))
            preview = escape_html(str(tool.get("preview", "")).strip())
            content = escape_html(str(tool.get("content", "")).strip())

            if content:
                tool_html = (
                    f"<blockquote expandable>🛠️ <b>Tool ({name})</b>"
                    + (f": <code>{preview}</code>" if preview else "")
                    + f"\n<pre><code>{content}</code></pre></blockquote>"
                )
            else:
                tool_html = f"🛠️ <b>Tool ({name})</b>" + (f": <code>{preview}</code>" if preview else "")

            sections.append(tool_html)

    # 3. Main Response Body
    if clean_text.strip():
        body_html = markdown_to_telegram_html(clean_text)
        sections.append(body_html)

    # 4. Turn End Metadata Footer
    if is_final and (duration is not None or agent_name):
        footer_parts = []
        if duration is not None:
            footer_parts.append(f"⚡ <code>{duration:.1f}s</code>")
        if agent_name:
            footer_parts.append(f"🤖 <b>{escape_html(agent_name)}</b>")
        if tool_count > 0:
            footer_parts.append(f"🛠️ <b>{tool_count} 工具</b>")

        if footer_parts:
            footer_html = "\n\n—\n" + " · ".join(footer_parts)
            if sections:
                sections[-1] = sections[-1] + footer_html
            else:
                sections.append(footer_html)

    full_output = "\n\n".join(s for s in sections if s.strip())
    return balance_telegram_html_tags(full_output)


def format_hermes_style(text: str) -> str:
    """
    Compatibility wrapper: Format CLI agent output into Hermes style.
    Maintains compatibility with tests and callers expecting format_hermes_style.
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

