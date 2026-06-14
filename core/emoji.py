"""Emoji 处理器"""

import json
import time
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.request import Request, urlopen

import emoji
import grapheme
from PIL import Image, ImageDraw, ImageFont

from .styles import TextSegment

try:
    from astrbot.api import logger
except Exception:  # 兼容独立测试/未安装 AstrBot 的环境
    import logging
    logger = logging.getLogger("astrbot_plugin_text2image_x.emoji")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


class EmojiHandler:
    """Emoji 处理器 - 优先使用 Twemoji CDN，离线/失败时回退到本地彩色字体或占位图"""

    SEPARATOR_CHARS = '━─═—_-~·•'

    # Twemoji CDN 源
    CDN_BASES = [
        "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72",
        "https://twemoji.maxcdn.com/v/latest/72x72",
        "https://abs.twimg.com/emoji/v2/72x72",
    ]

    def __init__(self, font_dir: Path = None, cache_dir: Path = None,
                 timeout: int = 10, failed_ttl: int = 3600,
                 emoji_mode: str = "auto",
                 fallback_font_provider: Optional[Callable[[int], Optional[ImageFont.FreeTypeFont]]] = None):
        """
        初始化 Emoji 处理器

        Args:
            font_dir: 字体目录（保留兼容性，未使用）
            cache_dir: Emoji 磁盘缓存目录，默认为插件根目录下的 .emoji-cache
            timeout: 下载超时时间（秒），默认 10 秒
            failed_ttl: 失败缓存 TTL（秒），默认 3600 秒
            emoji_mode: cdn/auto/font，见 _conf_schema.json 说明
            fallback_font_provider: 返回指定 size 彩色 emoji 字体的回调；在 auto/font 模式下使用
        """

        # 确定缓存目录
        if cache_dir is None and font_dir is not None:
            cache_dir = font_dir.parent / ".emoji-cache"
        elif cache_dir is None:
            cache_dir = Path.cwd() / ".emoji-cache"

        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._timeout = timeout
        self._failed_ttl = failed_ttl

        mode = str(emoji_mode).lower().strip()
        if mode not in {"cdn", "auto", "font"}:
            logger.warning(f"[Emoji] 未知 emoji_mode '{emoji_mode}'，使用默认值 auto")
            mode = "auto"
        self._mode = mode
        self._fallback_font_provider = fallback_font_provider

        self._cache: OrderedDict[str, Image.Image] = OrderedDict()
        self._cache_max_items = 512

        # 失败缓存：内存 + 持久化 JSON
        self._failed: Dict[str, float] = {}
        self._failed_file = self._cache_dir / "failed.json"
        self._failed_cleanup_interval = max(120, min(self._failed_ttl, 3600))
        self._last_failed_cleanup = 0.0
        self._load_failed_cache()

        # 占位图按尺寸缓存
        self._placeholders: Dict[int, Image.Image] = {}

    @property
    def use_fallback_font(self) -> bool:
        """当前模式是否允许使用本地彩色 emoji 字体兜底"""
        return self._mode in {"auto", "font"}

    @property
    def mode(self) -> str:
        return self._mode

    def split_text(self, text: str) -> List[TextSegment]:
        """将文本拆分为普通文字和 emoji（使用 grapheme cluster 分词）"""
        result = []
        plain_buf: List[str] = []

        for cluster in grapheme.graphemes(text):
            if emoji.is_emoji(cluster):
                if plain_buf:
                    result.extend(self._split_separators("".join(plain_buf)))
                    plain_buf = []
                result.append(TextSegment(text=cluster, is_emoji=True))
            else:
                plain_buf.append(cluster)

        if plain_buf:
            result.extend(self._split_separators("".join(plain_buf)))

        return result

    def _split_separators(self, text: str) -> List[TextSegment]:
        """拆分连续分隔符"""
        if not text:
            return []

        result = []
        i = 0
        while i < len(text):
            char = text[i]
            j = i + 1
            while j < len(text) and text[j] == char:
                j += 1

            if j - i >= 3 and char in self.SEPARATOR_CHARS:
                result.append(TextSegment(text=text[i:j], no_wrap=True))
            else:
                result.append(TextSegment(text=text[i:j]))
            i = j
        return result

    def render_emoji(self, emoji_text: str, size: int,
                     fallback_font: Optional[ImageFont.FreeTypeFont] = None) -> Optional[Image.Image]:
        """获取 emoji 图片，支持 CDN、本地字体、占位图多级兜底"""

        if not emoji_text:
            return None

        cache_key = f"{emoji_text}_{size}"
        now = time.time()

        if now - self._last_failed_cleanup >= self._failed_cleanup_interval:
            self._cleanup_failed_cache(now)

        # 1. 内存缓存
        if cache_key in self._cache:
            logger.debug(f"[Emoji] 内存缓存命中: {cache_key}")
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key].copy()

        # font 模式：完全不走网络
        if self._mode == "font":
            img = self._try_font_fallback(emoji_text, size, fallback_font)
            if img is not None:
                self._remember_cache(cache_key, img)
                return img.copy()
            return self._get_placeholder(size).copy()

        # 2. 持久失败缓存检查
        failed_time = self._failed.get(emoji_text)
        if failed_time is not None and now - failed_time < self._failed_ttl:
            logger.debug(f"[Emoji] 失败缓存命中: {repr(emoji_text)} (TTL 未过期)")
            if self._mode == "auto":
                img = self._try_font_fallback(emoji_text, size, fallback_font)
                if img is not None:
                    return img.copy()
                return self._get_placeholder(size).copy()
            return None

        # 3. 磁盘缓存
        codepoints = '_'.join(f'{ord(c):04X}' for c in emoji_text)
        cache_filename = f"{codepoints}_{size}.png"
        cache_file_path = self._cache_dir / cache_filename

        if cache_file_path.exists():
            try:
                with open(cache_file_path, 'rb') as f:
                    img = Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
                self._remember_cache(cache_key, img)
                logger.debug(f"[Emoji] 磁盘缓存命中: {cache_file_path}")
                return img.copy()
            except Exception as e:
                logger.warning(f"[Emoji] 磁盘缓存读取失败: {cache_file_path} - {e}")

        # 4. CDN 下载
        urls = self._get_twemoji_urls(emoji_text)
        last_error = None
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        for url in urls:
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=self._timeout) as response:
                    img_data = response.read()
                img = Image.open(BytesIO(img_data)).convert("RGBA").resize((size, size), Image.LANCZOS)

                self._remember_cache(cache_key, img)
                try:
                    img.save(cache_file_path, "PNG")
                    logger.debug(f"[Emoji] 磁盘缓存写入成功: {cache_file_path}")
                except Exception as e:
                    logger.warning(f"[Emoji] 磁盘缓存写入失败: {cache_file_path} - {e}")

                return img.copy()
            except Exception as e:
                last_error = e
                logger.debug(f"[Emoji] CDN 下载失败: {url} - {e}")
                continue

        # 5. 记录失败时间戳
        codepoints_str = ' '.join(f'U+{ord(c):04X}' for c in emoji_text)
        logger.warning(f"[Emoji] 获取失败: {repr(emoji_text)} ({codepoints_str}) - {last_error}")
        self._failed[emoji_text] = now
        self._save_failed_cache(now)

        # 6. 本地字体兜底 / 占位图 / cdn 模式返回 None
        if self._mode == "auto":
            img = self._try_font_fallback(emoji_text, size, fallback_font)
            if img is not None:
                return img.copy()
            return self._get_placeholder(size).copy()

        return None

    # ------------------------------------------------------------------ #
    # 本地字体 / 占位图辅助
    # ------------------------------------------------------------------ #

    def _try_font_fallback(self, emoji_text: str, size: int,
                           fallback_font: Optional[ImageFont.FreeTypeFont]) -> Optional[Image.Image]:
        """尝试使用本地彩色 emoji 字体渲染"""
        font = fallback_font
        if font is None and self._fallback_font_provider:
            font = self._fallback_font_provider(size)
        if font is None:
            return None
        return self._render_with_font(emoji_text, size, font)

    def _render_with_font(self, emoji_text: str, size: int,
                          font: ImageFont.FreeTypeFont) -> Optional[Image.Image]:
        """把 emoji 字符用彩色字体居中画到 size×size 透明画布上"""
        try:
            try:
                width = int(font.getlength(emoji_text))
            except Exception:
                bbox = font.getbbox(emoji_text)
                width = (bbox[2] - bbox[0]) if bbox else size

            metrics = font.getmetrics()
            if metrics:
                ascent, descent = metrics
                height = ascent + descent
            else:
                height = size

            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            x = max(0, (size - width) // 2)
            y = max(0, (size - height) // 2)

            draw.text((x, y), emoji_text, font=font, embedded_color=True)
            return img
        except Exception as e:
            logger.debug(f"[Emoji] 本地字体渲染失败: {repr(emoji_text)} - {e}")
            return None

    def _get_placeholder(self, size: int) -> Image.Image:
        """生成一个统一的占位图，避免布局塌陷"""
        if size in self._placeholders:
            return self._placeholders[size]

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = max(2, size // 8)
        color = (180, 180, 180, 255)
        radius = max(2, size // 8)
        width = max(1, size // 24)

        draw.rounded_rectangle(
            [pad, pad, size - pad, size - pad],
            radius=radius,
            outline=color,
            width=width,
        )

        try:
            font = ImageFont.load_default()
            text = "?"
            bbox = font.getbbox(text)
            if bbox:
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                tx = (size - tw) // 2
                ty = (size - th) // 2
                draw.text((tx, ty), text, font=font, fill=color)
        except Exception:
            pass

        self._placeholders[size] = img
        return img

    # ------------------------------------------------------------------ #
    # 失败缓存持久化
    # ------------------------------------------------------------------ #

    def _load_failed_cache(self):
        """从 failed.json 加载失败记录"""
        if not self._failed_file.exists():
            return
        try:
            with open(self._failed_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            entries = data.get("entries", {})
            now = time.time()
            loaded: Dict[str, float] = {}
            for k, ts in entries.items():
                if isinstance(ts, (int, float)) and now - float(ts) < self._failed_ttl:
                    loaded[str(k)] = float(ts)
            self._failed = loaded
            logger.info(f"[Emoji] 已加载持久失败缓存: {len(self._failed)} 条")
        except Exception as e:
            logger.warning(f"[Emoji] 加载失败缓存失败: {e}")

    def _save_failed_cache(self, now: float):
        """将失败记录写回 failed.json"""
        try:
            tmp = self._failed_file.with_suffix(".json.tmp")
            data = {"version": 1, "entries": self._failed}
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._failed_file)
        except Exception as e:
            logger.warning(f"[Emoji] 保存失败缓存失败: {e}")

    def _cleanup_failed_cache(self, now: float):
        """清理过期失败记录并持久化"""
        expired = [k for k, ts in self._failed.items() if now - ts >= self._failed_ttl]
        if expired:
            for key in expired:
                self._failed.pop(key, None)
            self._save_failed_cache(now)
        self._last_failed_cleanup = now

    # ------------------------------------------------------------------ #
    # CDN URL 生成
    # ------------------------------------------------------------------ #

    def _get_twemoji_urls(self, emoji_text: str) -> list:
        """生成所有可能的 Twemoji URL 格式（按命中概率排序）"""
        urls = []

        # 规范化：移除变体选择符；保留零宽连接符
        no_fe0f = [c for c in emoji_text if c != '\ufe0f']
        stripped_all = [c for c in emoji_text if c not in ('\ufe0f', '\u200d')]

        formats = []

        # 格式 1: canonical（移除 fe0f，保留 200d），Twemoji 最常用的命名方式
        canonical = '-'.join(f'{ord(c):x}' for c in no_fe0f)
        if canonical:
            formats.append(canonical)

        # 格式 2: 原始序列（带 fe0f），用于少数依赖 fe0f 的文件名
        original = '-'.join(f'{ord(c):x}' for c in emoji_text)
        if original and original != canonical:
            formats.append(original)

        # 格式 3: 完全清理（移除 fe0f 与 200d），用于罕见 fallback
        stripped = '-'.join(f'{ord(c):x}' for c in stripped_all)
        if stripped and stripped != canonical:
            formats.append(stripped)

        # 格式 4/5: 单字符及其 fe0f 变体（仅适用于简单 emoji）
        if len(stripped_all) == 1:
            base = f'{ord(stripped_all[0]):x}'
            if base not in formats:
                formats.append(base)
            fe0f_variant = f'{base}-fe0f'
            if fe0f_variant not in formats:
                formats.append(fe0f_variant)

        for cp in formats:
            for base in self.CDN_BASES:
                urls.append(f"{base}/{cp}.png")

        return urls

    def _remember_cache(self, key: str, image: Image.Image):
        """记录内存缓存并控制上限"""
        self._cache[key] = image
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_items:
            self._cache.popitem(last=False)
