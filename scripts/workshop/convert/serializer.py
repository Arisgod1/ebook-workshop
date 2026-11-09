"""把书内的内容文档序列化成 MOBI 的单一 HTML 文本流。

MOBI 只保存一条 HTML 流，所有章节首尾相接。章节之间的链接写成
filepos="000000000" 占位，等整条流写完后再按真实偏移回填。

图片写成 recindex="NNNNN"，指向资源记录里的序号。
"""

import re
import unicodedata

from .mobi import to_base

# MOBI 认可的块级标签与行内标签。
BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'li', 'blockquote', 'tr', 'center', 'ul', 'ol', 'table',
              'thead', 'tbody', 'section', 'article'}
INLINE_TAGS = {'b', 'strong', 'i', 'em', 'u', 'font', 'span', 'sup', 'sub',
               'small', 'big', 'a', 'img', 'br'}

# 这些标签的内容不保留：脚本与样式在电子书里没有意义。
DROPPED_TAGS = {'script', 'style', 'head'}

# 内联目录在 id_offsets 里用的键。不是书内路径，所以不会和文档撞名；
# guide 的 type="toc" 引用它，回填成正文里内联目录的起点。
TOC_ANCHOR = '@toc'
# 正文里内联目录容器的 id。用一个不容易和章节锚点撞名的前缀。
TOC_ID = 'mbp-toc'


class Serializer:
    """把多份内容文档写成一条 MOBI 文本流。"""

    def __init__(self, documents, images=None, toc=None, kf8=False):
        self.documents = documents
        self.images = images or {}
        self.toc = toc or []
        # KF8 与 MOBI 6 的图片引用方式不同，写出时要区分。
        self.kf8 = kf8
        self.buf = bytearray()
        self.id_offsets = {}
        self.href_offsets = {}
        self.image_index = {}
        self.start_offset = None
        # 片段在整条文本流里的起点。render() 只知道自己在片段内的位置，
        # 加上这个基址才是全局偏移；回填用的是全局偏移。
        self.fragment_start = 0
        self._start_href = None
        # 内联目录的锚点：guide 的 type="toc" 指向它。
        self._toc_anchor = None

    def __call__(self):
        self.buf = bytearray()
        self._toc_anchor = self._choose_toc_anchor()
        self.write(b'<html>')
        self.write_head()
        self.write_body()
        self.write(b'</html>')
        self.fixup_links()
        return bytes(self.buf)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.buf.extend(data)

    def write_head(self):
        """写出 head 与 guide。

        guide 里的 start 项决定阅读器打开书时停在哪一章，
        toc 项决定「跳转目录」按钮的行为。规范只允许一个 toc 项。

        MOBI 6 的读端（Calibre 的 reader/mobi6.py）不看 INDX 目录索引，
        它看的是正文里的内联 HTML 目录：guide 的 toc 指向锚点，
        从锚点往后扫 <a> 直到遇到 class="mbp_pagebreak" 为止。
        只写一个指向章节的 filepos 的话，目录里只会得到那一章的标题。
        所以在 MOBI 6 的正文最前面补一份内联目录，toc 指向它。
        """
        self.write(b'<head>')
        if self.toc:
            self.write(b'<guide>')
            # 第一个带链接的条目才是「从这里开始读」。顶层第一项可能是
            # 没有链接的分组标题（例如「第一部」），拿它当 start 会写不出
            # start 引用，Kindle 于是随便挑一个位置打开。
            start = _first_linked_entry(self.toc)
            toc_target = self._toc_anchor if self._toc_anchor else (start.href if start else None)
            if toc_target:
                # Kindle 用 filepos 跳转；Calibre 导入时按 href 的片段
                # 去正文里找那个 id，再把它之后的 <a> 当成目录项。
                # 两种写法都要有。
                self.write(b'<reference type="toc" title="Table of Contents" filepos=')
                self.href_offsets.setdefault(toc_target, []).append(len(self.buf))
                self.write(('0000000000 href="#%s" />' % TOC_ID).encode('utf-8'))
            if start is not None:
                self.write(b'<reference type="start" title="Start Reading" filepos=')
                self.href_offsets.setdefault(start.href, []).append(len(self.buf))
                self._start_href = start.href
                self.write(b'0000000000 />')
            self.write(b'</guide>')
        self.write(b'</head>')

    def write_body(self):
        self.write(b'<body>')
        self.body_start = len(self.buf)
        self._write_inline_toc()
        for document in self.documents:
            self.write_item(document)
        self.write(b'</body>')

    def _write_inline_toc(self):
        """在正文最前面写 MOBI 6 的内联目录。

        MOBI 6 用它在「跳转目录」里列出章节。条目按目录树缩进
        （blockquote 的左边距是读端推断层级的依据），
        末尾用一个 mbp_pagebreak 标记目录结束——读端扫到这里就停。

        KF8 不走这条路：它的目录由 NCX 索引提供，正文里多一份内联目录
        反而会让读者在正文开头看到一份重复的目录。
        """
        if self.kf8 or not self.toc or not self._toc_anchor:
            return
        # guide 的 toc 引用的就是这里：先把锚点位置记进 id_offsets。
        self.id_offsets[self._toc_anchor] = len(self.buf)
        self.write(self.serialize_fragment(self._toc_html(), ''))

    def _choose_toc_anchor(self):
        """内联目录在 id_offsets 里用的键。

        MOBI 6 的 guide 只能写 filepos，读端把它换成 '#fileposN' 锚点后
        再扫内联目录；所以这里只需要一个内部键，供 guide 的 toc 引用。
        KF8 的目录走 NCX 索引，不需要。

        目录树里一个带链接的条目都没有时不写内联目录：空目录只是
        白占一页，读端也还原不出任何东西。
        """
        if self.kf8 or not _has_linked_entries(self.toc):
            return None
        return TOC_ANCHOR

    def _toc_html(self):
        """把目录树渲染成内联目录的 HTML 片段。

        容器带 id：Calibre 导入 MOBI 时按 guide 里 toc 引用的片段
        找到这个元素，把它之后的 <a> 全部当成目录项。
        没有 id 就找不到，导入出的 EPUB 里没有目录。
        """
        parts = ['<div id="%s"><p><b>目录</b></p>' % TOC_ID]
        parts.append(self._render_toc_level(self.toc, 0))
        # 读端扫到 class="mbp_pagebreak" 就停止收集目录项。
        parts.append('<div class="mbp_pagebreak"></div></div>')
        return ''.join(parts)

    def _render_toc_level(self, entries, depth):
        """目录树的一层：有链接的条目缩进 depth 层。

        读端靠 blockquote 的左边距推断层级，所以层级要真的写进嵌套结构，
        不能只把条目平铺成一列。
        """
        parts = []
        indent = '<blockquote>' * (depth + 1)
        close = '</blockquote>' * (depth + 1)
        for entry in entries:
            if entry.href:
                parts.append('%s<p><a href="%s">%s</a></p>%s' % (
                    indent, escape_attr(entry.href), escape_text(entry.title), close))
            # 分组标题没有链接，它的子项整体再深一层。
            parts.append(self._render_toc_level(
                entry.children, depth + (1 if entry.href else 0)))
        return ''.join(parts)

    def write_item(self, document):
        """写出一份内容文档，并在开头记录它的起始偏移。"""
        href = document.get('href', '')
        self.id_offsets[href] = len(self.buf)
        body = document.get('body', '')
        self.write(self.serialize_fragment(body, href))
        self.write(b'<mbp:pagebreak/>')

    def serialize_fragment(self, html, base_href):
        """把一段 HTML 片段转成 MOBI 认可的标记。

        片段渲染时只知道自己在片段内的偏移；这里记下片段在
        整条文本流里的起点，render() 加上它才是全局偏移。
        """
        self.fragment_start = len(self.buf)
        out = bytearray()
        out.extend(self.strip_tags(html, base_href))
        return bytes(out)

    def strip_tags(self, html, base_href):
        """去掉不允许的标签，并保留可识别内容的部分。

        这里做的是保守处理：保留段落、标题、换行、强调这些有阅读价值的
        标记，其余一律丢弃但保留其文字。

        解析时按开闭标签配对，把文字归到它所在的元素里，避免同一段文字
        既出现在标签内又作为独立文本输出一次。
        """
        tree = parse_tree(html)
        out = bytearray()
        self.render(tree, base_href, out)
        return bytes(out)

    def render(self, node, base_href, out):
        """把解析好的元素树渲染成 MOBI 标记。"""
        for child in node[1]:
            if isinstance(child, str):
                out.extend(escape(child).encode('utf-8'))
                continue
            name = child[0].split(':')[-1].lower()
            attrs = child[2]
            if name in DROPPED_TAGS:
                continue
            # 记下锚点的位置，目录里指向 #片段 的链接才找得到落点。
            identifier = attrs.get('id')
            if identifier:
                self.id_offsets.setdefault('%s#%s' % (base_href, identifier),
                                           self.fragment_start + len(out))
            if name == 'img':
                source = attrs.get('src', '')
                # 先按文档位置换算成书内路径，再找资源。
                target = self.resolve(base_href, source) or source
                index = self.assign_image(target)
                if index is not None:
                    # 图片标签要用资源键（不是链接里的原始路径），
                    # 才能从 images 里读到真正的 media_type。
                    out.extend(self.image_tag(index, self._image_key(target)).encode('utf-8'))
                continue
            if name == 'br':
                out.extend(b'<br/>')
                continue
            if name == 'a':
                href = attrs.get('href', '')
                target = self.resolve(base_href, href)
                if target:
                    out.extend(b'<a filepos=')
                    # 回填用的是整条文本流里的绝对位置，不是本片段内的位置。
                    self.href_offsets.setdefault(target, []).append(
                        self.fragment_start + len(out))
                    out.extend(b'0000000000')
                    out.extend(b'>')
                    self.render(child, base_href, out)
                    out.extend(b'</a>')
                else:
                    self.render(child, base_href, out)
                continue
            if name in BLOCK_TAGS:
                # 内联目录末尾的分页标记要保留 class：读端靠它知道目录到此结束。
                if name == 'div' and 'mbp_pagebreak' in (attrs.get('class') or '').split():
                    out.extend(b'<div class="mbp_pagebreak"></div>')
                    continue
                # id 要写出来：目录锚点与 Calibre 的 guide 定位都靠它。
                # 属性里的引号必须转义，否则 id 含引号时会生成畸形标记。
                opening = '<%s%s>' % (name, ' id="%s"' % escape_attr(identifier)
                                      if identifier else '')
                out.extend(opening.encode('utf-8'))
                self.render(child, base_href, out)
                out.extend(('</' + name + '>').encode('utf-8'))
                continue
            if name in INLINE_TAGS:
                out.extend(('<' + name + '>').encode('utf-8'))
                self.render(child, base_href, out)
                out.extend(('</' + name + '>').encode('utf-8'))
                continue
            # 其余标签丢弃，但文字保留。
            self.render(child, base_href, out)

    def assign_image(self, source):
        """给图片分配资源序号。

        序号从 1 开始：读端用它减掉首条资源记录号再取记录。
        路径对不上时按文件名兜底，图片在子目录里也能找到。

        每次调用都返回同一个序号：同一张图引用两次不能占两个资源槽，
        否则读端的资源列表与实际记录数对不上，后面的图片全部错位。
        """
        key = self._image_key(source)
        if key is None:
            return None
        index = self.image_index.get(key)
        if index is None:
            index = len(self.image_index) + 1
            self.image_index[key] = index
        return index

    def _image_key(self, source):
        """找出图片在 images 里的键：先精确匹配，再按文件名兜底。

        链接里的路径与 images 的键可能不在同一基准（一个相对文档、
        一个相对内容目录），所以路径解析对不上时还要按文件名找。
        """
        if source in self.images:
            return source
        if not source:
            return None
        name = source.rsplit('/', 1)[-1]
        for candidate in self.images:
            if candidate.rsplit('/', 1)[-1] == name:
                return candidate
        return None

    def image_tag(self, index, target):
        """按目标格式生成图片标签。

        MOBI 6 用 recindex（资源记录号）；
        KF8 只认 kindle:embed:XXXX（base32 大写、1-based），
        写成 recindex 的话 Calibre 导入后图片会全部消失。
        """
        if not self.kf8:
            return '<img recindex="%05d"/>' % index
        mime = 'image/jpeg'
        image = self.images.get(target)
        if image is not None and getattr(image, 'media_type', None):
            mime = image.media_type
        return '<img src="kindle:embed:%s?mime=%s"/>' % (to_base(index, min_digits=4), mime)

    def resolve(self, base_href, href):
        """把相对链接解析成书内路径（含片段）。

        EPUB 的文档内链接写成 '#id' 或 'chapter.xhtml#id'。片段不能
        当成路径的一部分去拼，否则会得到一个并不存在的文件路径；
        这里先把片段拆出来，只对路径部分做归一化，再拼回片段。
        指向本文档的 '#id' 解析成 '<base_href>#id'，与目录项的键一致。
        """
        if not href or '://' in href:
            return None
        path, separator, fragment = href.partition('#')
        if not path:
            # 同文档内的锚点：目录项用的是 '<href>#<id>' 形式。
            if not separator or not fragment or not base_href:
                return None
            return '%s#%s' % (base_href, fragment)
        if path.startswith('/'):
            resolved = path[1:]
        else:
            directory = base_href.rsplit('/', 1)[0] if '/' in base_href else ''
            parts = directory.split('/') if directory else []
            for piece in path.split('/'):
                if piece in ('', '.'):
                    continue
                if piece == '..':
                    if parts:
                        parts.pop()
                    continue
                parts.append(piece)
            resolved = '/'.join(parts)
        if separator and fragment:
            return '%s#%s' % (resolved, fragment)
        return resolved

    def fixup_links(self):
        """把所有 filepos 占位符替换成真实偏移。

        找不到目标时跳过：回退到 body_start 会让链接静默指向书首，
        读者点进去看到的是第一章而不是该去的地方，比不跳更糟。
        带片段的链接先按完整键找，找不到再退回文档起点——
        目标文档存在、只是锚点没落到位置，指向文档开头仍然合理。
        """
        for href, offsets in self.href_offsets.items():
            target = self.id_offsets.get(href)
            if target is None and '#' in href:
                target = self.id_offsets.get(href.split('#', 1)[0])
            if target is None:
                continue
            if href == self._start_href:
                self.start_offset = target
            for position in offsets:
                self.buf[position:position + 10] = ('%010d' % target).encode('ascii')

def escape(text):
    """转义 HTML 特殊字符，并去掉软连字符。

    只转真正的裸字符：来源可能是已经转义过的 HTML 字符串
    （读入端把 XML 实体解成了裸字符，但内容仍是标记文本），
    这里的 '&' 若是某个实体的一部分就不能再转一次，
    否则 L&amp;N 会变成 L&amp;amp;N，读者看到字面的 &amp;。
    """
    text = _BARE_AMPERSAND.sub('&amp;', text)
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    text = text.replace('­', '')
    return unicodedata.normalize('NFC', text)


def escape_attr(text):
    """转义要写进属性值的文字（href、id 这类）。"""
    return escape(text).replace('"', '&quot;')


def _has_linked_entries(entries):
    """目录树里是否有至少一个带链接的条目。"""
    for entry in entries:
        if entry.href or _has_linked_entries(entry.children):
            return True
    return False


def _first_linked_entry(entries):
    """按深度优先找出第一个带链接的条目。"""
    for entry in entries:
        if entry.href:
            return entry
        found = _first_linked_entry(entry.children)
        if found is not None:
            return found
    return None


# escape_attr 的别名：目录项的标题与链接各自该走的转义路径。
escape_text = escape


# 裸 & ：后面不是实体名与分号。
_BARE_AMPERSAND = re.compile(r'&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#[0-9]{1,7}|#x[0-9a-fA-F]{1,6});)')


_TAG = re.compile(r'<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9:._-]*)([^>]*?)(/?)\s*>', re.S)

# 自闭合的 HTML 标签，不需要结束标签。
VOID_TAGS = {'br', 'hr', 'img', 'meta', 'link', 'input', 'col'}


def parse_tree(html):
    """把 HTML 片段解析成 (标签, 属性, 子节点) 的树。

    节点形式：('tag', (子节点...), 属性字典)，文本节点直接用字符串。
    只做轻量解析，不追求完整 HTML 规范：电子书里的标记来自受控的
    生成流程，不需要处理脚本、样式或畸形嵌套。
    """
    root = ('root', [], {})
    stack = [root]
    position = 0
    for match in _TAG.finditer(html):
        text = html[position:match.start()]
        if text:
            stack[-1][1].append(text)
        position = match.end()
        closing = match.group(1)
        name = match.group(2).lower()
        if closing:
            # 找到最近的同名开标签并闭合它。
            for index in range(len(stack) - 1, 0, -1):
                if stack[index][0] == name:
                    del stack[index:]
                    break
            continue
        if name in VOID_TAGS:
            stack[-1][1].append((name, [], parse_attrs(match.group(3))))
            continue
        node = (name, [], parse_attrs(match.group(3)))
        stack[-1][1].append(node)
        stack.append(node)
    text = html[position:]
    if text:
        stack[-1][1].append(text)
    return root


def parse_attrs(raw):
    """解析标签属性串。"""
    attrs = {}
    for match in re.finditer(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"|'
                             r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*\'([^\']*)\'', raw):
        if match.group(1) is not None:
            attrs[match.group(1).lower()] = match.group(2)
        else:
            attrs[match.group(3).lower()] = match.group(4)
    return attrs
