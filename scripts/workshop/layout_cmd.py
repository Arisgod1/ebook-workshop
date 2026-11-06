"""排版：套用中文小说预设或自定义边距、行距、缩进，正文内容不变。"""
import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from . import epub

PRESET_PATH = Path(__file__).resolve().parents[2] / 'assets' / 'cn-novel.css'


def layout_style(args):
    """The stylesheet to apply: the preset, with any requested overrides folded in."""
    style = PRESET_PATH.read_text(encoding='utf-8')
    custom = False
    for attribute, lower, upper, original, template in (
        ('margin', 0, 25, 'margin: 0 5%;', 'margin: 0 %g%%;'),
        ('line_height', 1, 3, 'line-height: 1.8;', 'line-height: %g;'),
        ('indent', 0, 6, 'text-indent: 2em;', 'text-indent: %gem;'),
        ('paragraph_gap', 0, 3, 'margin: .5em 0;', 'margin: %gem 0;'),
    ):
        value = getattr(args, attribute)
        if value is None:
            continue
        if not lower <= value <= upper:
            raise ValueError(f'{attribute} must be between {lower} and {upper}')
        style = style.replace(original, template % value, 1)
        custom = True
    style = style.encode('utf-8')
    stem = 'workshop-cn-novel'
    if custom:
        stem += '-' + hashlib.sha256(style).hexdigest()[:12]
    return stem, style


def layout(args):
    """Apply the reading preset to selected chapters, leaving the words untouched."""
    files, opf, package = epub.load(args.input)
    if not package.get('version', '').startswith('3'):
        raise ValueError('Layout preset requires EPUB 3')
    if any(meta.get('property') == 'rendition:layout' and (meta.text or '').strip() == 'pre-paginated'
           for meta in package.iter(epub.q(epub.O, 'meta'))) or any(
        'rendition:layout-pre-paginated' in ref.get('properties', '').split()
        for ref in package.iter(epub.q(epub.O, 'itemref'))):
        raise ValueError('Layout preset requires a reflowable EPUB')
    _, spine = epub.inventory(files, opf, package)
    eligible = {path for path, item in epub.reading_documents(spine)}
    style_id, style = layout_style(args)
    style_path = posixpath.join(posixpath.dirname(opf), style_id + '.css')
    manifest = package.find(epub.q(epub.O, 'manifest'))
    registered = [item for item in manifest if epub.resolve(opf, item.get('href'))[0] == style_path]
    if style_path in files or registered:
        if (files.get(style_path) != style or len(registered) != 1
                or registered[0].get('media-type') != 'text/css'):
            raise ValueError('Existing layout stylesheet conflicts with the preset')
    else:
        while any(item.get('id') == style_id for item in manifest):
            style_id += '-style'
        ET.SubElement(manifest, epub.q(epub.O, 'item'), {
            'id': style_id, 'href': posixpath.basename(style_path), 'media-type': 'text/css',
        })
    for path in dict.fromkeys(args.document):
        if path not in eligible:
            raise ValueError('Select a reading document from the EPUB spine: ' + path)
        root = epub.xml(files[path])
        head, body = root.find(epub.q(epub.H, 'head')), root.find(epub.q(epub.H, 'body'))
        if head is None or body is None or not epub.visible(body).strip():
            raise ValueError('Selected document has no readable body: ' + path)
        if any(set(e.get(epub.q(epub.EP, 'type'), '').split()) & {'cover', 'titlepage', 'toc'}
               for e in body.iter()):
            raise ValueError('Select a prose chapter instead of front matter: ' + path)
        original_text = epub.visible(body)
        classes = body.get('class', '').split()
        if 'workshop-cn-novel' not in classes:
            body.set('class', ' '.join(classes + ['workshop-cn-novel']))
        workshop_styles = {epub.resolve(opf, item.get('href'))[0] for item in manifest
                           if item.get('media-type') == 'text/css'
                           and re.fullmatch(r'workshop-cn-novel(?:-[0-9a-f]{12})?\.css', item.get('href', ''))
                           and item.get('id', '').startswith('workshop-cn-novel')}
        linked = False
        for link in head.findall(epub.q(epub.H, 'link')):
            if 'stylesheet' not in link.get('rel', '').split():
                continue
            target = epub.resolve(path, link.get('href', ''))[0]
            if target == style_path and not linked:
                linked = True
            elif target in workshop_styles:
                head.remove(link)
        if not linked:
            href = quote(posixpath.relpath(style_path, posixpath.dirname(path)))
            ET.SubElement(head, epub.q(epub.H, 'link'), {'rel': 'stylesheet', 'type': 'text/css', 'href': href})
        if epub.visible(body) != original_text:
            raise ValueError('Layout changed body text: ' + path)
        files[path] = epub.dump(root)
    files[style_path] = style
    files[opf] = epub.dump(package)
    args.output = epub.output_for(args, epub.book_title(package))
    epub.write(files, args.output)
    epub.report({'output': args.output, 'preset': args.preset, 'documents': list(dict.fromkeys(args.document))})


def register(sub):
    parser = sub.add_parser('layout', help='Apply a reading preset to selected EPUB 3 chapters')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--preset', choices=['cn-novel'], default='cn-novel')
    parser.add_argument('--document', action='append', required=True,
                        help='Exact chapter path inside the EPUB. Repeat for each chapter.')
    parser.add_argument('--margin', type=float, help='Left/right margin in percent')
    parser.add_argument('--line-height', type=float, help='Line height multiplier')
    parser.add_argument('--indent', type=float, help='First-line indent in em')
    parser.add_argument('--paragraph-gap', type=float, help='Vertical paragraph margin in em')
    parser.set_defaults(run=layout)
