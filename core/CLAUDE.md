[根目录](../CLAUDE.md) > **core**

# core — 文本渲染引擎

## 模块职责

将 Markdown 文本解析为带样式的文本片段，处理 Emoji，并通过 PIL 渲染为图片。

```
输入: 纯文本字符串 (含 Markdown 语法)
  ├── markdown.py   → 解析为 TextSegment 列表 (含样式标记)
  ├── emoji.py      → 识别/下载/缓存 Twemoji 图片
  ├── renderer.py   → 布局计算 + PIL 绘制
  └── styles.py     → 数据模型定义 (TextSegment, TableRow, TableCell)
输出: 临时 JPG 文件路径
```

## 入口与启动

```python
# 外部通过 core/__init__.py 导入
from .core import TextRenderer

# 实例化渲染器
renderer = TextRenderer(config_dict, font_dir_path)

# 渲染文本为图片
image_path = renderer.render("Hello **world**")
```

`__init__.py` 导出清单: `TextSegment`, `EmojiHandler`, `TextRenderer`

## 对外接口

### TextRenderer (core/renderer.py)

| 方法 | 签名 | 说明 |
|------|------|------|
| `render` | `(text: str) -> Optional[str]` | 主入口，渲染文本为图片，返回临时文件路径 |
| `__init__` | `(config: Dict, font_dir: Path)` | 初始化渲染器、Emoji 处理器、字体/宽度缓存 |

### EmojiHandler (core/emoji.py)

| 方法 | 签名 | 说明 |
|------|------|------|
| `split_text` | `(text: str) -> List[TextSegment]` | 将文本拆分为普通文本/Emoji 片段 |
| `render_emoji` | `(emoji: str, size: int) -> Optional[Image]` | 获取 Emoji 图片（三级缓存：内存→磁盘→CDN） |

### Markdown 解析器 (core/markdown.py)

| 函数 | 签名 | 说明 |
|------|------|------|
| `parse_markdown` | `(text: str, ctx: LineContext) -> List[TextSegment]` | 主解析入口，支持标题/引用/表格/列表/分割线/行内样式 |
| `parse_table` | `(text: str) -> Optional[List[TableRow]]` | 独立表格解析（兼容旧 no_wrap 格式） |

### 数据模型 (core/styles.py)

| 类 | 关键字段 |
|-----|---------|
| `TextSegment` | `text`, `bold`, `italic`, `code`, `strike`, `heading`, `quote`, `code_block`, `horizontal_rule`, `list_item`, `list_ordered`, `list_level`, `list_index`, `list_continuation`, `is_newline`, `url`, `is_image`, `is_emoji`, `no_wrap` |
| `TableCell` | `text`, `segments: List[TextSegment]` |
| `TableRow` | `cells: List[TableCell]`, `is_header: bool` |

## 关键依赖与配置

### 外部依赖

| 依赖 | 用途 |
|------|------|
| `Pillow (PIL) >= 9.0.0` | 图片绘制引擎 (Image, ImageDraw, ImageFont) |
| `urllib.request` (标准库) | Twemoji CDN 下载 |
| `astrbot.api.logger` | 统一日志输出 |
| `astrbot.api.AstrBotConfig` | 插件配置对象（可选，compat 处理） |

### 内部依赖关系

```
styles.py  ← (无内部依赖)
  ↑
markdown.py ← styles.py
  ↑
emoji.py ← styles.py
  ↑
renderer.py ← styles.py, markdown.py, emoji.py
  ↑
__init__.py ← styles.py, emoji.py, renderer.py
```

### 配置项（来自根 `_conf_schema.json`）

渲染器直接读取的配置键：

| 配置键 | 默认值 | 使用者 |
|--------|--------|--------|
| `image_width` | 375 | renderer.render() |
| `image_scale` | 2 | renderer.render() |
| `padding` / `padding_left` / `padding_right` | 24 | renderer.render() |
| `font_size` | 24 | renderer.render() |
| `line_height` | 1.6 | renderer.render() |
| `bg_color` / `text_color` | #ffffff / #333333 | renderer.render() |
| `font_name` | Source_Han_Serif_SC_Light_Light.otf | renderer._load_font() |
| `mono_font_name` | "" | renderer._load_mono_font() |
| `emoji_cache_dir` | .emoji-cache | EmojiHandler |
| `emoji_timeout` | 10 | EmojiHandler |
| `emoji_failed_ttl` | 3600 | EmojiHandler |
| `char_width_cache_limit` | 8192 | renderer._get_char_render_width() |
| `hide_table_first_column_label` | false | markdown._serialize_table() |

## 数据模型

### TextSegment — 文本片段（核心单位）

一个文本片段携带完整的样式信息。渲染器按片段逐个处理，字符级换行时会将一个片段拆分为多个同属性片段。

**样式优先级**（绘制阶段）:
1. `code_block` → 等宽字体、灰色背景
2. `heading` → 标题字号, 加粗
3. `code` → 等宽字体、灰色背景（行内）
4. `quote` → 引用竖线 + 灰色文字
5. `url` → 蓝色 + 下划线
6. `strike` → 灰色 + 删除线
7. `bold` → 偏移重绘法模拟加粗
8. `italic` → 偏浅色（非真实斜体）

### 表格模型

表格解析后转为列表形式（卡片式布局）:
- 表头行 → 字段名列表
- 数据行 → 每个单元格展开为 `字段名：内容` 的列表项
- `hide_table_first_column_label` 开关可隐藏第一列字段名

## 测试与质量

### 现有测试

| 文件 | 覆盖范围 | 类型 |
|------|----------|------|
| `../test_emoji_cache.py` | EmojiHandler 缓存流程（下载→磁盘→二次命中） | 集成测试 |

### 测试缺口（按优先级）

1. **高**: `markdown.py` 的 `parse_markdown()` — 各语法元素解析正确性
2. **高**: `renderer.py` 的 `render()` — 端到端渲染（可用固定文本 + 像素对比）
3. **中**: `renderer.py` 的换行/分词逻辑 — 中日韩混排、Emoji 混排
4. **中**: `markdown.py` 的表格解析 — 边界情况（空表、单列表、无表头）
5. **低**: `emoji.py` 的 `_get_twemoji_urls()` — URL 格式正确性

### 没有的质量工具

- 无 linter 配置（建议 `ruff`）
- 无类型检查（建议 `mypy` 严格模式）
- 无 CI 配置（建议 GitHub Actions）

## 常见问题 (FAQ)

### Q: 如何添加新的 Markdown 语法？

1. 在 `markdown.py` 的 `INLINE_PATTERNS` 中添加新模式（注意顺序：长模式优先）
2. 如需新样式字段，在 `styles.py` 的 `TextSegment` 中添加
3. 在 `renderer.py` 的绘制循环中处理新样式
4. 在 `_merge_segments()` 中添加合并条件

### Q: 为什么字体没生效？

检查顺序：
1. 字体文件是否在 `ziti/` 目录下
2. 日志中是否有 `[text2image-x] 检测到字体文件: ...`
3. 配置 `font_name` 是否正确（需含扩展名，如 `.otf`）
4. 如仍失败，回退到 `ImageFont.load_default()`（系统默认字体）

### Q: Emoji 渲染为空白怎么办？

1. 检查网络连接（需访问 Twemoji CDN）
2. 查看日志 `[Emoji] 获取失败`（CDN 不可达）
3. 若失败，Emoji 回退为文本字符绘制（`draw.text` 降级路径）
4. 失败 TTL 内不会重复请求，可调低 `emoji_failed_ttl` 加速重试

### Q: 如何调试渲染器？

在 `render()` 中插入临时调试代码：
- 检查 `render_items` 列表（每条目包含 segments 和宽度）
- 检查 `line_layout` 字典（quote_offset, list_indent, effective_width）
- 绘制阶段用 `draw.rectangle` 添加辅助框线

### Q: auto-recall（自动撤回）为什么不工作？

前提条件：
1. `recall_enabled = true`
2. `recall_time > 0`
3. 必须使用 **aiocqhttp** 平台（QQ 官方接口）
4. 如发送超时（retcode=1200），消息可能已送达但无法撤回

## 相关文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `__init__.py` | 5 | 模块导出 |
| `styles.py` | 46 | 数据模型定义 |
| `emoji.py` | 257 | Emoji 下载/缓存/渲染 |
| `markdown.py` | 548 | Markdown 解析器 |
| `renderer.py` | 1011 | 文本渲染器（核心） |

---

## 变更记录 (Changelog)

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-06-01 | — | 初始化模块 AI 上下文文档 |
