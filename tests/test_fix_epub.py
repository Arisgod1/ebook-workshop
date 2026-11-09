"""EPUB 读入/写出的缺陷回归：每个用例都拿真实生成的 EPUB 文件跑一遍。

样本全部在测试里现场生成。
不依赖外部工具或 Calibre。检查的是用户能看到的现象：正文还在不在、
链接指不指得到文件、图片和封面有没有丢、目录层级对不对。
"""

import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop import epub as epub_module
from workshop.convert import epub_input, epub_output, mobi_writer
from workshop.convert.book import Book, Document, Image, TocEntry


def make_epub(path, files, opf_path='OEBPS/content.opf'):
    """把一组包内文件写成一个真实、可加载的 EPUB。"""
    container = ('<?xml version="1.0"?><container '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                 '<rootfiles><rootfile full-path="%s" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>'
                 % opf_path)
    with zipfile.ZipFile(str(path), 'w') as archive:
        archive.writestr('mimetype', b'application/epub+zip',
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container.encode('utf-8'))
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode('utf-8')
            archive.writestr(name, data)
    return path


def package(manifest, spine='', language='zh-CN'):
    """生成一份最简单的 OPF，测试按需往里塞 manifest/spine。"""
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
            ' unique-identifier="bid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>测试书名</dc:title><dc:creator>测试作者</dc:creator>'
            '<dc:language>' + language + '</dc:language>'
            '<dc:identifier id="bid">urn:uuid:fix-epub'
            '</dc:identifier></metadata><manifest>' + manifest
            + '</manifest><spine>' + spine + '</spine></package>')


def simple_epub(path, documents, manifest_extra='', nav='', lang='zh-CN'):
    """常用样本：若干正文文档 + 可选 nav / guide / 额外 manifest 项。"""
    items = []
    spine = []
    for index, (href, _) in enumerate(documents, 1):
        ident = 'd%d' % index
        items.append('<item id="%s" href="%s" media-type="application/xhtml+xml"/>'
                     % (ident, href))
        spine.append('<itemref idref="%s"/>' % ident)
    if nav:
        nav_href, _ = nav
        items.append('<item id="nav" href="%s" media-type="application/xhtml+xml"'
                     ' properties="nav"/>' % nav_href)
    items.append(manifest_extra)
    files = {
        'OEBPS/content.opf': package(''.join(items), ''.join(spine), lang),
    }
    for href, content in documents:
        files['OEBPS/' + href] = content
    if nav:
        files['OEBPS/' + nav[0]] = nav[1]
    return make_epub(path, files)


def xhtml(title, body, xmlns_epub=False):
    ns = (' xmlns="http://www.w3.org/1999/xhtml"'
          + (' xmlns:epub="http://www.idpf.org/2007/ops"' if xmlns_epub else ''))
    return ('<?xml version="1.0" encoding="utf-8"?><html%s><head><title>%s</title>'
            '</head><body>%s</body></html>' % (ns, title, body))


def nav_document(entries, epub_type='toc'):
    """生成 EPUB 3 nav；entries 为 (标题, href, 子项) 三元组列表。"""
    def render(items):
        out = []
        for title, href, children in items:
            out.append('<li><a href="%s">%s</a>%s</li>' % (
                href, title, '<ol>' + ''.join(render(children)) + '</ol>' if children else ''))
        return out
    attr = ' epub:type="%s"' % epub_type if epub_type else ''
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"'
            ' xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>'
            '<body><nav%s id="toc"><ol>%s</ol></nav></body></html>'
            % (attr, ''.join(render(entries))))


def write_to(book, temp, name='out.epub'):
    target = Path(temp) / name
    epub_output.write(book, str(target))
    return target


def split_mobi(data):
    count = struct.unpack('>H', data[76:78])[0]
    offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
    return [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
            for i in range(count)]


class EpubEntityTests(unittest.TestCase):
    """XML 原生实体不能被 HTML 实体展开破坏。"""

    def test_xml_native_entities_and_html5_entities_survive(self):
        """&amp;/&lt;/&gt; 与 &nbsp; 混排时，整本书仍要读得进来。"""
        doc = ('<?xml version="1.0" encoding="utf-8"?>'
               '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
               '<body><p>Tom &amp; Jerry &lt;b&gt; &quot;x&quot; &apos;y&apos;</p>'
               '<p>不换行&nbsp;空格 &copy; &mdash;</p></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', [('ch1.xhtml', doc)])
            book = epub_input.read(str(source))
            content = book.documents[0].content
            self.assertIn('Tom &amp; Jerry', content)
            self.assertIn('&lt;b&gt;', content)
            self.assertIn('"x"', content)            # &quot; 解析成可见引号
            self.assertIn(' ', content)     # &nbsp; 展开成真字符
            # 写出的 XHTML 仍是合法 XML，再读回来正文不丢。
            target = write_to(book, temp)
            again = epub_input.read(str(target))
            self.assertTrue(any('Tom' in d.content for d in again.documents))

    def test_html5_entity_expanding_to_markup_is_escaped(self):
        """&nvlt; 之类展开后带 '<' 的实体要重新转义，不能变成标签。"""
        expanded = epub_input._expand_entities(b'<p>&nvlt; &amp;</p>')
        self.assertIn(b'&lt;', expanded)
        self.assertIn(b'&amp;', expanded)
        doc = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
               '<body><p>&nvlt; &amp;</p></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', [('ch1.xhtml', doc)])
            book = epub_input.read(str(source))
            self.assertIn('&lt;', book.documents[0].content)


    def test_html_entities_are_expanded_when_the_document_declares_no_dtd(self):
        """书里直接写 &nbsp; 却没声明 DTD 时，也要能解析。

        转换管线的读入端一直有这个兜底，但 inspect / validate / correct
        走的是 epub.xml()，遇到同一本书会直接报错。
        """
        from workshop import epub as epub_module
        data = (b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
                b'<body><p>a&nbsp;b</p><p>L&amp;N</p></body></html>')
        root = epub_module.xml(data)
        text = ''.join(root.itertext())
        self.assertIn('a b', text)
        self.assertIn('L&N', text)

    def test_declared_entities_are_still_refused(self):
        """带实体声明的 XML 仍然要拒绝，不能被兜底逻辑放过去。"""
        from workshop import epub as epub_module
        data = b'<!DOCTYPE l [<!ENTITY a "aaaa">]><l>&a;</l>'
        with self.assertRaises(ValueError):
            epub_module.xml(data)


class EpubRoundTripTests(unittest.TestCase):
    """读入 → 写出 → 再读入，正文和目录都不能消失。"""

    def test_documents_keep_xhtml_namespace_and_body(self):
        """写出的正文必须是完整 XHTML：带 XML 声明、xmlns、单个 body。"""
        docs = [('Text/ch1.xhtml', xhtml('第一章', '<h1 id="s1">第一章</h1><p>正文。</p>')),
                ('Text/ch2.xhtml', xhtml('第二章', '<h1>第二章</h1>'))]
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', docs)
            book = epub_input.read(str(source))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                for href, _ in docs:
                    data = archive.read('OEBPS/' + href)
                    self.assertTrue(data.startswith(b'<?xml'), href)
                    self.assertIn(b'xmlns="http://www.w3.org/1999/xhtml"', data, href)
                    self.assertEqual(data.count(b'<body'), 1, href)
            again = epub_input.read(str(target))
            self.assertGreaterEqual(len(again.documents), 2)
            self.assertTrue(any('第一章' in d.content for d in again.documents))
            self.assertTrue(any('第二章' in d.content for d in again.documents))

    def test_fragment_content_is_wrapped_as_full_xhtml(self):
        """别的输入端给的是片段时，写出端要补外壳，再读回来不丢正文。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('ch1.xhtml', content='<p>只有片段的正文</p>'))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                data = archive.read('OEBPS/ch1.xhtml')
            self.assertTrue(data.startswith(b'<?xml'))
            self.assertIn(b'xmlns="http://www.w3.org/1999/xhtml"', data)
            self.assertIn('只有片段的正文'.encode('utf-8'), data)
            again = epub_input.read(str(target))
            self.assertTrue(any('只有片段的正文' in d.content for d in again.documents))

    def test_language_is_not_hardcoded(self):
        """书的 dc:language 要带到写出的页面与 OPF。"""
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub',
                                 [('ch1.xhtml', xhtml('c', '<p>x</p>'))], lang='fr')
            book = epub_input.read(str(source))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                nav = archive.read('OEBPS/nav.xhtml')
                title_page = archive.read('OEBPS/title.xhtml')
                chapter = archive.read('OEBPS/ch1.xhtml')
            for data in (nav, title_page, chapter):
                self.assertIn(b'lang="fr"', data)
            self.assertEqual(book.language, 'fr')

    def test_fragment_is_stripped_from_ncx_content(self):
        """NCX 的 content/@src 不能带 #锚点，nav 里仍保留。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('ch1.xhtml', content=xhtml('c', '<h1 id="s">一</h1>')))
            book.toc.append(TocEntry('第一章', href='ch1.xhtml#s'))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                ncx = archive.read('OEBPS/toc.ncx').decode('utf-8')
                nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertIn('src="ch1.xhtml"', ncx)
            self.assertNotIn('src="ch1.xhtml#s"', ncx)
            self.assertIn('ch1.xhtml#s', nav)

    def test_non_image_resources_are_preserved(self):
        """音频、字体、视频要按原 media-type 还原，正文链接不能断。"""
        doc = xhtml('c', '<p><audio src="audio/track.mp3" controls="controls"></audio></p>')
        extra = ('<item id="aud" href="audio/track.mp3" media-type="audio/mpeg"/>'
                 '<item id="fnt" href="fonts/book.otf" '
                 'media-type="application/vnd.ms-opentype"/>'
                 '<item id="vid" href="video/clip.mp4" media-type="video/mp4"/>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', [('ch1.xhtml', doc)],
                                 manifest_extra=extra)
            files = {'OEBPS/audio/track.mp3': b'ID3fake',
                     'OEBPS/fonts/book.otf': b'OTTOfake',
                     'OEBPS/video/clip.mp4': b'\x00\x00\x00 ftypmp42'}
            # simple_epub 已经写好了包，这里把资源补进去。
            with zipfile.ZipFile(str(source), 'a') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            book = epub_input.read(str(source))
            self.assertEqual(set(book.resources),
                             {'audio/track.mp3', 'fonts/book.otf', 'video/clip.mp4'})
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                self.assertEqual(archive.read('OEBPS/audio/track.mp3'), b'ID3fake')
                self.assertEqual(archive.read('OEBPS/fonts/book.otf'), b'OTTOfake')
                opf = archive.read('OEBPS/content.opf')
            self.assertIn(b'audio/mpeg', opf)
            self.assertIn(b'application/vnd.ms-opentype', opf)
            epub_module.validate_files(epub_module.load(str(target))[0])


class EpubNavTests(unittest.TestCase):
    """EPUB 3 nav 的层级、标题、健壮性与相对路径。"""

    def test_nested_ol_builds_toc_tree(self):
        """嵌套 <ol> 的子项挂在 children 上，不能全降级成顶层。"""
        nav = nav_document([('第一部', '../ch1.xhtml', [
            ('第一章', '../ch1.xhtml', [('第一节', '../ch1.xhtml#s1', [])]),
            ('第二章', '../ch2.xhtml', []),
        ])])
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(
                Path(temp) / 'a.epub',
                [('ch1.xhtml', xhtml('c1', '<h1 id="s1">一</h1>')),
                 ('ch2.xhtml', xhtml('c2', '<h1>二</h1>'))],
                nav=('nav/toc.xhtml', nav))
            book = epub_input.read(str(source))
            self.assertEqual(len(book.toc), 1)
            self.assertEqual(book.toc[0].title, '第一部')
            self.assertEqual([e.title for e in book.toc[0].children],
                             ['第一章', '第二章'])
            self.assertEqual(book.toc[0].children[0].children[0].title, '第一节')
            # 写出后层级仍在。
            target = write_to(book, temp)
            again = epub_input.read(str(target))
            self.assertEqual([e.title for e in again.toc[0].children],
                             ['第一章', '第二章'])

    def test_nav_title_uses_all_visible_text(self):
        """锚里带 <span> 时标题要收进全部文字，不能变空。"""
        nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"'
               ' xmlns:epub="http://www.idpf.org/2007/ops"><body>'
               '<nav epub:type="toc"><ol><li><a href="ch1.xhtml">'
               '<span>第一章</span> 绪论</a></li></ol></nav></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub',
                                 [('ch1.xhtml', xhtml('c', '<p>x</p>'))],
                                 nav=('nav.xhtml', nav))
            book = epub_input.read(str(source))
            self.assertEqual(book.toc[0].title, '第一章 绪论')

    def test_toc_legacy_and_untyped_nav_are_recognised(self):
        """epub:type 多值 / toc-legacy / 无 type 但带 ol 都要认得出来。"""
        for epub_type, label in (('toc landmarks', '多值'), ('toc-legacy', 'legacy'),
                                 (None, '无 type')):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp:
                    nav = nav_document([('第一章', 'ch1.xhtml', [])], epub_type=epub_type)
                    source = simple_epub(Path(temp) / 'a.epub',
                                         [('ch1.xhtml', xhtml('c', '<p>x</p>'))],
                                         nav=('nav.xhtml', nav))
                    book = epub_input.read(str(source))
                    self.assertEqual([e.title for e in book.toc], ['第一章'])

    def test_nav_in_subdirectory_links_use_parent_path(self):
        """nav 放在子目录时，链接要算出 '../'，不能只往下拼。"""
        nav = nav_document([('第一章', '../Text/ch1.xhtml', [])])
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub',
                                 [('Text/ch1.xhtml', xhtml('c', '<p>x</p>'))],
                                 nav=('nav/toc.xhtml', nav))
            book = epub_input.read(str(source))
            self.assertEqual(book.toc[0].href, 'Text/ch1.xhtml')
            self.assertEqual(getattr(book, 'nav_href', None), 'nav/toc.xhtml')
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                self.assertIn('OEBPS/nav/toc.xhtml', archive.namelist())
                data = archive.read('OEBPS/nav/toc.xhtml')
            self.assertIn(b'href="../Text/ch1.xhtml"', data)
            self.assertIn(b'href="../style.css"', data)
            epub_module.validate_files(epub_module.load(str(target))[0])
            self.assertEqual(epub_input.read(str(target)).toc[0].href,
                             'Text/ch1.xhtml')


class EpubBrokenReferenceTests(unittest.TestCase):
    """正文引用了不存在的图片时，要当场说清楚，别拖到写出阶段。"""

    def test_missing_image_is_reported_at_read_time(self):
        doc = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
               '<head><title>章</title></head><body><p>正文<img src="missing.png"/></p></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', [('ch1.xhtml', doc)])
            with self.assertRaises(ValueError) as caught:
                epub_input.read(str(source))
        message = str(caught.exception)
        self.assertIn('不存在的图片', message)
        self.assertIn('missing.png', message)

    def test_image_that_exists_is_accepted(self):
        """图片真的在书里时不该报错。"""
        doc = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
               '<head><title>章</title></head><body><p><img src="p.png"/></p></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(
                Path(temp) / 'a.epub', [('ch1.xhtml', doc)],
                manifest_extra='<item id="img" href="p.png" media-type="image/png"/>')
            # simple_epub 只放文档，图片要另外塞进去。
            with zipfile.ZipFile(source, 'a') as archive:
                archive.writestr('OEBPS/p.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 20)
            book = epub_input.read(str(source))
        self.assertIn('p.png', book.images)


class EpubCoverTests(unittest.TestCase):
    """封面路径必须和 images 的键对齐，MOBI 里才有资源记录。"""

    def test_cover_key_matches_images_key(self):
        """cover-image 属性声明的封面，返回的键必须真的在 book.images 里。"""
        extra = ('<item id="cover-image" href="Images/cover.jpg" '
                 'media-type="image/jpeg" properties="cover-image"/>'
                 '<item id="img2" href="Images/inside.png" media-type="image/png"/>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub',
                                 [('ch1.xhtml', xhtml('c', '<p><img src="Images/inside.png"/></p>'))],
                                 manifest_extra=extra)
            with zipfile.ZipFile(str(source), 'a') as archive:
                archive.writestr('OEBPS/Images/cover.jpg', b'\xff\xd8\xff\xe0 cover')
                archive.writestr('OEBPS/Images/inside.png',
                                 b'\x89PNG\r\n\x1a\n' + b'\x00' * 30)
            book = epub_input.read(str(source))
            self.assertEqual(book.cover, 'Images/cover.jpg')
            self.assertIn(book.cover, book.images)
            # MOBI 里封面必须真的有一条资源记录，字节与源图一致。
            records = split_mobi(mobi_writer.build_mobi(book, kf8=False))
            self.assertIn(book.images[book.cover].data, records)
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                opf = archive.read('OEBPS/content.opf')
            self.assertIn(b'cover-image', opf)

    def test_all_referenced_images_and_cover_have_mobi_resource_records(self):
        """EPUB 含多张图 → MOBI/AZW3：每张图都有资源记录，recindex 指得到。"""
        jpg = b'\xff\xd8\xff\xe0 COVER-BYTES'
        first = b'\x89PNG\r\n\x1a\n' + b'A' * 40
        second = b'\x89PNG\r\n\x1a\n' + b'B' * 40
        doc = xhtml('c', '<p><img src="Images/a.png"/><img src="Images/b.png"/></p>')
        extra = ('<item id="cover-image" href="Images/cover.jpg" '
                 'media-type="image/jpeg" properties="cover-image"/>'
                 '<item id="i1" href="Images/a.png" media-type="image/png"/>'
                 '<item id="i2" href="Images/b.png" media-type="image/png"/>')
        with tempfile.TemporaryDirectory() as temp:
            source = simple_epub(Path(temp) / 'a.epub', [('ch1.xhtml', doc)],
                                 manifest_extra=extra)
            with zipfile.ZipFile(str(source), 'a') as archive:
                archive.writestr('OEBPS/Images/cover.jpg', jpg)
                archive.writestr('OEBPS/Images/a.png', first)
                archive.writestr('OEBPS/Images/b.png', second)
            book = epub_input.read(str(source))
            self.assertEqual(book.cover, 'Images/cover.jpg')
            for kf8 in (False, True):
                with self.subTest(kf8=kf8):
                    records = split_mobi(mobi_writer.build_mobi(book, kf8=kf8))
                    for data in (jpg, first, second):
                        self.assertIn(data, records, '图片资源记录丢了')
                    head = records[0]
                    last_text = struct.unpack('>H', head[8:10])[0]
                    stream = b''.join(
                        mobi_writer.palmdoc.decompress(
                            record[:len(record) - ((record[-1] & 0x3) + 1)])
                        for record in records[1:last_text + 1])
                    self.assertIn(b'recindex="00001"', stream) if not kf8 else None
                    if not kf8:
                        self.assertIn(b'recindex="00002"', stream)

    def test_guide_cover_page_in_subdirectory_is_resolved_from_itself(self):
        """guide 指向子目录里的封面页时，图要按封面页自身的位置解析。"""
        opf = ('<?xml version="1.0" encoding="utf-8"?>'
               '<package xmlns="http://www.idpf.org/2007/opf" version="2.0"'
               ' unique-identifier="bid"><metadata '
               'xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:title>书名</dc:title><dc:creator>作者</dc:creator>'
               '<dc:language>zh-CN</dc:language><dc:identifier id="bid">urn:uuid:c'
               '</dc:identifier></metadata><manifest>'
               '<item id="c" href="Images/cover.jpg" media-type="image/jpeg"/>'
               '<item id="cov" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>'
               '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
               '</manifest><spine><itemref idref="c1"/></spine>'
               '<guide><reference type="cover" title="封面" href="Text/cover.xhtml"/>'
               '</guide></package>')
        cover_page = xhtml('封面', '<div><img src="../Images/cover.jpg" alt="封面"/></div>')
        with tempfile.TemporaryDirectory() as temp:
            source = make_epub(Path(temp) / 'a.epub', {
                'OEBPS/content.opf': opf,
                'OEBPS/Text/cover.xhtml': cover_page,
                'OEBPS/ch1.xhtml': xhtml('c', '<h1>第一章</h1>'),
                'OEBPS/Images/cover.jpg': b'\xff\xd8\xff\xe0 cover',
            })
            book = epub_input.read(str(source))
            self.assertEqual(book.cover, 'Images/cover.jpg')
            self.assertIn(book.cover, book.images)
            records = split_mobi(mobi_writer.build_mobi(book, kf8=False))
            self.assertIn(book.images[book.cover].data, records)


    def test_cover_item_finds_a_guide_declared_cover_page(self):
        """guide 里 type="cover" 指向封面页时也要认出来。

        Sigil 做的书和老书常这样标记：metadata 里没有 meta name="cover"，
        只有 guide 的一条引用，且指向 XHTML 封面页而不是图片本身。
        """
        from workshop import epub as epub_module
        package = epub_module.xml(
            b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"'
            b' version="3.0" unique-identifier="id">'
            b'<manifest>'
            b'<item id="tp" href="Text/titlepage.xhtml" media-type="application/xhtml+xml"/>'
            b'<item id="img" href="Images/cover.jpg" media-type="image/jpeg"/>'
            b'</manifest>'
            b'<guide><reference type="cover" href="Text/titlepage.xhtml"/></guide>'
            b'</package>')
        item = epub_module.cover_item(package)
        self.assertIsNotNone(item)
        self.assertEqual(item.get('href'), 'Text/titlepage.xhtml')


class EpubOutputTests(unittest.TestCase):
    """写出端的改名、去重、标题页判定、目录同步。"""

    def test_title_page_is_not_guessed_from_content(self):
        """正文里提到 titlepage 这个词，整章不能被替换成标题页。"""
        chapter = xhtml('c1', '<h1>第一章</h1><p>本章讨论 titlepage 的排版问题，篇幅很长。</p>')
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('ch1.xhtml', content=chapter))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                data = archive.read('OEBPS/ch1.xhtml').decode('utf-8')
            self.assertIn('第一章', data)
            self.assertIn('titlepage 的排版问题', data)

    def test_explicit_titlepage_property_is_regenerated_only_for_that_page(self):
        """读入端标记了 titlepage 的文档才按新书名重新生成。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '新书名'
            book.author = '新作者'
            book.add_document(Document('Text/title.xhtml',
                                       content=xhtml('旧', '<h1>旧书名</h1>'),
                                       properties=['titlepage', 'non-linear']))
            book.add_document(Document('ch1.xhtml', content=xhtml('c', '<h1>第一章</h1>')))
            book.toc.append(TocEntry('扉页', href='Text/title.xhtml'))
            book.toc.append(TocEntry('第一章', href='ch1.xhtml'))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                title_page = archive.read('OEBPS/title.xhtml').decode('utf-8')
                chapter = archive.read('OEBPS/ch1.xhtml').decode('utf-8')
                nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertIn('新书名', title_page)
            self.assertNotIn('旧书名', title_page)
            self.assertIn('第一章', chapter)
            self.assertIn('href="title.xhtml"', nav)   # 目录跟着改名
            epub_module.validate_files(epub_module.load(str(target))[0])

    def test_title_xhtml_not_duplicated_in_manifest_or_spine(self):
        """读入端自带 title.xhtml 时，清单和 spine 里只能各出现一次。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('title.xhtml', content=xhtml('t', '<h1>书名</h1>')))
            book.add_document(Document('ch1.xhtml', content=xhtml('c', '<p>正文</p>')))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                package_xml = epub_module.xml(archive.read('OEBPS/content.opf'))
            hrefs = [e.get('href') for e in package_xml.findall(
                './/' + epub_module.q(epub_module.O, 'item'))]
            self.assertEqual(hrefs.count('title.xhtml'), 1)
            self.assertEqual(len(hrefs), len(set(hrefs)))
            idrefs = [e.get('idref') for e in package_xml.findall(
                './/' + epub_module.q(epub_module.O, 'itemref'))]
            self.assertEqual(len(idrefs), len(set(idrefs)))

    def test_renamed_document_updates_toc_and_relative_links(self):
        """扩展名不合规而改名后，目录和正文里的图片链接都要指得到文件。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document(
                'Text/chapter', content=xhtml('c', '<p><img src="../Images/p.png"/></p>')))
            book.add_image(Image('Images/p.png',
                                 b'\x89PNG\r\n\x1a\n' + b'\x00' * 20, 'image/png'))
            book.toc.append(TocEntry('第一章', href='Text/chapter'))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                names = archive.namelist()
                nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertIn('OEBPS/Images/p.png', names)
            chapter_href = next(n for n in names if n.startswith('OEBPS/Text/')
                                and n.endswith('.xhtml'))
            self.assertIn(chapter_href[len('OEBPS/'):], nav)
            epub_module.validate_files(epub_module.load(str(target))[0])

    def test_toc_entry_without_href_does_not_crash(self):
        """分组标题没有链接时写成 span，不能抛 AttributeError。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('ch1.xhtml', content=xhtml('c', '<p>x</p>')))
            book.toc.append(TocEntry('第一部'))
            target = write_to(book, temp)
            with zipfile.ZipFile(str(target)) as archive:
                nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertIn('<span>第一部</span>', nav)

    def test_relative_to_supports_parent_directories(self):
        """_relative_to 要能算 '../'，不能只往下拼。"""
        self.assertEqual(epub_output._relative_to('nav/toc.xhtml', 'Text/ch1.xhtml'),
                         '../Text/ch1.xhtml')
        self.assertEqual(epub_output._relative_to('nav.xhtml', 'Text/ch1.xhtml'),
                         'Text/ch1.xhtml')

    def test_children_of_renamed_toc_entries_are_updated(self):
        """改名要递归同步到子目录项。"""
        with tempfile.TemporaryDirectory() as temp:
            book = Book()
            book.title = '书名'
            book.add_document(Document('Text/chapter', content=xhtml('c', '<p>x</p>')))
            parent = TocEntry('第一部', href=None)
            parent.children.append(TocEntry('第一章', href='Text/chapter'))
            book.toc.append(parent)
            target = write_to(book, temp)
            epub_module.validate_files(epub_module.load(str(target))[0])
            with zipfile.ZipFile(str(target)) as archive:
                nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertNotIn('Text/chapter', nav)
            self.assertIn('Text/c0001.xhtml', nav)


if __name__ == '__main__':
    unittest.main()
