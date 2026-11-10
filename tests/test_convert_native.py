"""原生格式互转：读入 DOCX/ODT/HTML，写出 MOBI。

这些测试不依赖 Calibre，也不依赖任何外部二进制：
所有样本文件都在测试里现场生成。
"""

import re
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop.convert import book as book_module
from workshop.convert import (docx_input, epub_input, epub_output, html_input,
                             mobi_writer, odt_input)
from workshop.convert.mobi import decode_number
from workshop.convert.palmdoc import compress, decompress


class PalmDocTests(unittest.TestCase):
    """PalmDOC 压缩：与公开的 MOBI 规范一致。"""

    def test_roundtrip_preserves_bytes(self):
        """压缩再解压应还原原始字节。"""
        for data in [b'', b'a', b'hello world', '中文内容'.encode('utf-8'),
                     bytes(range(256)), b'x' * 5000]:
            self.assertEqual(decompress(compress(data)), data)

    def test_literals_use_expected_flags(self):
        """普通字符按字面输出，控制字符与高位字节成组输出。"""
        self.assertEqual(compress(b'AB'), b'AB')
        # 0x09-0x7F 是字面；空格加 ASCII 压成一个字节。
        self.assertEqual(compress(b' A'), b'\xc1')
        # 高位字节成组，组首字节是组长度。
        self.assertEqual(compress(b'\x80\x81'), b'\x02\x80\x81')

    def test_repeated_data_becomes_a_reference(self):
        """重复内容压成 LZ77 引用，引用字节以 0x80 开头。

        PalmDOC 在缓冲区两端各留 10 字节余量，所以开头一段始终按字面写，
        重复的部分从第 12 个字节才开始被引用。
        """
        compressed = compress(b'abcdefghij' * 20)
        self.assertEqual(compressed[:11], b'abcdefghija')
        self.assertTrue(compressed[11] & 0x80, compressed[11:15].hex())
        self.assertLess(len(compressed), len(b'abcdefghij' * 20))

    def test_match_length_never_exceeds_distance(self):
        """匹配不能越过当前位置，重复字节只能引用已写出的部分。"""
        data = b'ab' * 50
        self.assertEqual(decompress(compress(data)), data)


class DocxInputTests(unittest.TestCase):
    """DOCX 读入。"""

    def make_docx(self, path, body, styles=None, core=None):
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body>' + body + '</w:body></w:document>')
        if styles is None:
            styles = ('<?xml version="1.0" encoding="UTF-8"?>'
                      '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                      '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
                      '</w:styles>')
        if core is None:
            core = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
                    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<dc:title>测试书名</dc:title><dc:creator>测试作者</dc:creator>'
                    '</cp:coreProperties>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('_rels/.rels', '<Relationships/>')
            archive.writestr('word/document.xml', document)
            archive.writestr('word/styles.xml', styles)
            archive.writestr('docProps/core.xml', core)
        return path

    def test_headings_and_paragraphs_are_read(self):
        """标题与正文分别读出，标题进目录。"""
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_docx(
                Path(temp) / 'a.docx',
                '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>正文内容。</w:t></w:r></w:p>')
            book = docx_input.read(path)
        self.assertEqual(book.title, '测试书名')
        self.assertEqual(book.author, '测试作者')
        self.assertEqual([t.title for t in book.toc], ['第一章'])
        self.assertIn('正文内容。', book.documents[0].content)

    def test_chinese_heading_style_is_understood(self):
        """中文 Word 的「标题 1」样式也要认出来。"""
        import tempfile
        styles = ('<?xml version="1.0" encoding="UTF-8"?>'
                  '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:style w:type="paragraph" w:styleId="1"><w:name w:val="标题 1"/></w:style>'
                  '</w:styles>')
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_docx(
                Path(temp) / 'a.docx',
                '<w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:r><w:t>章节</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>内容。</w:t></w:r></w:p>', styles=styles)
            book = docx_input.read(path)
        self.assertEqual([t.title for t in book.toc], ['章节'])

    def test_file_without_document_xml_is_refused(self):
        """不是 Word 文档的文件要明确报错。"""
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('hello.txt', 'not a word document')
            with self.assertRaises(ValueError) as caught:
                docx_input.read(path)
        self.assertIn('document.xml', str(caught.exception))

    def test_list_items_become_lists(self):
        """带编号的段落渲染成列表。"""
        import tempfile
        body = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>'
                '<w:r><w:t>列表项一</w:t></w:r></w:p>'
                '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/></w:numPr></w:pPr>'
                '<w:r><w:t>子项</w:t></w:r></w:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_docx(Path(temp) / 'a.docx', body)
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('<ul>', content)
        self.assertIn('<li>列表项一</li>', content)
        self.assertIn('<li>子项</li>', content)

    def test_strict_namespace_is_understood(self):
        """Word 存成 Strict 命名空间时也要读得出标题和正文。"""
        import tempfile
        strict = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="%s"><w:body>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                    '<w:r><w:t>第一章</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>正文内容。</w:t></w:r></w:p>'
                    '</w:body></w:document>' % strict)
        styles = ('<?xml version="1.0" encoding="UTF-8"?>'
                  '<w:styles xmlns:w="%s">'
                  '<w:style w:type="paragraph" w:styleId="Heading1">'
                  '<w:name w:val="heading 1"/></w:style></w:styles>' % strict)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr('_rels/.rels', '<Relationships/>')
                archive.writestr('word/document.xml', document)
                archive.writestr('word/styles.xml', styles)
            book = docx_input.read(path)
        self.assertEqual([t.title for t in book.toc], ['第一章'])
        self.assertIn('正文内容。', book.documents[0].content)

    def test_tracked_deletions_are_skipped(self):
        """修订里删掉的文字不当作正文。"""
        import tempfile
        body = ('<w:p><w:r><w:t>保留的文字</w:t></w:r>'
                '<w:del><w:r><w:delText>删掉的文字</w:delText></w:r></w:del></w:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_docx(Path(temp) / 'a.docx', body)
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('保留的文字', content)
        self.assertNotIn('删掉的文字', content)

    def test_pictures_are_read_with_their_bytes(self):
        """正文里的图片按出现位置内嵌，并且真的带上图片数据。"""
        import tempfile
        body = ('<w:p><w:r><w:t>图前</w:t></w:r>'
                '<w:r><w:drawing><a:graphic><a:graphicData>'
                '<a:blip r:embed="rId5"/></a:graphicData></a:graphic></w:drawing></w:r>'
                '<w:r><w:t>图后</w:t></w:r></w:p>')
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
                    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
                    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<w:body>' + body + '</w:body></w:document>')
        relationships = ('<?xml version="1.0"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId5" Type="image" Target="media/image1.png"/>'
                         '</Relationships>')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'img.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', document)
                archive.writestr('word/_rels/document.xml.rels', relationships)
                archive.writestr('word/media/image1.png', b'\x89PNG\r\n\x1a\nDATA')
                archive.writestr('docProps/core.xml',
                                 '<?xml version="1.0"?><cp:coreProperties'
                                 ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
                                 ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
                                 '<dc:title>书名</dc:title></cp:coreProperties>')
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('图前<img src="media/image1.png" alt=""/>图后', content)
        self.assertEqual(list(book.images), ['media/image1.png'])
        self.assertEqual(book.images['media/image1.png'].data, b'\x89PNG\r\n\x1a\nDATA')
        self.assertEqual(book.images['media/image1.png'].media_type, 'image/png')

    def test_missing_picture_is_left_out(self):
        """关系指向的图片不在包里时不能写成空图。"""
        import tempfile
        body = ('<w:p><w:r><w:drawing><a:graphic><a:graphicData>'
                '<a:blip r:embed="rId9"/></a:graphicData></a:graphic></w:drawing></w:r></w:p>')
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
                    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
                    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<w:body>' + body + '</w:body></w:document>')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'img.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', document)
            book = docx_input.read(path)
        self.assertEqual(book.images, {})
        self.assertNotIn('<img', book.documents[0].content)


class OdtInputTests(unittest.TestCase):
    """ODT 读入。"""

    def make_odt(self, path, content=None, meta=None):
        if content is None:
            content = ('<?xml version="1.0" encoding="UTF-8"?>'
                       '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                       ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                       '<office:body><office:text>'
                       '<text:h text:outline-level="1">第一章</text:h>'
                       '<text:p>正文段落。</text:p>'
                       '<text:h text:outline-level="1">第二章</text:h>'
                       '<text:p>另一段。</text:p>'
                       '</office:text></office:body></office:document-content>')
        if meta is None:
            meta = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<office:meta><dc:title>ODT 书名</dc:title><dc:creator>ODT 作者</dc:creator>'
                    '</office:meta></office:document-meta>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text',
                             compress_type=zipfile.ZIP_STORED)
            archive.writestr('META-INF/manifest.xml', '<manifest/>')
            archive.writestr('content.xml', content)
            archive.writestr('meta.xml', meta)
        return path

    def test_headings_and_metadata(self):
        """标题层级与元数据都读得出来。"""
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_odt(Path(temp) / 'a.odt')
            book = odt_input.read(path)
        self.assertEqual(book.title, 'ODT 书名')
        self.assertEqual(book.author, 'ODT 作者')
        self.assertEqual([t.title for t in book.toc], ['第一章', '第二章'])
        self.assertIn('正文段落。', book.documents[0].content)

    def test_space_count_is_preserved(self):
        """text:s 表示的空格数要保留下来。"""
        import tempfile
        content = ('<?xml version="1.0" encoding="UTF-8"?>'
                   '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                   ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                   '<office:body><office:text>'
                   '<text:h text:outline-level="1">标题</text:h>'
                   '<text:p>前<text:s text:c="3"/>后</text:p>'
                   '</office:text></office:body></office:document-content>')
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_odt(Path(temp) / 'a.odt', content=content)
            book = odt_input.read(path)
        self.assertIn('前   后', book.documents[0].content)


class HtmlInputTests(unittest.TestCase):
    """HTML 读入。"""

    def test_three_level_toc_anchors_are_single_fragments(self):
        """三级标题的目录链接只能有一个片段。

        子项的 href 若在父项 href 后面继续拼锚点，会得到
        x.xhtml#s0002#s0003 这种坏链接，EPUB 校验直接判悬空。
        """
        book = html_input.read_text(
            '<html><body><h1>一</h1><p>a</p><h2>二</h2><p>b</p>'
            '<h3>三</h3><p>c</p></body></html>')
        links = [entry.href for top in book.toc for entry in top.walk() if entry.href]
        for link in links:
            self.assertLessEqual(link.count('#'), 1, '坏链接：' + link)
        self.assertIn('ch0001.xhtml#s0003', links)

    def test_headings_become_chapters(self):
        """标题标签切分章节，标题文字原样保留。

        二级标题是子章节，归在上一级标题下，不单独成篇。
        """
        book = html_input.read_text(
            '<html><head><title>网页书名</title></head><body>'
            '<h1>第一章 开始</h1><p>第一段。</p>'
            '<h2>1.1 小节</h2><p>小节内容。</p>'
            '<h1>第二章 结束</h1><p>末段。</p>'
            '</body></html>')
        self.assertEqual(book.title, '网页书名')
        self.assertEqual([t.title for t in book.toc], ['第一章 开始', '第二章 结束'])
        self.assertEqual([c.title for c in book.toc[0].children], ['1.1 小节'])
        self.assertIn('小节内容。', book.documents[0].content)
        self.assertIn('<h1>第一章 开始</h1>', book.documents[0].content)

    def test_script_content_is_ignored(self):
        """脚本内容不进入正文。"""
        book = html_input.read_text(
            '<html><body><h1>标题</h1><script>var x = "不应出现";</script><p>正文。</p></body></html>')
        self.assertNotIn('不应出现', book.documents[0].content)

    def test_chapter_class_container_is_a_chapter(self):
        """class="chapter" 的容器本身算一章，内容归在这一章里。"""
        book = html_input.read_text(
            '<html><body>'
            '<div class="chapter"><h2>第一章</h2><p>正文一。</p></div>'
            '<div class="chapter"><h2>第二章</h2><p>正文二。</p></div>'
            '</body></html>')
        self.assertEqual([t.title for t in book.toc_entries()], ['第一章', '第二章'])
        first = book.documents[0].content
        self.assertIn('正文一。', first)
        self.assertNotIn('正文二。', first)
        self.assertIn('正文二。', book.documents[1].content)


class EpubRoundTripTests(unittest.TestCase):
    """EPUB 读入再写出，链接要还对得上。

    真实电子书里文档常放在子目录、扩展名用 .html，
    目录里的链接是相对于目录文档的路径，这些都要接得住。
    """

    def make_epub(self, path):
        container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                     ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
                     ' media-type="application/oebps-package+xml"/></rootfiles></container>')
        package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
                   ' unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="book-id">urn:uuid:1</dc:identifier><dc:title>测试</dc:title>'
                   '<dc:creator>作者</dc:creator><dc:language>zh-CN</dc:language></metadata>'
                   '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                   '<item id="s1" href="Text/Section001.html" media-type="application/xhtml+xml"/>'
                   '<item id="img" href="Images/cover.jpg" media-type="image/jpeg"/>'
                   '</manifest><spine><itemref idref="s1"/></spine></package>')
        nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"'
               ' xmlns:epub="http://www.idpf.org/2007/ops"><body>'
               '<nav epub:type="toc"><ol><li><a href="Text/Section001.html">第一章</a></li></ol></nav>'
               '</body></html>')
        section = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
                   '<h1>第一章</h1><p>正文内容。</p>'
                   '<img src="../Images/cover.jpg" alt=""/></body></html>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            archive.writestr('META-INF/container.xml', container)
            archive.writestr('OEBPS/content.opf', package)
            archive.writestr('OEBPS/nav.xhtml', nav)
            archive.writestr('OEBPS/Text/Section001.html', section)
            archive.writestr('OEBPS/Images/cover.jpg', b'\xff\xd8\xff\xe0 fake jpeg')
        return path

    def test_links_survive_the_round_trip(self):
        """写出的 EPUB 里目录和图片链接都还要指得到东西。"""
        import tempfile
        from workshop import epub as epub_module
        from workshop.convert import epub_output

        with tempfile.TemporaryDirectory() as temp:
            source = self.make_epub(Path(temp) / 'a.epub')
            book = epub_input.read(source)
            target = Path(temp) / 'b.epub'
            epub_output.write(book, target)
            files, opf, package = epub_module.load(target)
            epub_module.validate_files(files)
            names = set(files)
            # 目录链接要能对应到实际存在的文档。
            nav = files['OEBPS/nav.xhtml'].decode('utf-8')
            self.assertIn('Text/Section001.html', nav)
            self.assertIn('OEBPS/Text/Section001.html', names)
            # 图片要跟着文档一起搬过去。
            self.assertIn('OEBPS/Images/cover.jpg', names)


def read_text_record(record):
    """按读端的规则解出一条文本记录。

    读端剥掉尾部 (计数字节 & 3) + 1 个字节；用错规则就测不出字节级缺陷。
    """
    trail = (record[-1] & 0x3) + 1
    return decompress(record[:len(record) - trail])


def exth_codes(record0):
    """遍历记录 0 里的 EXTH，返回条目号。

    EXTH 的位置是 16 + 头长，两个分支的头长不同（MOBI 6 是 232、KF8 是 264），
    所以要按头里的字段算，不能写死偏移。
    """
    header_length = struct.unpack('>I', record0[20:24])[0]
    start = 16 + header_length
    length = struct.unpack('>I', record0[start + 4:start + 8])[0]
    position = start + 12
    codes = []
    while position + 8 <= start + length:
        code, size = struct.unpack('>II', record0[position:position + 8])
        codes.append(code)
        position += size
    return codes


def split_records(data):
    """把 MOBI/AZW3 文件拆成记录列表。"""
    count = struct.unpack('>H', data[76:78])[0]
    offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
    return [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
            for i in range(count)]


class RenamedDocumentLinkTests(unittest.TestCase):
    """文档改名后，别的文档里指向它的链接要跟着改。"""

    def test_links_to_a_renamed_document_are_retargeted(self):
        """扩展名不合规的文档会被改名，正文里的链接要指向新名字。

        以前只改目录项，正文里的 <a> 还指着旧文件名，
        写出时的校验直接报「Broken resource reference」，转换失败。
        """
        container = ('<?xml version="1.0"?><container '
                     'xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                     '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                     'media-type="application/oebps-package+xml"/></rootfiles></container>')
        package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">x</dc:identifier><dc:title>书</dc:title>'
                   '<dc:language>zh</dc:language></metadata><manifest>'
                   '<item id="c1" href="chapter.one.txt" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="other.txt" media-type="application/xhtml+xml"/>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                   'properties="nav"/></manifest><spine><itemref idref="c1"/>'
                   '<itemref idref="c2"/></spine></package>')
        first = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                 '<head><title>章</title></head><body>'
                 '<p><a href="other.txt">到下一章</a></p></body></html>')
        second = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                  '<head><title>二</title></head><body><p>第二章</p></body></html>')
        nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
               'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>'
               '<body><nav epub:type="toc"><ol><li><a href="chapter.one.txt">一</a></li>'
               '<li><a href="other.txt">二</a></li></ol></nav></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.epub'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', b'application/epub+zip',
                                 compress_type=zipfile.ZIP_STORED)
                archive.writestr('META-INF/container.xml', container.encode())
                archive.writestr('OEBPS/content.opf', package.encode())
                archive.writestr('OEBPS/chapter.one.txt', first.encode())
                archive.writestr('OEBPS/other.txt', second.encode())
                archive.writestr('OEBPS/nav.xhtml', nav.encode())
            book = epub_input.read(source)
            target = Path(temp) / 'o.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                chapter = archive.read('OEBPS/c0001.xhtml').decode()
                directory = archive.read('OEBPS/nav.xhtml').decode()
        # 被改名的文档换了名字
        self.assertIn('OEBPS/c0002.xhtml', names)
        # 正文里的链接指向新名字
        self.assertIn('href="c0002.xhtml"', chapter)
        self.assertNotIn('other.txt', chapter)
        # 目录也一样
        self.assertNotIn('other.txt', directory)


class MobiFormatTests(unittest.TestCase):
    """MOBI 格式的几处硬性约定，错了会让阅读器读不出来。"""

    def build(self):
        book = book_module.Book(title='测试书名', author='测试作者', language='zh-CN')
        book.add_document(book_module.Document(
            'ch0001.xhtml',
            content='<html><body><h1>第一章</h1><p>正文内容。</p></body></html>'))
        book.toc = [book_module.TocEntry('第一章', 'ch0001.xhtml')]
        return book

    def records(self, data):
        return split_records(data)

    def test_every_text_record_is_compressed(self):
        """头里声明了压缩方式，每条文本记录都得压缩，不能有的压有的不压。

        文本记录的范围按头里的 last_text_record / first_non_text_record 判定，
        不能靠「记录里有没有 <html>」猜——多记录的书每条都不含 <html>。
        """
        book = self.build()
        # 多记录：正文要够长，切出好几条
        book.documents[0].content = (
            '<html><body><h1>第一章</h1><p>' + '正文内容。' * 3000 + '</p></body></html>')
        data = mobi_writer.build_mobi(book)
        records = self.records(data)
        head = records[0]
        last_text = struct.unpack('>H', head[8:10])[0]
        first_non_text = struct.unpack('>I', head[80:84])[0]
        self.assertGreater(last_text, 1, '这份正文应该切出多条文本记录')
        for index in range(1, last_text + 1):
            record = records[index]
            try:
                decoded = read_text_record(record).decode('utf-8')
            except UnicodeDecodeError:
                self.fail('第 %d 条文本记录不是有效的 PalmDOC 压缩数据' % index)
            self.assertTrue(decoded.strip(), '第 %d 条文本记录解出来是空的' % index)
        # 文本记录之后的都是索引与资源，不是正文
        for index in range(last_text + 1, first_non_text):
            record = records[index]
            self.assertNotIn(b'<html>', record[:64],
                             '第 %d 条不是文本记录却含正文' % index)

    def test_azw3_has_no_boundary_record(self):
        """AZW3 是纯 KF8 standalone，不写 BOUNDARY，也不写 EXTH 121。"""
        data = mobi_writer.build_mobi(self.build(), kf8=True)
        records = self.records(data)
        self.assertFalse(any(r[:8] == b'BOUNDARY' for r in records),
                         'standalone 不应有 BOUNDARY')
        codes = exth_codes(records[0])
        self.assertNotIn(121, codes, 'standalone 不应写 EXTH 121')

    def test_azw3_record0_is_a_kf8_header(self):
        """standalone 的记录 0 就是 KF8 头，文件版本写 8、头长写 264。"""
        data = mobi_writer.build_mobi(self.build(), kf8=True)
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(record0[16:20], b'MOBI')
        self.assertEqual(struct.unpack('>I', record0[20:24])[0], 264)
        self.assertEqual(struct.unpack('>I', record0[36:40])[0], 8)
        self.assertEqual(struct.unpack('>I', record0[104:108])[0], 8)

    def test_azw3_exth_is_at_280(self):
        """KF8 头长 264，EXTH 按 16 + 头长 定位在 280。"""
        data = mobi_writer.build_mobi(self.build(), kf8=True)
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(record0[280:284], b'EXTH')

    def test_image_records_have_no_magic_prefix(self):
        """图片记录就是图片本身，读端靠嗅探格式识别。"""
        from workshop.convert import book as book_module
        book = self.build()
        book.add_image(book_module.Image('cover.jpg', b'\xff\xd8\xff\xe0 jpegdata',
                                        'image/jpeg'))
        book.cover = 'cover.jpg'
        data = mobi_writer.build_mobi(book)
        records = self.records(data)
        images = [r for r in records if r[:3] == b'\xff\xd8\xff']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0], b'\xff\xd8\xff\xe0 jpegdata')

    def test_base32_uses_uppercase(self):
        """base32 编号用大写，读端的正则大小写敏感。"""
        from workshop.convert.mobi import to_base
        self.assertEqual(to_base(41, 32, 4), '0019')
        self.assertTrue(to_base(41, 32, 4).isupper() or to_base(41, 32, 4).isdigit())


class MobiWriterTests(unittest.TestCase):
    """MOBI 写出。"""

    def build(self):
        book = book_module.Book(title='测试书名', author='测试作者', language='zh-CN')
        book.add_document(book_module.Document(
            'ch0001.xhtml',
            content='<html><body><h1>第一章</h1><p>正文内容，重复的文字。重复的文字。</p></body></html>'))
        book.add_document(book_module.Document(
            'ch0002.xhtml',
            content='<html><body><h1>第二章</h1><p>更多内容。</p></body></html>'))
        book.toc = [book_module.TocEntry('第一章', 'ch0001.xhtml'),
                    book_module.TocEntry('第二章', 'ch0002.xhtml')]
        return book

    def test_pdb_header_is_well_formed(self):
        """PalmDB 头里的标识、记录数与索引表要正确。"""
        data = mobi_writer.build_mobi(self.build())
        self.assertEqual(data[60:68], b'BOOKMOBI')
        count = struct.unpack('>H', data[76:78])[0]
        offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
        self.assertEqual(offsets, sorted(offsets))
        for index, offset in enumerate(offsets):
            self.assertLessEqual(offset, len(data))

    def test_mobi_header_fields(self):
        """MOBI 头的关键字段按规范填写。"""
        data = mobi_writer.build_mobi(self.build())
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(record0[16:20], b'MOBI')
        self.assertEqual(struct.unpack('>I', record0[20:24])[0], 232)
        self.assertEqual(struct.unpack('>H', record0[0:2])[0], 2)     # PalmDOC 压缩
        self.assertEqual(struct.unpack('>I', record0[28:32])[0], 65001)  # UTF-8
        # locale 字段：2 字节恒为 0，接着子语言码和语言码。中文简体是 00 00 08 04。
        self.assertEqual(record0[92:96], b'\x00\x00\x08\x04')

    def test_language_code_follows_the_spec(self):
        """语言码按 MOBI 规范写，不是直接抄 ISO 码。"""
        from workshop.convert.languages import locale_code
        self.assertEqual(locale_code('zh-CN'), b'\x00\x00\x08\x04')
        self.assertEqual(locale_code('zh'), b'\x00\x00\x00\x04')
        self.assertEqual(locale_code('en-US'), b'\x00\x00\x04\x09')
        self.assertEqual(locale_code('ja'), b'\x00\x00\x00\x11')

    def test_record0_is_not_padded_twice(self):
        """记录 0 的尾部预留只加一次，不能多出 8KB 垃圾。"""
        data = mobi_writer.build_mobi(self.build())
        count = struct.unpack('>H', data[76:78])[0]
        offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
        length = offsets[1] - offsets[0]
        self.assertLess(length, 10000, '记录 0 长度异常')
        self.assertEqual(length % 4, 0, '记录 0 应按 4 字节对齐')

    def test_exth_carries_metadata(self):
        """EXTH 里能找到书名与作者。"""
        data = mobi_writer.build_mobi(self.build())
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        # 阅读器按 16 + 头长 定位 EXTH，MOBI 6 的头长是 232。
        start = 16 + struct.unpack('>I', record0[20:24])[0]
        self.assertEqual(record0[start:start + 4], b'EXTH')
        length, count = struct.unpack('>II', record0[start + 4:start + 12])
        entries = {}
        position = start + 12
        end = start + length
        while position + 8 <= end:
            code, size = struct.unpack('>II', record0[position:position + 8])
            raw = record0[position + 8:position + size]
            try:
                entries[code] = raw.decode('utf-8')
            except UnicodeDecodeError:
                entries[code] = raw      # 数值型条目按字节保存
            position += size
        self.assertEqual(position, end, 'EXTH 长度字段与实际内容不一致')
        self.assertEqual(entries[100], '测试作者')

    def test_exth_entries_are_found_in_both_header_shapes(self):
        """MOBI 6 与 KF8 的头长不同，但 EXTH 的遍历规则要一致。

        两种头都要能在同样的逻辑下找到书名与作者。
        """
        for kf8 in (False, True):
            data = mobi_writer.build_mobi(self.build(), kf8=kf8)
            record0 = data[struct.unpack('>L', data[78:82])[0]:]
            start = 16 + struct.unpack('>I', record0[20:24])[0]
            self.assertEqual(record0[start:start + 4], b'EXTH', 'kf8=%s 没找到 EXTH' % kf8)
            length = struct.unpack('>I', record0[start + 4:start + 8])[0]
            position = start + 12
            entries = {}
            while position + 8 <= start + length:
                code, size = struct.unpack('>II', record0[position:position + 8])
                entries[code] = record0[position + 8:position + size].decode('utf-8', 'replace')
                position += size
            self.assertEqual(entries.get(100), '测试作者', 'kf8=%s 的作者条目不对' % kf8)
            self.assertIn(503, entries, 'kf8=%s 缺书名条目' % kf8)
        self.assertEqual(entries[503], '测试书名')

    def text_of(self, data):
        """按读端的方式把正文记录解压拼回去。"""
        count = struct.unpack('>H', data[76:78])[0]
        offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
        records = [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
                   for i in range(count)]
        last = struct.unpack('>H', records[0][8:10])[0]
        parts = []
        for record in records[1:last + 1]:
            # 读端剥掉 (尾部计数字节 & 3) + 1 个字节。
            parts.append(decompress(record[:len(record) - ((record[-1] & 3) + 1)]))
        return b''.join(parts)

    def test_internal_links_do_not_corrupt_the_text(self):
        """跨文档链接回填的是整条文本流里的绝对位置。

        按片段内位置回填会写错地方，把后面的标签整段盖掉。
        """
        book = self.build()
        book.documents[0].content = (
            '<html><body><h1>第一章</h1><p><a href="ch0002.xhtml">跳到第二章</a>后面的文字。</p>'
            '</body></html>')
        stream = self.text_of(mobi_writer.build_mobi(book))
        stream.decode('utf-8')            # 必须是合法的 UTF-8
        self.assertEqual(stream.count(b'<mbp:pagebreak/>'), 2)
        for target in re.findall(rb'filepos=(\d{10})', stream):
            self.assertLess(int(target), len(stream))
        self.assertIn(b'<h1>\xe7\xac\xac\xe4\xba\x8c\xe7\xab\xa0</h1>', stream)

    def test_text_records_never_claim_four_overlap_bytes(self):
        """重叠计数字节只有 2 位，不能写 4（读端会算成 1）。"""
        data = mobi_writer.build_mobi(self.build())
        count = struct.unpack('>H', data[76:78])[0]
        offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
        records = [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
                   for i in range(count)]
        last = struct.unpack('>H', records[0][8:10])[0]
        for record in records[1:last + 1]:
            self.assertLessEqual(record[-1] & 0x3, 3)

    def test_text_is_recoverable(self):
        """文本记录按读端规则解压后，能逐字节还原出正文流。"""
        from workshop.convert.serializer import Serializer
        book = self.build()
        data = mobi_writer.build_mobi(book)
        records = split_records(data)
        head = records[0]
        last = struct.unpack('>H', head[8:10])[0]
        rebuilt = b''.join(read_text_record(record) for record in records[1:last + 1])
        documents = [{'href': document.href, 'body': document.content}
                     for document in book.linear_documents()]
        source = Serializer(documents, images=book.images, toc=book.toc)()
        self.assertEqual(rebuilt, source, '解压还原的文本流与源文本不一致')

    def test_guide_has_one_toc_reference(self):
        """guide 里只能有一个 toc 引用，否则设备会显示错乱。"""
        data = mobi_writer.build_mobi(self.build())
        count = struct.unpack('>H', data[76:78])[0]
        offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
        record = data[offsets[1]:offsets[2]]
        text = read_text_record(record).decode('utf-8')
        self.assertEqual(text.count('type="toc"'), 1)

    def test_ncx_is_not_duplicated(self):
        """NCX 目录只写一份。"""
        data = mobi_writer.build_mobi(self.build())
        self.assertEqual(data.count(b'<ncx '), 1)


class Azw3WriterTests(unittest.TestCase):
    """AZW3（纯 KF8 standalone）写出。"""

    def records(self, data):
        return split_records(data)

    def build(self):
        book = book_module.Book(title='测试书名', author='测试作者', language='zh-CN')
        book.add_document(book_module.Document(
            'ch0001.xhtml',
            content='<html><body><h1>第一章</h1><p>正文内容。</p></body></html>'))
        book.toc = [book_module.TocEntry('第一章', 'ch0001.xhtml')]
        return book

    def first_record(self, data):
        return self.records(data)[0]

    def test_standalone_has_no_boundary_and_ends_with_eof(self):
        """纯 KF8 文件没有 BOUNDARY，EOF 是最后一条记录。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        tags = [r[:8] for r in records]
        self.assertNotIn(b'BOUNDARY', tags)
        self.assertEqual(records[-1][:4], b'\xe9\x8e\r\n')

    def test_record0_is_the_only_kf8_header(self):
        """standalone 里只有一份 KF8 头，就是记录 0。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        headers = [r for r in records if r[16:20] == b'MOBI']
        self.assertEqual(len(headers), 1)
        self.assertIs(headers[0], records[0])
        self.assertEqual(struct.unpack('>I', records[0][20:24])[0], 264)

    def test_index_numbers_point_at_their_index_headers(self):
        """chunk / skel / ncx 索引号指向各自头记录，紧跟索引记录。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        for name, offset in (('chunk', 248), ('skel', 252), ('ncx', 244)):
            index = struct.unpack('>I', head[offset:offset + 4])[0]
            self.assertLess(index, len(records), name)
            self.assertEqual(records[index][:4], b'INDX', name)
            self.assertGreaterEqual(records[index].find(b'TAGX'), 0, name)
            self.assertEqual(records[index + 1][:4], b'INDX', name)
            self.assertEqual(struct.unpack('>I', records[index + 1][12:16])[0], 1, name)

    def test_standalone_exth_flags_match_calibre(self):
        """纯 KF8 的 EXTH 标志位是 0b1010000，与 Calibre 一致。"""
        data = mobi_writer.build_mobi(self.build(), kf8=True)
        flags = struct.unpack('>I', self.first_record(data)[128:132])[0]
        self.assertEqual(flags, 0b1010000)

    def test_plain_mobi_has_same_exth_flags(self):
        """纯 MOBI 6 的标志位也是 0b1010000。"""
        data = mobi_writer.build_mobi(self.build())
        flags = struct.unpack('>I', self.first_record(data)[128:132])[0]
        self.assertEqual(flags, 0b1010000)

    def test_kf8_index_slots_are_in_reader_order(self):
        """244 起是五个索引槽：ncx、chunk、skel、datp、guide。

        读端用 >4L 从 0xF8 一次读四个（chunk/skel/datp/guide）。
        把 guide 写到 datp 的位置上，真正的 guide 槽就是 0，
        读端会以为有 guide 索引，去解析 record 0 然后崩。
        """
        head = self.first_record(mobi_writer.build_mobi(self.build(), kf8=True))
        ncx, chunk, skel = struct.unpack_from('>3L', head, 244)
        datp, guide = struct.unpack_from('>2L', head, 256)
        self.assertNotEqual(chunk, 0xFFFFFFFF, 'chunk 槽不能是 NULL')
        self.assertNotEqual(skel, 0xFFFFFFFF, 'skel 槽不能是 NULL')
        self.assertNotEqual(ncx, 0xFFFFFFFF, 'ncx 槽不能是 NULL')
        # datp 未使用，必须是 NULL；guide 我们没有生成，也必须是 NULL。
        self.assertEqual(datp, 0xFFFFFFFF, 'datp 槽必须留 NULL')
        self.assertEqual(guide, 0xFFFFFFFF, 'guide 槽必须留 NULL')

    def test_chunk_index_carries_a_cncx_pool(self):
        """chunk 索引的 tag 2 是 CNCX 池偏移，池不能是空的。

        读端会做 cncx[tag_map[2][0]]，池为空就拿到 None 然后崩。
        """
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        chunk_index = struct.unpack('>I', head[248:252])[0]
        header = records[chunk_index]
        count = struct.unpack('>I', header[24:28])[0]
        num_cncx = struct.unpack('>I', header[52:56])[0]
        self.assertEqual(num_cncx, 1, 'chunk 索引必须带一个 CNCX 记录')
        pool = records[chunk_index + count + 1]
        self.assertGreater(len(pool), 0, 'CNCX 池不能为空')
        # 池里第一条是 <变长长度><UTF-8 选择器>。
        length, consumed = decode_number(pool)
        self.assertGreater(length, 0)
        self.assertTrue(pool[consumed:consumed + length].decode('utf-8').isdigit())

    def test_mobi6_index_offset_counts_padding(self):
        """MOBI 6 索引记录里的 IDXT 偏移要按对齐后的长度算。

        条目流补到 4 的倍数，少算那几个填充字节，
        读端就会在错位处找 IDXT，目录整条读不出。
        """
        for book in (self.build(), self.build_with_nine_entries()):
            data = mobi_writer.build_mobi(book)
            count = struct.unpack('>H', data[76:78])[0]
            offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
            records = [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
                       for i in range(count)]
            head = records[0]
            index = struct.unpack('>I', head[244:248])[0]
            index_record = records[index + 1]
            idxt_at = struct.unpack('>I', index_record[20:24])[0]
            self.assertEqual(index_record[idxt_at:idxt_at + 4], b'IDXT',
                             'IDXT 偏移指错了位置')

    def build_with_nine_entries(self):
        """九条目录项：条目流长度会落在需要对齐的边界上。"""
        book = book_module.Book(title='书名', author='作者', language='zh-CN')
        for index in range(9):
            href = 'c%d.xhtml' % index
            book.add_document(book_module.Document(
                href, content='<html><body><h1>第%d章</h1><p>正文</p></body></html>' % index))
            book.toc.append(book_module.TocEntry('第%d章' % index, href))
        return book

    def test_kf8_unknown_fields_follow_the_spec(self):
        """KF8 头 264 到 280 的四个未知字段取值是 0xFF 0x00 0xFF 0x00。"""
        head = self.first_record(mobi_writer.build_mobi(self.build(), kf8=True))
        self.assertEqual(head[264:280].hex(), 'ffffffff00000000ffffffff00000000')

    def test_kf8_index_has_a_header_and_an_index_record(self):
        """KF8 索引是两条记录：头记录和索引记录。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        chunk_index = struct.unpack('>I', head[248:252])[0]
        header, index_record = records[chunk_index], records[chunk_index + 1]
        self.assertEqual(header[:4], b'INDX')
        self.assertEqual(index_record[:4], b'INDX')
        self.assertEqual(struct.unpack('>I', header[12:16])[0], 0)
        self.assertEqual(struct.unpack('>I', index_record[12:16])[0], 1)

    def test_index_control_bytes_encode_value_groups(self):
        """控制字节里每个标签写的是「值组数」，不是整段掩码。

        写成整段掩码会让阅读器以为后面跟着一个字节长度，
        从下一个标签的控制字节开始读，整条索引流错位。
        """
        from workshop.convert.mobi import VALUES_PER_ENTRY
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        for name, offset in (('chunk', 248), ('skel', 252), ('ncx', 244)):
            index = struct.unpack('>I', head[offset:offset + 4])[0]
            index_header = records[index]
            tagx_at = index_header.find(b'TAGX')
            tagx_length = struct.unpack('>I', index_header[tagx_at + 4:tagx_at + 8])[0]
            index_record = records[index + 1]
            body = index_record[192:]
            label_length = body[0]
            control = body[1 + label_length]
            # 反向解码：每个标签读出组数，再按组数推算值个数。
            total_values = 0
            for pos in range(tagx_at + 12, tagx_at + tagx_length, 4):
                tag, values, mask, end = index_header[pos:pos + 4]
                if not tag or not mask:
                    continue
                shift = (mask & -mask).bit_length() - 1
                field = (control & mask) >> shift
                if tag not in (21, 22, 23):
                    # 层级字段可以没有（顶层项没有父项）。
                    self.assertGreater(field, 0, '%s 索引漏了标签 %d' % (name, tag))
                if not field:
                    continue
                per_entry = VALUES_PER_ENTRY.get(tag, 1)
                if bin(mask).count('1') > 1:
                    # 多比特段置满会被读成「后面跟一个字节长度」。
                    self.assertLess(field, mask >> shift,
                                    '%s 索引的标签 %d 写成整段掩码了' % (name, tag))
                total_values += field * per_entry
            self.assertGreater(total_values, 0, '%s 索引读不出值' % name)

    def test_ncx_titles_live_in_a_cncx_record(self):
        """目录标题放在 CNCX 池里，读者按 头记录+count+1 定位那条记录。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        index = struct.unpack('>I', head[244:248])[0]
        index_header = records[index]
        count = struct.unpack('>I', index_header[24:28])[0]
        num_cncx = struct.unpack('>I', index_header[52:56])[0]
        self.assertEqual(num_cncx, 1)
        cncx = records[index + count + 1]
        # CNCX 每条是 <encint 长度><UTF-8 文字>。
        self.assertGreater(len(cncx), 0)
        length = cncx[0] & 0x7F
        self.assertIn('第一章', cncx[1:1 + length].decode('utf-8'))

    def decode_index(self, header_record, index_record):
        """把索引解码成 [(标签, {标签号: [值]})]。"""
        tags = []
        tagx_at = header_record.find(b'TAGX')
        tagx_length = struct.unpack('>I', header_record[tagx_at + 4:tagx_at + 8])[0]
        for position in range(tagx_at + 12, tagx_at + tagx_length, 4):
            tag, values, mask, end = header_record[position:position + 4]
            if tag:
                tags.append((tag, values, mask))
        body = index_record[192:]
        entries = []
        position = 0
        # 条目流后面还跟着对齐填充和 IDXT，按条目数读完就停。
        count = struct.unpack('>I', header_record[36:40])[0]
        while position < len(body) and len(entries) < count:
            size = body[position]
            position += 1
            label = body[position:position + size].decode('utf-8')
            position += size
            control = body[position]
            position += 1
            values = {}
            for tag, count_per_entry, mask in tags:
                if not mask:
                    continue
                shift = (mask & -mask).bit_length() - 1
                groups = (control & mask) >> shift
                for _ in range(groups * count_per_entry):
                    value, consumed = decode_number(body[position:])
                    position += consumed
                    values.setdefault(tag, []).append(value)
            entries.append((label, values))
        return entries

    def test_nested_toc_is_written_in_full(self):
        """子目录项不能丢：MOBI/AZW3 的目录要包含整棵树的全部条目。"""
        book = book_module.Book(title='书名', author='作者', language='zh-CN')
        book.add_document(book_module.Document(
            'c1.xhtml', content='<html><body><h1>部</h1>'
                                '<h2 id="s0002">第一章</h2><p>正文。</p>'
                                '<h2 id="s0003">第二章</h2><p>更多。</p></body></html>'))
        book.toc = [book_module.TocEntry('第一部', 'c1.xhtml', children=[
            book_module.TocEntry('第一章', 'c1.xhtml#s0002'),
            book_module.TocEntry('第二章', 'c1.xhtml#s0003'),
        ])]
        records = self.records(mobi_writer.build_mobi(book, kf8=True))
        head = records[0]
        index = struct.unpack('>I', head[244:248])[0]
        entries = self.decode_index(records[index], records[index + 1])
        self.assertEqual([label for label, _ in entries], ['00', '01', '02'])
        # 排序后父项在前，两个子项在第 1 层，都指回父项（序号 0）。
        self.assertEqual(entries[0][1][4], [0])     # 顶层 depth 写 0
        self.assertEqual(entries[1][1][4], [1])
        self.assertEqual(entries[2][1][4], [1])
        self.assertEqual(entries[1][1][21], [0])
        self.assertEqual(entries[2][1][21], [0])
        self.assertEqual(entries[0][1][22], [1])    # first_child
        self.assertEqual(entries[0][1][23], [2])    # last_child
        # 父项长度盖住整章，子项各自到下一项为止。
        self.assertGreater(entries[0][1][2][0], entries[1][1][2][0])

    def test_ncx_index_header_fields_match_the_spec(self):
        """INDX 头记录的字段偏移按 MOBI 规范，错位会让读者找不到 IDXT。"""
        records = self.records(mobi_writer.build_mobi(self.build(), kf8=True))
        head = records[0]
        ncx_index = struct.unpack('>I', head[244:248])[0]
        index_header = records[ncx_index]
        read_u32 = lambda off: struct.unpack('>I', index_header[off:off + 4])[0]
        self.assertEqual(read_u32(4), 192, '头长')
        self.assertEqual(read_u32(16), 2, '索引类型')
        self.assertEqual(read_u32(24), 1, '索引记录数')
        self.assertEqual(read_u32(28), 65001, 'UTF-8 编码')
        self.assertEqual(read_u32(36), 1, '条目数')
        self.assertEqual(read_u32(180), 192, 'TAGX 偏移')
        # IDXT 偏移指向的字节必须是 IDXT 魔数。
        idxt_at = read_u32(20)
        self.assertEqual(index_header[idxt_at:idxt_at + 4], b'IDXT')

    def test_ncx_entries_use_short_ids_and_toc_lengths(self):
        """目录项用十六进制序号做标识，并给出到下一项的字节长度。"""
        book = self.build()
        book.add_document(book_module.Document(
            'ch0002.xhtml',
            content='<html><body><h1>第二章</h1><p>更多内容。</p></body></html>'))
        book.toc.append(book_module.TocEntry('第二章', 'ch0002.xhtml'))
        records = self.records(mobi_writer.build_mobi(book, kf8=True))
        head = records[0]
        index = struct.unpack('>I', head[244:248])[0]
        index_record = records[index + 1]
        body = index_record[192:]
        self.assertEqual(body[0], 2, '标识应是两字符的十六进制序号')
        self.assertEqual(body[1:3], b'00')
        # 第一条的 tag 1 之后应有非 0 的 length（tag 2）。
        control = body[3]
        self.assertTrue(control & 0x02, 'tag 2 (length) 应置位')

    def test_record_pointers_point_at_the_right_records(self):
        """FDST / FLIS / FCIS / 首条图片记录的编号必须指向真正的记录。

        records 列表不含 record0，所以 append 之后的 PDB 记录号是 len(records)，
        早先写成 len(records) - 1，四个指针整体错一条。

        注意 192 的语义按格式不同：KF8 是 FDST 记录号，MOBI 6 是
        首/末内容记录号的 u16 对，MOBI 6 的 FDST 记录号不在头里。
        """
        book = self.build()
        book.add_image(book_module.Image('p.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 16, 'image/png'))
        book.documents[0].content = ('<html><body><h1>第一章</h1><p><img src="p.png"/></p></body></html>')
        for kf8 in (False, True):
            records = self.records(mobi_writer.build_mobi(book, kf8=kf8))
            head = records[0]
            read = lambda offset: struct.unpack('>I', head[offset:offset + 4])[0]
            if kf8:
                self.assertEqual(records[read(192)][:4], b'FDST', 'kf8 的 FDST 指针')
            else:
                first, last = struct.unpack_from('>HH', head, 192)
                self.assertEqual(first, 1, 'MOBI 6 的首内容记录应为 1')
                self.assertLessEqual(last, len(records) - 1, 'MOBI 6 的末内容记录越界')
                self.assertEqual(head[196:200], b'\x00\x00\x00\x01')
            self.assertEqual(records[read(200)][:4], b'FCIS', 'kf8=%s 的 FCIS 指针' % kf8)
            self.assertEqual(records[read(208)][:4], b'FLIS', 'kf8=%s 的 FLIS 指针' % kf8)
            self.assertEqual(records[read(108)][:2], b'\x89P', 'kf8=%s 的图片记录指针' % kf8)

    def test_skeleton_and_chunk_tables_are_consistent(self):
        """skeleton 声明的 chunk 数必须等于 chunk 表的条目数。

        读端按每份 skeleton 的 chunk_count 累加去消费 chunk 表：
        少了会 IndexError，多了会静默拼错正文。
        """
        book = self.build()
        # 造一份超过一个 chunk 的正文，确保 chunk 数 > 1
        book.documents[0].content = (
            '<html><body><h1>第一章</h1><p>' + '很长的正文。' * 3000 + '</p></body></html>')
        records = self.records(mobi_writer.build_mobi(book, kf8=True))
        head = records[0]
        skel_index = struct.unpack('>I', head[252:256])[0]
        chunk_index = struct.unpack('>I', head[248:252])[0]
        chunk_count = struct.unpack('>I', records[chunk_index][36:40])[0]
        self.assertGreater(chunk_count, 1, '这份正文应该切出多块')
        header = records[skel_index]
        tagx_at = header.find(b'TAGX')
        tagx_length = struct.unpack('>I', header[tagx_at + 4:tagx_at + 8])[0]
        tags = {}
        for position in range(tagx_at + 12, tagx_at + tagx_length, 4):
            tag, vpe, mask, end = header[position:position + 4]
            if tag:
                tags[tag] = (vpe, mask)
        body = records[skel_index + 1][192:]
        size = body[0]
        position = 1 + size
        control = body[position]
        position += 1
        vpe, mask = tags[1]
        shift = (mask & -mask).bit_length() - 1
        groups = (control & mask) >> shift
        declared = None
        for index in range(groups * vpe):
            value, consumed = decode_number(body[position:])
            position += consumed
            if index == 0:
                declared = value
        self.assertEqual(declared, chunk_count,
                         'skeleton 说 %s 块，chunk 表里有 %d 块' % (declared, chunk_count))

    def test_rebuilding_from_the_indices_reproduces_the_text(self):
        """按读端的方式重建，结果必须与原始文本流逐字节相同。

        骨架留空、内容全在 chunk 里，插回骨架开头。
        骨架若也装全文、chunk 又放一遍，正文会成倍重复。
        """
        book = self.build()
        book.documents[0].content = (
            '<html><body><h1>第一章</h1><p>' + '正文内容反复出现。' * 2000 + '</p></body></html>')
        records = self.records(mobi_writer.build_mobi(book, kf8=True))
        head = records[0]
        last_text = struct.unpack('>H', head[8:10])[0]
        raw = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records[1:last_text + 1])
        skel_index = struct.unpack('>I', head[252:256])[0]
        chunk_index = struct.unpack('>I', head[248:252])[0]
        skeletons = self.decode_index(records[skel_index], records[skel_index + 1])
        chunks = self.decode_index(records[chunk_index], records[chunk_index + 1])
        rebuilt = []
        base = 0
        for _, geometry in skeletons:
            skeleton = raw[geometry[6][0]:geometry[6][0] + geometry[6][1]]
            base += geometry[6][1]
            for _, values in chunks:
                part = raw[base:base + values[6][1]]
                base += values[6][1]
                rebuilt.append(skeleton + part)
        self.assertEqual(b''.join(rebuilt), raw, '重建结果与原始文本流不一致')

    def test_standalone_file_is_readable(self):
        """记录 0 的 MOBI 标识、压缩方式与文本长度都能读出来。"""
        data = mobi_writer.build_mobi(self.build(), kf8=True)
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(record0[16:20], b'MOBI')
        self.assertEqual(struct.unpack('>H', record0[0:2])[0], 2)
        self.assertGreater(struct.unpack('>I', record0[4:8])[0], 0)



class StylesheetPreservationTests(unittest.TestCase):
    """原书自带的样式表不能被默认样式覆盖。"""

    def test_original_style_css_is_kept_verbatim(self):
        custom = 'body { font-family: "My Font"; color: #123; }\n'
        container = ('<?xml version="1.0"?><container '
                     'xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                     '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                     'media-type="application/oebps-package+xml"/></rootfiles></container>')
        package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">x</dc:identifier><dc:title>书</dc:title>'
                   '<dc:language>zh</dc:language></metadata><manifest>'
                   '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="css" href="style.css" media-type="text/css"/>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                   'properties="nav"/></manifest><spine><itemref idref="c1"/></spine></package>')
        doc = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
               '<head><title>章</title><link rel="stylesheet" href="style.css"/></head>'
               '<body><p>正文</p></body></html>')
        nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
               'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>'
               '<body><nav epub:type="toc"><ol><li><a href="c1.xhtml">章</a></li>'
               '</ol></nav></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.epub'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', b'application/epub+zip',
                                 compress_type=zipfile.ZIP_STORED)
                archive.writestr('META-INF/container.xml', container.encode())
                archive.writestr('OEBPS/content.opf', package.encode())
                archive.writestr('OEBPS/c1.xhtml', doc.encode())
                archive.writestr('OEBPS/nav.xhtml', nav.encode())
                archive.writestr('OEBPS/style.css', custom.encode())
            book = epub_input.read(source)
            target = Path(temp) / 'o.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as archive:
                written = archive.read('OEBPS/style.css').decode()
                opf = archive.read('OEBPS/content.opf').decode()
        self.assertEqual(written, custom)
        self.assertEqual(opf.count('href="style.css"'), 1, '样式表登记了两次')

    def test_other_stylesheets_do_not_leave_an_orphan_style_css(self):
        """原书只有别的 CSS 时，包里那份默认 style.css 必须有 manifest 项。

        生成的 nav 与标题页都引用 style.css，所以它必须存在；
        但如果清单里没有它，写出时的校验会报「资源不存在」。
        """
        container = ('<?xml version="1.0"?><container '
                     'xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                     '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                     'media-type="application/oebps-package+xml"/></rootfiles></container>')
        package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">x</dc:identifier><dc:title>书</dc:title>'
                   '<dc:language>zh</dc:language></metadata><manifest>'
                   '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="css" href="Styles/main.css" media-type="text/css"/>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                   'properties="nav"/></manifest><spine><itemref idref="c1"/></spine></package>')
        doc = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
               '<head><title>章</title><link rel="stylesheet" href="Styles/main.css"/>'
               '</head><body><p>正文</p></body></html>')
        nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
               'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>'
               '<body><nav epub:type="toc"><ol><li><a href="c1.xhtml">章</a></li>'
               '</ol></nav></body></html>')
        own = 'body{font-family:"MyFont";color:#123}'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.epub'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', b'application/epub+zip',
                                 compress_type=zipfile.ZIP_STORED)
                archive.writestr('META-INF/container.xml', container.encode())
                archive.writestr('OEBPS/content.opf', package.encode())
                archive.writestr('OEBPS/c1.xhtml', doc.encode())
                archive.writestr('OEBPS/nav.xhtml', nav.encode())
                archive.writestr('OEBPS/Styles/main.css', own.encode())
            book = epub_input.read(source)
            target = Path(temp) / 'o.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                opf = archive.read('OEBPS/content.opf').decode()
                kept = archive.read('OEBPS/Styles/main.css').decode()
        self.assertIn('OEBPS/Styles/main.css', names)
        self.assertIn('OEBPS/style.css', names)
        self.assertEqual(kept, own)
        self.assertIn('href="style.css"', opf, '默认 style.css 没登记进 manifest')
        self.assertIn('href="Styles/main.css"', opf)

if __name__ == '__main__':
    unittest.main()