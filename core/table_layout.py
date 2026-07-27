"""表格渲染：低宽度卡片式键值布局"""

from typing import Any, Dict, List, Optional, Tuple

from .styles import TableRow, TableCell, TextSegment


def _resolve_table_data(table_data: List[TableRow]) -> Tuple[List[str], List[TableRow]]:
    """从表格数据中提取表头和数据行"""
    headers: List[str] = []
    data_rows: List[TableRow] = []
    max_cols = 0

    for row in table_data:
        max_cols = max(max_cols, len(row.cells))
        if row.is_header and not headers:
            headers = [cell.text for cell in row.cells]
        else:
            data_rows.append(row)

    if not data_rows:
        data_rows = table_data

    if not headers:
        headers = [f"字段{i + 1}" for i in range(max_cols)]

    return headers, data_rows


def _cell_is_empty(cell: TableCell) -> bool:
    return not cell.segments or not any(seg.text for seg in cell.segments)


def _copy_segments(segments: List[TextSegment]) -> List[TextSegment]:
    """复制行内样式片段"""
    return [
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
        for seg in segments
    ]


def _make_label_text(field_name: str) -> str:
    return f"{field_name}："


def calc_table_card_height(
    renderer: Any,
    table_data: List[TableRow],
    table_line_height: int,
    font: Any,
    mono_font: Any,
    content_width: int,
    scale: int,
    hide_first_col_label: bool,
) -> int:
    """计算卡片式表格的总高度"""
    headers, data_rows = _resolve_table_data(table_data)
    if not data_rows:
        return table_line_height

    card_padding = int(8 * scale)
    card_margin = int(10 * scale)
    bar_width = max(1, int(4 * scale))
    inner_width = max(1, content_width - card_padding * 2 - bar_width)

    label_gap = int(6 * scale)
    labels = [
        _make_label_text(headers[col_idx] if col_idx < len(headers) else f"字段{col_idx + 1}")
        for col_idx in range(len(headers))
    ]

    max_label_width = 0
    start_col = 1 if hide_first_col_label else 0
    for col_idx in range(start_col, len(labels)):
        max_label_width = max(max_label_width, int(font.getlength(labels[col_idx])))

    label_col_width = min(max_label_width + label_gap, int(inner_width * 0.45))
    value_col_width = max(1, inner_width - label_col_width - label_gap)

    total_height = 0
    for row in data_rows:
        title_lines = 0
        field_lines = 0

        col_start = 0
        if hide_first_col_label:
            title_cell = row.cells[0] if row.cells else None
            if title_cell and not _cell_is_empty(title_cell):
                title_segments = _copy_segments(title_cell.segments)
                for seg in title_segments:
                    seg.bold = True
                title_lines = len(renderer._wrap_text_segments_for_render(
                    title_segments, font, mono_font, inner_width
                ))
            col_start = 1

        for col_idx in range(col_start, len(row.cells)):
            cell = row.cells[col_idx]
            if _cell_is_empty(cell):
                continue

            label_text = labels[col_idx] if col_idx < len(labels) else _make_label_text(f"字段{col_idx + 1}")
            label_segment = TextSegment(text=label_text, bold=True)
            label_wrap = renderer._wrap_text_segments_for_render(
                [label_segment], font, mono_font, label_col_width
            )

            value_wrap = renderer._wrap_text_segments_for_render(
                _copy_segments(cell.segments), font, mono_font, value_col_width
            )

            field_lines += max(len(label_wrap), len(value_wrap))

        if field_lines == 0 and title_lines == 0:
            field_lines = 1

        total_height += title_lines * table_line_height + field_lines * table_line_height + card_padding * 2

    if len(data_rows) > 1:
        total_height += card_margin * (len(data_rows) - 1)

    return total_height


def draw_table_cards(
    renderer: Any,
    draw: Any,
    table_data: List[TableRow],
    x: int,
    y: int,
    content_width: int,
    font: Any,
    mono_font: Any,
    font_size: int,
    table_line_height: int,
    scale: int,
    text_rgb: Tuple[int, int, int],
    bg_rgb: Tuple[int, int, int],
    hide_first_col_label: bool,
) -> int:
    """绘制卡片式表格，返回底部 y 坐标"""
    headers, data_rows = _resolve_table_data(table_data)
    if not data_rows:
        return y

    card_padding = int(8 * scale)
    card_margin = int(10 * scale)
    bar_width = max(1, int(4 * scale))
    inner_width = max(1, content_width - card_padding * 2 - bar_width)

    label_gap = int(6 * scale)
    labels = [
        _make_label_text(headers[col_idx] if col_idx < len(headers) else f"字段{col_idx + 1}")
        for col_idx in range(len(headers))
    ]

    max_label_width = 0
    start_col = 1 if hide_first_col_label else 0
    for col_idx in range(start_col, len(labels)):
        max_label_width = max(max_label_width, int(font.getlength(labels[col_idx])))

    label_col_width = min(max_label_width + label_gap, int(inner_width * 0.45))
    value_col_width = max(1, inner_width - label_col_width - label_gap)

    bar_color = (100, 149, 237)
    card_bg = (245, 245, 245)
    label_color = (90, 90, 90)

    current_y = y

    for row in data_rows:
        card_lines: List[Tuple[List[Any], int, int]] = []

        col_start = 0
        title_height = 0
        if hide_first_col_label:
            title_cell = row.cells[0] if row.cells else None
            if title_cell and not _cell_is_empty(title_cell):
                title_segments = _copy_segments(title_cell.segments)
                for seg in title_segments:
                    seg.bold = True
                title_lines = renderer._wrap_text_segments_for_render(
                    title_segments, font, mono_font, inner_width
                )
                card_lines.append((title_lines, 0, 0))
                title_height = len(title_lines) * table_line_height

        for col_idx in range(col_start, len(row.cells)):
            cell = row.cells[col_idx]
            if _cell_is_empty(cell):
                continue

            label_text = labels[col_idx] if col_idx < len(labels) else _make_label_text(f"字段{col_idx + 1}")
            label_segment = TextSegment(text=label_text, bold=True)
            label_lines = renderer._wrap_text_segments_for_render(
                [label_segment], font, mono_font, label_col_width
            )

            value_segments = _copy_segments(cell.segments)
            value_lines = renderer._wrap_text_segments_for_render(
                value_segments, font, mono_font, value_col_width
            )

            # Each field occupies max line count of label/value
            line_count = max(len(label_lines), len(value_lines))
            card_lines.append((label_lines, value_lines, line_count))

        line_count = sum(item[2] for item in card_lines)
        if line_count == 0:
            card_lines = [([TextSegment(text="", bold=True)], [], 1)]
            line_count = 1

        card_height = title_height + line_count * table_line_height + card_padding * 2

        draw.rounded_rectangle(
            [x, current_y, x + content_width, current_y + card_height],
            radius=6 * scale,
            fill=card_bg,
        )
        draw.rectangle(
            [x, current_y, x + bar_width, current_y + card_height],
            fill=bar_color,
        )

        text_x = x + bar_width + card_padding
        line_y = current_y + card_padding

        for item in card_lines:
            label_lines, value_lines, count = item
            is_title = (value_lines == 0)

            for row_line_idx in range(count):
                label_line = label_lines[row_line_idx] if row_line_idx < len(label_lines) else []
                value_line = value_lines[row_line_idx] if row_line_idx < len(value_lines) else []

                renderer._draw_segment_line(
                    draw,
                    label_line,
                    text_x,
                    line_y,
                    table_line_height,
                    font,
                    mono_font,
                    font_size,
                    scale,
                    text_rgb,
                    override_color=None if is_title else label_color,
                )

                if value_line:
                    renderer._draw_segment_line(
                        draw,
                        value_line,
                        text_x + label_col_width + label_gap,
                        line_y,
                        table_line_height,
                        font,
                        mono_font,
                        font_size,
                        scale,
                        text_rgb,
                    )

                line_y += table_line_height

        current_y += card_height + card_margin

    return current_y
