"""安全的 XML 解析。

ElementTree 默认接受 DTD 里的实体定义，几十字节就能展开成上百兆，
把命令行工具拖死。这里放行普通的 DOCTYPE 声明，但拒绝带内部子集
或实体定义的那种——它们才是实体炸弹的载体。

检查要在**解码之后**做：XML 可能是 UTF-16，按字节匹配的规则会被
另一种编码绕过去（同一份实体炸弹换成 UTF-16 就能溜进来）。
"""

import re
import xml.etree.ElementTree as ET

# 带内部子集的 DOCTYPE：<!DOCTYPE ... [ ... ]>。
_INTERNAL_SUBSET = re.compile(r'<!DOCTYPE[^>\[]*\[', re.IGNORECASE)
# 实体定义。
_ENTITY = re.compile(r'<!ENTITY', re.IGNORECASE)

# 检查只看文件开头这么多字节：实体声明总在文件头部。
HEAD_LIMIT = 8192


class XmlError(ValueError):
    """XML 不安全或无法解析。"""


def _decode_head(data):
    """取文件开头的文字，无论它是 UTF-8 还是 UTF-16。

    解码不出来就退回按字节看——宁可误报也不要漏掉实体声明。
    """
    for encoding in ('utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be'):
        try:
            return data.decode(encoding, errors='strict')
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('latin-1', errors='replace')


def _looks_like(text, pattern):
    """在文字里找特征。

    UTF-16 的 ASCII 字符之间夹着零字节，直接匹配会漏。
    把零字节去掉再找一次，两种编码都拦得住。
    """
    if pattern.search(text):
        return True
    squeezed = text.replace('\x00', '')
    return bool(pattern.search(squeezed))


def parse(data):
    """解析 XML，拒绝带内部子集或实体定义的内容。"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    head = _decode_head(data[:HEAD_LIMIT * 4])
    if _looks_like(head, _INTERNAL_SUBSET) or _looks_like(head, _ENTITY):
        raise XmlError('XML contains entity declarations; refusing to expand it')
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlError('Cannot parse XML: %s' % exc) from exc
