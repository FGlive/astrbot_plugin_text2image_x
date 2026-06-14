"""
Emoji 稳定加载功能测试脚本

测试场景：
1. grapheme 分词正确性（ZWJ 组合、国旗、肤色修饰符）
2. Twemoji URL 规范生成
3. 持久失败缓存（跨实例）
4. font 模式离线渲染（如果有本地彩色 emoji 字体）
5. CDN 首次下载 + 磁盘缓存回读
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.emoji import EmojiHandler
from core.renderer import TextRenderer


def _cache_dir():
    return Path(__file__).parent / ".emoji-cache"


def _cleanup_failed_json():
    for path in (_cache_dir() / "failed.json", _cache_dir() / "failed.json.tmp"):
        if path.exists():
            path.unlink()


def test_segmentation():
    """测试复杂 emoji 不被拆散"""
    print("\n[测试] grapheme 分词")
    handler = EmojiHandler(cache_dir=_cache_dir())

    def emoji_texts(text):
        return [seg.text for seg in handler.split_text(text) if seg.is_emoji]

    family = emoji_texts("家人👨‍👩‍👧‍👦一起")
    assert family == ["👨‍👩‍👧‍👦"], f"ZWJ 家庭被拆分: {family}"

    flag = emoji_texts("国旗🇨🇳飘扬")
    assert flag == ["🇨🇳"], f"国旗被拆分: {flag}"

    skin = emoji_texts("挥手👋🏻")
    assert skin == ["👋🏻"], f"肤色修饰符被拆分: {skin}"

    heart = emoji_texts("红心❤️跳动")
    assert heart == ["❤️"], f"变体选择符被拆分: {heart}"

    print("  ✓ 复杂 emoji 分词正确")


def test_twemoji_urls():
    """测试 URL 生成包含规范 codepoint"""
    print("\n[测试] Twemoji URL 生成")
    handler = EmojiHandler(cache_dir=_cache_dir())

    flag_urls = handler._get_twemoji_urls("🇨🇳")
    assert any("1f1e8-1f1f3.png" in u for u in flag_urls), f"国旗 URL 缺失: {flag_urls[:3]}"

    heart_urls = handler._get_twemoji_urls("❤️")
    assert any("2764.png" in u for u in heart_urls), f"红心 URL 缺失: {heart_urls[:3]}"
    assert any("2764-fe0f.png" in u for u in heart_urls), f"红心 fe0f URL 缺失: {heart_urls[:3]}"

    keycap_urls = handler._get_twemoji_urls("#️⃣")
    assert any("23-20e3.png" in u for u in keycap_urls), f"keycap URL 缺失: {keycap_urls[:3]}"

    print("  ✓ URL 生成正确")


def test_persistent_failed_cache():
    """测试失败缓存跨实例持久化"""
    print("\n[测试] 失败缓存持久化")
    _cleanup_failed_json()

    handler1 = EmojiHandler(cache_dir=_cache_dir(), failed_ttl=3600)
    now = time.time()
    handler1._failed["😈"] = now
    handler1._save_failed_cache(now)

    handler2 = EmojiHandler(cache_dir=_cache_dir(), failed_ttl=3600)
    assert "😈" in handler2._failed, "失败记录未持久化加载"

    # 过期项应被过滤
    handler1._failed.clear()
    handler1._failed["😈"] = now - 7200
    handler1._save_failed_cache(now)
    handler3 = EmojiHandler(cache_dir=_cache_dir(), failed_ttl=3600)
    assert "😈" not in handler3._failed, "过期失败记录应被清理"

    _cleanup_failed_json()
    print("  ✓ 失败缓存持久化正确")


def test_font_mode_render():
    """测试 font 模式离线渲染（依赖本地彩色字体）"""
    print("\n[测试] font 模式离线渲染")
    config = {
        "image_width": 375,
        "image_scale": 2,
        "padding": 24,
        "font_size": 24,
        "line_height": 1.6,
        "bg_color": "#ffffff",
        "text_color": "#333333",
        "emoji_cache_dir": str(_cache_dir()),
        "emoji_timeout": 10,
        "emoji_failed_ttl": 3600,
        "emoji_mode": "font",
        "emoji_font_name": "",
    }
    font_dir = Path(__file__).parent / "ziti"
    renderer = TextRenderer(config, font_dir)

    if renderer._load_emoji_font(int(24 * 2 * 1.1)) is None:
        print("  ⚠ 未检测到本地彩色 emoji 字体，跳过 font 模式绘制验证")
        return

    result_path = renderer.render("离线字体模式：🎉🇨🇳👋🏻")
    assert result_path and Path(result_path).exists(), "font 模式渲染失败"
    os.remove(result_path)
    print("  ✓ font 模式渲染成功")


def test_auto_fallback_when_failed():
    """auto 模式下失败缓存命中时仍走本地字体/占位图"""
    print("\n[测试] auto 模式失败缓存兜底")
    _cleanup_failed_json()

    handler = EmojiHandler(cache_dir=_cache_dir(), failed_ttl=3600, emoji_mode="auto")
    # 获取一个可用的彩色 emoji 字体
    font_dir = Path(__file__).parent / "ziti"
    renderer = TextRenderer(
        {"emoji_mode": "auto", "emoji_font_name": "", "emoji_cache_dir": str(_cache_dir())},
        font_dir,
    )
    emoji_font = renderer._load_emoji_font(72)

    now = time.time()
    handler._failed["🎉"] = now
    handler._save_failed_cache(now)

    # 即使标记为失败，auto 模式也应拿到兜底图片（字体或占位图）
    img = handler.render_emoji("🎉", 72, fallback_font=emoji_font)
    assert img is not None, "auto 模式失败缓存应返回兜底图片"
    assert img.size == (72, 72), "兜底图片尺寸应正确"

    _cleanup_failed_json()
    print("  ✓ auto 模式失败缓存兜底正常")


def test_cdn_mode_returns_none():
    """cdn 模式下失败缓存命中时返回 None，保持旧文本回退"""
    print("\n[测试] cdn 模式失败返回 None")
    _cleanup_failed_json()

    handler = EmojiHandler(cache_dir=_cache_dir(), failed_ttl=3600, emoji_mode="cdn")
    now = time.time()
    handler._failed["🎉"] = now
    handler._save_failed_cache(now)

    img = handler.render_emoji("🎉", 72)
    assert img is None, "cdn 模式失败缓存应返回 None"

    _cleanup_failed_json()
    print("  ✓ cdn 模式失败返回 None 正常")


def test_cdn_cache():
    """CDN 首次下载 + 磁盘缓存回读"""
    print("\n[测试] CDN 下载与磁盘缓存")
    config = {
        "image_width": 375,
        "image_scale": 2,
        "padding": 24,
        "font_size": 24,
        "line_height": 1.6,
        "bg_color": "#ffffff",
        "text_color": "#333333",
        "emoji_cache_dir": str(_cache_dir()),
        "emoji_timeout": 10,
        "emoji_failed_ttl": 3600,
        "emoji_mode": "auto",
        "emoji_font_name": "",
    }
    font_dir = Path(__file__).parent / "ziti"
    renderer = TextRenderer(config, font_dir)

    test_texts = [
        "测试表情：😀😂🥺🎉🎊",
        "更多表情：❤️✨🔥💯🎨",
        "动物表情：🐶🐱🐼🦊🦋",
    ]

    print("  [第 1 轮] 首次渲染（可能从 CDN 下载）")
    for text in test_texts:
        result_path = renderer.render(text)
        assert result_path, f"首次渲染失败: {text}"
        os.remove(result_path)

    print("  [第 2 轮] 再次渲染（应从磁盘缓存读取）")
    for text in test_texts:
        result_path = renderer.render(text)
        assert result_path, f"缓存渲染失败: {text}"
        os.remove(result_path)

    if _cache_dir().exists():
        files = list(_cache_dir().glob("*.png"))
        print(f"  [信息] 缓存目录 PNG 文件数: {len(files)}")

    print("  ✓ CDN 下载与磁盘缓存正常")


def main():
    print("=" * 60)
    print("Emoji 稳定加载功能测试")
    print("=" * 60)

    _cache_dir().mkdir(parents=True, exist_ok=True)
    _cleanup_failed_json()

    test_segmentation()
    test_twemoji_urls()
    test_persistent_failed_cache()
    test_auto_fallback_when_failed()
    test_cdn_mode_returns_none()
    test_font_mode_render()
    test_cdn_cache()

    print("\n" + "=" * 60)
    print("所有测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
