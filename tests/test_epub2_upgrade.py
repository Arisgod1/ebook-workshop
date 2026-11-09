"""EPUB 2 的书要先转成 EPUB 3，排版、分页、换封面才做得动。

老书常见：package 声明 version="2.0"、只有 NCX 没有 nav 文档。
这些命令以前一律报错，把用户推去装 Calibre——但本项目自带的
convert 就能转，所以这里直接自动转。
"""

import json
import struct
import sys
import unittest
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop import epub, epub3


def _png(width=4, height=4):
    """一个能通过格式校验的小 PNG。"""
    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)
    raw = b''.join(b'\x00' + b'\x33\x66\x99' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


def make_epub2(path):
    """写一个 EPUB 2 的书：package 2.0、有 NCX、没有 nav。"""
    container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                 ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
                 ' media-type="application/oebps-package+xml"/></rootfiles></container>')
    package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"'
               ' unique-identifier="book-id">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:identifier id="book-id">urn:uuid:test</dc:identifier>'
               '<dc:title>老书</dc:title><dc:language>zh-CN</dc:language></metadata>'
               '<manifest>'
               '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
               '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>'
               '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
               '</manifest>'
               '<spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>'
               '</package>')
    page = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第1章</title></head>'
            '<body><h1>第1章</h1><p>正文第一段。</p></body></html>')
    ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           '<head><meta name="dtb:uid" content="urn:uuid:test"/></head>'
           '<docTitle><text>老书</text></docTitle><navMap>'
           '<navPoint id="n1" playOrder="1"><navLabel><text>第1章</text></navLabel>'
           '<content src="c1.xhtml"/></navPoint>'
           '</navMap></ncx>')
    with zipfile.ZipFile(path, 'w') as handle:
        handle.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        handle.writestr('META-INF/container.xml', container)
        handle.writestr('OEBPS/content.opf', package)
        handle.writestr('OEBPS/c1.xhtml', page)
        handle.writestr('OEBPS/c2.xhtml', page)
        handle.writestr('OEBPS/toc.ncx', ncx)
    return path


class Epub3UpgradeTests(unittest.TestCase):
    """EPUB 2 自动转 EPUB 3。"""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = make_epub2(self.root / 'old.epub')

    def tearDown(self):
        self.temp.cleanup()

    def test_epub2_is_upgraded(self):
        files, opf, package = epub3.load_epub3(self.book)
        self.assertEqual(package.get('version'), '3.0')

    def test_a_nav_document_is_added(self):
        files, opf, package = epub3.load_epub3(self.book)
        self.assertIn('OEBPS/nav.xhtml', files)
        nav = epub.xml(files['OEBPS/nav.xhtml'])
        self.assertTrue(any(element.tag == epub.q(epub.H, 'nav') for element in nav.iter()))

    def test_nav_links_resolve(self):
        """nav 里的链接要指向真实存在的文档。"""
        files, opf, package = epub3.load_epub3(self.book)
        nav = epub.xml(files['OEBPS/nav.xhtml'])
        for element in nav.iter(epub.q(epub.H, 'a')):
            href = element.get('href')
            self.assertIn('OEBPS/' + href, files, 'nav 链接指向了不存在的文件：' + href)

    def test_the_manifest_lists_the_nav(self):
        files, opf, package = epub3.load_epub3(self.book)
        items = package.findall('./' + epub.q(epub.O, 'manifest') + '/' + epub.q(epub.O, 'item'))
        self.assertTrue(any('nav' in (item.get('properties') or '').split() for item in items))

    def test_epub3_books_are_left_alone(self):
        files, opf, package = epub.load(self.book)
        package.set('version', '3.0')
        again = epub3.load_epub3(self.book)
        self.assertEqual(again[2].get('version'), '3.0')

    def test_upgraded_book_passes_validation(self):
        files, opf, package = epub3.load_epub3(self.book)
        epub.validate_files(files)


class Epub2CommandTests(unittest.TestCase):
    """排版、分页、换封面在 EPUB 2 上要能直接跑。"""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = make_epub2(self.root / 'old.epub')

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(root / 'scripts' / 'ebook.py')] + list(args)
        return subprocess.run(command, capture_output=True, text=True)

    def test_layout_works_on_epub2(self):
        output = self.root / 'layout.epub'
        result = self.run_cli('layout', str(self.book), '--preset', 'cn-novel',
                              '--document', 'OEBPS/c1.xhtml', '--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

    def test_paginate_works_on_epub2(self):
        output = self.root / 'paged.epub'
        result = self.run_cli('paginate', str(self.book), '--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertGreater(report['synthetic_pages'], 0)

    def test_cover_works_on_epub2(self):
        image = self.root / 'cover.png'
        image.write_bytes(_png())
        output = self.root / 'covered.epub'
        result = self.run_cli('cover', str(self.book), '--image', str(image),
                              '--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())


if __name__ == '__main__':
    unittest.main()
