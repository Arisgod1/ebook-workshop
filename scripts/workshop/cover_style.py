"""换封面：把指定图片装进书里，并让封面入口都指向它。"""
import hashlib
import posixpath
import xml.etree.ElementTree as ET

from . import epub, names
from .style import CSS


def _page(title, body):
    """封面页本身。只是一张图，不借用正文的页面生成器。"""
    return ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="' + epub.H
            + '" xmlns:epub="' + epub.EP + '"><head><title>' + title
            + '</title><meta charset="utf-8"/><link rel="stylesheet" href="style.css"/>'
              '</head><body>' + body + '</body></html>').encode()


def replace_cover(files, opf, package, data):
    """Point the book at a new cover image without touching the text."""
    mime, ext, width, height = names.image_info(data)
    result = dict(files)
    base = posixpath.dirname(opf)
    # A distinct resource avoids rewriting existing references that might use the old cover elsewhere.
    stem = 'workshop-cover-' + hashlib.sha256(data).hexdigest()[:12]
    image_path = posixpath.join(base, stem + '.' + ext)
    document_path = posixpath.join(base, stem + '.xhtml')
    result[image_path] = data
    result[document_path] = _page('封面', '<section class="cover" epub:type="cover"><img src="' + stem + '.' + ext
                                  + '" alt="封面" width="' + str(width) + '" height="' + str(height) + '"/></section>')
    # page() assumes stylesheet at the package root. Build provides it; imported books may not.
    if posixpath.join(base, 'style.css') not in result:
        result[posixpath.join(base, 'style.css')] = CSS.encode()
    manifest = package.find(epub.q(epub.O, 'manifest'))
    spine = package.find(epub.q(epub.O, 'spine'))
    old = epub.cover_item(package)
    old_image_path = epub.resolve(opf, old.get('href'))[0] if old is not None else None
    if old is not None:
        properties = [p for p in old.get('properties', '').split() if p != 'cover-image']
        if properties:
            old.set('properties', ' '.join(properties))
        else:
            old.attrib.pop('properties', None)
    if stem + '-image' in {e.get('id') for e in manifest}:
        raise ValueError('This exact cover is already present; inspect instead of replacing it again')
    removed_cover_paths = set()
    items = {e.get('id'): e for e in manifest}
    for reference in list(spine):
        item = items.get(reference.get('idref'))
        if item is None or item.get('media-type') != 'application/xhtml+xml':
            continue
        path, _ = epub.resolve(opf, item.get('href'))
        root = epub.xml(result[path])
        body = root.find(epub.q(epub.H, 'body'))
        is_cover = any('cover' in e.get(epub.q(epub.EP, 'type'), '').split() for e in root.iter())
        linked = [epub.resolve(path, e.get('src') or e.get(epub.q(epub.XLINK, 'href')) or e.get('href'))[0]
                  for e in body.iter() if e.tag in (epub.q(epub.H, 'img'), epub.q(epub.SVG, 'image'))
                  and (e.get('src') or e.get(epub.q(epub.XLINK, 'href')) or e.get('href'))] if body is not None else []
        image_only = body is not None and not ''.join(body.itertext()).strip() and old_image_path is not None and linked == [old_image_path]
        if is_cover or (image_only and reference is list(spine)[0]):
            spine.remove(reference)
            removed_cover_paths.add(path)
    for suffix, href, media, properties in [('image', stem + '.' + ext, mime, 'cover-image'),
                                            ('page', stem + '.xhtml', 'application/xhtml+xml', '')]:
        attributes = {'id': stem + '-' + suffix, 'href': href, 'media-type': media}
        if properties:
            attributes['properties'] = properties
        ET.SubElement(manifest, epub.q(epub.O, 'item'), attributes)
    if not any(e.get('href') == 'style.css' for e in manifest):
        ET.SubElement(manifest, epub.q(epub.O, 'item'),
                           {'id': stem + '-style', 'href': 'style.css', 'media-type': 'text/css'})
    spine.insert(0, ET.Element(epub.q(epub.O, 'itemref'), {'idref': stem + '-page'}))
    metadata = package.find(epub.q(epub.O, 'metadata'))
    for element in list(metadata):
        if element.tag == epub.q(epub.O, 'meta') and element.get('name') == 'cover':
            metadata.remove(element)
    ET.SubElement(metadata, epub.q(epub.O, 'meta'), {'name': 'cover', 'content': stem + '-image'})
    guide = package.find(epub.q(epub.O, 'guide'))
    if guide is not None:
        for element in guide:
            if element.get('type') == 'cover':
                element.set('href', stem + '.xhtml')
    # Make Go To Cover and NCX cover entries target the replacement rather than the retained old resource.
    for item in list(manifest):
        if 'nav' not in item.get('properties', '').split() and item.get('media-type') != 'application/x-dtbncx+xml':
            continue
        path, _ = epub.resolve(opf, item.get('href'))
        root = epub.xml(result[path])
        changed = False
        for element in root.iter():
            for attribute in ('href', 'src'):
                if attribute not in element.attrib:
                    continue
                target, _ = epub.resolve(path, element.get(attribute))
                if target in removed_cover_paths:
                    element.set(attribute, posixpath.relpath(document_path, posixpath.dirname(path)))
                    changed = True
        if changed:
            result[path] = epub.dump(root)
    result[opf] = epub.dump(package)
    return result
