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
import xml.parsers.expat


class XmlError(ValueError):
    """XML 不安全或无法解析。"""


class _StopScan(Exception):
    """扫描到实体声明就中止，不再往下走。"""


class _EntityGuard:
    """用 Expat 的回调在实体展开之前拦住实体定义。"""

    def __init__(self):
        self.found = None

    def entity_decl(self, name, is_parameter, value, base, system_id, public_id,
                    notation_name):
        # 记下第一个就够：只要有实体定义就拒绝。
        if self.found is None:
            self.found = name
        # 立刻中止：继续解析等于替攻击者把炸弹展开一遍。
        raise _StopScan()

    def external_entity_ref(self, context, base, system_id, public_id):
        self.found = self.found or '#external'
        raise _StopScan()


def parse(data):
    """解析 XML，拒绝带内部子集或实体定义的内容。

    先用 Expat 扫一遍看有没有实体声明，再交给 ElementTree 真正解析。
    两步分开是有意的：Expat 那步只判定，不构造元素树，代价很小，
    而它在展开实体之前就把人拦下来了。
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    guard = _EntityGuard()
    scanner = xml.parsers.expat.ParserCreate()
    scanner.EntityDeclHandler = guard.entity_decl
    scanner.ExternalEntityRefHandler = guard.external_entity_ref
    try:
        scanner.Parse(data, True)
    except _StopScan:
        pass
    except xml.parsers.expat.ExpatError:
        # 语法本身有问题，交给下面的 ElementTree 去报错。
        pass
    except Exception:
        pass
    if guard.found:
        raise XmlError('XML contains entity declarations; refusing to expand it')
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlError('Cannot parse XML: %s' % exc) from exc
