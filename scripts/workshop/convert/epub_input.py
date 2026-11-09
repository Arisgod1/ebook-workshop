"""EPUB 读入：把已有的 EPUB 读成转换中间表示。

项目里已经有 EPUB 的读写实现（workshop.epub），这里只做「读成中间表示」
这一层，好让 EPUB 也能参与格式互转。

坐标约定：Book 里的 href（文档、图片、样式、资源、目录链接）一律相对于
OPF 所在的内容目录（通常是 OEBPS），而不是相对于链接所在的那份文档。
epub_output 也把文件全部写在内容目录下，两端才对得上。
"""

import html
import html.entities
import posixpath
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from .. import epub
from . import paths, xmlsafe
from .book import Book, Document, Image, TocEntry

# 一些电子书在 XHTML 里直接写 HTML 实体（如 &nbsp;）而没有声明 DTD。
# XML 解析器不认这些实体，遇到就把它们换成对应的字符。
_ENTITY = re.compile(rb'&([a-zA-Z][a-zA-Z0-9]*);')
# XML 自己就认这五个，展开成裸字符反而会把合法 XHTML 弄成非法 XML。
_XML_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}
# 裸 & 要重新转义，否则片段里原有的 & 会让写出的 XHTML 解析失败。
_BARE_AMPERSAND = re.compile(
    r'&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#[0-9]{1,7}|#x[0-9a-fA-F]{1,6});)')
_BODY_TAG = re.compile(r'</?body\b[^>]*>', re.I)
# 这几类 manifest 项有专门的读入路径，不重复收进 resources。
_HANDLED_MEDIA_TYPES = {
    'application/xhtml+xml', 'text/css', 'application/x-dtbncx+xml',
    'application/oebps-package+xml',
}


def _local(tag):
    """去掉命名空间，只留元素名；老 EPUB 常常不带命名空间。"""
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _children(element, name):
    """按本地名取直接子元素。"""
    return [child for child in element if _local(child.tag) == name]


def _type_values(element):
    """读 epub:type（兼容没加命名空间的写法），可能含多个值。"""
    value = element.get(epub.q(epub.EP, 'type')) or element.get('type') or ''
    return value.split()


def _expand_entities(data):
    """把 HTML 独有的实体替换成字符，让 XML 解析器能读。

    XML 原生实体必须原样留着；展开出来的字符里若含 & < > 也要重新转义，
    否则一份本来合法的 XHTML 会被改坏。
    """

    def replace(match):
        name = match.group(1).decode('ascii')
        if name in _XML_ENTITIES:
            return match.group(0)
        char = html.entities.html5.get(name + ';')
        if char is None:
            return match.group(0)
        return html.escape(char, quote=False).encode('utf-8')

    return _ENTITY.sub(replace, data)


def _parse(data):
    """解析一份 XHTML，遇到 HTML 实体时先展开。"""
    try:
        return epub.xml(data)
    except ET.ParseError:
        return epub.xml(_expand_entities(data))


def read(path):
    """读入一个 EPUB 文件，返回中间表示。"""
    files, opf, package = epub.load(path)
    _, spine = epub.inventory(files, opf, package)
    # 内容目录不一定是 OEBPS：老工具用 OPS，Sigil 常写 EPUB。
    # 以 package 文档所在目录为准，正文、图片、目录才对得齐。
    base = opf.rsplit('/', 1)[0] if '/' in opf else ''

    book = Book()
    book.title = epub.book_title(package)
    for creator in package.findall('.//' + epub.q(epub.D, 'creator')):
        if creator.text:
            book.author = creator.text.strip()
            break
    for language in package.findall('.//' + epub.q(epub.D, 'language')):
        if language.text:
            book.language = language.text.strip()
            break
    for identifier in package.findall('.//' + epub.q(epub.D, 'identifier')):
        if identifier.text:
            book.identifier = identifier.text.strip()
            break
    _read_metadata(package, book)

    # 中间表示没有专门的非图片资源字段（不能改 book.py），读入端按需挂上：
    # href -> (原始字节, media-type)。写出端用 getattr 取，别的输入端不受影响。
    book.resources = {}

    spine_docs = []
    seen = set()
    for name, item in spine:
        if name in seen or item.get('media-type') != 'application/xhtml+xml':
            continue
        if 'nav' in (item.get('properties') or '').split():
            continue
        seen.add(name)
        if name in files:
            spine_docs.append((name, item, False))
    # 不在 spine 里的 XHTML（封面页、扉页、附录）同样要带走：目录或正文
    # 里的链接会指向它们，丢掉就变成断链。这类文档按非线性处理。
    for entry in _manifest_items(package):
        if entry.get('media-type') != 'application/xhtml+xml':
            continue
        if 'nav' in (entry.get('properties') or '').split():
            continue
        name = _resolve(opf, entry.get('href'))
        if name is None or name in seen or name not in files:
            continue
        seen.add(name)
        spine_docs.append((name, entry, True))

    documents = []
    for name, item, extra in spine_docs:
        if extra:
            # 附属页面是可选的：格式坏掉就跳过，不让一份附录拖垮整本书。
            try:
                root = _parse(files[name])
            except (ET.ParseError, ValueError):
                continue
        else:
            root = _parse(files[name])
        body = _find_body(root)
        if body is None:
            continue
        properties = []
        semantic = set()
        for element in body.iter():
            semantic.update(_type_values(element))
        # 标题页由读入端显式标记，写出端不再靠正文里的字符串去猜。
        if 'titlepage' in semantic:
            properties.append('titlepage')
        if 'cover' in semantic:
            properties.append('cover')
        if extra or semantic & {'cover', 'titlepage'}:
            properties.append('non-linear')
        documents.append(Document(_relative(name, base), content=_document_html(root, body),
                                  properties=properties))
    if not documents:
        raise ValueError('EPUB contains no readable documents')
    for document in documents:
        book.add_document(document)

    _read_images(files, package, opf, book, base)
    _read_styles(files, package, opf, base, book)
    book.toc = _read_toc(files, package, opf, base, book)
    # 目录或正文可能指向不在 spine 里的 XHTML（封面页、附录）。
    # 为这些链接补一份文档，写出的 EPUB 才不会有断链。
    _include_linked_documents(files, base, book)
    book.cover = _find_cover(package, opf, files, base, book)
    _check_references(files, package, opf, base, book)
    return book


def _check_references(files, package, opf, base, book):
    """正文里引用的图片必须真的在这本书里。

    缺图片的书到写出阶段才报「断链」，报错指向写出端，看起来像是转换
    出了问题；实际是源书自己引用了一张不存在的图。在这里先说清楚。
    """
    known = set(book.images) | set(book.resources)
    for document in book.documents:
        for element in _document_elements(document.content):
            name = _local(element.tag)
            if name not in ('img', 'image'):
                continue
            source = element.get('src') or element.get(epub.q(epub.XLINK, 'href')) or ''
            if not source or '://' in source or source.startswith(('data:', '#')):
                continue
            # src 相对于文档自身，换算成相对内容目录的形式再比对。
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(document.href), unquote(source.partition('#')[0])))
            if target.startswith('../'):
                continue
            if target not in known:
                raise ValueError(
                    'EPUB 引用了一张不存在的图片：%s 里指向 %s。'
                    '这份源书本身缺图片，请补齐后重试，或先删掉这个引用。'
                    % (document.href, source))


def _document_elements(content):
    """把文档内容解析出元素，解析不了就当没有。"""
    try:
        return list(_parse(content.encode('utf-8')).iter())
    except (ET.ParseError, ValueError):
        return []


def _include_linked_documents(files, base, book):
    """补上被目录/正文引用、但不在 spine 里的 XHTML 文档。"""
    known = {document.href for document in book.documents}
    wanted = set()
    for entry in book.toc:
        for item in entry.walk():
            path, _, _ = (item.href or '').partition('#')
            if path and '://' not in path:
                wanted.add(path)
    for name, data in files.items():
        if not name.endswith(('.xhtml', '.html', '.htm')):
            continue
        safe = paths.relative_to(base, name)
        if safe is None or safe in known or safe not in wanted:
            continue
        try:
            root = _parse(data)
        except (ET.ParseError, ValueError):
            continue
        body = _find_body(root)
        if body is None:
            continue
        book.add_document(Document(safe, content=_document_html(root, body),
                                  properties=['non-linear']))
        known.add(safe)


def _manifest_items(package):
    return package.findall('./' + epub.q(epub.O, 'manifest') + '/' + epub.q(epub.O, 'item'))


def _relative(path, base=''):
    """把包内路径转成相对于内容目录的形式。"""
    return paths.relative_to(base, path) or path


def _resolve(source, href):
    """把相对 source 文件的引用解析成包内路径。

    允许 '../' 往内容目录里退（nav 常放在子目录），但越出包根或写成
    绝对/外部 URL 的一律丢弃，绝不让它们变成包里的坏链接。
    """
    if not href:
        return None
    path = unquote(href.split('#', 1)[0])
    if '://' in path or path.startswith(('mailto:', 'tel:')):
        return None
    if path.startswith('/'):
        joined = posixpath.normpath(path.lstrip('/'))
    else:
        directory = source.rsplit('/', 1)[0] if '/' in source else ''
        joined = posixpath.normpath(posixpath.join(directory, path))
    if joined in ('', '.', '..') or joined.startswith(('../', '/')):
        return None
    return paths.safe_path(joined)


def _normalise_href(href, source, base):
    """把目录里的链接换算成相对内容目录的形式。"""
    if not href:
        return None
    if '://' in href or href.startswith(('mailto:', 'tel:')):
        return href
    path, separator, fragment = href.partition('#')
    if not path:
        return _relative(source, base) + (separator + fragment if separator else '')
    joined = _resolve(source, path)
    if joined is None:
        return None
    relative = paths.relative_to(base, joined)
    if relative is None:
        return None
    return relative + (separator + fragment if separator else '')


def _find_body(root):
    """找 body；没有 XHTML 命名空间的老书也要找得到。"""
    if _local(root.tag) == 'body':
        return root
    bodies = _children(root, 'body')
    return bodies[0] if bodies else None


def _read_metadata(package, book):
    """把 OPF 里其余的 dc 元数据带进中间表示。"""
    metadata = package.find('./' + epub.q(epub.O, 'metadata'))
    if metadata is None:
        return
    handled = {'title', 'creator', 'language', 'identifier'}
    for element in metadata:
        if not element.tag.startswith('{%s}' % epub.D):
            continue
        name = element.tag.split('}')[-1]
        if name in handled:
            continue
        value = (element.text or '').strip()
        if not value:
            continue
        book.metadata.setdefault(name, []).append(value)
    book.publisher = (book.metadata.get('publisher') or [''])[0]
    book.description = (book.metadata.get('description') or [''])[0]


def _read_styles(files, package, opf, base, book):
    """收下书里的样式表；正文的排版全靠这些 CSS。"""
    for entry in _manifest_items(package):
        if entry.get('media-type') != 'text/css':
            continue
        path = _resolve(opf, entry.get('href'))
        if path is None or path not in files:
            continue
        safe = paths.relative_to(base, path)
        if safe is None:
            continue
        book.styles[safe] = files[path].decode('utf-8', 'replace')


def _attributes(element):
    """序列化元素属性，把命名空间还原成已声明的前缀。"""
    prefixes = {epub.EP: 'epub', epub.XLINK: 'xlink', epub.XMLNS: 'xml'}
    parts = []
    for key, value in element.attrib.items():
        if key.startswith('{'):
            uri, name = key[1:].split('}', 1)
            prefix = prefixes.get(uri)
            if prefix is None:
                continue
            key = '%s:%s' % (prefix, name)
        parts.append(' %s="%s"' % (key, html.escape(value)))
    return ''.join(parts)


def _document_html(root, body):
    """把一份 XHTML 文档转成完整、可序列化的 XHTML 字符串。

    外壳必须带 XHTML 命名空间：没有它，写出的 EPUB 再读回来时找不到
    body，正文会整本丢掉。body 的属性（id/class）也要留着，目录里指向
    body 锚点的链接才不会断。XML 声明由写出端统一加：放进这里的字符串
    会被 MOBI 的文本流当成可见文字印出来。
    """
    inner = (body.text or '') + ''.join(ET.tostring(child, encoding='unicode')
                                        for child in body)
    # 根元素已经声明了默认命名空间，子元素上的重复声明去掉。
    inner = inner.replace(' xmlns="%s"' % epub.H, '')
    inner = _BODY_TAG.sub('', inner)          # 保证只有一个 body
    inner = _BARE_AMPERSAND.sub('&amp;', inner)
    title = ''
    styles = []
    heads = _children(root, 'head')
    head = heads[0] if heads else None
    if head is not None:
        titles = _children(head, 'title')
        if titles:
            title = ''.join(titles[0].itertext()).strip()
        for link in _children(head, 'link'):
            if 'stylesheet' not in (link.get('rel') or ''):
                continue
            href = link.get('href')
            if href:
                styles.append(href)
    head_html = '<head><title>%s</title>%s</head>' % (
        html.escape(title),
        ''.join('<link rel="stylesheet" href="%s"/>' % html.escape(href)
                for href in styles))
    return ('<html xmlns="%s" xmlns:epub="%s" xmlns:xlink="%s">%s<body%s>%s</body></html>'
            % (epub.H, epub.EP, epub.XLINK, head_html, _attributes(body), inner))


def _read_images(files, package, opf, book, base=''):
    """收集图片，并保留音频、字体、视频等非图片资源。

    只按 media-type 分流：图片进 book.images，其余进 book.resources，
    写出端按原 media-type 还原成 manifest 项和文件。
    """
    for entry in _manifest_items(package):
        media_type = entry.get('media-type') or ''
        href = entry.get('href')
        if not href:
            continue
        path = _resolve(opf, href)
        if path is None or path not in files:
            continue
        safe = paths.relative_to(base, path)
        if safe is None:
            continue
        if media_type.startswith('image/'):
            book.add_image(Image(safe, files[path], media_type))
        elif media_type not in _HANDLED_MEDIA_TYPES:
            book.resources[safe] = (files[path],
                                    media_type or 'application/octet-stream')


def _read_toc(files, package, opf, base='', book=None):
    """读目录：优先用 EPUB 3 的 nav 文档，没有就退回 NCX。"""
    nav_paths = []
    for entry in _manifest_items(package):
        if 'nav' not in (entry.get('properties') or '').split():
            continue
        path = _resolve(opf, entry.get('href'))
        if path and path in files:
            nav_paths.append(path)
    for path in nav_paths:
        try:
            root = _parse(files[path])
        except (ET.ParseError, ValueError):
            continue
        navs = [element for element in root.iter() if _local(element.tag) == 'nav']
        chosen = None
        # epub:type 可以是多值，也常见老书写成 toc-legacy。
        for wanted in ('toc', 'toc-legacy'):
            chosen = next((nav for nav in navs if wanted in _type_values(nav)), None)
            if chosen is not None:
                break
        if chosen is None:
            # 老写法：没有 epub:type，但就是一个带 ol 的目录 nav。
            chosen = next((nav for nav in navs if not _type_values(nav)
                           and any(_local(e.tag) == 'ol' for e in nav.iter())), None)
        if chosen is None:
            continue
        entries = _nav_entries(chosen, path, base)
        if entries:
            # 目录链接原本是相对 nav 文档的；写出端把 nav 放回同一位置，
            # 链接才不用改基准（nav 在子目录时要能算出 '../'）。
            if book is not None:
                book.nav_href = paths.relative_to(base, path)
            return entries
    return _read_ncx(files, package, opf, base)


def _nav_entries(nav, source, base):
    """读 EPUB 3 nav 文档里的目录树。"""
    for ordered_list in _children(nav, 'ol'):
        return _nav_list(ordered_list, source, base)
    return []


def _nav_list(ordered_list, source, base):
    """把一个 <ol> 里的 <li> 读成目录项，子列表挂在 children 上。"""
    entries = []
    for item in _children(ordered_list, 'li'):
        link = next(iter(_children(item, 'a')), None)
        if link is None:
            link = next(iter(_children(item, 'span')), None)
        title = ''.join(link.itertext()).strip() if link is not None else ''
        href = None
        if link is not None and _local(link.tag) == 'a':
            href = _normalise_href(link.get('href'), source, base)
        children = []
        for sublist in _children(item, 'ol'):
            children.extend(_nav_list(sublist, source, base))
        if title or children:
            entry = TocEntry(title, href=href)
            entry.children.extend(children)
            entries.append(entry)
    return entries


def _read_ncx(files, package, opf, base=''):
    """读 NCX 目录。老书和很多中文电子书用的是这种。"""
    for entry in _manifest_items(package):
        if entry.get('media-type') != 'application/x-dtbncx+xml':
            continue
        path = _resolve(opf, entry.get('href'))
        if path is None or path not in files:
            continue
        try:
            root = xmlsafe.parse(files[path])
        except (ET.ParseError, ValueError):
            continue
        navmap = next((e for e in root.iter() if _local(e.tag) == 'navMap'), None)
        if navmap is None:
            continue
        entries = []
        for point in _children(navmap, 'navPoint'):
            entry_item = _ncx_point(point, path, base)
            if entry_item is not None:
                entries.append(entry_item)
        if entries:
            return entries
    return []


def _ncx_point(point, source, base=''):
    """把一个 NCX 目录点转成目录项，子点挂在 children 上。"""
    label = next(iter(_children(point, 'navLabel')), None)
    text_element = next(iter(_children(label, 'text')), None) if label is not None else None
    title = ''.join(text_element.itertext()).strip() if text_element is not None else ''
    content = next(iter(_children(point, 'content')), None)
    href = _normalise_href(content.get('src'), source, base) if content is not None else None
    entry = TocEntry(title, href=href)
    for child in _children(point, 'navPoint'):
        child_entry = _ncx_point(child, source, base)
        if child_entry is not None:
            entry.children.append(child_entry)
    return entry if title or entry.children else None


def _find_cover(package, opf, files=None, base='', book=None):
    """找出封面图片在书内的路径。

    常见标记有三种：manifest 的 cover-image 属性、<meta name="cover">、
    guide 里 type="cover" 的封面页。返回值必须是 book.images 里真实存在
    的键（相对内容目录），否则 MOBI/AZW3 写封面资源时会对不上。
    """
    def key_for(path):
        if not path:
            return None
        relative = paths.relative_to(base, path)
        if relative is None:
            return None
        if book is None:
            return relative
        image = book.image_for(relative)
        return image.href if image is not None else None

    item = epub.cover_item(package)
    if item is not None:
        key = key_for(_resolve(opf, item.get('href')))
        if key:
            return key

    # guide 里的封面引用通常指向封面页，那页里嵌的就是封面图。
    guide = package.find(epub.q(epub.O, 'guide'))
    if guide is None:
        guide = next((e for e in package.iter() if _local(e.tag) == 'guide'), None)
    if guide is not None:
        for reference in _children(guide, 'reference'):
            if reference.get('type') != 'cover':
                continue
            path = _resolve(opf, reference.get('href'))
            if not files or path is None or path not in files:
                continue
            for source in _page_images(files[path]):
                # 以封面页自身的路径为基准，封面页在子目录时才找得到图。
                image_path = _resolve(path, source)
                if image_path and image_path in files:
                    key = key_for(image_path)
                    if key:
                        return key
    return None


def _page_images(data):
    """找出一份 XHTML 里引用的图片路径。"""
    try:
        root = _parse(data)
    except (ET.ParseError, ValueError):
        return []
    sources = []
    for element in root.iter():
        if _local(element.tag) not in ('img', 'image'):
            continue
        source = element.get('src') or element.get(epub.q(epub.XLINK, 'href'))
        if source:
            sources.append(source)
    return sources
