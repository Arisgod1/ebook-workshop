"""DOCX 读入：把 Word 文档读成转换中间表示。

DOCX 是 ZIP 包，关键部件：

  word/document.xml        正文
  word/styles.xml          样式表，标题级别在这里定义
  word/numbering.xml       列表编号定义
  word/_rels/document.xml.rels  正文里引用的资源（图片等）
  word/media/              图片文件
  docProps/core.xml        书名作者等元数据

只用标准库的 zipfile 与 xml.etree 实现（zip 的打开经 archive 做安全校验）。
"""

import re
import xml.etree.ElementTree as ET

from . import htmltext, paths, xmlsafe
from .archive import Archive, ArchiveError
from .book import Book, Document, Image

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
DC = 'http://purl.org/dc/elements/1.1/'
DCTERMS = 'http://purl.org/dc/terms/'
CP = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
APP = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'

# Strict 命名空间。Word 2007 用 Transitional，2010 以后可以存成 Strict，
# 元素名一样，只是命名空间地址不同。
W_STRICT = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
R_STRICT = 'http://purl.oclc.org/ooxml/officeDocument/relationships'
APP_STRICT = 'http://purl.oclc.org/ooxml/officeDocument/extended-properties'

# docProps/core.xml 里的书名作者按 dc: 写，但有的生成器用 dcterms: 前缀。
DC_NAMESPACES = (DC, DCTERMS)

# 两种命名空间下的 Word 标签。
WORD_NAMESPACES = (W, W_STRICT)
RELATIONSHIP_NAMESPACES = (R, R_STRICT)

# 样式名到标题级别的对应。Word 的默认标题样式名有中英文两套。
HEADING_NAMES = {
    'heading 1': 1, 'heading 2': 2, 'heading 3': 3, 'heading 4': 4,
    'heading 5': 5, 'heading 6': 6,
    '标题 1': 1, '标题 2': 2, '标题 3': 3, '标题 4': 4, '标题 5': 5, '标题 6': 6,
}

# 英文版 DOCX 的标题样式 ID。
HEADING_IDS = {
    'heading1': 1, 'heading2': 2, 'heading3': 3, 'heading4': 4,
    'heading5': 5, 'heading6': 6,
}


def q(namespace, name):
    return '{%s}%s' % (namespace, name)


def wq(name):
    """Word 标签的查找路径。

    Transitional 和 Strict 两种命名空间都要认，
    所以返回的是一个候选路径列表。
    """
    return [q(namespace, name) for namespace in WORD_NAMESPACES]


def find(element, name):
    """按 Word 标签名找子元素，两种命名空间都试。"""
    for path in wq(name):
        found = element.find(path)
        if found is not None:
            return found
    return None

def attr(element, name):
    """按 Word 属性名取值，两种命名空间都试。"""
    for namespace in WORD_NAMESPACES:
        value = element.get(q(namespace, name))
        if value is not None:
            return value
    return None


def rel_attr(element, name):
    """按关系属性名取值。"""
    for namespace in RELATIONSHIP_NAMESPACES:
        value = element.get(q(namespace, name))
        if value is not None:
            return value
    return None


def findall(element, name):
    """按 Word 标签名找全部子元素。"""
    result = []
    for path in wq(name):
        result.extend(element.findall(path))
    return result


def _outline_level(element):
    """读 pPr 里的 outlineLvl，换成从 0 开始的级别。

    0 是一级标题，8 是九级；9 明确表示「正文」，不是标题。
    """
    if element is None:
        return None
    outline = find(element, 'outlineLvl')
    if outline is None:
        return None
    try:
        value = int(attr(outline, 'val'))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 8 else None


# 标题样式名。Word 会按界面语言本地化内置样式名，所以不能只认英文。
# 数字后缀是本地化后唯一稳定的部分。
_HEADING_NAME = re.compile(
    r'^(?:heading|标题|標題|überschrift|ueberschrift|titre|titolo|'
    r'título|titulo|overskrift|rubrik|nagłówek|nadpis|заголовок|見出し|제목)'
    r'\s*(\d+)$',
    re.IGNORECASE,
)


def _heading_from_name(name):
    """从（可能是本地化的）样式名里认出标题级别。"""
    match = _HEADING_NAME.match((name or '').strip())
    if match is None:
        return None
    return int(match.group(1))


class DocxError(ValueError):
    """DOCX 文件本身有问题。"""


def read(path):
    """读入一个 DOCX 文件，返回中间表示。"""
    try:
        archive = Archive(path)
    except ArchiveError as exc:
        raise DocxError('Not a readable DOCX (ZIP) file: %s' % exc) from exc
    with archive:
        names = set(archive.namelist())
        if 'word/document.xml' not in names:
            raise DocxError('DOCX has no word/document.xml; it is not a Word document')
        return _read_parts(archive, names)


def _read_parts(archive, names):
    book = Book()
    relationships = _read_relationships(archive, names)
    media = _read_media(archive, names, relationships)
    heading_levels = _read_heading_levels(archive, names)
    _read_core_properties(archive, names, book)
    notes = _read_notes(archive, names)

    body = _document_body(archive, names)
    if body is None:
        raise DocxError('DOCX document.xml has no body')

    toc = []
    chapters = []
    paragraphs = []
    items = []
    current = None
    index = 0
    # 每个标题都先建一个章节；级别比当前章深的标题归入它的父章，
    # 之后由 _chapter_html 递归渲染，避免把二级标题拆成独立文件。
    stack = []

    for element in _body_blocks(body):
        name = element.tag.split('}')[-1]
        if name == 'p':
            text, level = _paragraph_text(element, heading_levels)
            parts = _paragraph_parts(element, relationships, media)
            kind = _paragraph_kind(element)
            if level:
                index += 1
                current = {'level': level, 'title': text, 'index': index, 'paragraphs': [],
                           'children': []}
                chapters.append(current)
                while stack and stack[-1]['level'] >= level:
                    stack.pop()
                if stack:
                    stack[-1]['children'].append(current)
                stack.append(current)
            elif kind is not None:
                # 列表项单独记下来，渲染时归成列表。
                item = {'parts': parts, 'level': kind[1]}
                if current is not None:
                    current.setdefault('items', []).append(item)
                else:
                    items.append(item)
            elif current is not None:
                current['paragraphs'].append(parts)
            else:
                paragraphs.append(parts)
        elif name == 'tbl':
            table = _table_text(element)
            block = [('text', table)] if table else []
            if current is not None:
                current['paragraphs'].append(block)
            else:
                paragraphs.append(block)

    if not chapters and not paragraphs and not items:
        raise DocxError('DOCX contains no readable text')

    # 没有标题时，把整本书当作一章。
    if not chapters:
        chapters = [{'level': 1, 'title': book.title or '正文', 'index': 1,
                     'paragraphs': paragraphs, 'items': items, 'children': []}]
        paragraphs = []
        items = []
    elif paragraphs or items:
        # 第一个标题之前的段落不能丢：它们通常是前言、序言或版权说明。
        # 挂到第一章正文前面，读者打开书就能看到。
        first = chapters[0]
        first['paragraphs'] = paragraphs + first['paragraphs']
        first['items'] = items + first.get('items', [])
        paragraphs = []
        items = []

    # 只有正文里真正引用到的图片才收进书里。
    for chapter in chapters:
        for paragraph in chapter['paragraphs']:
            _register_images(book, paragraph)
        for item in chapter.get('items', []):
            _register_images(book, item.get('parts', []))

    top_level = [c for c in chapters if c['level'] == 1] or chapters
    for chapter in top_level:
        href = 'ch%04d.xhtml' % chapter['index']
        content = _chapter_html(chapter, book)
        book.add_document(Document(href, content=content))
        toc.append(htmltext.toc_entry(chapter, href))
    book.toc = toc
    if book.title:
        book.add_document(Document('title.xhtml', content=htmltext.title_page(book)))
    _add_notes_document(book, notes)
    return book


def _add_notes_document(book, notes):
    """把脚注与尾注写成书末的「注释」页。

    注释不塞进正文章节里：一段正文里可能引用多条注释，
    集中放一页，正文只留上标链接，读起来和纸书一致。
    """
    if not notes:
        return
    blocks = []
    for part, heading in (('word/footnotes.xml', '脚注'), ('word/endnotes.xml', '尾注')):
        entries = notes.get(part)
        if not entries:
            continue
        blocks.append('<h2>%s</h2>' % heading)
        for number in sorted(entries):
            blocks.append('<p id="note%d">[%d] %s</p>'
                          % (number, number, htmltext.escape(entries[number])))
    body = ''.join(blocks)
    book.add_document(Document('notes.xhtml',
                               content=htmltext.document('注释', 1, body)))


def _body_blocks(body):
    """按文档顺序铺平正文里的段落与表格。

    w:sdt（内容控件）里的正文挂在 sdtContent 下，直接遍历 body 会整段丢掉；
    修订容器与自定义 XML 里也可能包着段落，所以这里递归到底。
    """
    for element in body:
        name = element.tag.split('}')[-1]
        if name in ('p', 'tbl'):
            yield element
        elif name in ('sdt', 'sdtContent', 'customXml', 'ins', 'moveTo'):
            for item in _body_blocks(element):
                yield item


def _read_relationships(archive, names):
    """解析 document.xml.rels，得到 rId 到目标路径的映射。"""
    result = {}
    if 'word/_rels/document.xml.rels' not in names:
        return result
    try:
        root = xmlsafe.parse(archive.read('word/_rels/document.xml.rels'))
    except (ET.ParseError, ValueError):
        return result
    for entry in root:
        identifier = entry.get('Id')
        target = entry.get('Target')
        if identifier and target:
            result[identifier] = _resolve('word', target)
    return result


def _resolve(base, target):
    """把关系里的相对路径解析成包内路径。"""
    if target.startswith('/'):
        return target[1:]
    parts = base.split('/')
    for piece in target.split('/'):
        if piece in ('', '.'):
            continue
        if piece == '..':
            if parts:
                parts.pop()
            continue
        parts.append(piece)
    return '/'.join(parts)


def _read_heading_levels(archive, names):
    """从 styles.xml 找出每个样式对应的标题级别。"""
    levels = {}
    if 'word/styles.xml' not in names:
        return levels
    try:
        root = xmlsafe.parse(archive.read('word/styles.xml'))
    except (ET.ParseError, ValueError):
        return levels
    styles = {}
    for style in findall(root, 'style'):
        style_id = attr(style, 'styleId')
        if style_id:
            styles[style_id] = style
    for style_id, style in styles.items():
        level = _style_heading_level(style, styles, set())
        if level:
            levels[style_id] = level
    return levels


def _style_heading_level(style, styles, seen):
    """算出单个样式的标题级别，顺着 basedOn 链往上找。

    自定义样式常常只写 basedOn="Heading1"，自身没有级别；
    本地化样式名再改一个名字时，链上的英文名才是唯一线索。
    """
    style_id = attr(style, 'styleId')
    if style_id in seen:
        return None
    if style_id:
        seen.add(style_id)

    name_element = find(style, 'name')
    name = (attr(name_element, 'val') if name_element is not None else '') or ''
    level = HEADING_IDS.get((style_id or '').lower())
    if level is None:
        level = HEADING_NAMES.get(name.strip().lower())
    if level is None:
        level = _heading_from_name(name)
    if level is None:
        # outlineLvl 比名字权威：0 表示一级标题。
        outline = _outline_level(find(style, 'pPr'))
        if outline is not None:
            level = outline + 1
    if level is None:
        based_on = find(style, 'basedOn')
        parent_id = attr(based_on, 'val') if based_on is not None else None
        if parent_id:
            parent = styles.get(parent_id)
            if parent is not None:
                level = _style_heading_level(parent, styles, seen)
    return level


def _core_value(root, name):
    """按 dc: 与 dcterms: 两种命名空间取 core.xml 里的值。"""
    for namespace in DC_NAMESPACES:
        value = root.findtext(q(namespace, name))
        if value and value.strip():
            return value.strip()
    return ''


def _read_notes(archive, names):
    """读脚注与尾注：返回 {部件名: {编号: 文字}}。

    Word 把脚注放在 word/footnotes.xml、尾注放在 word/endnotes.xml，
    正文里只有 <w:footnoteReference w:id="N"/> 这样的引用标记。
    不读这两个部件，脚注的文字就整个消失了——用户看不出来，
    只觉得「这本书的注释怎么没了」。
    """
    result = {}
    for part, tag in (('word/footnotes.xml', 'footnote'), ('word/endnotes.xml', 'endnote')):
        if part not in names:
            continue
        try:
            root = xmlsafe.parse(archive.read(part))
        except (ET.ParseError, ValueError):
            continue
        notes = {}
        for element in root:
            if element.tag.split('}')[-1] != tag:
                continue
            identifier = attr(element, 'id')
            # id 为 -1 与 0 的是分隔符（续页横线之类），不是真的注释。
            if identifier is None:
                continue
            try:
                number = int(identifier)
            except (TypeError, ValueError):
                continue
            if number < 1:
                continue
            text = _collect_text(element)
            if text:
                notes[number] = text
        if notes:
            result[part] = notes
    return result


def _read_core_properties(archive, names, book):
    """读 docProps/core.xml 里的书名、作者等，以及 app.xml 里的出版信息。"""
    if 'docProps/core.xml' in names:
        try:
            root = xmlsafe.parse(archive.read('docProps/core.xml'))
        except (ET.ParseError, ValueError):
            root = None
        if root is not None:
            title = _core_value(root, 'title')
            creator = _core_value(root, 'creator')
            language = _core_value(root, 'language')
            if title:
                book.title = title
            if creator:
                book.author = creator
            if language:
                book.language = language
            description = _core_value(root, 'description')
            if description:
                book.description = description

    # app.xml 是 Word 自己写的应用信息，Company 就是出版社。
    if 'docProps/app.xml' not in names:
        return
    try:
        root = xmlsafe.parse(archive.read('docProps/app.xml'))
    except (ET.ParseError, ValueError):
        return
    for namespace in (APP, APP_STRICT):
        company = root.findtext(q(namespace, 'Company'))
        if company and company.strip():
            book.publisher = company.strip()
            break


def _document_body(archive, names):
    try:
        root = xmlsafe.parse(archive.read('word/document.xml'))
    except (ET.ParseError, ValueError) as exc:
        raise DocxError('DOCX document.xml is not valid XML: %s' % exc) from exc
    return find(root, 'body')


def _paragraph_text(element, heading_levels):
    """取出一段文字，并判断它是不是标题。

    段落自己写的 outlineLvl 最权威，直接格式会覆盖样式；
    其次才是 pStyle 指向的样式（其中已沿 basedOn 链展开）。
    """
    text = _collect_text(element)
    properties = find(element, 'pPr')
    level = _outline_level(properties)
    if level is not None:
        return text, level + 1
    style_element = None
    if properties is not None:
        style_element = find(properties, 'pStyle')
    if style_element is not None:
        style_id = attr(style_element, 'val')
        if style_id:
            level = heading_levels.get(style_id)
            if level is None:
                level = HEADING_IDS.get(style_id.lower())
            if level is None:
                # 样式表缺失或没登记时，按样式 ID 的约定再认一次。
                level = _heading_from_name(style_id)
    return text, level


def _collect_text(element):
    """收集一个元素里的全部文字，制表符与换行保留为对应字符。

    修订过的文字：插入的内容照常读出，删除的内容跳过——
    校对时看到被删掉的旧句子没有意义。
    """
    parts = []
    for node in element.iter():
        name = node.tag.split('}')[-1]
        if name == 'delText':
            # 修订删除的文字，不算正文。
            continue
        if name == 't':
            parts.append(node.text or '')
        elif name == 'tab':
            parts.append('\t')
        elif name in ('br', 'cr'):
            parts.append('\n')
        elif name == 'noBreakHyphen':
            parts.append('-')
    return ''.join(parts).strip()




def _paragraph_kind(element):
    """判断段落是普通段落还是列表项。

    返回 None 表示普通段落，返回 (是否有序, 缩进层数) 表示列表项。
    """
    properties = find(element, 'pPr')
    if properties is None:
        return None
    numbering = find(properties, 'numPr')
    if numbering is None:
        return None
    level_element = find(numbering, 'ilvl')
    try:
        level = int(attr(level_element, 'val')) if level_element is not None else 0
    except (TypeError, ValueError):
        level = 0
    return (False, level)


def _paragraph_parts(element, relationships, media):
    """按出现顺序取出一段里的文字与图片。"""
    return _walk_parts(element, relationships, media, root=True)


def _walk_parts(element, relationships, media, root=False):
    """递归收集文字与图片。

    不用 element.iter()：图片里的文本框也带 w:t，但那是图片的一部分，
    不能当正文抓出来。这里按文档顺序走，遇到图片就记一张图。
    """
    parts = []
    if element.text and not root:
        parts.append(('text', element.text))
    for child in element:
        name = child.tag.split('}')[-1]
        if name == 'delText':
            # 修订删除的文字，不算正文。
            pass
        elif name == 't':
            parts.append(('text', child.text or ''))
        elif name == 'tab':
            parts.append(('text', '\t'))
        elif name in ('br', 'cr'):
            parts.append(('text', '\n'))
        elif name == 'noBreakHyphen':
            parts.append(('text', '-'))
        elif name in ('footnoteReference', 'endnoteReference'):
            # 脚注/尾注的引用标记。文字在另一个部件里，这里只记编号。
            identifier = attr(child, 'id')
            try:
                number = int(identifier)
            except (TypeError, ValueError):
                number = None
            if number is not None and number > 0:
                parts.append(('note', number))
        elif name == 'blip':
            image = _blip_image(child, relationships, media)
            if image is not None:
                parts.append(('image', image))
        else:
            parts.extend(_walk_parts(child, relationships, media))
        if child.tail:
            parts.append(('text', child.tail))
    return parts


def _blip_image(blip, relationships, media):
    """把 drawing 里的图片引用换成真实图片；找不到就跳过。"""
    identifier = rel_attr(blip, 'embed') or rel_attr(blip, 'link')
    if not identifier:
        return None
    target = relationships.get(identifier)
    if not target:
        return None
    image = media.get(target)
    if image is None:
        return None
    return image


def _media_type(data, target):
    """按字节判断图片类型，认不出时退回扩展名。

    Word 会把图片放在 word/media/ 下，扩展名也通常正确，但两种线索
    都可能出错：复制粘贴来的图常带着错误的扩展名。字节最可靠。
    """
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data.startswith(b'BM'):
        return 'image/bmp'
    if data[:4] in (b'II*\x00', b'MM\x00*'):
        return 'image/tiff'
    if data.lstrip()[:5] == b'<?xml' and b'<svg' in data[:512]:
        return 'image/svg+xml'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    suffix = target.rsplit('.', 1)[-1].lower() if '.' in target else ''
    return {
        'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'gif': 'image/gif', 'bmp': 'image/bmp', 'tif': 'image/tiff',
        'tiff': 'image/tiff', 'svg': 'image/svg+xml', 'webp': 'image/webp',
    }.get(suffix)


def _read_media(archive, names, relationships):
    """读入文档引用到的图片。

    只加载 relationships 指向的文件：包里的东西不一定都用得上。
    不在包内的目标直接跳过，绝不生成空图片。
    """
    media = {}
    wanted = set(relationships.values())
    for target in sorted(wanted):
        if target not in names:
            continue
        data = archive.read(target)
        media_type = _media_type(data, target)
        if media_type is None:
            continue
        href = paths.relative_to('word', target)
        if href is None:
            continue
        media[target] = Image(href, data, media_type)
    return media


def _table_text(element):
    """把表格转成几行文本。"""
    lines = []
    for row in findall(element, 'tr'):
        cells = []
        for cell in findall(row, 'tc'):
            cells.append(_collect_text(cell))
        if any(cells):
            lines.append('　'.join(cells))
    return '\n'.join(lines)


def _list_html(items):
    """把列表项渲染成 ul/li。

    缩进层数不同的项分成不同的列表，层数用嵌套表示。
    """
    if not items:
        return []
    parts = []
    depth = 0
    for item in items:
        level = item.get('level', 0)
        if level > depth:
            # 层级可能一次跳好几级，每一级都要开一层，否则结尾会
            # 按 depth 关掉比实际开过的更多层，标签直接不匹配。
            parts.extend(['<ul>'] * (level - depth))
            depth = level
        elif level < depth:
            while depth > level:
                parts.append('</ul>')
                depth -= 1
        text = _parts_html(item.get('parts', []))
        if text:
            parts.append('<li>' + text + '</li>')
    while depth > 0:
        parts.append('</ul>')
        depth -= 1
    # 第一层的列表项也要有 ul 包着。
    if parts and not parts[0].startswith('<ul'):
        parts.insert(0, '<ul>')
        parts.append('</ul>')
    return parts


def _chapter_html(chapter, book):
    """生成一章的 XHTML，子章节接在父章正文之后。"""
    return htmltext.chapter_document(
        chapter,
        lambda paragraph: ''.join(_paragraphs_html([paragraph])),
        lambda section: ''.join(_list_html(section.get('items', []))))


def _register_images(book, parts):
    """把一段里引用到的图片登记到书里。"""
    for kind, value in parts:
        if kind == 'image':
            book.add_image(value)


def _paragraphs_html(paragraphs):
    """把段落列表渲染成 <p>，图片按出现位置内嵌。"""
    parts = []
    for paragraph in paragraphs:
        html = _parts_html(paragraph)
        if html:
            parts.append('<p>' + html + '</p>')
    return parts


def _parts_html(parts):
    """渲染一段里的文字与图片。"""
    out = []
    for kind, value in parts:
        if kind == 'image':
            out.append('<img src="%s" alt=""/>' % htmltext.escape_attr(value.href))
        elif kind == 'note':
            # 脚注引用：上标数字，链到书末的注释页。
            out.append('<sup class="workshop-note"><a href="notes.xhtml#note%d">[%d]</a></sup>'
                       % (value, value))
        else:
            out.append(htmltext.escape(value).replace('\n', '<br/>'))
    return ''.join(out).strip()
