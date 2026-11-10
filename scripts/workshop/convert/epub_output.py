"""EPUB 写出：把中间表示的书写成 EPUB 3。

复用项目已有的 EPUB 容器读写（workshop.epub），这里只负责把中间表示
翻译成书内的文档、目录和清单。

Book 里的 href 一律相对内容目录（OEBPS）；目录文档的链接另外换算成
相对目录文档自身的位置，nav/NCX 放在子目录时也要算得上退。
"""

import datetime
import html
import posixpath
import re
import uuid
import xml.etree.ElementTree as ET

from .. import epub
from ..style import CSS as BASE_CSS
from . import paths

# EPUB 3 要求的固定命名空间。
XHTML = 'http://www.w3.org/1999/xhtml'
CONTENT_DIR = 'OEBPS'
NAV_HREF = 'nav.xhtml'
NCX_HREF = 'toc.ncx'
STYLE_HREF = 'style.css'


def write(book, path):
    """把中间表示的书写成 EPUB 文件。"""
    files, opf, package = _assemble(book)
    epub.write(files, path)
    return files


def _assemble(book):
    """生成 EPUB 的全部部件。"""
    files = {'mimetype': b'application/epub+zip'}
    # 原书自带 style.css 时以它为准：那是作者排的版，不能被默认样式覆盖。
    # 别的读入端没有样式表，才写默认那份。
    if STYLE_HREF in book.styles:
        files[CONTENT_DIR + '/' + STYLE_HREF] = book.styles[STYLE_HREF].encode('utf-8')
    else:
        files[CONTENT_DIR + '/' + STYLE_HREF] = BASE_CSS.encode()
    files['META-INF/container.xml'] = (
        '<?xml version="1.0"?><container xmlns="' + epub.C
        + '" version="1.0"><rootfiles><rootfile full-path="' + CONTENT_DIR + '/content.opf"'
          ' media-type="application/oebps-package+xml"/></rootfiles></container>').encode()

    identifier = book.identifier or ('urn:uuid:%s' % uuid.uuid4())
    spine = []
    manifest = []
    # 读入端记下了 nav 文档原本在内容目录里的位置；放回同一位置，
    # 目录链接的基准才不变。别的输入端没有这个属性就用根目录。
    nav_href = getattr(book, 'nav_href', None) or NAV_HREF
    if (not nav_href.endswith('.xhtml') or nav_href.startswith('/')
            or paths.safe_path(nav_href) is None):
        nav_href = NAV_HREF
    # 标题页是显式的：读入端用 Document.properties 标记 'titlepage'，
    # 别的输入端仍可能用固定的 'title.xhtml' 名字。正文里提到
    # "titlepage" 这个词绝不能被当成标题页覆盖掉。
    title_page = next((document for document in book.documents
                       if 'titlepage' in document.properties
                       or document.href == 'title.xhtml'), None)

    def add_document(ident, href, media_type, content, properties=None):
        files[CONTENT_DIR + '/' + href] = _xhtml_document(
            content, href, book.language or 'zh-CN').encode('utf-8')
        item = {'id': ident, 'href': href, 'media-type': media_type}
        if properties:
            item['properties'] = properties
        manifest.append(item)
        return ident

    # 先决定每份文档最终写在哪：标题页用 'title.xhtml'，其余沿用读入时
    # 的名字，只有扩展名不合规或撞上保留名才改名。重名会让 manifest 和
    # spine 里各出现两条，读端直接报重复条目。
    used = {STYLE_HREF, NAV_HREF, nav_href, NCX_HREF, 'content.opf'}
    renames = {}
    hrefs = []
    for index, document in enumerate(book.documents, 1):
        original = document.href
        if document is title_page:
            href = 'title.xhtml'
        else:
            href = original
            used.add(original)
        if (not href.lower().endswith(('.xhtml', '.html', '.htm'))
                or href in {STYLE_HREF, NCX_HREF, NAV_HREF, nav_href, 'content.opf'}
                or (document is title_page and href != original and href in used)):
            href = _unused_href(posixpath.join(posixpath.dirname(original) or '',
                                               'c%04d.xhtml' % index), used)
        if document is title_page and href in used and href != original:
            href = _unused_href('title.xhtml', used)
        hrefs.append(href)
        used.add(href)
        if href != original:
            renames[original] = (href, original)

    # 正文文档。改名要同步到目录 href 和正文里的相对链接。
    for index, (document, href) in enumerate(zip(book.documents, hrefs), 1):
        content = document.content
        regenerated = False
        if document is title_page and book.title:
            content = _title_page(book)
            regenerated = True
        if document.href in renames and not regenerated:
            content = _rewrite_links(content, document.href, href)
        ident = _unique_ident('d%04d' % index, manifest)
        add_document(ident, href, 'application/xhtml+xml', content)
        spine.append(ident)
    if title_page is None and book.title:
        href = _unused_href('title.xhtml', used)
        used.add(href)
        ident = _unique_ident('title', manifest)
        add_document(ident, href, 'application/xhtml+xml', _title_page(book))
        spine.insert(0, ident)
    if renames:
        _apply_renames(book.toc, dict((old, new) for old, (new, _) in renames.items()))

    # 图片。沿用读入时的包内路径，文档里的引用才不用改。
    for image in book.images.values():
        href = image.href
        if href.startswith(CONTENT_DIR + '/'):
            href = href[len(CONTENT_DIR) + 1:]
        files[CONTENT_DIR + '/' + href] = image.data
        ident = _unique_ident('img%04d' % (len(manifest) + 1), manifest)
        item = {'id': ident, 'href': href, 'media-type': image.media_type}
        if image.href == book.cover:
            item['properties'] = 'cover-image'
        manifest.append(item)

    # 音频、字体、视频等资源按原 media-type 还原。
    for href, (data, media_type) in getattr(book, 'resources', {}).items():
        files[CONTENT_DIR + '/' + href] = data
        ident = _unique_ident('res%04d' % (len(manifest) + 1), manifest)
        manifest.append({'id': ident, 'href': href, 'media-type': media_type})

    # 样式表。原样带走，正文的排版才保得住。
    # style.css 已在开头写好并登记，这里只处理别的样式表。
    for href, text in book.styles.items():
        if href in {STYLE_HREF, NCX_HREF, NAV_HREF, nav_href, 'content.opf'} or href in hrefs:
            continue
        files[CONTENT_DIR + '/' + href] = text.encode('utf-8')
        ident = _unique_ident('css%04d' % (len(manifest) + 1), manifest)
        manifest.append({'id': ident, 'href': href, 'media-type': 'text/css'})

    # 目录文档。
    add_document('nav', nav_href, 'application/xhtml+xml',
                 _nav(book, nav_href), properties='nav')

    # NCX，老设备需要。
    files[CONTENT_DIR + '/' + NCX_HREF] = _ncx(book, identifier)

    package = _package(book, identifier, manifest, spine)
    files[CONTENT_DIR + '/content.opf'] = epub.dump(package)
    return files, CONTENT_DIR + '/content.opf', package


def _unused_href(href, used):
    """给撞名的文件找一个没被占用的名字。"""
    stem, dot, extension = href.rpartition('.')
    if not dot:
        stem, extension = href, '.xhtml'
    else:
        extension = '.' + extension
    candidate = href
    counter = 1
    while candidate in used or paths.safe_path(candidate) is None:
        candidate = '%s_%d%s' % (stem, counter, extension)
        counter += 1
    return candidate


def _unique_ident(ident, manifest):
    """清单里的 id 必须唯一；不能只按序号猜。"""
    taken = {item['id'] for item in manifest}
    if ident not in taken:
        return ident
    counter = 1
    while '%s_%d' % (ident, counter) in taken:
        counter += 1
    return '%s_%d' % (ident, counter)


def _xhtml_document(content, href, language='zh-CN'):
    """把中间表示的 content 变成一份完整、合法的 XHTML 文档。

    读入端产出的是完整 XHTML，别的输入端可能给片段或省略了外壳；
    这里统一补齐 XML 声明、命名空间和单个 body，让 epub.xml 能解析、
    让下游再读回来时找得到正文。
    """
    text = content.strip()
    if text.startswith('<?xml'):
        text = text.split('?>', 1)[1].lstrip()
    if text.lower().startswith('<!doctype'):
        text = text.split('>', 1)[1].lstrip()
    body = _body_content(text)
    head = _head_content(text)
    prefix = '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
    attributes = ''
    if text.lower().startswith('<html'):
        opening = text.find('>')
        if opening != -1:
            attributes = text[len('<html'):opening].rstrip('/')
    namespaces = ''
    # 属性可能用单引号也可能用双引号：两种形式都算已声明，
    # 否则会再追加一份，生成重复属性，解析直接失败。
    declared = attributes.replace("'", '"')
    if 'xmlns="%s"' % XHTML not in declared:
        namespaces += ' xmlns="%s"' % XHTML
    if 'xmlns:epub=' not in declared:
        namespaces += ' xmlns:epub="%s"' % epub.EP
    if 'lang=' not in declared:
        namespaces += ' lang="%s" xml:lang="%s"' % (
            html.escape(language), html.escape(language))
    if not head:
        head = '<head><title></title><meta charset="utf-8"/></head>'
    # 样式表链接：文档自己没引才补。写出来的书带了 style.css，
    # 正文文档却不引用它，缩进行距这些规则就整份不生效——
    # 之前只在「文档连 head 都没有」时才补，正好漏掉了自带 head 的正文。
    if 'stylesheet' not in head:
        href = html.escape(_relative_to(href, STYLE_HREF))
        if '</head>' in head:
            head = head.replace('</head>', '<link rel="stylesheet" href="%s"/></head>' % href, 1)
        else:
            head += '<link rel="stylesheet" href="%s"/>' % href
    # body 自己的属性要留着：目录可能指向 <body id="...">，
    # 丢掉了就成了悬空锚点。片段没有 body 标签时用空的。
    body_attributes = ''
    lowered = text.lower()
    body_start = lowered.find('<body')
    if body_start != -1:
        body_open = text.find('>', body_start)
        if body_open != -1:
            body_attributes = text[body_start + len('<body'):body_open].rstrip('/')
    return '%s<html%s%s>%s<body%s>%s</body></html>' % (
        prefix, attributes, namespaces, head, body_attributes, body)


def _head_content(text):
    lower = text.lower()
    start = lower.find('<head')
    end = lower.find('</head>')
    if start != -1 and end > start:
        return text[start:end + len('</head>')]
    return '<head><title></title></head>'


def _body_content(text):
    """取出 body 里的内容；片段则原样返回。"""
    lower = text.lower()
    start = lower.find('<body')
    if start == -1:
        return text
    opening = text.find('>', start)
    if opening == -1:
        return ''
    end = lower.find('</body>', opening)
    return text[opening + 1:end if end != -1 else len(text)]


def _rewrite_links(content, old_href, new_href):
    """文档改名后，正文里指向别的资源的相对链接要跟着换基准。"""
    if old_href is None:
        return content
    old_dir = posixpath.dirname(old_href)
    new_dir = posixpath.dirname(new_href)
    if old_dir == new_dir:
        return content

    def replace(match):
        value = match.group(2)
        if not value or '://' in value or value.startswith(('#', '/', 'mailto:', 'data:')):
            return match.group(0)
        path, separator, fragment = value.partition('#')
        resolved = posixpath.normpath(posixpath.join(old_dir, path))
        if resolved.startswith('../'):
            return match.group(0)
        relative = posixpath.relpath(resolved, new_dir or '.') if resolved else path
        return '%s"%s%s"' % (match.group(1), relative,
                             separator + fragment if separator else '')

    return re.sub(r'((?:src|href)\s*=\s*)"([^"]*)"', replace, content)


def _apply_renames(entries, renames):
    """文档改名后，把目录里指向它的链接也改过去。"""
    for entry in entries:
        if entry.href:
            path, separator, fragment = entry.href.partition('#')
            if path in renames:
                entry.href = renames[path] + (separator + fragment if separator else '')
        _apply_renames(entry.children, renames)


def _page(title, body, language='zh-CN', base='index.xhtml'):
    return ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="' + XHTML
            + '" xmlns:epub="' + epub.EP + '" lang="' + html.escape(language)
            + '" xml:lang="' + html.escape(language) + '"><head><title>'
            + html.escape(title)
            + '</title><meta charset="utf-8"/><link rel="stylesheet" href="'
            + html.escape(_relative_to(base, STYLE_HREF)) + '"/>'
              '</head><body>' + body + '</body></html>')


def _title_page(book):
    """生成标题页。"""
    return _page(book.title, '<section epub:type="titlepage"><h1>'
                + html.escape(book.title) + '</h1><p>' + html.escape(book.author)
                + '</p></section>', book.language or 'zh-CN', 'title.xhtml')


def _nav(book, nav_href=NAV_HREF):
    """生成 EPUB 3 的目录文档。"""
    items = [_nav_entry(entry, nav_href) for entry in book.toc]
    body = ('<nav epub:type="toc" id="toc"><h1>目录</h1><ol>'
            + ''.join(items) + '</ol></nav>')
    return _page('目录', body, book.language or 'zh-CN', nav_href)


def _nav_entry(entry, nav_href=NAV_HREF):
    """目录树的一个节点，缩进用子列表表示。"""
    children = ''.join(_nav_entry(child, nav_href) for child in entry.children)
    # 没有链接的分组标题写成 span，不生成指不到文件的 <a>。
    if entry.href:
        href = _relative_to(nav_href, entry.href)
        label = '<a href="%s">%s</a>' % (html.escape(href), html.escape(entry.title))
    else:
        label = '<span>%s</span>' % html.escape(entry.title)
    item = '<li>' + label
    if children:
        item += '<ol>' + children + '</ol>'
    return item + '</li>'


def _relative_to(base, target):
    """算出从 base 所在目录到 target 的相对路径。

    target 是相对内容目录的路径，base 也是；两者都可能在子目录里，
    因此必须用 posixpath.relpath 产生 '../'，不能只往下拼。
    """
    if not target:
        return ''
    if '://' in target or target.startswith(('mailto:', 'tel:', 'data:')):
        return target
    path, separator, fragment = target.partition('#')
    if path.startswith('/'):
        path = path.lstrip('/')
    base_dir = posixpath.dirname(base)
    relative = posixpath.relpath(posixpath.normpath(path) if path else '.',
                                 base_dir or '.') if path else ''
    if not path:
        # 纯片段链接指向当前目录文档自身。
        relative = posixpath.basename(base)
    if relative == '.':
        relative = posixpath.basename(base)
    return relative + (separator + fragment if separator else '')


def _ncx(book, identifier):
    """生成 NCX 目录，老设备用。

    dtb:uid 必须与 OPF 的 dc:identifier 一致，否则 EPUBCheck 报 NCX-001。
    """
    root = ET.Element(epub.q(epub.N, 'ncx'), {'version': '2005-1'})
    head = ET.SubElement(root, epub.q(epub.N, 'head'))
    ET.SubElement(head, epub.q(epub.N, 'meta'), {'name': 'dtb:uid',
                                                 'content': identifier})
    title = ET.SubElement(root, epub.q(epub.N, 'docTitle'))
    ET.SubElement(title, epub.q(epub.N, 'text')).text = book.title
    navmap = ET.SubElement(root, epub.q(epub.N, 'navMap'))
    _ncx_entries(navmap, book.toc)
    return epub.dump(root)


def _ncx_entries(parent, entries, counter=None):
    """递归写出 NCX 目录项。"""
    if counter is None:
        counter = [0]
    for entry in entries:
        counter[0] += 1
        point = ET.SubElement(parent, epub.q(epub.N, 'navPoint'),
                              {'id': 'np%04d' % counter[0], 'playOrder': str(counter[0])})
        label = ET.SubElement(point, epub.q(epub.N, 'navLabel'))
        ET.SubElement(label, epub.q(epub.N, 'text')).text = entry.title
        # NCX 的 content/@src 不能带锚点：层级靠 playOrder、锚点另由 nav 保存。
        src = _relative_to(NCX_HREF, (entry.href or '').partition('#')[0])
        ET.SubElement(point, epub.q(epub.N, 'content'), {'src': src})
        if entry.children:
            _ncx_entries(point, entry.children, counter)


def _package(book, identifier, manifest, spine):
    """生成 OPF 包文档。"""
    package = ET.Element(epub.q(epub.O, 'package'),
                         {'version': '3.0', 'unique-identifier': 'book-id'})
    metadata = ET.SubElement(package, epub.q(epub.O, 'metadata'))
    for tag, value in (('identifier', identifier), ('title', book.title),
                       ('creator', book.author), ('language', book.language)):
        if not value:
            continue
        element = ET.SubElement(metadata, epub.q(epub.D, tag))
        element.text = value
        if tag == 'identifier':
            element.set('id', 'book-id')
    extra = dict((name, list(values)) for name, values in book.metadata.items())
    if book.publisher:
        extra['publisher'] = [book.publisher]
    if book.description:
        extra['description'] = [book.description]
    for name, values in extra.items():
        if name in ('title', 'creator', 'language', 'identifier'):
            continue
        for value in values:
            element = ET.SubElement(metadata, epub.q(epub.D, name))
            element.text = value
    # EPUB 3 要求 dcterms:modified，且必须是写出时刻。
    ET.SubElement(metadata, epub.q(epub.O, 'meta'), {
        'property': 'dcterms:modified'}).text = datetime.datetime.now(
            datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    cover_ident = next((item['id'] for item in manifest
                        if 'cover-image' in (item.get('properties') or '').split()), None)
    if cover_ident:
        ET.SubElement(metadata, epub.q(epub.O, 'meta'),
                      {'name': 'cover', 'content': cover_ident})

    manifest_element = ET.SubElement(package, epub.q(epub.O, 'manifest'))
    for item in manifest:
        attributes = {'id': item['id'], 'href': item['href'],
                      'media-type': item['media-type']}
        if item.get('properties'):
            attributes['properties'] = item['properties']
        ET.SubElement(manifest_element, epub.q(epub.O, 'item'), attributes)
    ET.SubElement(manifest_element, epub.q(epub.O, 'item'),
                  {'id': 'ncx', 'href': NCX_HREF,
                   'media-type': 'application/x-dtbncx+xml'})
    ET.SubElement(manifest_element, epub.q(epub.O, 'item'),
                  {'id': 'css', 'href': STYLE_HREF, 'media-type': 'text/css'})

    spine_element = ET.SubElement(package, epub.q(epub.O, 'spine'), {'toc': 'ncx'})
    for ident in spine:
        ET.SubElement(spine_element, epub.q(epub.O, 'itemref'), {'idref': ident})
    return package


