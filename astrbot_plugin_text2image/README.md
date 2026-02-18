# 文字转图片

> 本项目基于 https://github.com/Rensr0/astrbot_plugin_text2image 二次修改发布。

AstrBot 插件，将 Bot 文字回复渲染为图片。

## 功能

- 自适应高度
- 手机宽度 (375px @2x 高清)
- 支持 emoji（Twemoji 彩色图标）
- 支持自动撤回

## 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| enable_render | 启用渲染 | true |
| render_scope | llm_only / all_text | llm_only |
| render_char_threshold | 字符阈值，0=不限 | 0 |
| image_width | 逻辑宽度 | 375 |
| image_scale | 渲染倍数 | 2 |
| padding | 上下内边距（左右默认值） | 24 |
| padding_left | 左边距（默认继承 padding） | 24 |
| padding_right | 右边距（默认继承 padding） | 24 |
| font_size | 字体大小 | 24 |
| line_height | 行高倍数 | 1.6 |
| bg_color | 背景色 | #ffffff |
| text_color | 文字色 | #333333 |
| keep_llm_log | 保留原LLM回复日志 | true |
| font_name | 主字体文件名（放置于 ziti 目录） | Source_Han_Serif_SC_Light_Light.otf |
| mono_font_name | 等宽字体文件名（放置于 ziti 目录） | 空（自动回退系统字体） |
| recall_enabled | 启用自动撤回 | false |
| recall_time | 撤回时间（秒） | 30 |

### 字体配置说明

- **字体放置位置**：将字体文件（`.otf` / `.ttf` / `.ttc`）放入插件目录的 `ziti` 文件夹中
- **主字体 (`font_name`)**：用于普通文本渲染，文件不存在时自动使用默认字体
- **等宽字体 (`mono_font_name`)**：用于代码块渲染，留空或加载失败时自动回退到系统字体（Consolas、Courier New 等）
- **字体扫描**：插件加载时会自动扫描 `ziti` 目录并在日志中显示可用字体列表

## 致谢

- [小钊 / astrbot_plugin_recall_xz](https://github.com/zxqtd/astrbot_plugin_recall_xz) - 自动撤回功能参考
- [传话筒·立绘对话框](https://github.com/bvzrays/astrbot_plugin_chuanhuatong) - 原始项目
