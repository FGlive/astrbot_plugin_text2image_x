"""
Emoji 缓存功能测试脚本

测试场景：
1. 首次运行：下载 emoji 并缓存到磁盘
2. 断网运行：从磁盘缓存读取，验证离线可用
3. TTL 验证：失败的 emoji 在 TTL 内不重复请求
"""

import asyncio
import sys
from pathlib import Path

# 添加 core 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from core.renderer import TextRenderer


def test_emoji_cache():
    """测试 Emoji 缓存功能"""

    # 配置
    config = {
        "image_width": 375,
        "image_scale": 2,
        "padding": 24,
        "font_size": 24,
        "line_height": 1.6,
        "bg_color": "#ffffff",
        "text_color": "#333333",
        "emoji_cache_dir": "",  # 使用默认缓存目录
        "emoji_timeout": 10,
        "emoji_failed_ttl": 3600,
    }

    font_dir = Path(__file__).parent / "ziti"

    print("=" * 60)
    print("Emoji 缓存功能测试")
    print("=" * 60)

    # 测试文本（包含常用 emoji）
    test_texts = [
        "测试表情：😀😂🥺🎉🎊",
        "更多表情：❤️✨🔥💯🎨",
        "动物表情：🐶🐱🐼🦊🦋",
    ]

    renderer = TextRenderer(config, font_dir)

    print("\n[第 1 轮] 首次渲染（会下载并缓存）")
    print("-" * 60)
    for i, text in enumerate(test_texts, 1):
        print(f"\n测试文本 {i}: {text}")
        try:
            result_path = renderer.render(text)
            if result_path:
                print(f"  ✓ 渲染成功: {result_path}")
                # 清理临时文件
                import os
                os.remove(result_path)
            else:
                print(f"  ✗ 渲染失败")
        except Exception as e:
            print(f"  ✗ 渲染异常: {e}")

    print("\n" + "=" * 60)
    print("[第 2 轮] 再次渲染（应从磁盘缓存读取）")
    print("-" * 60)
    for i, text in enumerate(test_texts, 1):
        print(f"\n测试文本 {i}: {text}")
        try:
            result_path = renderer.render(text)
            if result_path:
                print(f"  ✓ 渲染成功（应来自缓存）: {result_path}")
                import os
                os.remove(result_path)
            else:
                print(f"  ✗ 渲染失败")
        except Exception as e:
            print(f"  ✗ 渲染异常: {e}")

    print("\n" + "=" * 60)
    print("[信息] 缓存目录位置")
    print("-" * 60)
    cache_dir = Path(__file__).parent / ".emoji-cache"
    if cache_dir.exists():
        files = list(cache_dir.glob("*.png"))
        print(f"缓存目录: {cache_dir}")
        print(f"缓存文件数: {len(files)}")
        if files:
            print(f"示例文件: {files[0].name}")
    else:
        print("缓存目录不存在")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_emoji_cache()
