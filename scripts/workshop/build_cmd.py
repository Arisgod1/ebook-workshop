"""转换：把 TXT 或其他格式整理成 EPUB，并填写书名作者。"""
import datetime
import html
import json
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import epub, render, text


def build(args):
    """Turn a TXT file into an EPUB, keeping front matter, notes and afterword in place."""
    content, encoding = text.decode(args.input, args.encoding)
    prefix, chapters = text.split_text(content, args.pattern)
    if not chapters:
        raise ValueError('No chapter headings found; supply a matching --pattern with named number/title groups')
    fixes = json.loads(Path(args.headings_json).read_text()) if args.headings_json else {}
    if not isinstance(fixes, dict) or any(not str(k).isdigit() or not 1 <= int(k) <= len(chapters) or not isinstance(v, str)
                                         for k, v in fixes.items()):
        raise ValueError('Heading overrides must map valid 1-based chapter positions to exact verified titles')
    files = {'mimetype': b'application/epub+zip', 'OEBPS/style.css': render.CSS.encode()}
    files['META-INF/container.xml'] = ('<?xml version="1.0"?><container xmlns="' + epub.C
                                       + '" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
                                         ' media-type="application/oebps-package+xml"/></rootfiles></container>').encode()
    files['OEBPS/title.xhtml'] = render.page(args.title, '<section class="title" epub:type="titlepage"><h1>'
                                       + html.escape(args.title) + '</h1><p>' + html.escape(args.author) + '</p></section>')
    spine, nav, originals = ['title'], [], {}
    if prefix:
        files['OEBPS/front.xhtml'] = render.page('原文件前置内容', ''.join('<p>' + html.escape(p) + '</p>' for p in prefix))
        spine.append('front')
        nav.append(('front', '原文件前置内容'))
    for index, chapter in enumerate(chapters, 1):
        ident = 'c%04d' % index
        heading = fixes.get(str(index), chapter['heading'])
        files['OEBPS/' + ident + '.xhtml'] = render.page(
            heading, '<section epub:type="chapter"><h1>' + html.escape(heading) + '</h1>'
            + ''.join('<p>' + html.escape(p) + '</p>' for p in chapter['paragraphs']) + '</section>')
        originals[ident] = chapter['paragraphs']
        spine.append(ident)
        nav.append((ident, heading if args.full_toc else '第%d节' % index))
    navbody = ('<nav epub:type="toc" id="toc"><h1>目录</h1><ol>'
               + ''.join('<li><a href="' + ident + '.xhtml">' + html.escape(label) + '</a></li>' for ident, label in nav)
               + '</ol></nav>')
    files['OEBPS/nav.xhtml'] = render.page('目录', navbody)
    uid = 'urn:uuid:' + str(uuid.uuid4())
    ncx = ET.Element(epub.q(epub.N, 'ncx'), {'version': '2005-1'})
    head = ET.SubElement(ncx, epub.q(epub.N, 'head'))
    ET.SubElement(head, epub.q(epub.N, 'meta'), {'name': 'dtb:uid', 'content': uid})
    ET.SubElement(ET.SubElement(ncx, epub.q(epub.N, 'docTitle')), epub.q(epub.N, 'text')).text = args.title
    navmap = ET.SubElement(ncx, epub.q(epub.N, 'navMap'))
    for index, (ident, label) in enumerate(nav, 1):
        point = ET.SubElement(navmap, epub.q(epub.N, 'navPoint'), {'id': 'n%d' % index})
        ET.SubElement(ET.SubElement(point, epub.q(epub.N, 'navLabel')), epub.q(epub.N, 'text')).text = label
        ET.SubElement(point, epub.q(epub.N, 'content'), {'src': ident + '.xhtml'})
    files['OEBPS/toc.ncx'] = epub.dump(ncx)
    package = ET.Element(epub.q(epub.O, 'package'), {'version': '3.0', 'unique-identifier': 'book-id'})
    metadata = ET.SubElement(package, epub.q(epub.O, 'metadata'))
    for tag, value in [('identifier', uid), ('title', args.title), ('creator', args.author), ('language', args.language)]:
        element = ET.SubElement(metadata, epub.q(epub.D, tag))
        element.text = value
        if tag == 'identifier':
            element.set('id', 'book-id')
    ET.SubElement(metadata, epub.q(epub.O, 'meta'), {'property': 'dcterms:modified'}).text = \
        datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    manifest = ET.SubElement(package, epub.q(epub.O, 'manifest'))
    for name in files:
        if not name.startswith('OEBPS/'):
            continue
        short = name.split('/', 1)[1]
        ident = short.rsplit('.', 1)[0]
        mime = ('application/xhtml+xml' if short.endswith('.xhtml')
                else 'text/css' if short.endswith('.css')
                else 'application/x-dtbncx+xml')
        element = ET.SubElement(manifest, epub.q(epub.O, 'item'), {'id': ident, 'href': short, 'media-type': mime})
        if ident == 'nav':
            element.set('properties', 'nav')
    spine_element = ET.SubElement(package, epub.q(epub.O, 'spine'), {'toc': 'toc'})
    for ident in spine:
        ET.SubElement(spine_element, epub.q(epub.O, 'itemref'), {'idref': ident})
    files['OEBPS/content.opf'] = epub.dump(package)
    for ident, paragraphs in originals.items():
        root = epub.xml(files['OEBPS/' + ident + '.xhtml'])
        assert [''.join(e.itertext()) for e in root.iter(epub.q(epub.H, 'p'))] == paragraphs
    if args.cover:
        files = render.replace_cover(files, 'OEBPS/content.opf', package, Path(args.cover).read_bytes())
    args.output = epub.output_for(args, args.title)
    epub.write(files, args.output)
    epub.report({'output': args.output, 'encoding': encoding, 'chapters': len(chapters),
                 'preserved_frontmatter': bool(prefix), 'heading_overrides': len(fixes),
                 'paragraph_roundtrip': 'passed',
                 'filename_note': 'Use the standard book title as filename; keep workflow labels in reports'})


def convert(args):
    """Convert other formats with Calibre, keeping the title and author."""
    suffix = Path(args.input).suffix.lower()
    if suffix == '.doc':
        raise ValueError('Save the legacy Word .doc as .docx in Word or LibreOffice before converting')
    if args.format == 'epub' and suffix == '.epub':
        raise ValueError('Input is already EPUB; use inspect, correct, layout or cover')
    converter = shutil.which('ebook-convert')
    mac = Path('/Applications/calibre.app/Contents/MacOS/ebook-convert')
    if not converter and mac.is_file():
        converter = str(mac)
    if not converter:
        raise ValueError('Install Calibre and make ebook-convert available to convert this format')
    title = args.title
    if args.format == 'azw3' and suffix == '.epub':
        _, _, package = epub.load(args.input)
        title = title or epub.book_title(package)
    with tempfile.TemporaryDirectory() as temp:
        generated = Path(temp) / ('book.' + args.format)
        command = [converter, str(Path(args.input).resolve()), str(generated)]
        if args.format == 'epub':
            command.extend(['--epub-version', '3'])
        for flag, value in (('--title', title), ('--authors', args.author)):
            if value:
                command.extend([flag, value])
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode or not generated.is_file() or not generated.stat().st_size:
            raise ValueError('Calibre conversion failed; check whether the input format and file are supported')
        if args.format == 'epub':
            files, opf, package = epub.load(generated)
            if suffix == '.pdf':
                _, spine = epub.inventory(files, opf, package)
                bodies = (epub.xml(files[name]).find(epub.q(epub.H, 'body')) for name, item in spine
                          if item.get('media-type') == 'application/xhtml+xml'
                          and 'nav' not in item.get('properties', '').split())
                if not any(any(ch.isalnum() for ch in epub.visible(body)) for body in bodies if body is not None):
                    raise ValueError('PDF conversion produced no readable text; run OCR on scanned pages before converting')
            title = epub.book_title(package)
            args.output = epub.output_for(args, title or Path(args.input).stem)
            epub.write(files, args.output)
        else:
            output = Path(epub.output_for(args, title or Path(args.input).stem, '.azw3'))
            if output.resolve() == Path(args.input).resolve():
                raise ValueError('Output would replace the source')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('xb') as destination, generated.open('rb') as source:
                shutil.copyfileobj(source, destination)
            args.output = str(output)
    epub.report({'output': args.output, 'format': args.format,
                 'next_step': 'Review metadata and TOC' if args.format == 'epub'
                 else 'Use Calibre Send to device for USB delivery'})


def register(sub):
    parser = sub.add_parser('build')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--title', required=True)
    parser.add_argument('--author', required=True)
    parser.add_argument('--language', default='zh-CN')
    parser.add_argument('--encoding')
    parser.add_argument('--pattern', default=text.PATTERN)
    parser.add_argument('--headings-json')
    parser.add_argument('--cover')
    parser.add_argument('--full-toc', action='store_true')
    parser.set_defaults(run=build)
    parser = sub.add_parser('convert', help='Convert with Calibre')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--title')
    parser.add_argument('--author')
    parser.add_argument('--format', choices=['epub', 'azw3'], default='epub')
    parser.set_defaults(run=convert)
