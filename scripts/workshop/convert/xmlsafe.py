"""安全的 XML 解析。

ElementTree 默认接受 DTD 里的实体定义，几十字节就能展开成上百兆，
把命令行工具拖死。这里放行普通的 DOCTYPE 声明，但拒绝带内部子集
或实体定义的那种——它们才是实体炸弹的载体。

检查用**解析器自己的回调**而不是扫文本：DOCTYPE 声明可以出现在根元素
之前的任意位置（前面垫一段长注释就能躲过「只看文件开头」的检查），
扫文本永远有被绕开的可能。Expat 的 EntityDeclHandler 在解析到实体
定义的那一刻就会告诉我们，正好在展开之前。

编码也不用操心：Expat 自己认 UTF-8 / UTF-16 / 带 BOM 的形式。
"""

import xml.etree.ElementTree as ET

from .. import epub


class XmlError(ValueError):
    """XML 不安全或无法解析。"""


def parse(data):
    """解析 XML，拒绝带内部子集或实体定义的内容。

    先用 Expat 扫一遍看有没有实体声明，再交给 ElementTree 真正解析。
    两步分开是有意的：Expat 那步只判定，不构造元素树，代价很小，
    而它在展开实体之前就把人拦下来了。

    编码也不用操心：Expat 自己认 UTF-8 / UTF-16 / 带 BOM 的形式。
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    try:
        epub.refuse_entities(data)
    except ValueError as exc:
        raise XmlError(str(exc)) from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlError('Cannot parse XML: %s' % exc) from exc
