# Emoji 缓存功能验证指南

## 实施完成的功能

### 1. 磁盘缓存
- **位置**: `插件根目录/.emoji-cache/`
- **文件命名**: `{codepoints}_{size}.png`
- **示例**: `1F600_72.png` (🚀 emoji, 72px)
- **作用**: 跨会话持久化缓存，减少网络请求

### 2. 失败缓存 TTL
- **默认 TTL**: 3600 秒（1 小时）
- **配置项**: `emoji_failed_ttl`
- **作用**: 失败的 emoji 在 TTL 内不再重复请求，避免反复失败

### 3. 下载超时配置
- **默认超时**: 10 秒
- **配置项**: `emoji_timeout`
- **作用**: 可根据网络环境调整超时时间

---

## 验证步骤

### 步骤 1：首次渲染（下载并缓存）

在机器人中发送一条包含 emoji 的消息，例如：
```
测试表情：😀😂🥺🎉🎊
```

**预期结果**:
- ✓ 图片正常渲染
- ✓ 控制台日志显示 `[Emoji] 获取失败: ...` 或无错误（表示下载成功）
- ✓ 缓存目录 `插件根目录/.emoji-cache/` 下生成了 PNG 文件

**检查缓存**:
```bash
cd "D:\Vibe Coding\astrbot_plugin_text2image\astrbot_plugin_text2image"
ls .emoji-cache/
# 应看到类似 1F600_72.png 的文件
```

---

### 步骤 2：断网验证（从磁盘缓存加载）

**方法 A - 断网测试**:
1. 暂时禁用网络（如关闭 Wi-Fi）
2. 重启 AstrBot（清空内存缓存）
3. 再次发送相同的 emoji 消息

**预期结果**:
- ✓ 图片正常渲染（来自磁盘缓存）
- ✓ 控制台无下载相关日志

**方法 B - 禁用 CDN 测试**:
1. 临时修改 `core/emoji.py` 中的 `CDN_BASES` 为无效地址
2. 重启 AstrBot
3. 发送已缓存的 emoji

---

### 步骤 3：失败 TTL 验证

**方法**:
1. 构造一个不存在的 emoji（或临时断网）
2. 发送消息，确认失败
3. 控制台日志显示 `[Emoji] 获取失败: ...`
4. 在 TTL 内（默认 1 小时）再次发送相同 emoji
5. 观察控住台日志

**预期结果**:
- ✓ TTL 期内无重复下载请求（直接返回 None）
- ✓ 过期后重新尝试下载

**调整 TTL 进行快速测试**:
在配置文件中设置较短的 TTL：
```json
"emoji_failed_ttl": 10
```

---

### 步骤 4：超时配置验证

**调整超时**:
在配置文件中：
```json
"emoji_timeout": 5
```

**预期结果**:
- ✓ 网络慢时 5 秒后超时
- ✓ 日志显示超时错误

---

## 配置文件示例

在插件配置中添加（`_conf_schema.json` 已更新）：

```json
{
  "emoji_cache_dir": "",
  "emoji_timeout": 10,
  "emoji_failed_ttl": 3600
}
```

**参数说明**:
- `emoji_cache_dir`: 留空使用默认目录（推荐），或指定绝对路径
- `emoji_timeout`: 下载超时（秒），网络差可调至 15-30
- `emoji_failed_ttl`: 失败 TTL（秒），测试环境可设为 60

---

## 代码变更摘要

### `core/emoji.py`
- `EmojiHandler.__init__`: 新增 `cache_dir`, `timeout`, `failed_ttl` 参数
- `EmojiHandler.render_emoji`: 新增磁盘缓存读写、失败 TTL 检查逻辑
- `EmojiHandler._failed`: 从 `set` 改为 `Dict[str, float]`（记录时间戳）

### `core/renderer.py`
- `TextRenderer.__init__`: 从配置读取 emoji 参数并传递给 `EmojiHandler`

### `_conf_schema.json`
- 新增配置项：`emoji_cache_dir`, `emoji_timeout`, `emoji_failed_ttl`

---

## 故障排查

### 问题：缓存目录未创建
**检查**: 确认插件有写入权限
```bash
# 手动创建测试
mkdir -p ".emoji-cache"
```

### 问题：缓存文件存在但仍下载
**原因**: 内存缓存未命中但磁盘缓存读取失败
**检查**: 查看控住台日志中是否有 `[Emoji] 磁盘缓存读取失败` 警告

### 问题：失败的 emoji 仍重复请求
**检查**:
1. 确认 `emoji_failed_ttl` 配置正确加载
2. 查看重启后 `_failed` 字典是否被正确初始化

---

## 性能影响评估

- **首次渲染**: 无影响（仍需下载）
- **后续渲染**:
  - 磁盘缓存命中: **显著提升**（避免网络请求）
  - 失败 TTL 命中: **显著提升**（避免重复失败请求）
- **磁盘占用**: 每个 emoji 约 5-15 KB（取决于 size）

---

## 实施原则符合性

✓ **KISS**: 最小增量改动，复用现有结构
✓ **YAGNI**: 仅实现必需功能（磁盘缓存、TTL、超时配置）
✓ **DRY**: 统一缓存逻辑（内存 + 磁盘）
✓ **单一职责**: `EmojiHandler` 仍只负责 emoji 处理
✓ **开闭原则**: 通过配置扩展，无需修改核心逻辑
