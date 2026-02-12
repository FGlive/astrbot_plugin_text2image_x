"""轻量级 Markdown 解析器"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from .styles import TextSegment, TableRow, TableCell

# 行内样式正则模式（从长到短排序）
INLINE_PATTERNS = [
    (r'\*\*(.+?)\*\*', 'bold'),      # **粗体**
    (r'__(.+?)__', 'bold'),          # __粗体__
    (r'~~(.+?)~~', 'strike'),        # ~~删除线~~
    (r'``(.+?)``', 'code'),          # ``代码``
    (r'\*(.+?)\*', 'italic'),        # *斜体*
    (r'_(.+?)_', 'italic'),          # _斜体_
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
            code_content = '\n'.join(ctx.code_lines)
            ctx.code_lines.clear()
            return [TextSegment(text=code_content, code_block=True, no_wrap=True)]
        else:
            ctx.code_lines.append(text)
            return []

    # 检查代码块开始
    code_block_match = re.match(r'^```(\w*)\s*$', text)
    if code_block_match:
        ctx.in_code_block = True
        ctx.code_block_lang = code_block_match.group(1)
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
        segments = _parse_inline_styles(content)
        for seg in segments:
            seg.heading = level
        return segments

    # 检查引用 (>)
    if text.startswith('>'):
        quote_text = text[1:].lstrip()
        segments = _parse_inline_styles(quote_text)
        for seg in segments:
            seg.quote = True
        return segments

    # 检查无序列表 (* + -)
    unordered_match = re.match(r'^(\s*)([*+-])\s+(.+)$', text)
    if unordered_match:
        indent = unordered_match.group(1)
        content = unordered_match.group(3)
        list_level = len(indent) // 2  # 每2个空格算一级缩进
        segments = _parse_inline_styles(content)
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
        segments = _parse_inline_styles(content)
        for seg in segments:
            seg.list_item = True
            seg.list_ordered = True
            seg.list_level = list_level
            seg.list_index = index
        return segments

    # 普通行内解析
    return _parse_inline_styles(text)


def _parse_line(text: str, ctx: LineContext) -> list[TextSegment]:
    """解析单行"""
    if not text:
        return []

    # 检查标题
    heading_match = re.match(r'^(#{1,6})\s+(.+)$', text)
    if heading_match:
        level = len(heading_match.group(1))
        content = heading_match.group(2)
        segments = _parse_inline_styles(content)
        for seg in segments:
            seg.heading = level
        return segments

    # 检查引用
    if text.startswith('>'):
        quote_text = text[1:].lstrip()
        segments = _parse_inline_styles(quote_text)
        for seg in segments:
            seg.quote = True
        return segments

    return _parse_inline_styles(text)


def _serialize_table(ctx: LineContext) -> list[TextSegment]:
    """将表格转换为列表形式的 TextSegment 集合"""
    if not ctx.table_rows:
        return []

    # 提取表头作为字段名
    headers = []
    data_rows = []
    max_cols = 0

    for row in ctx.table_rows:
        max_cols = max(max_cols, len(row.cells))
        if row.is_header and not headers:
            headers = [cell.text for cell in row.cells]
        else:
            data_rows.append(row)

    # 如果没有表头，使用默认字段名
    if not headers:
        headers = [f"字段{i + 1}" for i in range(max_cols)]

    # 如果所有行都是表头，则没有数据行
    if not data_rows:
        data_rows = ctx.table_rows

    # 为每个单元格生成列表项
    result_segments = []
    for row in data_rows:
        for col_idx, cell in enumerate(row.cells):
            # 获取字段名
            field_name = headers[col_idx] if col_idx < len(headers) else f"字段{col_idx + 1}"

            # 创建标签片段（字段名）
            label_seg = TextSegment(text=f"{field_name}：")
            label_seg.list_item = True
            label_seg.list_ordered = False
            label_seg.list_level = 0

            # 获取单元格内容片段并继承列表属性
            cell_content = []
            for seg in cell.segments:
                seg.list_item = True
                seg.list_ordered = False
                seg.list_level = 0
                cell_content.append(seg)

            # 如果单元格有内容，合并标签和内容
            if cell_content and any(seg.text for seg in cell_content):
                result_segments.append(label_seg)
                result_segments.extend(cell_content)
            else:
                # 单元格为空，只显示标签
                result_segments.append(label_seg)

    return result_segments


def _parse_inline_styles(text: str) -> list[TextSegment]:
    """解析行内样式"""
    if not text:
        return []

    segments = _parse_recursive(text)
    return _merge_segments(segments)


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
                last.list_continuation == seg.list_continuation):
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
