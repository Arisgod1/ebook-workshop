"""需要 EPUB 3 的命令的共同前置：老书先转成 EPUB 3。

EPUB 2 的书缺 nav 文档、缺 package 里的 rendition 属性，
排版、分页、换封面这几件事都做不了。以前这些命令一律报错，
用户只能去装 Calibre——但本项目自带的 convert 就能把 EPUB 2 转成 EPUB 3。

这里统一处理：读到 EPUB 2 就先在内存里转换，再把结果交给命令。
输出还是写用户指定的那个文件，不留中间文件。
"""

import xml.etree.ElementTree as ET

from . import epub


class NeedsEpub3(ValueError):
    """这本书没法自动转成 EPUB 3。"""


def load_epub3(path):
    """读入一本书，保证它是 EPUB 3。

    返回 (files, opf, package)。
    """
    files, opf, package = epub.load(path)
    if package.get('version', '').startswith('3'):
        return files, opf, package
    converted = _to_epub3(files, opf, package)
    if converted is None:
        raise NeedsEpub3(
            'This book is EPUB 2 and cannot be rewritten as EPUB 3 automatically; '
            'convert it with another tool first')
    return converted


def _to_epub3(files, opf, package):
    """把 EPUB 2 的包改成 EPUB 3 的结构。

    EPUB 3 要求：package 声明 version="3.0"、有一个 nav 文档、
    文档是 XHTML。书里原有的 NCX 保留，老设备还能用。
    """
    version = package.get('version')
    if version and not version.startswith('2'):
        return None
    package.set('version', '3.0')

    # 找书脊里的第一份文档，做成 nav 文档的来源。
    documents = []
    for name, item in epub.reading_documents(epub.inventory(files, opf, package)[1]):
        documents.append((name, item))
    if not documents:
        return None

    nav_name = 'OEBPS/nav.xhtml'
    if nav_name in files:
        return files, opf, package

    # 目录优先按原有的 NCX 还原：它才是作者排的目录结构。
    # 没有 NCX 才退回「每份文档一条」的平铺目录。
    entries = _ncx_entries(files, opf, package, documents)
    body = ''.join(entries)
    nav = ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html>'
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="%s">'
           '<head><title>目录</title></head>'
           '<body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>%s</ol></nav></body></html>'
           % (epub.EP, body))
    files[nav_name] = nav.encode('utf-8')

    # 清单里补上 nav 文档；manifest 元素名在 EPUB 2 与 3 里相同。
    manifest = package.find('./' + epub.q(epub.O, 'manifest'))
    if manifest is not None and not any(
            entry.get('href') == 'nav.xhtml'
            for entry in manifest.findall(epub.q(epub.O, 'item'))):
        ident = 'nav'
        existing = {entry.get('id') for entry in manifest.findall(epub.q(epub.O, 'item'))}
        while ident in existing:
            ident += 'x'
        element = ET.SubElement(manifest, epub.q(epub.O, 'item'),
                                 {'id': ident, 'href': 'nav.xhtml',
                                  'media-type': 'application/xhtml+xml',
                                  'properties': 'nav'})
        element.tail = None

    # 改好的 package 要写回 book 里，否则输出文件仍标着 EPUB 2：
    # 下次读它还要再升一遍，而 nav 已经存在，逻辑就乱了。
    files[opf] = epub.dump(package)
    return files, opf, package


def _ncx_entries(files, opf, package, documents):
    """按原有 NCX 生成 nav 的列表项；没有 NCX 就按文档平铺。"""
    items = package.findall('./' + epub.q(epub.O, 'manifest') + '/' + epub.q(epub.O, 'item'))
    for item in items:
        if item.get('media-type') != 'application/x-dtbncx+xml':
            continue
        path, _ = epub.resolve(opf, item.get('href'))
        if path not in files:
            continue
        try:
            root = epub.xml(files[path])
        except ValueError:
            continue
        navmap = root.find(epub.q(epub.N, 'navMap'))
        if navmap is None:
            continue
        entries = _ncx_list(navmap, opf)
        if entries:
            return entries
    # 没有 NCX：每份文档一条，标题取文档自己的 title。
    entries = []
    for name, _ in documents:
        title = ''
        root = epub.xml(files[name])
        head = root.find(epub.q(epub.H, 'head'))
        if head is not None:
            element = head.find(epub.q(epub.H, 'title'))
            if element is not None and element.text:
                title = element.text.strip()
        entries.append('<li><a href="%s">%s</a></li>'
                       % (_nav_href(opf, name), _escape(title or name)))
    return entries


def _ncx_list(parent, opf):
    """把 NCX 的 navPoint 树转成 nav 的 <li> 列表，保留层级。"""
    entries = []
    for point in parent.findall(epub.q(epub.N, 'navPoint')):
        label = point.find(epub.q(epub.N, 'navLabel'))
        text = label.find(epub.q(epub.N, 'text')) if label is not None else None
        title = (text.text or '').strip() if text is not None else ''
        content = point.find(epub.q(epub.N, 'content'))
        href = content.get('src') if content is not None else None
        children = _ncx_list(point, opf)
        if href:
            link = '<a href="%s">%s</a>' % (_escape(href), _escape(title))
        else:
            link = '<span>%s</span>' % _escape(title)
        if children:
            link += '<ol>' + ''.join(children) + '</ol>'
        entries.append('<li>' + link + '</li>')
    return entries


def _nav_href(opf, name):
    """nav 文档里的链接要相对它自己的位置。

    nav 文档放在内容目录下（OEBPS/nav.xhtml），所以链接要写成
    相对内容目录的形式，不能带 OEBPS/ 前缀。
    """
    base = opf.rsplit('/', 1)[0] if '/' in opf else ''
    if base and name.startswith(base + '/'):
        return name[len(base) + 1:]
    return name


def _escape(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))
