"""Emoji 处理器"""

import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import urlopen, Request

from PIL import Image

from .styles import TextSegment


class EmojiHandler:
    """Emoji 处理器 - 使用 Twemoji (Twitter/X) CDN"""
    
    # 更完整的 Emoji 正则表达式（覆盖 Unicode 15.0+）
    PATTERN = re.compile(
        r"""
        (?:
            # 基础Emoji字符（核心区间）
            [\U00002139\U00002194-\U00002199\U000021A9-\U000021AA\U0000231A-\U0000231B\U00002328\U000023CF\U000023E9-\U000023F3\U000023F8-\U000023FA\U000024C2\U000025AA-\U000025AB\U000025B6\U000025C0\U000025FB-\U000025FE\U00002600-\U000026FF\U00002702-\U000027BF\U00002934-\U00002935\U00002B05-\U00002B07\U00002B1B-\U00002B1C\U00002B50\U00002B55\U00003030\U0000303D\U00003297\U00003299]
            |
            [\U0001F004\U0001F0CF\U0001F170-\U0001F171\U0001F17E-\U0001F17F\U0001F18E\U0001F191-\U0001F19A\U0001F1E0-\U0001F1FF\U0001F201-\U0001F202\U0001F21A\U0001F22F\U0001F232-\U0001F23A\U0001F250-\U0001F251]
            |
            [\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF]
            |
            [\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U0001FB00-\U0001FBFF\U0001FC00-\U0001FCFF\U0001FD00-\U0001FDFF\U0001FE00-\U0001FEFF\U0001FF00-\U0001FFFF]
            # 肤色修饰符
            |
            [\U0001F3FB-\U0001F3FF]
        )
        # 可选的修饰符（变体选择符 + 零宽连接符）
        (?:[\U0000200D\U0000FE00-\U0000FE0F])*
        """,
        re.UNICODE | re.VERBOSE
    )
    
    SEPARATOR_CHARS = '━─═—_-~·•'
    
    # Twemoji CDN 源
    CDN_BASES = [
        "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72",
        "https://twemoji.maxcdn.com/v/latest/72x72",
        "https://abs.twimg.com/emoji/v2/72x72",
    ]
    
    def __init__(self, font_dir: Path = None):
        self._font_dir = font_dir
        self._cache: Dict[str, Image.Image] = {}
        self._failed: set = set()
    
    def split_text(self, text: str) -> List[TextSegment]:
        """将文本拆分为普通文字和 emoji"""
        result = []
        last_end = 0
        
        for match in self.PATTERN.finditer(text):
            # 添加 emoji 之前的普通文本
            if match.start() > last_end:
                plain = text[last_end:match.start()]
                result.extend(self._split_separators(plain))
            
            # 添加 emoji
            result.append(TextSegment(text=match.group(), is_emoji=True))
            last_end = match.end()
        
        # 添加最后的普通文本
        if last_end < len(text):
            result.extend(self._split_separators(text[last_end:]))
        
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
    
    def render_emoji(self, emoji: str, size: int) -> Optional[Image.Image]:
        """从 Twemoji CDN 获取 emoji 图片"""
        cache_key = f"{emoji}_{size}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()
        
        if emoji in self._failed:
            return None
        
        # 生成所有可能的 URL
        urls = self._get_twemoji_urls(emoji)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        last_error = None
        for url in urls:
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=10) as response:
                    img = Image.open(BytesIO(response.read())).convert("RGBA")
                    img = img.resize((size, size), Image.LANCZOS)
                    self._cache[cache_key] = img
                    return img.copy()
            except Exception as e:
                last_error = e
                continue
        
        # 所有 URL 都失败，打印调试信息
        codepoints = ' '.join(f'U+{ord(c):04X}' for c in emoji)
        print(f"[Emoji] 获取失败: {repr(emoji)} ({codepoints}) - {last_error}")
        self._failed.add(emoji)
        return None
    
    def _get_twemoji_urls(self, emoji: str) -> list:
        """生成所有可能的 Twemoji URL 格式"""
        urls = []
        
        # 清理 emoji（移除变体选择符但保留零宽连接符用于组合emoji）
        cleaned_no_fe0f = emoji.replace('\ufe0f', '')
        cleaned_all = emoji.replace('\ufe0f', '').replace('\u200d', '')
        
        # 不同的 codepoint 格式
        formats = set()
        
        # 格式1: 移除 fe0f 的完整序列（保留 200d）
        formats.add('-'.join(f'{ord(c):x}' for c in cleaned_no_fe0f))
        
        # 格式2: 完全清理后的序列
        formats.add('-'.join(f'{ord(c):x}' for c in cleaned_all))
        
        # 格式3: 只取第一个字符
        if cleaned_all:
            formats.add(f'{ord(cleaned_all[0]):x}')
        
        # 格式4: 原始带 fe0f
        formats.add('-'.join(f'{ord(c):x}' for c in emoji))
        
        # 格式5: 单字符带 fe0f
        if cleaned_all:
            formats.add(f'{ord(cleaned_all[0]):x}-fe0f')
        
        # 组合所有 CDN 和格式
        for cp in formats:
            for base in self.CDN_BASES:
                urls.append(f"{base}/{cp}.png")
        
        return urls
