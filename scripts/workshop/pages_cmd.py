"""Kindle 准备：估算页码，并按邮件或 USB 方式准备文件。"""
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import epub, names
from . import build_cmd
from . import epub3


def paginate(args):
    """Insert estimated page markers at paragraph boundaries and list them in the navigation."""
    if args.chars < 1:
        raise ValueError('--chars must be positive')
    files, opf, package = epub3.load_epub3(args.input)
    items, spine = epub.inventory(files, opf, package)
    nav = next((epub.resolve(opf, e.get('href'))[0] for e in items.values()
                if 'nav' in e.get('properties', '').split()), None)
    if nav is None:
        raise ValueError('EPUB 3 navigation document missing')
    if any('page-list' in e.get(epub.q(epub.EP, 'type'), '').split() for e in epub.xml(files[nav]).iter()):
        raise ValueError('Existing page-list: retain it or inspect before replacing; no automatic overwrite')
    markers, counter, processed = [], 0, []
    skip = set(args.exclude or [])
    for path, item in spine:
        if path in skip or item.get('media-type') != 'application/xhtml+xml' or path == nav:
            continue
        root = epub.xml(files[path])
        body = root.find(epub.q(epub.H, 'body'))
        if body is None:
            continue
        if any('pagebreak' in e.get(epub.q(epub.EP, 'type'), '').split() for e in body.iter()):
            raise ValueError('Existing page markers: ' + path)
        if any(set(e.get(epub.q(epub.EP, 'type'), '').split()) & {'cover', 'titlepage', 'toc'} for e in body.iter()):
            continue
        original = epub.visible(body)
        blocks = [e for e in body.iter()
                  if e.tag in (epub.q(epub.H, 'p'), epub.q(epub.H, 'h1'), epub.q(epub.H, 'h2'), epub.q(epub.H, 'h3'))
                  and ''.join(e.itertext()).strip()]
        if not blocks:
            continue
        parents = {child: parent for parent in body.iter() for child in parent}
        count, first = 0, True
        for block in blocks:
            if first or count >= args.chars:
                first = False
                count = 0
                counter += 1
                ident = 'workshop-page-%d' % counter
                if any(e.get('id') == ident for e in root.iter()):
                    raise ValueError('Page marker ID already exists')
                marker = ET.Element(epub.q(epub.H, 'span'), {
                    'id': ident, epub.q(epub.EP, 'type'): 'pagebreak', 'role': 'doc-pagebreak',
                    'aria-label': str(counter), 'title': str(counter)})
                parent = parents[block]
                parent.insert(list(parent).index(block), marker)
                markers.append((counter, path, ident))
            count += len(re.sub(r'\s', '', ''.join(block.itertext())))
        if epub.visible(body) != original:
            raise ValueError('Pagination changed visible text: ' + path)
        files[path] = epub.dump(root)
        processed.append(path)
    if not markers:
        raise ValueError('No eligible content to paginate')
    nav_root = epub.xml(files[nav])
    nav_body = nav_root.find(epub.q(epub.H, 'body'))
    page_list = ET.SubElement(nav_body, epub.q(epub.H, 'nav'),
                              {epub.q(epub.EP, 'type'): 'page-list', 'hidden': 'hidden', 'id': 'workshop-page-list'})
    ET.SubElement(page_list, epub.q(epub.H, 'h2')).text = '估算页码'
    entries = ET.SubElement(page_list, epub.q(epub.H, 'ol'))
    for number, path, ident in markers:
        link = ET.SubElement(ET.SubElement(entries, epub.q(epub.H, 'li')), epub.q(epub.H, 'a'),
                             {'href': posixpath.relpath(path, posixpath.dirname(nav)) + '#' + ident})
        link.text = str(number)
    files[nav] = epub.dump(nav_root)
    ncx_id = package.find(epub.q(epub.O, 'spine')).get('toc')
    if ncx_id and ncx_id in items:
        ncx = epub.resolve(opf, items[ncx_id].get('href'))[0]
        root = epub.xml(files[ncx])
        if root.find(epub.q(epub.N, 'pageList')) is not None:
            raise ValueError('Existing NCX pageList requires inspection')
        # EPUB NCX does not require playOrder. Omit it throughout to avoid conflicting orders.
        for element in root.iter():
            element.attrib.pop('playOrder', None)
        page_list = ET.SubElement(root, epub.q(epub.N, 'pageList'))
        ET.SubElement(ET.SubElement(page_list, epub.q(epub.N, 'navLabel')), epub.q(epub.N, 'text')).text = '估算页码'
        for number, path, ident in markers:
            target = ET.SubElement(page_list, epub.q(epub.N, 'pageTarget'),
                                   {'id': 'workshop-pt%d' % number, 'type': 'normal', 'value': str(number)})
            ET.SubElement(ET.SubElement(target, epub.q(epub.N, 'navLabel')), epub.q(epub.N, 'text')).text = str(number)
            ET.SubElement(target, epub.q(epub.N, 'content'),
                          {'src': posixpath.relpath(path, posixpath.dirname(ncx)) + '#' + ident})
        files[ncx] = epub.dump(root)
    args.output = names.output_for(args, epub.book_title(package))
    epub.write(files, args.output)
    epub.report({'output': args.output, 'synthetic_pages': counter,
                 'target_nonwhite_characters_per_page': args.chars, 'documents_paginated': len(processed),
                 'excluded': sorted(skip), 'text_roundtrip': 'passed',
                 'meaning': 'Estimated fixed reference pages, not paper pages or screen turns',
                 'device_validation': 'Not tested; Send to Kindle can alter/discard page information'})


def kindle(args):
    """Prepare the file for the chosen delivery method."""
    if args.method == 'usb':
        if args.pages:
            raise ValueError('For USB, generate estimated pages in Calibre when sending to the device')
        # AZW3 原生就能生成，不需要 Calibre。
        # USB 方式约定产出 AZW3，--output 给的是路径，
        # 扩展名不对时按 .azw3 改写，免得产出名不副实的文件。
        args.format, args.title, args.author = 'azw3', None, None
        if args.output and Path(args.output).suffix.lower() != '.azw3':
            args.output = str(Path(args.output).with_suffix('.azw3'))
        return build_cmd.convert(args)
    files, opf, package = epub.load(args.input)
    items, _ = epub.inventory(files, opf, package)
    has_pages = any(
        any('page-list' in e.get(epub.q(epub.EP, 'type'), '').split() or e.tag == epub.q(epub.N, 'pageList')
            for e in epub.xml(files[epub.resolve(opf, item.get('href'))[0]]).iter())
        for item in items.values()
        if 'nav' in item.get('properties', '').split() or item.get('media-type') == 'application/x-dtbncx+xml')
    if args.pages and not has_pages:
        return paginate(args)
    args.output = names.output_for(args, epub.book_title(package))
    epub.write(files, args.output)
    epub.report({'output': args.output, 'existing_page_list_preserved': has_pages,
                 'next_step': 'Send EPUB via Send to Kindle; page display depends on Amazon conversion and the device'})


def register(sub):
    parser = sub.add_parser('paginate')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--chars', type=int, default=500,
                        help='Approximate non-whitespace characters per synthetic page; 500 is a Chinese reading convention, not an industry standard')
    parser.add_argument('--exclude', action='append',
                        help='Exact archive path of a document to leave unnumbered, e.g. OEBPS/notes.xhtml')
    parser.set_defaults(run=paginate)
    parser = sub.add_parser('kindle', help='Prepare an EPUB for email or convert to AZW3 for USB')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--method', choices=['email', 'usb'], required=True)
    parser.add_argument('--pages', action='store_true', help='Add estimated pages to EPUB for email, if absent')
    parser.add_argument('--chars', type=int, default=500)
    parser.add_argument('--exclude', action='append')
    parser.set_defaults(run=kindle)
