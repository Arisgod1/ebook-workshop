"""EPUB container primitives: read, inspect and write the archive without changing the text."""
import json
import os
import posixpath
import re
import struct
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


def xml(data):
    if re.search(br'<!\s*(ENTITY|DOCTYPE[^>]*\[)', data, re.I):
        raise ValueError('XML entity declarations are not supported')
    return ET.fromstring(data)


def dump(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


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
    container = xml(files['META-INF/container.xml'])
    opf = container.find('.//' + q(C, 'rootfile')).get('full-path')
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
        with zipfile.ZipFile(temp, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in files.items():
                if name != 'mimetype':
                    archive.writestr(name, body, zipfile.ZIP_DEFLATED)
        os.link(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def book_title(package):
    return (package.findtext('.//' + q(D, 'title')) or '').strip()


def output_for(args, title, extension='.epub'):
    """Default destination next to the source, named after the book."""
    if args.output:
        return args.output
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')
    if not safe:
        raise ValueError('No usable book title; specify --output')
    return str(Path(args.input).parent / '整理后' / (safe + extension))


def image_info(data):
    """Read PNG or JPEG dimensions without re-encoding the image."""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'image/png', 'png', width, height
    if data.startswith(b'\xff\xd8'):
        index = 2
        while index < len(data):
            if data[index] != 255:
                index += 1
                continue
            while index < len(data) and data[index] == 255:
                index += 1
            marker = data[index]
            index += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            size = struct.unpack('>H', data[index:index + 2])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack('>HH', data[index + 3:index + 7])
                return 'image/jpeg', 'jpg', width, height
            if size < 2:
                break
            index += size
    raise ValueError('Cover must be an intact PNG or JPEG (copy original bytes; no silent conversion)')


def cover_item(package):
    """The manifest item declared as the cover image, if there is one."""
    items = package.findall('./' + q(O, 'manifest') + '/' + q(O, 'item'))
    for entry in items:
        if 'cover-image' in entry.get('properties', '').split():
            return entry
    guide = next((e.get('content') for e in package.findall('.//' + q(O, 'meta')) if e.get('name') == 'cover'), None)
    return next((entry for entry in items if entry.get('id') == guide), None)


def text_slots(element):
    """Text and tail values of an element in document order, for position-based edits."""
    slots = []
    def walk(node):
        slots.append((node, 'text'))
        for child in node:
            walk(child)
            slots.append((child, 'tail'))
    walk(element)
    return slots


def find_text_positions(element, needle):
    """Every (node, field, offset) where needle appears in one text node."""
    matches = []
    for node, field in text_slots(element):
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
