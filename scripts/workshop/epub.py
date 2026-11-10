"""EPUB container primitives: read, inspect and write the archive without changing the text."""
import html.entities
import json
import os
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

H = 'http://www.w3.org/1999/xhtml'
O = 'http://www.idpf.org/2007/opf'
D = 'http://purl.org/dc/elements/1.1/'
N = 'http://www.daisy.org/z3986/2005/ncx/'
EP = 'http://www.idpf.org/2007/ops'
C = 'urn:oasis:names:tc:opendocument:xmlns:container'
XMLNS = 'http://www.w3.org/XML/1998/namespace'
XLINK = 'http://www.w3.org/1999/xlink'
SVG = 'http://www.w3.org/2000/svg'

for prefix, uri in [('', H), ('opf', O), ('dc', D), ('epub', EP), ('ncx', N)]:
    ET.register_namespace(prefix, uri)


def q(ns, name):
    return '{' + ns + '}' + name


def default_file_mode():
    """新建文件应有的权限：0666 扣掉 umask。"""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


# XHTML 里常见的 HTML 实体，XML 解析器不认（它们只在 HTML 里定义）。
# 有些书直接写 &nbsp; 却没有声明 DTD，转换管线会先展开它们再解析。
_HTML_ENTITY = re.compile(rb'&([a-zA-Z][a-zA-Z0-9]{1,31});')

# XML 自带的五个实体：展开它们会破坏 XML，必须原样留着。
_XML_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}


def _expand_html_entities(data):
    """把 HTML 实体换成字符，让 XML 解析器能读。

    只展开 XML 不认识的那些：&amp; 展成裸 & 会让整份文档变非法。
    """
    def replace(match):
        name = match.group(1).decode('ascii')
        if name in _XML_ENTITIES:
            return match.group(0)
        char = html.entities.html5.get(name + ';')
        if char is None:
            return match.group(0)
        # 换出来的字符本身可能是 & 或 <，要重新转义。
        return char.replace('&', '&amp;').replace('<', '&lt;').encode('utf-8')
    return _HTML_ENTITY.sub(replace, data)


def xml(data):
    if re.search(br'<!\s*(ENTITY|DOCTYPE[^>]*\[)', data, re.I):
        raise ValueError('XML entity declarations are not supported')
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        # 未声明的 HTML 实体：展开后再试一次。
        expanded = _expand_html_entities(data)
        if expanded == data:
            raise
        return ET.fromstring(expanded)


def dump(root):
    """把元素树写成 XHTML 字节。

    ElementTree 的序列化是递归的，嵌套太深会抛 RecursionError，
    那一屏堆栈对用户毫无意义。换成一句能看懂的话。
    """
    try:
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)
    except RecursionError as error:
        raise ValueError('文档嵌套层级过深，无法写出；'
                         '请检查源文件是否有异常结构') from error


def dump_text(element):
    """把一个元素序列化成字符串（不带 XML 声明）。

    同样要挡住深嵌套：读入端逐个文档序列化时，异常源在这里。
    """
    try:
        return ET.tostring(element, encoding='unicode')
    except RecursionError as error:
        raise ValueError('文档嵌套层级过深，无法处理；'
                         '请检查源文件是否有异常结构') from error


def report(value):
    """Print a machine-readable result for the caller."""
    print(json.dumps(value, ensure_ascii=False, indent=2))


def load(path):
    """Read an EPUB into {path: bytes} plus its package document."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP paths')
        for name in names:
            if name.startswith('/') or '..' in name.split('/') or '\\' in name:
                raise ValueError('Unsafe ZIP path: ' + name)
        if sum(item.file_size for item in archive.infolist()) > 1024 ** 3:
            raise ValueError('Archive expands beyond 1 GiB; inspect before processing')
        files = {name: archive.read(name) for name in names if not name.endswith('/')}
    if files.get('mimetype') != b'application/epub+zip':
        raise ValueError('Missing/incorrect EPUB mimetype')
    container_data = files.get('META-INF/container.xml')
    if container_data is None:
        raise ValueError('Missing META-INF/container.xml; this is not a valid EPUB')
    rootfile = xml(container_data).find('.//' + q(C, 'rootfile'))
    if rootfile is None or not rootfile.get('full-path'):
        raise ValueError('container.xml names no package document')
    opf = rootfile.get('full-path')
    if opf not in files:
        raise ValueError('Package document is missing from the archive: ' + opf)
    package = xml(files[opf])
    if 'META-INF/encryption.xml' in files:
        raise ValueError('Encrypted resources present; inspect separately, do not remove encryption')
    return files, opf, package


def resolve(base, href):
    """Resolve a reference inside the archive to (path, fragment)."""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return None, None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path))) if parts.path else base
    if path.startswith('../') or path.startswith('/'):
        raise ValueError('Reference leaves EPUB root: ' + href)
    return path, unquote(parts.fragment)


def inventory(files, opf, package):
    """Return manifest items by id and the reading order as (path, item) pairs."""
    items = {e.get('id'): e for e in package.findall('./' + q(O, 'manifest') + '/' + q(O, 'item'))}
    spine = []
    for entry in package.findall('./' + q(O, 'spine') + '/' + q(O, 'itemref')):
        if entry.get('idref') not in items:
            raise ValueError('Spine item absent from manifest: ' + str(entry.get('idref')))
        item = items[entry.get('idref')]
        path, _ = resolve(opf, item.get('href'))
        spine.append((path, item))
    return items, spine


def visible(root):
    """Text a reader would see, ignoring markup."""
    return ''.join(root.itertext())


def reading_documents(spine):
    """Content documents in reading order, excluding navigation and front matter."""
    documents = []
    for path, item in spine:
        if item.get('media-type') != 'application/xhtml+xml':
            continue
        if 'nav' in item.get('properties', '').split():
            continue
        documents.append((path, item))
    return documents


def validate_files(files):
    """Check that resources, ids and anchors inside the archive all resolve."""
    roots = {}
    for name, data in files.items():
        if name.endswith(('.xml', '.opf', '.ncx', '.xhtml', '.html')):
            roots[name] = xml(data)
    rootfile = roots['META-INF/container.xml'].find('.//' + q(C, 'rootfile')).get('full-path')
    package = roots[rootfile]
    items, spine = inventory(files, rootfile, package)
    for item in items.values():
        target, _ = resolve(rootfile, item.get('href'))
        if target not in files:
            raise ValueError('Missing manifest resource: ' + str(target))
    for name, root in roots.items():
        ids = [e.get('id') for e in root.iter() if e.get('id')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate IDs in ' + name)
        for element in root.iter():
            for attr, val in element.attrib.items():
                if attr.split('}')[-1] not in ('href', 'src'):
                    continue
                target, fragment = resolve(name, val)
                if target is None:
                    continue
                if target not in files:
                    raise ValueError('Broken resource reference: ' + name + ' -> ' + val)
                if fragment:
                    if target not in roots:
                        continue
                    if not any(e.get('id') == fragment or e.get(q(XMLNS, 'id')) == fragment for e in roots[target].iter()):
                        raise ValueError('Broken anchor: ' + name + ' -> ' + val)
    return {'markup_files': len(roots), 'manifest_items': len(items), 'spine_items': len(spine)}


def write(files, output):
    """Validate and save an EPUB, refusing to overwrite an existing file."""
    output = Path(output)
    if output.exists():
        raise ValueError('Output exists; choose a new path (source files are never overwritten)')
    output.parent.mkdir(parents=True, exist_ok=True)
    validate_files(files)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.epub', delete=False) as handle:
        temp = Path(handle.name)
    try:
        # 临时文件默认 0600，改成普通新建文件该有的权限，
        # 免得产出的电子书别人读不了。Windows 上没有 POSIX 权限位，跳过。
        if os.name != 'nt':
            os.chmod(str(temp), default_file_mode())
        with zipfile.ZipFile(temp, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in files.items():
                if name != 'mimetype':
                    archive.writestr(name, body, compress_type=zipfile.ZIP_DEFLATED)
        _move_into_place(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def _move_into_place(temp, output):
    """把临时文件放到目标位置，且不覆盖已存在的文件。

    POSIX 上用硬链接：它不能覆盖已有目标，所以「不覆盖」的契约天然成立。
    Windows 上硬链接要额外权限、也不总是可用，改用改名——先确认目标不存在，
    存在就报错，避免默默覆盖别人的文件。
    """
    if os.name != 'nt':
        os.link(str(temp), str(output))
        return
    if output.exists():
        raise ValueError('Output exists; choose a new path (source files are never overwritten)')
    os.replace(str(temp), str(output))


def book_title(package):
    return (package.findtext('.//' + q(D, 'title')) or '').strip()


def cover_item(package):
    """The manifest item declared as the cover image, if there is one.

    三种常见标记都要认：manifest 的 cover-image 属性、
    <meta name="cover">，以及 guide 里 type="cover" 的引用。
    最后一种在 Sigil / 老书里很常见，它指向封面页而不是图片本身，
    这里返回封面页的 manifest 项，由调用方顺着页里的 img 找图。
    """
    items = package.findall('./' + q(O, 'manifest') + '/' + q(O, 'item'))
    for entry in items:
        if 'cover-image' in entry.get('properties', '').split():
            return entry
    meta = next((e.get('content') for e in package.findall('.//' + q(O, 'meta'))
                 if e.get('name') == 'cover'), None)
    if meta:
        found = next((entry for entry in items if entry.get('id') == meta), None)
        if found is not None:
            return found
    guide = package.find(q(O, 'guide'))
    if guide is not None:
        for reference in guide.findall(q(O, 'reference')):
            if (reference.get('type') or '').lower() != 'cover':
                continue
            href = (reference.get('href') or '').partition('#')[0]
            if not href:
                continue
            found = next((entry for entry in items if entry.get('href') == href), None)
            if found is not None:
                return found
    return None


def text_slots(element, skip=()):
    """Text and tail values of an element in document order, for position-based edits.

    skip 里的标签整棵子树都不遍历：脚本与样式不是给人读的内容，
    在里面做替换既没意义，也可能改坏页面。
    """
    slots = []
    def walk(node):
        if node.tag in skip:
            return
        slots.append((node, 'text'))
        for child in node:
            walk(child)
            slots.append((child, 'tail'))
    walk(element)
    return slots


def find_text_positions(element, needle, skip=()):
    """Every (node, field, offset) where needle appears in one text node."""
    matches = []
    for node, field in text_slots(element, skip):
        value = getattr(node, field) or ''
        start = value.find(needle)
        while start != -1:
            matches.append((node, field, start))
            start = value.find(needle, start + 1)
    return matches


def locate_range(element, start, end):
    """Find the single text node holding characters [start, end) of an element.

    Returns (node, field, local_start, local_end), or None when the range
    spans more than one node.
    """
    offset = 0
    for node, field in text_slots(element):
        value = getattr(node, field) or ''
        if offset <= start < end <= offset + len(value):
            return node, field, start - offset, end - offset
        offset += len(value)
    return None


def apply_text_edits(changes):
    """Apply node-local edits right to left, refusing overlapping ranges."""
    for (node, field), replacements in changes.items():
        value = getattr(node, field)
        limit = len(value)
        for start, end, replacement in sorted(replacements, reverse=True):
            if end > limit:
                raise ValueError('Corrections overlap')
            value = value[:start] + replacement + value[end:]
            limit = start
        setattr(node, field, value)
