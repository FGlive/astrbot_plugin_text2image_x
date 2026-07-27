"""轻量级 Markdown 解析器"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from .styles import TextSegment, TableRow, TableCell

# 行内样式正则模式（从长到短排序；链接/图片必须最先匹配）
INLINE_PATTERNS = [
    (r'!\[([^\]]*)\]\(([^)]+)\)', 'image'),    # ![图片](url)
    (r'\[([^\]]*)\]\(([^)]+)\)', 'link'),       # [链接](url)
    (r'\*\*(.+?)\*\*', 'bold'),      # **粗体**
    (r'__(.+?)__', 'bold'),          # __粗体__
    (r'~~(.+?)~~', 'strike'),        # ~~删除线~~
    (r'``(.+?)``', 'code'),          # ``代码``
    (r'\*(.+?)\*', 'italic'),        # *斜体*
    (r'(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)', 'italic'),  # _斜体_（需词边界）
    (r'`(.+?)`', 'code'),            # `代码`
]


@dataclass
class LineContext:
    """行上下文信息"""
    in_code_block: bool = False
    code_block_lang: str = ""
    code_lines: list[str] = field(default_factory=list)
    in_table: bool = False
    table_rows: List[TableRow] = field(default_factory=list)
    table_header_parsed: bool = False
    hide_table_first_column_label: bool = False
    em_open_bold: bool = False
    em_open_italic: bool = False
    em_open_strike: bool = False
    em_pending_bold_close: bool = False


def parse_markdown(text: str, ctx: LineContext = None) -> list[TextSegment]:
    """
    解析 Markdown 行为样式片段列表
    支持标题、引用、代码块、表格、分割线、行内样式
    """
    if ctx is None:
        ctx = LineContext()

    if not text:
        return []

    # 处理代码块
    if ctx.in_code_block:
        if text.strip() == '```':
            ctx.in_code_block = False
            code_lines = ctx.code_lines[:]
            ctx.code_lines.clear()

            # 将代码块内容按行输出，每行后追加一个强制换行段，避免渲染层把多行压到同一逻辑行
            segments: list[TextSegment] = []
            for line in code_lines:
                segments.append(TextSegment(text=line, code_block=True, no_wrap=True))
                segments.append(TextSegment(text="", is_newline=True))
            return segments
        else:
            ctx.code_lines.append(text)
            return []

    # 检查代码块开始
    code_block_match = re.match(r'^```(\w*)\s*$', text)
    if code_block_match:
        ctx.in_code_block = True
        ctx.code_block_lang = code_block_match.group(1)
        # 代码块内禁用自动闭合，进入代码块时重置跨行状态
        ctx.em_open_bold = False
        ctx.em_open_italic = False
        ctx.em_open_strike = False
        ctx.em_pending_bold_close = False
        return []

    # 检查分割线 (--- 或 *** 或 ___)
    if re.match(r'^[\s\-*_]{3,}\s*$', text.strip()):
        # 如果在表格中，结束表格并返回列表形式
        if ctx.in_table:
            table_segments = _serialize_table(ctx)
            ctx.in_table = False
            ctx.table_rows.clear()
            ctx.table_header_parsed = False
            return table_segments

        # 分割线作为样式边界，重置跨行强调状态
        ctx.em_open_bold = False
        ctx.em_open_italic = False
        ctx.em_open_strike = False
        ctx.em_pending_bold_close = False
        return [TextSegment(text="", horizontal_rule=True)]

    # 检查表格
    table_match = re.match(r'^\|(.+)\|\s*$', text)
    if table_match:
        row_text = table_match.group(1).strip()
        cells = [c.strip() for c in row_text.split('|')]

        # 检查是否是分隔行 (|---|---|)
        if re.match(r'^[\s\-:]+$', cells[0] if cells else ''):
            ctx.table_header_parsed = True
            return []

        # 解析单元格内容
        cell_segments = []
        for cell_text in cells:
            segments = _parse_inline_styles(cell_text)
            cell_segments.append(TableCell(text=cell_text, segments=segments))

        is_header = not ctx.table_header_parsed
        ctx.table_rows.append(TableRow(cells=cell_segments, is_header=is_header))
        ctx.in_table = True
        return []

    # 如果之前在表格中，现在表格结束了
    if ctx.in_table:
        table_segments = _serialize_table(ctx)
        ctx.in_table = False
        ctx.table_rows.clear()
        ctx.table_header_parsed = False
        # 返回表格后继续解析当前行
        segments = _parse_line(text, ctx)
        # 在表格和当前行之间插入换行
        if segments:
            return table_segments + [TextSegment(text="", horizontal_rule=False)] + segments
        return table_segments

    # 检查标题 (# ## ### 等)
    heading_match = re.match(r'^(#{1,6})\s+(.+)$', text)
    if heading_match:
        level = len(heading_match.group(1))
        content = heading_match.group(2)
        segments = _parse_inline_styles_with_autoclose(content, ctx)
        for seg in segments:
            seg.heading = level
        return segments

    # 检查引用 (>)
    if text.startswith('>'):
        quote_text = text[1:].lstrip()
        segments = _parse_inline_styles_with_autoclose(quote_text, ctx)
        for seg in segments:
            seg.quote = True
        return segments

    # 检查无序列表 (* + -)
    unordered_match = re.match(r'^(\s*)([*+-])\s+(.+)$', text)
    if unordered_match:
        indent = unordered_match.group(1)
        content = unordered_match.group(3)
        list_level = len(indent) // 2  # 每2个空格算一级缩进
        segments = _parse_inline_styles_with_autoclose(content, ctx)
        for seg in segments:
            seg.list_item = True
            seg.list_ordered = False
            seg.list_level = list_level
        return segments

    # 检查有序列表 (1. 2. 3.)
    ordered_match = re.match(r'^(\s*)(\d+)\.\s+(.+)$', text)
    if ordered_match:
        indent = ordered_match.group(1)
        index = int(ordered_match.group(2))
        content = ordered_match.group(3)
        list_level = len(indent) // 2  # 每2个空格算一级缩进
        segments = _parse_inline_styles_with_autoclose(content, ctx)
        for seg in segments:
            seg.list_item = True
            seg.list_ordered = True
            seg.list_level = list_level
            seg.list_index = index
        return segments

    # 普通行内解析
    return _parse_inline_styles_with_autoclose(text, ctx)


def _parse_line(text: str, ctx: LineContext) -> list[TextSegment]:
    """解析单行"""
    if not text:
        return []

    # 检查标题
    heading_match = re.match(r'^(#{1,6})\s+(.+)$', text)
    if heading_match:
        level = len(heading_match.group(1))
        content = heading_match.group(2)
        segments = _parse_inline_styles_with_autoclose(content, ctx)
        for seg in segments:
            seg.heading = level
        return segments

    # 检查引用
    if text.startswith('>'):
        quote_text = text[1:].lstrip()
        segments = _parse_inline_styles_with_autoclose(quote_text, ctx)
        for seg in segments:
            seg.quote = True
        return segments

    return _parse_inline_styles_with_autoclose(text, ctx)


def _serialize_table(ctx: LineContext) -> list[TextSegment]:
    """将当前累积的表格生成为结构化表格占位段"""
    if not ctx.table_rows:
        return []

    rows = [TableRow(cells=[
        TableCell(text=cell.text, segments=[
            TextSegment(
                text=seg.text,
                is_emoji=seg.is_emoji,
                bold=seg.bold,
                italic=seg.italic,
                code=seg.code,
                strike=seg.strike,
                url=seg.url,
                is_image=seg.is_image,
            )
            for seg in cell.segments
        ])
        for cell in row.cells
    ], is_header=row.is_header) for row in ctx.table_rows]

    return [TextSegment(text="", is_table=True, table_rows=rows)]


def _parse_inline_styles_with_autoclose(text: str, ctx: LineContext) -> list[TextSegment]:
    """按上下文自动闭合强调样式后解析行内样式。"""
    if not text:
        return []

    normalized = _normalize_escaped_for_recursive(text)
    segments, next_bold, next_italic, next_strike, next_pending_bold_close = _parse_line_with_emphasis_state(
        normalized,
        ctx.em_open_bold,
        ctx.em_open_italic,
        ctx.em_open_strike,
        ctx.em_pending_bold_close,
    )

    for seg in segments:
        seg.text = seg.text.replace("＊", "*").replace("⎽", "_").replace("⍗", "`").replace("∼", "~").replace("＼", "\\")

    ctx.em_open_bold = next_bold
    ctx.em_open_italic = next_italic
    ctx.em_open_strike = next_strike
    ctx.em_pending_bold_close = next_pending_bold_close
    return _merge_segments(segments)


def _parse_inline_styles(text: str) -> list[TextSegment]:
    """解析行内样式"""
    if not text:
        return []

    # 保护转义字符，避免被递归解析器误判
    normalized = _normalize_escaped_for_recursive(text)
    segments = _parse_recursive(normalized)
    for seg in segments:
        seg.text = seg.text.replace("＊", "*").replace("⎽", "_").replace("⍗", "`").replace("∼", "~").replace("＼", "\\")
    return _merge_segments(segments)


def _normalize_escaped_for_recursive(text: str) -> str:
    """保护转义字符，避免被递归解析器误判。"""
    # 顺序：先处理 \\ 避免干扰其他转义
    result = text.replace("\\\\", "＼")
    result = result.replace("\\*", "＊")
    result = result.replace("\\_", "⎽")
    result = result.replace("\\`", "⍗")
    result = result.replace("\\~", "∼")
    return result


def _parse_line_with_emphasis_state(text: str,
                                    bold_open: bool,
                                    italic_open: bool,
                                    strike_open: bool,
                                    pending_bold_close: bool) -> tuple[list[TextSegment], bool, bool, bool, bool]:
    """按顺序解析文本并维护跨行强调状态。"""
    segments: list[TextSegment] = []
    buffer: list[str] = []
    in_code = False
    i = 0

    def flush_buffer():
        if not buffer:
            return

        value = ''.join(buffer)
        # 仅保护状态机已消耗的样式字符（* 和 `），_ 和 ~ 由递归解析器处理
        safe_value = value.replace('*', '＊').replace('`', '⍗')
        parsed = _parse_inline_styles(safe_value)
        if not parsed:
            parsed = [TextSegment(text=safe_value)]

        for seg in parsed:
            seg.text = seg.text.replace('＊', '*').replace('⍗', '`')
            if not seg.code and not seg.code_block:
                if bold_open:
                    seg.bold = True
                if italic_open:
                    seg.italic = True
                if strike_open:
                    seg.strike = True
            segments.append(seg)

        buffer.clear()

    def has_content_after(start: int) -> bool:
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch.isspace() or ch in ('`',):
                continue
            return True
        return False

    while i < len(text):
        if text[i] == '`':
            in_code = not in_code
            buffer.append(text[i])
            i += 1
            continue

        if in_code:
            buffer.append(text[i])
            i += 1
            continue

        if text.startswith("~~", i):
            flush_buffer()
            if strike_open:
                strike_open = False
            else:
                if has_content_after(i + 2):
                    strike_open = True
                else:
                    buffer.append("~~")
            i += 2
            continue

        if text.startswith("**", i):
            flush_buffer()
            pending_bold_close = False
            if bold_open:
                bold_open = False
            else:
                if has_content_after(i + 2) or italic_open:
                    bold_open = True
                else:
                    buffer.append("**")
            i += 2
            continue

        if text[i] == '*':
            flush_buffer()
            if italic_open:
                italic_open = False
                pending_bold_close = False
            elif pending_bold_close and bold_open and not has_content_after(i + 1):
                # 支持跨行被拆开的 "**" 结束标记（如 "**重点*\n*"）
                bold_open = False
                pending_bold_close = False
            elif bold_open and not has_content_after(i + 1):
                # 当前行以单星结束时，标记下一行若仅剩一个星号则补全粗体闭合
                pending_bold_close = True
            else:
                pending_bold_close = False
                if has_content_after(i + 1) or bold_open:
                    italic_open = True
                else:
                    buffer.append('*')
            i += 1
            continue

        buffer.append(text[i])
        i += 1

    flush_buffer()
    return segments, bold_open, italic_open, strike_open, pending_bold_close


def _parse_recursive(text: str) -> list[TextSegment]:
    """递归解析嵌套样式"""
    segments: list[TextSegment] = []
    pos = 0

    while pos < len(text):
        earliest_match = None
        pattern_idx = -1
        earliest_pos = len(text)

        for idx, (pattern, _) in enumerate(INLINE_PATTERNS):
            match = re.search(pattern, text[pos:])
            if match:
                match_start = pos + match.start()
                if match_start < earliest_pos:
                    earliest_pos = match_start
                    earliest_match = (match.start(), match.end(), match)
                    pattern_idx = idx

        if earliest_match:
            start, end, match = earliest_match
            style_type = INLINE_PATTERNS[pattern_idx][1]

            if start > 0:
                segments.append(TextSegment(text=text[pos:pos + start]))

            inner_text = match.group(1)
            inner_segments = _parse_recursive(inner_text)

            for seg in inner_segments:
                _apply_style(seg, style_type)
                if style_type == 'link':
                    seg.url = match.group(2)
                elif style_type == 'image':
                    seg.url = match.group(2)
                    seg.is_image = True
            segments.extend(inner_segments)

            pos += end
        else:
            remaining = text[pos:]
            if remaining:
                segments.append(TextSegment(text=remaining))
            break

    return segments


def _apply_style(segment: TextSegment, style_type: str):
    """应用样式到片段"""
    if style_type == 'bold':
        segment.bold = True
    elif style_type == 'italic':
        segment.italic = True
    elif style_type == 'code':
        segment.code = True
    elif style_type == 'strike':
        segment.strike = True


def _merge_segments(segments: list[TextSegment]) -> list[TextSegment]:
    """合并相邻的同样式文本"""
    if not segments:
        return []

    merged = [segments[0]]

    for seg in segments[1:]:
        last = merged[-1]

        if (last.text and seg.text and
                not seg.is_emoji and not last.is_emoji and
                not seg.no_wrap and not last.no_wrap and
                last.heading == seg.heading and
                last.quote == seg.quote and
                last.code_block == seg.code_block and
                last.horizontal_rule == seg.horizontal_rule and
                last.bold == seg.bold and
                last.italic == seg.italic and
                last.code == seg.code and
                last.strike == seg.strike and
                last.list_item == seg.list_item and
                last.list_ordered == seg.list_ordered and
                last.list_level == seg.list_level and
                last.list_index == seg.list_index and
                last.list_continuation == seg.list_continuation and
                last.url == seg.url and
                last.is_image == seg.is_image and
                last.is_table == seg.is_table and
                last.table_rows == seg.table_rows):
            last.text += seg.text
        else:
            merged.append(seg)

    return merged


def parse_table(text: str) -> Optional[List[TableRow]]:
    """解析表格文本为 TableRow 列表"""
    lines = text.strip().split('\n')
    if not lines:
        return None

    rows = []
    for i, line in enumerate(lines):
        # 移除 │ 符号
        line = line.replace('│', '').strip()
        if not line or re.match(r'^[\s|\-:]+$', line):
            continue

        cells = [c.strip() for c in line.split('|')]
        cell_segments = []
        for cell_text in cells:
            if not cell_text:
                continue
            segments = _parse_inline_styles(cell_text)
            cell_segments.append(TableCell(text=cell_text, segments=segments))

        if cell_segments:
            is_header = (i == 0)
            rows.append(TableRow(cells=cell_segments, is_header=is_header))

    return rows if rows else None
