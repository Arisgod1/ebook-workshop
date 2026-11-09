"""HTML 读入：把 HTML 文件或网页存成的书读成转换中间表示。

只用标准库的 html.parser 实现。章节识别按标题标签（h1 到 h6）来分。
标题文字原样保留——转换不是编辑，不该悄悄改掉原文。

单个 HTML 文件通常是一整本书；多个文件时每个文件算一本书的一部分。
"""

import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import htmltext, paths
from .book import Book, Document, Image

# 标题标签对应的级别。
HEADING_TAGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}
# 会切出段落的块级标签。
BLOCK_TAGS = ('p', 'div', 'section', 'article', 'li', 'blockquote', 'tr')
# 章节容器的标签：class="chapter" 只在块级容器上才算章节标记。
CONTAINER_TAGS = ('div', 'section', 'article')
# 行内标签：保留成 HTML 片段，正文里的强调与链接才不会丢。
INLINE_TAGS = ('b', 'strong', 'i', 'em', 'u', 'span', 'a', 'code', 'sub', 'sup',
               'small', 'cite', 'q', 'abbr', 'del', 'ins')

_IMAGE_TYPES = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'bmp': 'image/bmp', 'tif': 'image/tiff',
    'tiff': 'image/tiff', 'svg': 'image/svg+xml', 'webp': 'image/webp',
}


class HtmlError(ValueError):
    """HTML 文件本身有问题。"""


class _Extractor(HTMLParser):
    """把 HTML 拆成段落、标题与图片。

    blocks 里的每一项是 [类型, 级别, parts]，parts 是按原文顺序排列的
    (kind, value)：文字、图片、行内标签。输出时只转义文字，标签作为
    markup 写出，图片则换成书内路径。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.title = ''
        self._buffer = []
        self._heading = None
        self._skip = 0
        self._in_title = False
        # 章节容器与块级标签分开记：内层同名的 <div> 结束时不会把
        # 容器提前关掉；容器用独立的 _containers 栈管理。
        self._frames = []         # [(tag, container or None)]，按打开顺序
        self._inline_open = []    # 未闭合的行内标签
        self._containers = []     # 当前打开的章节容器（支持嵌套）

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ('script', 'style'):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == 'title':
            self._in_title = True
            return
        if tag == 'img':
            source = attributes.get('src')
            if source:
                self._buffer.append(('image', source))
            return
        if tag in ('br', 'hr'):
            self._buffer.append(('text', '\n'))
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._heading = HEADING_TAGS[tag]
            self._buffer = []
            self._frames.append((tag, None))
            return
        if (tag in CONTAINER_TAGS
                and 'chapter' in (attributes.get('class') or '').split()):
            # class="chapter" 的容器本身算一章。先判容器再判普通块级标签，
            # 多 class（class="note chapter"）也能认出来。
            self._flush()
            block = ['heading', 1, [('text', '')]]
            container = {'title': None, 'block': block}
            self.blocks.append(block)
            self._frames.append((tag, container))
            self._containers.append(container)
            return
        if tag in BLOCK_TAGS:
            self._flush()
            self._frames.append((tag, None))
            return
        if tag in INLINE_TAGS:
            if tag == 'a':
                href = _safe_href(attributes.get('href'))
                if not href:
                    return        # 没有合法目标的 <a> 只保留文字
                opening = '<a href="%s">' % htmltext.escape_attr(href)
            else:
                opening = '<%s>' % tag
            self._buffer.append(('tag', opening))
            self._inline_open.append(tag)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == 'title':
            self._in_title = False
            return
        if tag in HEADING_TAGS:
            self._end_heading(tag)
            return
        if tag in INLINE_TAGS:
            if tag in self._inline_open:
                while self._inline_open:
                    opened = self._inline_open.pop()
                    self._buffer.append(('tag', '</%s>' % opened))
                    if opened == tag:
                        break
            return
        if tag in BLOCK_TAGS or tag in CONTAINER_TAGS:
            self._flush()
            self._drop_frames(tag)

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self._buffer.append(('text', data))

    def _end_heading(self, tag):
        """标题结束：容器里的第一个标题是章名，其余标题保留成子章节。"""
        level = self._heading or HEADING_TAGS[tag]
        parts = [part for part in self._buffer if part[0] == 'text']
        text = _plain(parts)
        if self._containers:
            container = self._containers[-1]
            if container['title'] is None:
                container['title'] = text
            else:
                self.blocks.append(['heading', level, parts])
        else:
            self.blocks.append(['heading', level, parts])
        self._buffer = []
        self._heading = None
        # 标题里没闭合的行内标签不影响标题文字，也不能漏到下一个段落。
        self._inline_open = []
        self._drop_frames(tag)

    def _drop_frames(self, tag):
        """弹出直到 tag 的所有框架，并收尾被跳过的容器。"""
        index = None
        for position in range(len(self._frames) - 1, -1, -1):
            if self._frames[position][0] == tag:
                index = position
                break
        if index is None:
            return
        dropped = self._frames[index:]
        del self._frames[index:]
        for _tag, container in reversed(dropped):
            if container is not None:
                self._finish_container(container)

    def _finish_container(self, container):
        """容器结束：章名写进预先占位的标题块（不用插值，位置不会错位）。"""
        if container in self._containers:
            self._containers.remove(container)
        # 没有标题或标题是空白时退化成「正文」，不要往目录里塞空条目。
        if not (container['title'] or '').strip():
            container['title'] = '正文'
        container['block'][2] = [('text', container['title'])]

    def _flush(self):
        """把缓冲区里的内容作为一个段落输出。"""
        if self._heading is not None:
            return
        # 段落结束时把没闭合的行内标签补齐，写出的 XHTML 才是良构的。
        for opened in reversed(self._inline_open):
            self._buffer.append(('tag', '</%s>' % opened))
        self._inline_open = []
        if any(kind == 'image' or (kind == 'text' and value.strip())
               for kind, value in self._buffer):
            self.blocks.append(['paragraph', 0, self._buffer])
        self._buffer = []

    def finish(self):
        if self._heading is not None:
            self._end_heading('h1')
        self._flush()
        for container in list(reversed(self._containers)):
            self._finish_container(container)
        self._frames = []
        return self.blocks


def _plain(parts):
    """取一段里的纯文字，用于标题与空内容判定。"""
    return ''.join(value for kind, value in parts if kind == 'text').strip()


def _inline_html(parts, images, anchors=None):
    """渲染一段里的文字、图片与行内标签，保留出现顺序。

    anchors 是「包里确实存在的链接目标」集合。指向别处的相对链接
    会让 EPUB 写出时校验失败，这里退化成纯文字——宁可少一个链接，
    也不能让整本书写不出来。
    """
    out = []
    downgraded = 0          # 有多少个 <a> 被换成了 <span>，闭合标签要跟着换
    for kind, value in parts:
        if kind == 'text':
            out.append(htmltext.escape(value).replace('\n', '<br/>'))
        elif kind == 'image':
            href = images.get(value)
            if href:
                out.append('<img src="%s" alt=""/>' % htmltext.escape_attr(href))
        elif kind == 'tag':
            if value.startswith('</a>'):
                out.append('</span>' if downgraded else value)
                if downgraded:
                    downgraded -= 1
            elif anchors is None or _anchor_ok(value, anchors):
                out.append(value)
            else:
                out.append('<span>')
                downgraded += 1
    return ''.join(out).strip()


_HREF = re.compile(r'^\s*<a\s[^>]*href\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)


def _anchor_ok(tag, anchors):
    """这个 <a> 的 href 是否指向包里存在的目标。

    外链不需要目标在包里；页内锚点（#x）与相对路径需要。
    """
    match = _HREF.match(tag)
    if not match:
        return True
    href = match.group(1)
    if href.startswith(('http://', 'https://', 'mailto:', 'ftp://')):
        return True
    path = href.partition('#')[0]
    fragment = href.partition('#')[2]
    if not path:
        # 页内锚点：目标文档自己必须有这个 id，否则写出时判悬空。
        # 简化为要求同页有对应 id 太复杂，这里一律退化成文字——
        # 页内锚点在电子书里本来也少见。
        return False
    if path not in anchors:
        return False
    if fragment:
        # 带片段的链接要求目标文档里有这个 id；我们生成的文档
        # 只有章节锚点，跨文档片段一律退化成文字。
        return False
    return True



def _paragraph_html(parts, images, anchors=None):
    html = _inline_html(parts, images, anchors)
    return '<p>' + html + '</p>' if html else ''


def _safe_href(href):
    """链接地址：外链与包内相对路径都留着，越界路径丢掉。"""
    if not href:
        return ''
    if href.startswith(('#', 'http://', 'https://', 'mailto:', 'ftp://')):
        return href
    path = href.partition('#')[0]
    if not path or paths.safe_path(path) is None:
        return ''
    return href


def _safe_src(source):
    """把 img 的 src 清洗成包内相对路径；外链、data URI 返回 None。"""
    if not source:
        return None
    split = urlsplit(source.strip())
    if split.scheme or split.netloc:
        return None
    path = unquote(split.path)
    if path.startswith('/'):
        return None
    return paths.safe_path(path)


def _media_type(href):
    """只认图片类型；别的后缀（.txt、.html…）一律不算图片。"""
    suffix = href.rsplit('.', 1)[-1].lower() if '.' in href else ''
    return _IMAGE_TYPES.get(suffix)


def _image_mapping(blocks, book, base_dir):
    """给正文里引用到的图片建立 src -> 书内路径 的映射。

    read() 有磁盘位置，会把相邻的图片文件读进 book.images；读不到的
    （外链、data URI、缺文件）就不写 img，免得 EPUB 清单里出现悬空引用。
    read_text() 没有磁盘位置，只保留相对路径引用，由调用方补资源。
    """
    sources = dict.fromkeys(
        value for _kind, _level, parts in blocks
        for part_kind, value in parts if part_kind == 'image')
    mapping = {}
    used = set()
    for source in sources:
        safe = _safe_src(source)
        if safe is None:
            continue
        if base_dir is None:
            # read_text() 没有磁盘位置，读不到图片字节。
            # 保留引用会让 EPUB 清单里出现不存在的资源、写出时校验失败，
            # 所以宁可不写这张图。
            continue
        candidate = (base_dir / safe).resolve()
        if not candidate.is_relative_to(base_dir.resolve()) or not candidate.is_file():
            continue
        media_type = _media_type(safe)
        if media_type is None:
            continue
        href = posixpath.join('images', safe)
        if href not in used:
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            book.add_image(Image(href, data, media_type))
            used.add(href)
        mapping[source] = href
    return mapping


def read(path, encoding='utf-8'):
    """读入一个 HTML 文件，返回中间表示。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise HtmlError('Cannot read HTML file: %s' % exc) from exc
    for candidate in (encoding, 'utf-8-sig', 'gb18030'):
        try:
            text = data.decode(candidate)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise HtmlError('Cannot decode HTML file; supply the correct encoding')
    return _build(text, Path(path).resolve().parent)


def read_text(text):
    """从一段 HTML 文本构造中间表示。"""
    return _build(text, None)


def _build(text, base_dir=None):
    parser = _Extractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise HtmlError('Cannot parse HTML: %s' % exc) from exc
    blocks = parser.finish()

    book = Book(title=parser.title)
    chapters = []
    paragraphs = []
    current = None
    index = 0
    stack = []

    for kind, level, parts in blocks:
        if kind == 'heading':
            index += 1
            title = _plain(parts)
            current = {'level': level, 'title': title, 'index': index,
                       'paragraphs': [], 'children': []}
            chapters.append(current)
            while stack and stack[-1]['level'] >= level:
                stack.pop()
            if stack:
                stack[-1]['children'].append(current)
            stack.append(current)
        elif current is not None:
            current['paragraphs'].append(parts)
        else:
            paragraphs.append(parts)

    if not chapters and not paragraphs:
        raise HtmlError('HTML contains no readable text')

    if not chapters:
        chapters = [{'level': 1, 'title': book.title or '正文', 'index': 1,
                     'paragraphs': paragraphs, 'children': []}]
        paragraphs = []

    # 顶层章节是没有父章节的那些，而不是硬编码 level==1：
    # 以 h2 开头的文档，h2 就是顶层，不该整章丢弃。
    top_level = _roots(chapters)
    if paragraphs:
        # 第一个标题之前的前言、序言、版权说明挂到第一章正文前面。
        top_level[0]['paragraphs'] = paragraphs + top_level[0]['paragraphs']
        paragraphs = []

    images = _image_mapping(blocks, book, base_dir)
    # 包里会存在的文档与图片：链接指向它们的才保留成 <a>。
    anchors = {'ch%04d.xhtml' % chapter['index'] for chapter in top_level}
    anchors.add('title.xhtml')
    anchors.update(images.values())
    for chapter in top_level:
        href = 'ch%04d.xhtml' % chapter['index']
        book.add_document(Document(href, content=_chapter_html(chapter, images, anchors)))
        book.toc.append(htmltext.toc_entry(chapter, href))
    if book.title:
        book.add_document(Document('title.xhtml', content=htmltext.title_page(book)))
    return book


def _roots(chapters):
    """找出没有父章节的章节，即顶层章节。"""
    attached = set()
    for chapter in chapters:
        for child in chapter['children']:
            attached.add(id(child))
    roots = [c for c in chapters if id(c) not in attached]
    if not roots:
        smallest = min(c['level'] for c in chapters)
        roots = [c for c in chapters if c['level'] == smallest]
    return roots


def _chapter_html(chapter, images, anchors=None):
    return htmltext.chapter_document(
        chapter, lambda paragraph: _paragraph_html(paragraph, images, anchors))
