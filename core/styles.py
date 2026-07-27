"""样式定义"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class TextSegment:
    """文本片段"""
    text: str
    is_emoji: bool = False
    no_wrap: bool = False
    # Markdown 样式
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    # 行级样式
    heading: int = 0          # 标题级别: 0=无, 1-6
    quote: bool = False       # 引用
    code_block: bool = False  # 代码块
    horizontal_rule: bool = False  # 分割线
    # 列表样式
    list_item: bool = False       # 是否列表项
    list_ordered: bool = False    # 是否有序列表
    list_level: int = 0           # 列表缩进级别
    list_index: int = 0           # 有序列表序号（无序为0）
    list_continuation: bool = False  # 是否列表换行延续
    is_newline: bool = False      # 是否强制换行
    url: str = ""                 # 链接 URL（[text](url) 或 ![alt](url)）
    is_image: bool = False        # 是否为图片语法
    # 表格结构化数据标记
    is_table: bool = False        # 是否为表格占位段
    table_rows: List["TableRow"] = field(default_factory=list)


@dataclass
class TableCell:
    """表格单元格"""
    text: str
    segments: List[TextSegment]


@dataclass
class TableRow:
    """表格行"""
    cells: List[TableCell]
    is_header: bool = False
