"""ODT 读入：把 LibreOffice / OpenOffice 文档读成转换中间表示。

ODT 是 ZIP 包，关键部件：

  content.xml          正文，命名空间为 text:、office:、draw:、table:
  meta.xml             元数据（dc:title、dc:creator 等）
  Pictures/            图片文件

只用标准库的 zipfile 与 xml.etree 实现（zip 的打开经 archive 做安全校验，
XML 部件与包内路径在这里再做一次独立校验）。
"""

import re
import xml.etree.ElementTree as ET

from . import htmltext, paths
from .archive import Archive, ArchiveError
from .book import Book, Document, Image

TEXT = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
OFFICE = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
DC = 'http://purl.org/dc/elements/1.1/'
META = 'urn:oasis:names:tc:opendocument:xmlns:meta:1.0'
XLINK = 'http://www.w3.org/1999/xlink'

# 单个 XML 部件解压后的上限。带实体声明的部件一律不解析（下面），因此
# 这个上限同时也是「展开后」的上限，几百字节的炸弹不会再变成几百兆文本。
MAX_XML_PART = 64 * 1024 ** 2

# 带内部子集的 DOCTYPE 或实体定义，就是实体炸弹的载体。
# 扫描整个部件而不是只看开头：声明可以藏在长注释后面绕过只看开头的检查。
_UNSAFE_XML = re.compile(rb'<!\s*(?:ENTITY|DOCTYPE[^>\[]*\[)', re.IGNORECASE)

# ODT 里的内部链接指向书签或别的 ODT 文件，转出的 EPUB 里没有这些目标。
# 只有这些外部协议写进 XHTML 后仍指得到东西。
_EXTERNAL_SCHEMES = ('http://', 'https://', 'mailto:', 'ftp://')


def q(namespace, name):
    return '{%s}%s' % (namespace, name)


class OdtError(ValueError):
    """ODT 文件本身有问题。"""


class _UnsafeXml(OdtError):
    """XML 部件带实体声明或大得异常，拒绝展开。"""


class _MalformedXml(OdtError):
    """XML 部件本身格式不对。"""


def _parse_xml(data, label):
    """解析一个 ODT 的 XML 部件，拒绝 DTD/实体与超大的部件。"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if len(data) > MAX_XML_PART:
        raise _UnsafeXml('%s is %d bytes; refusing to parse XML parts over %d bytes'
                         % (label, len(data), MAX_XML_PART))
    if _UNSAFE_XML.search(data):
        raise _UnsafeXml('%s contains a DTD/entity declaration; refusing to expand it' % label)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise _MalformedXml('%s is not valid XML: %s' % (label, exc)) from exc


def read(path):
    """读入一个 ODT 文件，返回中间表示。"""
    try:
        archive = Archive(path)
    except ArchiveError as exc:
        raise OdtError('Not a readable ODT (ZIP) file: %s' % exc) from exc
    with archive:
        names = set(archive.namelist())
        if 'content.xml' not in names:
            raise OdtError('ODT has no content.xml; it is not an OpenDocument text file')
        return _read_parts(archive, names)


class _Blocks:
    """遍历 office:text 时累积的章节与段落状态。

    text:section 是块级容器，里面的 text:h / text:p / text:list 和外层
    规则完全一样，所以递归时也带着同一份状态，标题才进得了目录。
    """

    def __init__(self):
        self.chapters = []
        self.paragraphs = []
        self.current = None
        self.index = 0
        self.stack = []

    def add_heading(self, element):
        title = _text_of(element)
        try:
            level = int(element.get(q(TEXT, 'outline-level')) or 1)
        except (TypeError, ValueError):
            level = 1
        self.index += 1
        current = {'level': level, 'title': title, 'index': self.index,
                   'paragraphs': [], 'children': []}
        self.chapters.append(current)
        while self.stack and self.stack[-1]['level'] >= level:
            self.stack.pop()
        if self.stack:
            self.stack[-1]['children'].append(current)
        self.stack.append(current)
        self.current = current

    def add_block(self, content):
        if not content:
            return
        if self.current is not None:
            self.current['paragraphs'].append(content)
        else:
            self.paragraphs.append(content)


def _read_blocks(container, pictures, blocks):
    """按 h/p/list/section 的规则处理一个块级容器的子元素。"""
    for element in container:
        name = element.tag.split('}')[-1]
        if name == 'h':
            blocks.add_heading(element)
        elif name == 'p':
            blocks.add_block(_paragraph_html(element, pictures))
        elif name == 'list':
            # 列表整体转成 ul/ol。
            blocks.add_block(_list_html(element, pictures))
        elif name == 'section':
            # section 只是容器：递归进去，标题照样建章节、段落照样归位。
            _read_blocks(element, pictures, blocks)
        elif name in ('table', 'table-row', 'table-cell'):
            blocks.add_block(_paragraph_html(element, pictures))


def _read_parts(archive, names):
    book = Book()
    _read_meta(archive, names, book)
    pictures = _read_pictures(archive, names, book)

    root = _parse_xml(archive.read('content.xml'), 'content.xml')

    body = root.find(q(OFFICE, 'body'))
    if body is None:
        raise OdtError('ODT content.xml has no office:body')

    text_body = body.find(q(OFFICE, 'text'))
    if text_body is None:
        raise OdtError('ODT has no office:text section')

    blocks = _Blocks()
    _read_blocks(text_body, pictures, blocks)
    chapters = blocks.chapters
    paragraphs = blocks.paragraphs

    if not chapters and not paragraphs:
        raise OdtError('ODT contains no readable text')

    if not chapters:
        chapters = [{'level': 1, 'title': book.title or '正文', 'index': 1,
                     'paragraphs': paragraphs, 'children': []}]
        paragraphs = []

    top_level = [c for c in chapters if c['level'] == 1] or chapters
    for chapter in top_level:
        href = 'ch%04d.xhtml' % chapter['index']
        book.add_document(Document(href, content=_chapter_html(chapter)))
        book.toc.append(htmltext.toc_entry(chapter, href))
    if book.title:
        book.add_document(Document('title.xhtml', content=htmltext.title_page(book)))
    return book


def _read_meta(archive, names, book):
    """读 meta.xml 里的书名、作者等。

    dc:title 这些元素在 office:meta 里面，要往下找一层。
    元数据是可选的，格式坏了可以跳过；但带实体炸弹的部件必须继续报错，
    不能靠吞异常假装安全。
    """
    if 'meta.xml' not in names:
        return
    try:
        root = _parse_xml(archive.read('meta.xml'), 'meta.xml')
    except _MalformedXml:
        return
    holder = root.find(q(OFFICE, 'meta'))
    if holder is None:
        holder = root

    def value(tag):
        found = holder.find(q(DC, tag))
        if found is None:
            found = root.find(q(DC, tag))
        return (found.text or '') if found is not None else ''

    book.title = value('title').strip()
    book.author = value('creator').strip()
    language = value('language').strip()
    if language:
        book.language = language
    book.description = value('description').strip()


def _picture_path(name):
    """只认 Pictures/ 下的规范相对路径；越界或非规范写法一律拒绝。

    Archive 在 ZIP 层已经挡过一次，这里是读入插件自己的第二道检查：
    写进 Book 的图片 href 必须始终留在 Pictures/ 里，不能让
    'Pictures/../evil.png' 被规范化成书根目录下的 evil.png。
    """
    if not name or not name.startswith('Pictures/') or name.endswith('/'):
        return None
    if '\\' in name or ':' in name.split('/')[0]:
        return None
    if any(piece in ('', '.', '..') for piece in name.split('/')):
        return None
    return name


def _picture_for(href, pictures):
    """把正文里的图片引用归一到包内路径，只找确实存在的图片。"""
    cleaned = paths.safe_path(href)
    if cleaned is None or _picture_path(cleaned) is None:
        return None
    return pictures.get(cleaned)


def _read_pictures(archive, names, book):
    """收集包内的图片，返回路径到 Image 的映射。"""
    pictures = {}
    for name in sorted(names):
        safe = _picture_path(name)
        if safe is None:
            continue
        media_type = 'image/png' if safe.lower().endswith('.png') else \
            'image/jpeg' if safe.lower().endswith(('.jpg', '.jpeg')) else \
            'image/gif' if safe.lower().endswith('.gif') else 'application/octet-stream'
        image = Image(safe, archive.read(name), media_type)
        pictures[safe] = image
        book.add_image(image)
    return pictures


def _plain_text(element):
    """按文档顺序收集一个元素里的全部文字（含 text:a 的锚文本）。"""
    parts = []
    if element.text:
        parts.append(element.text)
    for child in element:
        name = child.tag.split('}')[-1]
        if name == 's':
            # ODF 的空格不可折叠，用不换行空格表示，HTML 渲染才不会把它们并成普通空格。
            parts.append('\xa0' * _space_count(child.get(q(TEXT, 'c'))))
        elif name == 'tab':
            parts.append('\t')
        elif name == 'line-break':
            parts.append('\n')
        else:
            parts.append(_plain_text(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def _text_of(element):
    """收集标题等元素里的可见文字，链接里的锚文本不能丢。"""
    return _plain_text(element).strip()


def _space_count(value):
    """text:s 的 text:c 属性表示空格个数。

    只有正数才算个数；0、负数或不是数字时表示一个空格都没有。
    """
    if value is None:
        return 1
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def _safe_link(href):
    """链接地址只保留写出后仍指得到的那些。

    ODT 的内部链接（相对路径、#书签）指向的目标不会出现在生成的书里，
    原样写成 <a href> 会让 epub.validate_files 判「悬空引用」，整本 EPUB
    写不出来。这类链接退化成纯文字，锚文本一个字都不丢。
    """
    if not href:
        return ''
    if any(href.strip().lower().startswith(scheme) for scheme in _EXTERNAL_SCHEMES):
        return href
    return ''


def _paragraph_html(element, pictures):
    """把一个 ODT 段落转成 HTML 片段。

    段落里可能嵌套 text:span、text:a 等；链接必须成对写出开闭标签，
    否则生成的 XHTML 不合法，EPUB 写出阶段会直接失败。
    """
    text = _inline_html(element, pictures).strip()
    if not text:
        return ''
    return '<p>' + text.replace('\n', '<br/>') + '</p>'


def _inline_html(element, pictures, in_link=False):
    """递归渲染一段里的行内内容，保持文档顺序。

    in_link 用来防止 text:a 里再嵌 text:a 生成非法的嵌套 <a>。
    """
    parts = []
    if element.text:
        parts.append(htmltext.escape(element.text))
    for child in element:
        name = child.tag.split('}')[-1]
        if name == 's':
            # ODF 的空格不可折叠，用不换行空格表示。
            parts.append('\xa0' * _space_count(child.get(q(TEXT, 'c'))))
        elif name == 'tab':
            parts.append('\t')
        elif name == 'line-break':
            parts.append('\n')
        elif name == 'a':
            href = '' if in_link else _safe_link(child.get(q(XLINK, 'href')) or '')
            inner = _inline_html(child, pictures, in_link=in_link or bool(href))
            if href:
                parts.append('<a href="%s">%s</a>' % (htmltext.escape_attr(href), inner))
            else:
                parts.append(inner)
        elif name == 'image':
            # 只保留包里真实存在的图片，并按清洗后的路径引用。
            image = _picture_for(child.get(q(XLINK, 'href')) or '', pictures)
            if image is not None:
                parts.append('<img src="%s" alt=""/>' % htmltext.escape_attr(image.href))
        else:
            parts.append(_inline_html(child, pictures, in_link=in_link))
        if child.tail:
            parts.append(htmltext.escape(child.tail))
    return ''.join(parts)


def _list_html(element, pictures):
    """把一个 text:list 转成 ul 或 ol。

    列表是有序还是无序，看 text:list 的 list-style 属性；
    找不到就按无序处理。
    """
    ordered = False
    for style in element.iter(q(TEXT, 'list-style')):
        if style.get(q(TEXT, 'num-format')):
            ordered = True
            break
        name = style.get(q(TEXT, 'name')) or ''
        if 'number' in name.lower() or 'arabic' in name.lower():
            ordered = True
            break

    tag = 'ol' if ordered else 'ul'
    parts = ['<' + tag + '>']
    for item in element.findall(q(TEXT, 'list-item')):
        # 列表项里的嵌套列表单独处理，不混进本项的文字。
        nested = []
        for child in list(item):
            if child.tag == q(TEXT, 'list'):
                nested.append(_list_html(child, pictures))
            else:
                content = _paragraph_html(child, pictures)
                if content:
                    inner = content.strip()
                    if inner.startswith('<p>') and inner.endswith('</p>'):
                        inner = inner[3:-4]
                    if inner:
                        parts.append('<li>' + inner + '</li>')
        for piece in nested:
            parts.append(piece)
    parts.append('</' + tag + '>')
    return ''.join(parts)


def _chapter_html(chapter):
    """生成一章的 XHTML。段落已经是渲染好的 HTML 片段，直接接上去。"""
    return htmltext.chapter_document(chapter, lambda paragraph: paragraph or '')
