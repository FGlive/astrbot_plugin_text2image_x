"""文本渲染器"""

import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .styles import TextSegment, TableRow
from .table_layout import calc_table_card_height, draw_table_cards
from .emoji import EmojiHandler
from .markdown import parse_markdown, LineContext, parse_table, _serialize_table

try:
    from astrbot.api import logger
except Exception:  # 兼容独立测试/未安装 AstrBot 的环境
    import logging
    logger = logging.getLogger("astrbot_plugin_text2image_x.renderer")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

# 不能出现在行首的标点符号（避头标点）
NO_LINE_START = set('，。、；：？！）】》」』"\',.;:?!)>]}·…—～')


class TextRenderer:
    """文本渲染器"""

    def __init__(self, config: Dict[str, Any], font_dir: Path):
        import tempfile
        
        self.config = config
        self.font_dir = font_dir
        
        # 从配置中读取 Emoji 相关参数
        emoji_timeout = int(self._get_config("emoji_timeout", 10))
        emoji_failed_ttl = int(self._get_config("emoji_failed_ttl", 3600))
        emoji_cache_dir = self._get_config("emoji_cache_dir", None)
        emoji_mode = str(self._get_config("emoji_mode", "auto")).lower().strip()

        # 转换缓存目录路径（如果提供）
        cache_dir = Path(emoji_cache_dir) if emoji_cache_dir else font_dir.parent / ".emoji-cache"

        self._emoji_font_cache: Dict[str, Optional[ImageFont.FreeTypeFont]] = {}
        self.emoji_handler = EmojiHandler(
            font_dir=font_dir,
            cache_dir=cache_dir,
            timeout=emoji_timeout,
            failed_ttl=emoji_failed_ttl,
            emoji_mode=emoji_mode,
            fallback_font_provider=self._load_emoji_font,
        )
        self._font_cache: Dict[str, ImageFont.FreeTypeFont] = {}
        self._mono_font_cache: Dict[str, ImageFont.FreeTypeFont] = {}
        self._char_width_cache: OrderedDict[Tuple[int, str, bool], int] = OrderedDict()
        self._char_width_cache_limit = max(1024, int(self._get_config("char_width_cache_limit", 8192)))

    def _get_config(self, key: str, default: Any) -> Any:
        return self.config.get(key, default)

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """加载字体
        
        优先级: 配置字体 -> 默认字体 -> 系统默认字体
        """
        cache_key = f"{size}_{bold}"
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        # 从配置获取字体文件名，默认使用 Source Han Serif
        configured_font = self._get_config("font_name", "Source_Han_Serif_SC_Light_Light.otf")
        default_font = "Source_Han_Serif_SC_Light_Light.otf"
        
        # 按优先级尝试加载字体
        for font_name in [configured_font, default_font]:
            if not font_name:
                continue
                
            font_path = self.font_dir / font_name
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), size=size)
                    self._font_cache[cache_key] = font
                    return font
                except Exception:
                    continue
        
        # 所有尝试都失败，回退到系统默认字体
        return ImageFont.load_default()

    def _load_mono_font(self, size: int) -> Optional[ImageFont.FreeTypeFont]:
        """加载等宽字体
        
        优先级: 配置字体 -> ziti 目录字体文件 -> 系统字体探测
        """
        cache_key = f"mono_{size}"
        if cache_key in self._mono_font_cache:
            return self._mono_font_cache[cache_key]

        # 优先从配置获取等宽字体
        configured_font = self._get_config("mono_font_name", "")
        if configured_font:
            font_path = self.font_dir / configured_font
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), size=size)
                    self._mono_font_cache[cache_key] = font
                    return font
                except Exception:
                    pass  # 配置字体加载失败，继续尝试系统字体

        # 系统字体名称列表（按优先级）
        mono_font_names = [
            "Consola.ttf", "Consolas.ttf",
            "Courier New.ttf", "cour.ttf",
            "SourceCodePro.ttf", "FiraCode.ttf",
        ]

        # 系统字体路径列表（按优先级）
        system_font_paths = [
            Path("C:/Windows/Fonts"),
            Path("/usr/share/fonts"),
            Path("/System/Library/Fonts"),
            self.font_dir,  # 最后尝试 ziti 目录
        ]

        for font_dir in system_font_paths:
            if not font_dir.exists():
                continue
            for font_name in mono_font_names:
                font_path = font_dir / font_name
                if font_path.exists():
                    try:
                        font = ImageFont.truetype(str(font_path), size=size)
                        self._mono_font_cache[cache_key] = font
                        return font
                    except Exception:
                        continue

        self._mono_font_cache[cache_key] = None
        return None

    def _load_emoji_font(self, size: int) -> Optional[ImageFont.FreeTypeFont]:
        """加载彩色 emoji 字体

        优先级: 配置字体 -> 系统字体探测 -> ziti 目录探测
        """
        cache_key = f"emoji_{size}"
        if cache_key in self._emoji_font_cache:
            return self._emoji_font_cache[cache_key]

        configured_font = self._get_config("emoji_font_name", "")
        candidates = []

        if configured_font:
            candidates.append(self.font_dir / configured_font)

        system_font_paths = [
            Path("C:/Windows/Fonts"),
            Path("/usr/share/fonts"),
            Path("/System/Library/Fonts"),
            self.font_dir,
        ]
        system_font_names = [
            "NotoColorEmoji.ttf",
            "NotoColorEmoji-Regular.ttf",
            "TwemojiMozilla.ttf",
            "Segoe UI Emoji.ttf",
            "seguiemj.ttf",
            "Apple Color Emoji.ttc",
            "Apple Color Emoji.ttf",
        ]

        for font_dir in system_font_paths:
            if not font_dir.exists():
                continue
            for font_name in system_font_names:
                candidates.append(font_dir / font_name)

        for font_path in candidates:
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), size=size)
                    self._emoji_font_cache[cache_key] = font
                    return font
                except Exception:
                    continue

        self._emoji_font_cache[cache_key] = None
        return None

    def _hex_to_rgb(self, color_value: str, fallback: str = "#ffffff") -> Tuple[int, int, int]:
        """颜色字符串转 RGB，兼容十六进制与命名色。"""
        try:
            return ImageColor.getrgb(str(color_value).strip())
        except Exception:
            try:
                return ImageColor.getrgb(str(fallback).strip())
            except Exception:
                return 255, 255, 255

    def render(self, text: str) -> Optional[str]:
        """渲染文本为图片"""
        # 兼容 LLM 输出的字面量 \n（仅在无真实换行时处理）
        if "\\n" in text and "\n" not in text:
            text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
        width = int(self._get_config("image_width", 375))
        scale = int(self._get_config("image_scale", 2))
        padding = int(self._get_config("padding", 24))
        padding_left = int(self._get_config("padding_left", padding))
        padding_right = int(self._get_config("padding_right", padding))
        font_size = int(self._get_config("font_size", 24))
        line_height = float(self._get_config("line_height", 1.6))
        bg_color = str(self._get_config("bg_color", "#ffffff"))
        text_color = str(self._get_config("text_color", "#333333"))

        real_width = width * scale
        real_padding_y = padding * scale
        real_padding_left = max(0, padding_left * scale)
        real_padding_right = max(0, padding_right * scale)
        real_font_size = font_size * scale
        emoji_size = int(real_font_size * 1.1)

        # 若可能使用本地彩色 emoji 字体，提前加载一次（按实际渲染尺寸）
        emoji_font = None
        if self.emoji_handler.use_fallback_font:
            emoji_font = self._load_emoji_font(emoji_size)

        content_left = min(real_width - 1, real_padding_left) if real_width > 0 else 0
        content_right = max(content_left + 1, real_width - real_padding_right)
        content_right = min(real_width, content_right)
        text_area_width = max(1, content_right - content_left)

        font = self._load_font(real_font_size)
        font_height = self._get_font_height(font, real_font_size)
        line_pixel_height = int(real_font_size * line_height)
        table_line_height = int(real_font_size * 1.35)

        # 解析所有行
        lines = text.split('\n')
        hide_first_col_label = bool(self._get_config("hide_table_first_column_label", False))
        ctx = LineContext(hide_table_first_column_label=hide_first_col_label)
        render_items = []  # (segments, is_empty, is_table, is_hr, table_data)

        for line in lines:
            md_segments = parse_markdown(line, ctx)

            # 检查是否是分割线
            if md_segments and md_segments[0].horizontal_rule:
                render_items.append(([], False, False, True, None))
                continue

            # 检查是否是结构化表格标记
            if md_segments and md_segments[0].is_table:
                table_data = md_segments[0].table_rows
                if table_data:
                    render_items.append(([], False, True, False, table_data))
                continue

            # 保留旧兼容分支
            if md_segments and md_segments[0].no_wrap and md_segments[0].text:
                table_data = parse_table(md_segments[0].text)
                if table_data:
                    render_items.append(([], False, True, False, table_data))
                    continue

            if not md_segments:
                # 表格/代码块解析过程中的中间行不应生成空白占位
                if ctx.in_table or ctx.in_code_block:
                    continue
                render_items.append(([], True, False, False, None))
                continue

            # 处理每个片段
            segments = []
            for seg in md_segments:
                if getattr(seg, "is_newline", False):
                    segments.append(seg)
                    continue

                if seg.code_block:
                    # 对代码块每行进行 emoji 分割（markdown.py 已按行拆分；这里只做 emoji 拆分，不再 split('\n')）
                    emoji_subs = self.emoji_handler.split_text(seg.text)
                    for es in emoji_subs:
                        # 保留 code_block 属性
                        es.code_block = True
                        es.no_wrap = True
                        # 如果是 emoji，不设置 code_block（避免等宽字体渲染问题）
                        if es.is_emoji:
                            es.code_block = False
                            es.no_wrap = False
                        segments.append(es)
                    continue

                if seg.text:
                    emoji_subs = self.emoji_handler.split_text(seg.text)
                    for es in emoji_subs:
                        # Emoji 片段不继承 code 属性，避免等宽字体渲染问题
                        es.bold = seg.bold
                        es.italic = seg.italic
                        es.strike = seg.strike
                        es.code = False if es.is_emoji else seg.code

                        # 行级/列表属性显式继承，避免 0 值被 or 短路污染
                        es.heading = seg.heading
                        es.quote = seg.quote
                        es.code_block = seg.code_block
                        es.horizontal_rule = seg.horizontal_rule
                        es.list_item = seg.list_item
                        es.list_ordered = seg.list_ordered
                        es.list_level = seg.list_level
                        es.list_index = seg.list_index
                        es.list_continuation = seg.list_continuation

                        segments.append(es)
                elif not seg.is_emoji:
                    segments.append(seg)

            # 计算宽度并分词
            line_segments = []
            current_x = 0
            list_is_item = any(seg.list_item for seg in segments)
            list_continuation_active = False

            line_layout = self._build_line_layout(
                segments=segments,
                text_area_width=text_area_width,
                font=font,
                scale=scale,
                min_content_width=max(20, emoji_size),
            )
            effective_width = line_layout["effective_width"]

            for seg in segments:
                if getattr(seg, "is_newline", False):
                    if line_segments:
                        if list_is_item and list_continuation_active:
                            for prev_seg, _ in line_segments:
                                prev_seg.list_continuation = True
                        render_items.append((line_segments, False, False, False, None))
                        line_segments = []
                        current_x = 0
                    else:
                        render_items.append(([], True, False, False, None))
                    continue

                if seg.code_block:
                    # 代码块使用等宽字体计算宽度，与绘制阶段保持一致
                    code_calc_font = self._load_mono_font(real_font_size) or font
                    text_width = sum(self._get_char_render_width(code_calc_font, c, seg.bold) for c in seg.text)
                    if current_x + text_width > effective_width - 2 and current_x > 0:
                        if list_is_item:
                            if list_continuation_active:
                                for prev_seg, _ in line_segments:
                                    prev_seg.list_continuation = True
                            list_continuation_active = True
                        render_items.append((line_segments, False, False, False, None))
                        line_segments = []
                        current_x = 0
                    line_segments.append((seg, text_width))
                    current_x += text_width
                    continue

                # 标题片段进入字符级换行处理
                if seg.is_emoji:
                    # 添加 2px 安全余量
                    if current_x + emoji_size > effective_width - 2 and current_x > 0:
                        if list_is_item:
                            if list_continuation_active:
                                for prev_seg, _ in line_segments:
                                    prev_seg.list_continuation = True
                            list_continuation_active = True
                        render_items.append((line_segments, False, False, False, None))
                        line_segments = []
                        current_x = 0
                    line_segments.append((seg, emoji_size))
                    current_x += emoji_size
                elif seg.no_wrap:
                    seg_width = sum(self._get_char_render_width(font, c, seg.bold) for c in seg.text)
                    if current_x + seg_width > effective_width - 2 and current_x > 0:
                        if list_is_item:
                            if list_continuation_active:
                                for prev_seg, _ in line_segments:
                                    prev_seg.list_continuation = True
                            list_continuation_active = True
                        render_items.append((line_segments, False, False, False, None))
                        line_segments = []
                        current_x = 0
                    line_segments.append((seg, seg_width))
                    current_x += seg_width
                else:
                    # 确定用于计算宽度的字体
                    calc_font = font
                    if seg.heading:
                        heading_size = int(real_font_size * (1.8 - seg.heading * 0.15))
                        calc_font = self._load_font(heading_size)
                    elif seg.code or seg.code_block:
                        code_font = self._load_mono_font(real_font_size)
                        if code_font:
                            calc_font = code_font

                    chars = list(seg.text)
                    for i, char in enumerate(chars):
                        # 使用实际渲染宽度计算，避免字符右侧被裁切
                        char_width = self._get_char_render_width(calc_font, char, seg.bold)
                        # 添加 2px 安全余量，避免极端字符切边
                        need_wrap = current_x + char_width > effective_width - 2 and current_x > 0
                        if need_wrap and char in NO_LINE_START:
                            need_wrap = False

                        if not need_wrap and i == len(chars) - 2 and line_segments:
                            next_char = chars[i + 1]
                            next_width = self._get_char_render_width(calc_font, next_char, seg.bold)
                            if current_x + char_width + next_width > effective_width - 2:
                                render_items.append((line_segments, False, False, False, None))
                                line_segments = []
                                current_x = 0
                                need_wrap = False

                        if need_wrap:
                            if list_is_item and list_continuation_active:
                                for prev_seg, _ in line_segments:
                                    prev_seg.list_continuation = True
                            render_items.append((line_segments, False, False, False, None))
                            line_segments = []
                            current_x = 0
                            if list_is_item:
                                list_continuation_active = True
                        line_segments.append((TextSegment(text=char,
                                                           bold=seg.bold,
                                                           italic=seg.italic,
                                                           code=seg.code,
                                                           strike=seg.strike,
                                                           heading=seg.heading,
                                                           quote=seg.quote,
                                                           list_item=seg.list_item,
                                                           list_ordered=seg.list_ordered,
                                                           list_level=seg.list_level,
                                                           list_index=seg.list_index,
                                                           list_continuation=list_continuation_active,
                                                           url=seg.url,
                                                           is_image=seg.is_image), char_width))
                        current_x += char_width

            if line_segments:
                if list_is_item and list_continuation_active:
                    for prev_seg, _ in line_segments:
                        prev_seg.list_continuation = True
                render_items.append((line_segments, False, False, False, None))

        # 收尾：如果文本以表格结束，补结构化表格数据
        if ctx.in_table and ctx.table_rows:
            table_segments = _serialize_table(ctx)
            if table_segments and table_segments[0].table_rows:
                render_items.append(([], False, True, False, table_segments[0].table_rows))
            ctx.in_table = False
            ctx.table_rows.clear()
            ctx.table_header_parsed = False

        # 计算画布高度
        total_height = 0
        for segments, is_empty, is_table, is_hr, _ in render_items:
            if is_hr:
                total_height += int(line_pixel_height * 0.8)
            elif is_table:
                table_h = self._calc_table_height(_, table_line_height, font,
                                                  text_area_width, scale)
                total_height += table_h
            elif is_empty:
                total_height += int(line_pixel_height * 0.5)
            else:
                line_h = line_pixel_height
                if any(seg.code_block for seg, _ in segments):
                    line_h = int(line_pixel_height * 0.8)

                # 计算该行中的最大字体高度（用于标题行自适应）
                max_font_height = font_height
                has_emoji = any(seg.is_emoji for seg, _ in segments)
                for seg, _ in segments:
                    if seg.heading:
                        heading_size = int(real_font_size * (1.8 - seg.heading * 0.15))
                        heading_font = self._load_font(heading_size)
                        h_height = self._get_font_height(heading_font, heading_size)
                        max_font_height = max(max_font_height, h_height)

                # 如果行中包含 emoji，确保行高能容纳 emoji
                if has_emoji:
                    max_font_height = max(max_font_height, emoji_size)

                # 如果最大字体高度大于基础字体高度，调整行高
                if max_font_height > font_height:
                    # 保持 line_height 系数，用最大字体高度重新计算
                    line_h = int(max_font_height * line_height)
                    # 确保至少不小于原行高
                    line_h = max(line_h, line_pixel_height)

                total_height += line_h

        canvas_height = total_height + real_padding_y * 2

        # 创建画布
        bg_rgb = self._hex_to_rgb(bg_color, "#ffffff")
        text_rgb = self._hex_to_rgb(text_color, "#333333")
        canvas = Image.new("RGBA", (real_width, canvas_height), (*bg_rgb, 255))
        draw = ImageDraw.Draw(canvas)

        # 绘制
        y = real_padding_y
        for segments, is_empty, is_table, is_hr, table_data in render_items:
            if is_hr:
                # 绘制分割线
                hr_y = y + int(line_pixel_height * 0.4)
                draw.line([(content_left, hr_y), (content_right, hr_y)],
                         fill=(200, 200, 200), width=2)
                y += int(line_pixel_height * 0.8)
                continue

            if is_table:
                y = self._draw_table(draw, table_data, content_left, y, text_area_width,
                                    font, real_font_size, table_line_height,
                                    scale, text_rgb, bg_rgb)
                continue

            if is_empty:
                y += int(line_pixel_height * 0.5)
                continue

            is_code_line = any(seg.code_block for seg, _ in segments)
            is_list_continuation = any(seg.list_continuation for seg, _ in segments)

            line_segments_only = [seg for seg, _ in segments]
            line_layout = self._build_line_layout(
                segments=line_segments_only,
                text_area_width=text_area_width,
                font=font,
                scale=scale,
                min_content_width=max(20, emoji_size),
            )
            is_quote_line = line_layout["quote_offset"] > 0
            is_list_line = bool(line_layout["list_bullet_text"])

            # 计算该行的实际行高和最大字体高度
            current_line_height = line_pixel_height
            max_font_height_in_line = font_height
            has_emoji = any(seg.is_emoji for seg, _ in segments)

            for seg, _ in segments:
                if seg.heading:
                    heading_size = int(real_font_size * (1.8 - seg.heading * 0.15))
                    heading_font = self._load_font(heading_size)
                    h_height = self._get_font_height(heading_font, heading_size)
                    max_font_height_in_line = max(max_font_height_in_line, h_height)

            # 如果行中包含 emoji，确保行高能容纳 emoji
            if has_emoji:
                max_font_height_in_line = max(max_font_height_in_line, emoji_size)

            # 根据最大字体高度调整行高
            if max_font_height_in_line > font_height:
                current_line_height = int(max_font_height_in_line * line_height)
                current_line_height = max(current_line_height, line_pixel_height)

            if is_code_line:
                current_line_height = int(line_pixel_height * 0.8)

            # 代码块背景
            if is_code_line:
                bg_x = content_left
                bg_y = y - 2 * scale
                bg_h = current_line_height + 4 * scale
                draw.rounded_rectangle([bg_x, bg_y, content_right, bg_y + bg_h],
                                     radius=4 * scale, fill=(245, 245, 245))

            # 引用左边框
            quote_bar_width = line_layout["quote_bar_width"]
            if is_quote_line:
                bar_x = content_left
                bar_y = y
                bar_h = current_line_height
                draw.rectangle([bar_x, bar_y, bar_x + quote_bar_width, bar_y + bar_h],
                             fill=(100, 149, 237))

            x = content_left + line_layout["quote_offset"]

            if is_list_line:
                x += line_layout["list_indent"]
                bullet_text = line_layout["list_bullet_text"]
                bullet_width = line_layout["list_bullet_width"]

                if not is_list_continuation:
                    bullet_y = y + (current_line_height - font_height) // 2
                    draw.text((x, bullet_y), bullet_text, font=font, fill=text_rgb)
                x += bullet_width

            for idx, (seg, w) in enumerate(segments):
                if seg.is_emoji:
                    emoji_img = self.emoji_handler.render_emoji(seg.text, emoji_size, fallback_font=emoji_font)
                    if emoji_img:
                        emoji_y = y + (current_line_height - emoji_size) // 2
                        canvas.paste(emoji_img, (x, emoji_y), emoji_img)
                        x += w
                    else:
                        # Emoji 渲染失败时回退为文本绘制，确保至少显示字符且位置正确
                        emoji_fallback_width = int(font.getlength(seg.text))
                        text_y = y + (current_line_height - font_height) // 2
                        draw.text((x, text_y), seg.text, font=font, fill=text_rgb)
                        x += emoji_fallback_width
                    continue

                draw_font = font
                draw_color = text_rgb
                current_font_size = real_font_size

                if seg.heading:
                    current_font_size = int(real_font_size * (1.8 - seg.heading * 0.15))
                    draw_font = self._load_font(current_font_size, bold=True)
                    current_font_height = self._get_font_height(draw_font, current_font_size)
                elif seg.code or seg.code_block:
                    code_font = self._load_mono_font(real_font_size)
                    if code_font:
                        draw_font = code_font
                        current_font_height = self._get_font_height(draw_font, real_font_size)
                    else:
                        current_font_height = font_height
                else:
                    current_font_height = font_height

                # 使用当前行的行高进行垂直居中
                text_y = y + (current_line_height - current_font_height) // 2

                if seg.code and not seg.code_block:
                    prev_is_code = idx > 0 and segments[idx - 1][0].code and not segments[idx - 1][0].code_block
                    if not prev_is_code:
                        run_width = w
                        next_idx = idx + 1
                        while next_idx < len(segments):
                            next_seg, next_w = segments[next_idx]
                            if not next_seg.code or next_seg.code_block:
                                break
                            run_width += next_w
                            next_idx += 1

                        pad = max(1, int(2 * scale))
                        bg_x = x - pad
                        bg_y = text_y - 2 * scale
                        bg_w = run_width + pad * 2
                        bg_h = current_font_height + 4 * scale
                        draw.rounded_rectangle([bg_x, bg_y, bg_x + bg_w, bg_y + bg_h],
                                             radius=2 * scale, fill=(235, 235, 235))
                    draw_color = (60, 60, 60)

                if seg.strike:
                    draw_color = (160, 160, 160)

                if seg.italic and not seg.code and not seg.code_block:
                    draw_color = (max(draw_color[0] - 20, 0),
                                 max(draw_color[1] - 20, 0),
                                 max(draw_color[2] - 20, 0))

                if seg.quote:
                    draw_color = (80, 80, 80)

                if seg.url:
                    draw_color = (0, 0, 238)

                draw.text((x, text_y), seg.text, font=draw_font, fill=draw_color)

                if seg.url:
                    underline_y = text_y + current_font_height + max(1, scale)
                    draw.line([(x, underline_y), (x + w, underline_y)],
                             fill=(0, 0, 238), width=max(1, scale))

                if seg.strike:
                    strike_y = text_y + current_font_height // 2 - 1
                    draw.line([(x, strike_y), (x + w, strike_y)],
                             fill=draw_color, width=max(1, scale))

                if seg.bold and not seg.code and not seg.code_block:
                    for offset_x, offset_y in [(1, 0), (0, 1), (1, 1), (-1, 0), (0, -1)]:
                        draw.text((x + offset_x, text_y + offset_y), seg.text,
                                 font=draw_font, fill=draw_color)

                x += w

            y += current_line_height

        return self._save_image(canvas, bg_rgb)

    def _get_font_height(self, font: ImageFont.FreeTypeFont, fallback: int) -> int:
        """安全获取字体高度"""
        try:
            ascent, descent = font.getmetrics()
            if ascent is None or descent is None:
                return fallback
            return ascent + descent
        except Exception:
            return fallback

    def _build_line_layout(self,
                           segments: List[TextSegment],
                           text_area_width: int,
                           font: ImageFont.FreeTypeFont,
                           scale: int,
                           min_content_width: int) -> Dict[str, Any]:
        """统一计算行布局参数，确保换行与绘制阶段口径一致。"""
        quote_bar_width = 3 * scale
        quote_offset = quote_bar_width + 4 * scale if any(seg.quote for seg in segments) else 0

        list_ordered = False
        list_level = 0
        list_index = 0
        list_bullet_text = ""
        list_bullet_width = 0

        for seg in segments:
            if seg.list_item:
                list_ordered = seg.list_ordered
                list_level = max(0, seg.list_level)
                list_index = max(0, seg.list_index)
                list_bullet_text = f"{list_index}." if list_ordered else "•"
                list_bullet_width = sum(self._get_char_render_width(font, c) for c in list_bullet_text) + int(font.getlength(" "))
                break

        list_indent_per_level = 20 * scale
        raw_list_indent = list_level * list_indent_per_level

        available_for_list = max(0, text_area_width - quote_offset - list_bullet_width - min_content_width)
        list_indent = min(raw_list_indent, available_for_list)

        content_start_offset = quote_offset + list_indent + list_bullet_width
        effective_width = max(1, text_area_width - content_start_offset)

        return {
            "quote_bar_width": quote_bar_width,
            "quote_offset": quote_offset,
            "list_ordered": list_ordered,
            "list_level": list_level,
            "list_index": list_index,
            "list_bullet_text": list_bullet_text,
            "list_bullet_width": list_bullet_width,
            "list_indent": list_indent,
            "effective_width": effective_width,
        }

    def _get_char_render_width(self, font: ImageFont.FreeTypeFont, char: str,
                               is_bold: bool = False) -> int:
        """获取字符的实际渲染宽度

        使用 font.getbbox() 获取真实边界框宽度，避免字符实际像素宽度大于 advance width
        导致右侧被裁切。对于粗体字符，额外添加 2px 宽度补偿。

        Args:
            font: 字体对象
            char: 单个字符
            is_bold: 是否为粗体（用于宽度补偿）

        Returns:
            字符宽度（像素）
        """
        cache_key = (id(font), char, is_bold)
        cached = self._char_width_cache.get(cache_key)
        if cached is not None:
            self._char_width_cache.move_to_end(cache_key)
            return cached

        try:
            # 尝试使用 getbbox 获取实际渲染边界
            bbox = font.getbbox(char)
            if bbox:
                left, top, right, bottom = bbox
                width = right - left
            else:
                width = int(font.getlength(char))
        except Exception:
            width = int(font.getlength(char))

        # 粗体补偿：粗体绘制时使用多次偏移（±1 像素），需要额外空间
        if is_bold:
            width += 2

        self._char_width_cache[cache_key] = width
        self._char_width_cache.move_to_end(cache_key)
        if len(self._char_width_cache) > self._char_width_cache_limit:
            self._char_width_cache.popitem(last=False)
        return width

    def wrap_text_segments(
            self,
            segments: List[TextSegment],
            font: ImageFont.FreeTypeFont,
            mono_font: Optional[ImageFont.FreeTypeFont],
            max_width: int,
    ) -> List[List[Tuple[TextSegment, int]]]:
        """公开不换行的片段排版入口，供表格布局使用。"""
        return self._wrap_text_segments_for_render(segments, font, mono_font, max_width)

    def _wrap_text_segments_for_render(
            self,
            segments: List[TextSegment],
            font: ImageFont.FreeTypeFont,
            mono_font: Optional[ImageFont.FreeTypeFont],
            max_width: int,
    ) -> List[List[Tuple[TextSegment, int]]]:
        """将带样式的片段按宽度换行（用于卡片渲染）"""
        if max_width <= 0:
            calc_font = mono_font or font
            line = [(seg, sum(self._get_char_render_width(calc_font, c, seg.bold) for c in seg.text))
                    for seg in segments if seg.text]
            return [line] if line else [[]]

        lines: List[List[Tuple[TextSegment, int]]] = []
        current: List[Tuple[TextSegment, int]] = []
        current_width = 0

        for seg in segments:
            text = seg.text or ""
            if not text:
                continue

            calc_font = mono_font or font if seg.code else font

            for char in text:
                char_width = self._get_char_render_width(calc_font, char, seg.bold)
                need_wrap = current and (current_width + char_width > max_width - 2)
                if need_wrap and char in NO_LINE_START:
                    need_wrap = False

                if need_wrap:
                    lines.append(current)
                    current = []
                    current_width = 0

                current.append((TextSegment(
                    text=char,
                    bold=seg.bold,
                    italic=seg.italic,
                    code=seg.code,
                    strike=seg.strike,
                    url=seg.url,
                    is_image=seg.is_image,
                ), char_width))
                current_width += char_width

        if current:
            lines.append(current)

        if not lines:
            lines.append([])

        return lines

    def _draw_segment_line(
        self,
        draw: ImageDraw.ImageDraw,
        line: List[Tuple[TextSegment, int]],
        x: int,
        y: int,
        line_height: int,
        font: ImageFont.FreeTypeFont,
        mono_font: Optional[ImageFont.FreeTypeFont],
        font_size: int,
        scale: int,
        text_rgb: Tuple[int, int, int],
        override_color: Optional[Tuple[int, int, int]] = None,
    ) -> int:
        """绘制已排版好的单行片段，处理粗体/斜体/删除线/链接/代码背景"""
        if not line:
            return x

        idx = 0
        ops: List[Tuple[str, int, ImageFont.FreeTypeFont, Tuple[int, int, int], bool, bool, TextSegment]] = []
        backgrounds: List[Tuple[int, int, int, int]] = []

        draw_x = x
        while idx < len(line):
            seg, w = line[idx]
            draw_font = font
            draw_color = override_color or text_rgb
            current_font_height = self._get_font_height(font, font_size)

            if seg.code and mono_font:
                draw_font = mono_font
                current_font_height = self._get_font_height(draw_font, font_size)

            if override_color is None:
                if seg.code:
                    draw_color = (60, 60, 60)
                if seg.italic and not seg.code and not seg.code_block:
                    draw_color = (max(draw_color[0] - 20, 0),
                                  max(draw_color[1] - 20, 0),
                                  max(draw_color[2] - 20, 0))
                if seg.strike:
                    draw_color = (160, 160, 160)
                if seg.url:
                    draw_color = (0, 0, 238)

            seg_y = y + (line_height - current_font_height) // 2

            if seg.code:
                run_text = seg.text
                run_width = w
                next_idx = idx + 1
                while (next_idx < len(line) and
                       line[next_idx][0].code and not line[next_idx][0].code_block):
                    next_seg, next_w = line[next_idx]
                    run_text += next_seg.text
                    run_width += next_w
                    next_idx += 1

                pad = max(1, int(2 * scale))
                bg_x = draw_x - pad
                bg_y = seg_y - 2 * scale
                bg_w = run_width + pad * 2
                bg_h = current_font_height + 4 * scale
                backgrounds.append((bg_x, bg_y, bg_w, bg_h))

                ops.append((run_text, draw_x, draw_font, draw_color, False, False, TextSegment(text="", url=seg.url)))
                draw_x += run_width
                idx = next_idx
                continue

            ops.append((seg.text, draw_x, draw_font, draw_color, seg.bold, seg.strike, seg))
            draw_x += w
            idx += 1

        for bg_x, bg_y, bg_w, bg_h in backgrounds:
            draw.rounded_rectangle([bg_x, bg_y, bg_x + bg_w, bg_y + bg_h],
                                   radius=2 * scale, fill=(235, 235, 235))

        for text, op_x, op_font, color, is_bold, is_strike, seg in ops:
            draw.text((op_x, y + (line_height - self._get_font_height(op_font, font_size)) // 2),
                      text, font=op_font, fill=color)

            if is_bold:
                for offset_x, offset_y in [(1, 0), (0, 1), (1, 1), (-1, 0), (0, -1)]:
                    draw.text((op_x + offset_x,
                               y + (line_height - self._get_font_height(op_font, font_size)) // 2 + offset_y),
                              text, font=op_font, fill=color)

            if is_strike:
                current_font_height = self._get_font_height(op_font, font_size)
                strike_y = y + (line_height - current_font_height) // 2 + current_font_height // 2 - 1
                draw.line([(op_x, strike_y), (op_x + int(draw.textlength(text, font=op_font)), strike_y)],
                          fill=color, width=max(1, scale))

            if seg.url:
                current_font_height = self._get_font_height(op_font, font_size)
                underline_y = y + (line_height - current_font_height) // 2 + current_font_height + max(1, scale)
                line_width = int(draw.textlength(text, font=op_font))
                draw.line([(op_x, underline_y), (op_x + line_width, underline_y)],
                          fill=(0, 0, 238), width=max(1, scale))

        return draw_x

    def _calc_table_height(self, table_data: List[TableRow], line_height, font,
                           content_width: int, scale: int) -> int:
        """计算表格高度（卡片式布局）"""
        return calc_table_card_height(
            self,
            table_data,
            line_height,
            font,
            self._load_mono_font(getattr(font, "size", None) or 0),
            content_width,
            scale,
            bool(self._get_config("hide_table_first_column_label", False)),
        )

    def _draw_table(self, draw, table_data: List[TableRow], x, y, content_width,
                   font, font_size, line_height, scale,
                   text_rgb, bg_rgb) -> int:
        """绘制表格（卡片式布局）"""
        mono_font = self._load_mono_font(getattr(font, "size", None) or 0)
        return draw_table_cards(
            self,
            draw,
            table_data,
            x,
            y,
            content_width,
            font,
            mono_font,
            font_size,
            line_height,
            scale,
            text_rgb,
            bg_rgb,
            bool(self._get_config("hide_table_first_column_label", False)),
        )

    def _save_image(self, canvas, bg_rgb) -> str:
        """保存图片"""
        tmp = tempfile.NamedTemporaryFile(prefix="text2img_", suffix=".jpg", delete=False)
        canvas_rgb = Image.new("RGB", canvas.size, bg_rgb)
        canvas_rgb.paste(canvas, mask=canvas.split()[3] if canvas.mode == 'RGBA' else None)
        canvas_rgb.save(tmp.name, format="JPEG", quality=80)
        return tmp.name
