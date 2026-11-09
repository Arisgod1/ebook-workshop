"""更换封面：使用指定图片，或搬用另一份书里的封面，正文保持不变。"""
import hashlib
from pathlib import Path

from . import cover_style, epub, epub3, names


def _source_cover_image(files, opf, package):
    """从另一本书里取出封面图片的字节。

    cover_item() 可能返回图片项，也可能返回封面页——
    老书和 Sigil 做的书常在 guide 里指向一个 XHTML 封面页，
    真正那张图在页里的 <img>。
    """
    item = epub.cover_item(package)
    if item is None:
        raise ValueError('Source EPUB has no declared cover')
    path, _ = epub.resolve(opf, item.get('href'))
    if path not in files:
        raise ValueError('Source EPUB cover resource is missing: ' + str(item.get('href')))
    if (item.get('media-type') or '').startswith('image/'):
        return files[path]
    # 封面页：找页里第一张图片。
    root = epub.xml(files[path])
    for element in root.iter():
        if element.tag not in (epub.q(epub.H, 'img'), epub.q(epub.SVG, 'image')):
            continue
        source = element.get('src') or element.get(epub.q(epub.XLINK, 'href'))
        if not source:
            continue
        image_path, _ = epub.resolve(path, source)
        if image_path in files:
            return files[image_path]
    raise ValueError('Source EPUB cover page references no image: ' + str(item.get('href')))


def cover(args):
    """Swap in a cover image and repoint every cover entry inside the book."""
    files, opf, package = epub3.load_epub3(args.input)
    if args.from_epub:
        source, source_opf, source_package = epub.load(args.from_epub)
        data = _source_cover_image(source, source_opf, source_package)
    else:
        data = Path(args.image).read_bytes()
    result = cover_style.replace_cover(files, opf, package, data)
    # Existing content documents are copied unchanged; only package and new cover resources differ.
    navigation = {epub.resolve(opf, e.get('href'))[0] for e in package.findall('./' + epub.q(epub.O, 'manifest')
                                                                                 + '/' + epub.q(epub.O, 'item'))
                  if 'nav' in e.get('properties', '').split() or e.get('media-type') == 'application/x-dtbncx+xml'}
    assert all(result[name] == body for name, body in files.items() if name != opf and name not in navigation)
    args.output = names.output_for(args, epub.book_title(package))
    epub.write(result, args.output)
    mime, ext, width, height = names.image_info(data)
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
