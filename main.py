"""
文字转图片插件
- 自适应高度，手机宽度
- 支持 emoji（Twemoji）
- 支持自动撤回
"""

import asyncio
import base64
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star

import astrbot.api.message_components as Comp

from .core import TextRenderer

# 尝试导入 aiocqhttp 事件类型
try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    HAS_AIOCQHTTP = True
except ImportError:
    HAS_AIOCQHTTP = False
    AiocqhttpMessageEvent = None

PLAIN_COMPONENT_TYPES = tuple(
    getattr(Comp, name)
    for name in ("Plain", "Text")
    if hasattr(Comp, name)
)

_URL_RE = re.compile(r"https?://[^\s<>,\"{}|\\^`\[\])]+", re.IGNORECASE)


class Text2ImagePlugin(Star):
    """文字转图片插件"""

    PLUGIN_ID = "astrbot_plugin_text2image_x"

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self._cfg_obj: AstrBotConfig | dict | None = config
        self._base_dir = Path(__file__).resolve().parent
        self._font_dir = self._base_dir / "ziti"
        self._render_semaphore = asyncio.Semaphore(3)
        self._recall_tasks: list[asyncio.Task] = []
        self._renderer_lock = threading.Lock()
        self._renderer: Optional[TextRenderer] = None
        self._renderer_cfg_fp: Optional[tuple[Any, ...]] = None

        # 扫描 ziti 目录的字体文件
        self._available_fonts = self._scan_fonts()
        if self._available_fonts:
            logger.info(f"[text2image-x] 检测到字体文件: {', '.join(self._available_fonts)}")

        logger.info("[text2image-x] 插件已加载")

    def cfg(self) -> Dict[str, Any]:
        try:
            return self._cfg_obj if isinstance(self._cfg_obj, dict) else (self._cfg_obj or {})
        except Exception:
            return {}

    def _cfg_bool(self, key: str, default: bool) -> bool:
        val = self.cfg().get(key, default)
        return bool(val) if not isinstance(val, str) else val.lower() in {"1", "true", "yes", "on"}

    def _scan_fonts(self) -> list[str]:
        """扫描 ziti 目录的字体文件
        
        Returns:
            字体文件名列表（不含路径）
        """
        font_extensions = {".otf", ".ttf", ".ttc"}
        available_fonts = []
        
        if not self._font_dir.exists():
            logger.debug(f"[text2image-x] 字体目录不存在: {self._font_dir}")
            return available_fonts
        
        try:
            for file_path in self._font_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in font_extensions:
                    available_fonts.append(file_path.name)
        except Exception as e:
            logger.warning(f"[text2image-x] 扫描字体目录失败: {e}")
        
        return sorted(available_fonts)

    async def terminate(self):
        """插件卸载时取消所有撤回任务"""
        for task in self._recall_tasks:
            task.cancel()
        self._recall_tasks.clear()

    def _schedule_recall(self, client, message_id: int):
        """安排撤回消息"""
        recall_time = int(self.cfg().get("recall_time", 0))
        if recall_time <= 0:
            return
        
        async def do_recall():
            try:
                await asyncio.sleep(recall_time)
                await client.delete_msg(message_id=message_id)
                logger.debug(f"[text2image-x] 已撤回消息: {message_id}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"[text2image-x] 撤回消息失败: {e}")
        
        task = asyncio.create_task(do_recall())
        task.add_done_callback(lambda t: self._recall_tasks.remove(t) if t in self._recall_tasks else None)
        self._recall_tasks.append(task)

    def _build_renderer_cfg_fp(self, cfg: Dict[str, Any]) -> tuple[Any, ...]:
        keys = (
            "image_width",
            "image_scale",
            "padding",
            "padding_left",
            "padding_right",
            "font_size",
            "line_height",
            "bg_color",
            "text_color",
            "font_name",
            "mono_font_name",
            "emoji_cache_dir",
            "emoji_timeout",
            "emoji_failed_ttl",
            "emoji_mode",
            "emoji_font_name",
            "hide_table_first_column_label",
        )
        return tuple(cfg.get(k) for k in keys)

    def _get_renderer(self) -> TextRenderer:
        cfg = self.cfg()
        cfg_fp = self._build_renderer_cfg_fp(cfg)
        with self._renderer_lock:
            if self._renderer is None or self._renderer_cfg_fp != cfg_fp:
                self._renderer = TextRenderer(cfg, self._font_dir)
                self._renderer_cfg_fp = cfg_fp
            return self._renderer

    async def _render_async(self, text: str) -> Optional[str]:
        try:
            renderer = self._get_renderer()
            return await asyncio.to_thread(renderer.render, text)
        except Exception as exc:
            logger.error("[text2image-x] 渲染失败: %s", exc)
            return None

    def _chain_to_plain_text(self, chain: list[Any]) -> Optional[str]:
        if not chain:
            return None
        builder: list[str] = []
        for seg in chain:
            if PLAIN_COMPONENT_TYPES and isinstance(seg, PLAIN_COMPONENT_TYPES):
                builder.append(getattr(seg, "text", "") or "")
            elif hasattr(seg, "text") and seg.__class__.__name__.lower() in {"plain", "text"}:
                builder.append(getattr(seg, "text", "") or "")
            # 跳过非 Plain 组件（At, Reply, Image 等），继续提取
        if not builder:
            return None
        text = "".join(builder).strip()
        return text if text else None

    def _extract_urls(self, text: str) -> list[str]:
        """从文本中提取 URL 列表（去重并保持顺序）

        Args:
            text: 原始文本

        Returns:
            提取到的链接列表
        """
        if not text:
            return []
        urls = _URL_RE.findall(text)
        if not urls:
            return []
        # 去除尾部常见标点（如 Markdown 链接的右括号、逗号、句末标点）
        cleaned = [url.rstrip("),.;:!?\"'>") for url in urls]
        # 使用 dict.fromkeys 去重并保留顺序
        return list(dict.fromkeys(cleaned))

    async def _send_link_forward(self, event: AstrMessageEvent, links: list[str]) -> None:
        """将链接以 QQ 合并转发消息的形式发送到当前对话

        Args:
            event: 当前消息事件
            links: 要发送的链接列表
        """
        if not links:
            return

        if not HAS_AIOCQHTTP or not isinstance(event, AiocqhttpMessageEvent):
            logger.warning(
                "[text2image-x] send_links_as_forward 仅支持 aiocqhttp (QQ) 平台，"
                "当前平台不会发送合并转发消息"
            )
            return

        sender_name = str(self.cfg().get("forward_sender_name", "AI 助手") or "AI 助手")
        intro = str(self.cfg().get("forward_intro_text", "以下为本条回复中的可点击链接") or "")

        # 最佳-effort 获取 Bot 自身 QQ 号
        uin = "0"
        raw_event = getattr(event.message_obj, "raw_message", None)
        if raw_event is not None and hasattr(raw_event, "get"):
            uin = str(raw_event.get("self_id", "0"))
        if uin == "0" and hasattr(event.message_obj, "self_id"):
            uin = str(getattr(event.message_obj, "self_id", "0"))

        nodes: list[Comp.Node] = []
        if intro:
            nodes.append(Comp.Node(name=sender_name, uin=uin, content=[Comp.Plain(text=intro)]))
        for link in links:
            nodes.append(Comp.Node(name=sender_name, uin=uin, content=[Comp.Plain(text=link)]))

        if not nodes:
            return

        try:
            await event.send(MessageChain([Comp.Nodes(nodes=nodes)]))
            logger.info(f"[text2image-x] 已发送链接合并转发消息，共 {len(links)} 个链接")
        except Exception as exc:
            logger.error("[text2image-x] 发送链接合并转发消息失败: %s", exc)

    @filter.on_decorating_result(priority=-10)
    async def on_decorating_result(self, event: AstrMessageEvent, *args, **kwargs):
        if not self._cfg_bool("enable_render", True):
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        render_scope = str(self.cfg().get("render_scope", "llm_only")).lower()
        resp = event.get_extra("llm_resp")
        is_llm_response = isinstance(resp, LLMResponse)

        if render_scope == "llm_only" and not is_llm_response:
            return

        text = self._chain_to_plain_text(result.chain)
        if not text:
            return

        preview = text.replace("\n", "\\n")
        keep_llm_log = self._cfg_bool("keep_llm_log", True)
        if keep_llm_log:
            logger.info(f"[text2image-x] LLM回复: {preview}")

        # 如果开启链接合并转发，先提取并发送链接
        if self._cfg_bool("send_links_as_forward", False):
            links = self._extract_urls(text)
            if links:
                await self._send_link_forward(event, links)

        char_threshold = int(self.cfg().get("render_char_threshold", 0))
        if char_threshold > 0 and len(text) > char_threshold:
            return

        async with self._render_semaphore:
            image_path = await self._render_async(text)

        if not image_path:
            return

        # 先读取图片为 base64（统一处理，避免文件问题）
        try:
            with open(image_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"[text2image-x] 读取图片失败: {e}")
            return
        finally:
            # 读取完成后立即删除临时文件
            try:
                os.remove(image_path)
            except Exception:
                pass

        # 检查是否需要自动撤回
        recall_enabled = self._cfg_bool("recall_enabled", False)
        recall_time = int(self.cfg().get("recall_time", 0))
        
        logger.debug(f"[text2image-x] recall_enabled={recall_enabled}, recall_time={recall_time}")
        
        if recall_enabled and recall_time > 0:
            # 检查是否是 aiocqhttp 事件类型
            if HAS_AIOCQHTTP and isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                logger.debug(f"[text2image-x] 检测到 aiocqhttp 事件, client={client}")
                
                if client is not None:
                    group_id = event.get_group_id()
                    user_id = event.get_sender_id()
                    
                    logger.debug(f"[text2image-x] group_id={group_id}, user_id={user_id}")
                    
                    # 构建消息（使用 base64）
                    msg = [{'type': 'image', 'data': {'file': f'base64://{img_data}'}}]
                    
                    try:
                        # 发送消息
                        if group_id:
                            send_result = await client.send_group_msg(group_id=int(group_id), message=msg)
                        else:
                            send_result = await client.send_private_msg(user_id=int(user_id), message=msg)
                        
                        logger.debug(f"[text2image-x] send_result={send_result}")
                        
                        # 安排撤回
                        if send_result:
                            msg_id = send_result.get('message_id')
                            if msg_id:
                                self._schedule_recall(client, int(msg_id))
                                logger.info(f"[text2image-x] 已安排 {recall_time}s 后撤回消息 {msg_id}")
                        
                        # 清空原消息链，阻止重复发送；存文本供下游上下文使用
                        event.set_extra("text2image_rendered_text", text)
                        result.chain.clear()
                        event.stop_event()
                        return
                    except Exception as e:
                        error_str = str(e)
                        # 超时错误（1200）消息可能已发送，不回退
                        if 'retcode=1200' in error_str or 'Timeout' in error_str:
                            logger.warning(f"[text2image-x] 发送超时但消息可能已送达，无法撤回")
                            event.set_extra("text2image_rendered_text", text)
                            result.chain.clear()
                            event.stop_event()
                            return
                        logger.warning(f"[text2image-x] 撤回模式发送失败: {e}，回退普通模式")
            else:
                logger.debug(f"[text2image-x] 非 aiocqhttp 事件类型，使用普通模式")

        # 普通模式：组件级别替换 Plain → Image，文本存入 event extra 供上下文使用
        try:
            image_comp = Comp.Image(file=f'base64://{img_data}')
            # 找到第一个 Plain 组件的位置，用 Image 替换；移除其余 Plain
            new_chain = []
            plain_replaced = False
            for seg in result.chain:
                is_plain = (
                    (PLAIN_COMPONENT_TYPES and isinstance(seg, PLAIN_COMPONENT_TYPES))
                    or (hasattr(seg, "text") and seg.__class__.__name__.lower() in {"plain", "text"})
                )
                if is_plain:
                    if not plain_replaced:
                        new_chain.append(image_comp)
                        plain_replaced = True
                    # 其余 Plain 跳过（已被合并渲染为一张图）
                else:
                    new_chain.append(seg)
            if not plain_replaced:
                new_chain.append(image_comp)
            result.chain = new_chain
            event.set_extra("text2image_rendered_text", text)
            logger.info(f"[text2image-x] 已渲染为图片，文本已存入上下文")
        except Exception as exc:
            logger.error("[text2image-x] 创建图片组件失败: %s", exc)

    @filter.on_llm_response(priority=100000)
    async def save_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        event.set_extra("llm_resp", resp)
