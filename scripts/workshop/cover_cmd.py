"""更换封面：使用指定图片，或搬用另一份书里的封面，正文保持不变。"""
import hashlib
from pathlib import Path

from . import epub, render


def cover(args):
    """Swap in a cover image and repoint every cover entry inside the book."""
    files, opf, package = epub.load(args.input)
    if not package.get('version', '').startswith('3'):
        raise ValueError('Convert EPUB 2 to EPUB 3 before editing with this helper')
    if args.from_epub:
        source, source_opf, source_package = epub.load(args.from_epub)
        item = epub.cover_item(source_package)
        if item is None:
            raise ValueError('Source EPUB has no declared cover')
        path, _ = epub.resolve(source_opf, item.get('href'))
        data = source[path]
    else:
        data = Path(args.image).read_bytes()
    result = render.replace_cover(files, opf, package, data)
    # Existing content documents are copied unchanged; only package and new cover resources differ.
    navigation = {epub.resolve(opf, e.get('href'))[0] for e in package.findall('./' + epub.q(epub.O, 'manifest')
                                                                                 + '/' + epub.q(epub.O, 'item'))
                  if 'nav' in e.get('properties', '').split() or e.get('media-type') == 'application/x-dtbncx+xml'}
    assert all(result[name] == body for name, body in files.items() if name != opf and name not in navigation)
    args.output = epub.output_for(args, epub.book_title(package))
    epub.write(result, args.output)
    mime, ext, width, height = epub.image_info(data)
    epub.report({'output': args.output, 'cover_pixels': [width, height],
                 'cover_sha256': hashlib.sha256(data).hexdigest(),
                 'original_image_bytes_preserved': True,
                 'original_content_documents_unchanged': True,
                 'cover_navigation_updated': True,
                 'note': 'Review edition/cover provenance statements in any existing colophon separately'})


def register(sub):
    parser = sub.add_parser('cover')
    parser.add_argument('input')
    parser.add_argument('--output')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--image')
    source.add_argument('--from-epub')
    parser.set_defaults(run=cover)
