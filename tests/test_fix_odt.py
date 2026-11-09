"""ODT 读入的缺陷回归：每条用例都用现场生成的真实 ODT 文件。

覆盖：text:a 的开闭标签与锚文本、text:a 内嵌套 span、DTD/实体炸弹、
图片归档路径校验、text:section 里的标题层级、ODT→EPUB 端到端写出。
"""

import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop import epub as epub_module
from workshop.convert import epub_output, odt_input


ODT_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content'
            ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
            ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
            ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
            ' xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<office:body><office:text>')
ODT_TAIL = '</office:text></office:body></office:document-content>'
META = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta'
        ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<office:meta><dc:title>ODT 书名</dc:title>'
        '<dc:creator>ODT 作者</dc:creator></office:meta></office:document-meta>')


def content(body):
    return ODT_HEAD + body + ODT_TAIL


def make_odt(path, content_xml, extra=None, meta=META):
    """写一个真实的最小 ODT 包。"""
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text',
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/manifest.xml', '<manifest/>')
        archive.writestr('content.xml', content_xml)
        if meta is not None:
            archive.writestr('meta.xml', meta)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


class OdtLinkTests(unittest.TestCase):
    """text:a 必须成对写出标签，锚文本与嵌套结构不能丢。"""

    def test_anchor_has_balanced_tag_and_anchor_text(self):
        """开标签、闭标签、锚文本与链接后的 tail 都在，XHTML 可解析。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p>见 <text:a xlink:href="https://example.com/x">链接锚文本</text:a>'
                ' 与尾声。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        html = book.documents[0].content
        self.assertIn('<a href="https://example.com/x">链接锚文本</a>', html)
        self.assertIn('与尾声。', html)
        self.assertEqual(html.count('<a '), html.count('</a>'))
        root = ET.fromstring(html)
        anchor = root.find('.//{http://www.w3.org/1999/xhtml}a')
        self.assertEqual(''.join(anchor.itertext()), '链接锚文本')

    def test_span_nested_inside_anchor_keeps_document_order(self):
        """a 里嵌 span / s / line-break 时顺序不乱，且没有嵌套 a。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p>'
                '<text:a xlink:href="https://example.com/y">前'
                '<text:span>中<text:s text:c="2"/>间</text:span>'
                '<text:line-break/>后</text:a>'
                ' 尾。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        html = book.documents[0].content
        self.assertEqual(html.count('<a '), 1)
        self.assertEqual(html.count('</a>'), 1)
        self.assertIn('前中  间<br/>后</a> 尾。', html)
        self.assertNotIn('<a href="https://example.com/y"><a ', html)
        ET.fromstring(html)

    def test_nested_anchor_does_not_emit_illegal_anchor(self):
        """a 里再套 a 时内层退化成文字，整段仍是合法 XHTML。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p><text:a xlink:href="https://example.com/outer">外'
                '<text:a xlink:href="https://example.com/inner">内</text:a>'
                '外尾</text:a></text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        html = book.documents[0].content
        self.assertEqual(html.count('<a '), 1)
        self.assertEqual(html.count('</a>'), 1)
        self.assertIn('外内外尾</a>', html)
        ET.fromstring(html)

    def test_title_anchor_text_is_not_lost(self):
        """标题里的 text:a 锚文本也要进目录标题。"""
        body = ('<text:h text:outline-level="1">读 '
                '<text:a xlink:href="https://example.com/">这里</text:a> 开始</text:h>'
                '<text:p>正文。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        self.assertEqual(book.toc[0].title, '读 这里 开始')

    def test_link_href_does_not_break_out_of_attribute(self):
        """href 里的引号要转义，不能注入 onclick 之类的新属性。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p><text:a xlink:href=\'https://example.com/" onclick="bad()\'>'
                '锚</text:a></text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        html = book.documents[0].content
        self.assertNotIn(' onclick="', html)
        self.assertIn('&quot; onclick=&quot;', html)
        root = ET.fromstring(html)
        self.assertEqual(len(root.find('.//{http://www.w3.org/1999/xhtml}a').attrib), 1)

    def test_internal_link_becomes_plain_text_and_epub_writes(self):
        """ODT 内部链接没有写出端目标，不能变成 EPUB 里的悬空引用。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p>见 <text:a xlink:href="#bookmark">内部锚文本</text:a> 尾。</text:p>'
                '<text:p>见 <text:a xlink:href="other.odt">另一文件</text:a> 尾。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            source = make_odt(Path(temp) / 'a.odt', content(body))
            book = odt_input.read(source)
            target = Path(temp) / 'a.epub'
            epub_output.write(book, target)
            files, _, _ = epub_module.load(target)
        html = book.documents[0].content
        self.assertIn('内部锚文本', html)
        self.assertIn('另一文件', html)
        self.assertNotIn('<a href="#bookmark"', html)
        self.assertNotIn('other.odt', html)
        epub_module.validate_files(files)

    def test_odt_with_anchor_converts_to_epub_end_to_end(self):
        """带 text:a 的真实 ODT 转 EPUB 必须成功，产物能解析且锚文本还在。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p>见 <text:a xlink:href="https://example.com/x">'
                '<text:span>带 span 的</text:span>锚文本</text:a> 尾。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            source = make_odt(Path(temp) / 'a.odt', content(body))
            target = Path(temp) / 'a.epub'
            epub_output.write(odt_input.read(source), target)
            files, _, _ = epub_module.load(target)
        epub_module.validate_files(files)
        self.assertIn('带 span 的锚文本'.encode('utf-8'), files['OEBPS/ch0001.xhtml'])


class OdtXmlSafetyTests(unittest.TestCase):
    """所有 XML 部件都要拒绝 DTD/实体，且不能靠吞异常躲过去。"""

    @staticmethod
    def bomb(comment=b''):
        data = (b'<?xml version="1.0" encoding="UTF-8"?>' + comment
                + b'<!DOCTYPE office:document-content ['
                  b'<!ENTITY lol "lol">'
                  b'<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
                  b']>'
                  b'<office:document-content>&lol1;</office:document-content>')
        return data

    def test_content_xml_entity_bomb_is_rejected(self):
        """content.xml 带实体炸弹时立刻报错，而不是展开成巨大文本。"""
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', self.bomb())
            with self.assertRaises(odt_input.OdtError) as caught:
                odt_input.read(path)
        self.assertIn('DTD/entity', str(caught.exception))
        self.assertNotIn('lol', str(caught.exception))

    def test_bomb_hidden_behind_long_comment_is_rejected(self):
        """长注释把声明推到前 8192 字节之外也不能绕过检查。"""
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', self.bomb(b'<!--' + b'x' * 20000 + b'-->'))
            with self.assertRaises(odt_input.OdtError) as caught:
                odt_input.read(path)
        # 必须是因为实体声明被拒，而不是碰巧因为别的解析问题报错。
        self.assertIn('DTD/entity', str(caught.exception))

    def test_utf16_entity_bomb_is_rejected(self):
        """换成 UTF-16 编码的实体炸弹也要被拒。

        按字节匹配的检查会被另一种编码绕过去：同一份声明换成 UTF-16
        就溜进来，展开成几百万字符，把命令挂住。
        """
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', self.bomb())
            # 把 content.xml 换成 UTF-16 再包回去。
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries['content.xml'] = entries['content.xml'].replace(
                b'encoding="UTF-8"', b'encoding="UTF-16"').decode('utf-8').encode('utf-16')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, body in entries.items():
                    archive.writestr(name, body)
            with self.assertRaises(odt_input.OdtError):
                odt_input.read(path)
        # 只要拒绝了就行：换成 UTF-16 后实体声明的字节形态也变了，
        # 报的具体原因可能不同（unbound prefix 等）。

    def test_entity_bomb_is_rejected_in_every_encoding(self):
        """实体炸弹换个编码也要被拒。

        按字节匹配的检查会被另一种编码绕过去：同一份声明换成 UTF-16
        就溜进来，展开成几百万字符，把命令挂住。
        """
        from workshop.convert import xmlsafe
        payload = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]>'
                   '<l>&a;</l>')
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be'):
            with self.assertRaises(xmlsafe.XmlError, msg=encoding):
                xmlsafe.parse(payload.encode(encoding))

    def test_normal_xml_is_accepted_in_every_encoding(self):
        """正常的 XML 不能因为编码检查被误拒。"""
        from workshop.convert import xmlsafe
        payload = '<?xml version="1.0"?><root><a>x</a></root>'
        for encoding in ('utf-8', 'utf-16', 'utf-16-le'):
            root = xmlsafe.parse(payload.encode(encoding))
            self.assertEqual(root.tag, 'root', encoding)

    def test_meta_xml_entity_bomb_is_rejected_instead_of_swallowed(self):
        """meta.xml 的实体炸弹不能因为「元数据可选」就被吞掉。"""
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content(
                '<text:h text:outline-level="1">第一章</text:h><text:p>正文。</text:p>'),
                meta=self.bomb())
            with self.assertRaises(odt_input.OdtError) as caught:
                odt_input.read(path)
        self.assertIn('meta.xml', str(caught.exception))

    def test_oversized_xml_part_is_rejected_before_parsing(self):
        """单部件超过上限时先拒绝，不把上百兆文本读进内存。"""
        original = odt_input.MAX_XML_PART
        odt_input.MAX_XML_PART = 128
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = make_odt(Path(temp) / 'a.odt', content(
                    '<text:h text:outline-level="1">第一章</text:h>'
                    '<text:p>' + '正' * 200 + '</text:p>'))
                with self.assertRaises(odt_input.OdtError) as caught:
                    odt_input.read(path)
        finally:
            odt_input.MAX_XML_PART = original
        self.assertIn('refusing to parse', str(caught.exception))

    def test_plain_external_doctype_is_still_accepted(self):
        """普通外部 DOCTYPE（无内部子集、无实体）不是炸弹，不应误伤。"""
        plain = content('<text:h text:outline-level="1">第一章</text:h>'
                        '<text:p>正文。</text:p>').replace(
                            '?><office:', '?><!DOCTYPE office:document-content SYSTEM "x.dtd"><office:', 1)
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', plain))
        self.assertIn('正文。', book.documents[0].content)


class OdtPicturePathTests(unittest.TestCase):
    """图片归档名与正文引用都必须留在 Pictures/ 内。"""

    def test_traversal_archive_entry_is_refused(self):
        """带 '..' 的归档条目连读入都过不去。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p><draw:frame><draw:image xlink:href="Pictures/../evil.png"/>'
                '</draw:frame></text:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content(body),
                            extra={'Pictures/../evil.png': b'\x89PNG\r\n\x1a\n'})
            with self.assertRaises(odt_input.OdtError):
                odt_input.read(path)

    def test_traversal_href_cannot_reach_a_real_picture(self):
        """正文用 '..' 绕到 Pictures/ 外的真实图片时，既不能引用也不能收进书里。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p><draw:frame><draw:image xlink:href="Pictures/../evil.png"/>'
                '</draw:frame></text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body),
                                           extra={'evil.png': b'\x89PNG\r\n\x1a\n'}))
        self.assertNotIn('<img', book.documents[0].content)
        self.assertNotIn('evil.png', book.documents[0].content)
        self.assertFalse(any('..' in href.split('/') for href in book.images))
        self.assertEqual(book.images, {})

    def test_absolute_and_backslash_hrefs_are_dropped(self):
        """绝对路径、反斜杠路径、越界相对路径都不能写成书内资源。"""
        for href in ('/etc/passwd', 'Pictures\\evil.png', '../../outside.png',
                     'C:/secret.png'):
            body = ('<text:h text:outline-level="1">第一章</text:h>'
                    '<text:p><draw:frame><draw:image xlink:href="%s"/>'
                    '</draw:frame></text:p>' % href)
            with tempfile.TemporaryDirectory() as temp:
                book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
            self.assertNotIn('<img', book.documents[0].content)
            self.assertNotIn('outside.png', book.documents[0].content)

    def test_good_picture_survives_odt_to_epub(self):
        """正常的 Pictures/ 路径照旧读得出来，且写进 EPUB 后链接有效。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p><draw:frame><draw:image xlink:href="Pictures/p.png"/>'
                '</draw:frame></text:p>')
        with tempfile.TemporaryDirectory() as temp:
            source = make_odt(Path(temp) / 'a.odt', content(body),
                              extra={'Pictures/p.png': b'\x89PNG\r\n\x1a\n'})
            target = Path(temp) / 'a.epub'
            epub_output.write(odt_input.read(source), target)
            files, _, _ = epub_module.load(target)
        epub_module.validate_files(files)
        self.assertIn(b'Pictures/p.png', files['OEBPS/ch0001.xhtml'])
        self.assertEqual(files['OEBPS/Pictures/p.png'], b'\x89PNG\r\n\x1a\n')


class OdtSectionTests(unittest.TestCase):
    """text:section 里的标题要按层级进目录并带上锚点。"""

    def test_section_heading_becomes_child_chapter(self):
        """section 里的 h2 既是子章节，也在正文里带 id 和层级标签。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:p>开头。</text:p>'
                '<text:section text:name="s1">'
                '<text:h text:outline-level="2">小节标题</text:h>'
                '<text:p>小节正文。</text:p>'
                '<text:list><text:list-item><text:p>列表项。</text:p></text:list-item></text:list>'
                '</text:section>'
                '<text:h text:outline-level="1">第二章</text:h>'
                '<text:p>结尾。</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        self.assertEqual([entry.title for entry in book.toc], ['第一章', '第二章'])
        children = book.toc[0].children
        self.assertEqual([entry.title for entry in children], ['小节标题'])
        href = children[0].href
        self.assertTrue(re.fullmatch(r'ch0001\.xhtml#s\d{4}', href), href)
        first = book.documents[0].content
        self.assertIn('<h2 id="%s">小节标题</h2>' % href.split('#')[1], first)
        self.assertIn('<p>小节正文。</p>', first)
        self.assertIn('<ul><li>列表项。</li></ul>', first)
        # 标题不能再被压成一个段落里的纯文本。
        self.assertNotIn('<p>小节标题', first)
        self.assertIn('结尾。', book.documents[1].content)

    def test_nested_section_headings_keep_their_depth(self):
        """section 套 section 时 h2/h3 的父子关系与锚点都正确。"""
        body = ('<text:h text:outline-level="1">第一章</text:h>'
                '<text:section text:name="outer">'
                '<text:h text:outline-level="2">二级</text:h>'
                '<text:p>二级正文。</text:p>'
                '<text:section text:name="inner">'
                '<text:h text:outline-level="3">三级</text:h>'
                '<text:p>三级正文。</text:p>'
                '</text:section></text:section>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        second = book.toc[0].children[0]
        third = second.children[0]
        self.assertEqual(second.title, '二级')
        self.assertEqual(third.title, '三级')
        self.assertLessEqual(second.href.count('#'), 1)
        self.assertLessEqual(third.href.count('#'), 1)
        html = book.documents[0].content
        self.assertIn('<h3 id="%s">三级</h3>' % third.href.split('#')[1], html)
        self.assertIn('三级正文。', html)

    def test_section_only_document_keeps_heading_text(self):
        """整篇只有 section 时，标题与正文都不会被静默丢掉。"""
        body = ('<text:section text:name="only">'
                '<text:h text:outline-level="1">唯一标题</text:h>'
                '<text:p>唯一正文。</text:p>'
                '</text:section>')
        with tempfile.TemporaryDirectory() as temp:
            book = odt_input.read(make_odt(Path(temp) / 'a.odt', content(body)))
        self.assertEqual([entry.title for entry in book.toc], ['唯一标题'])
        self.assertIn('<h1>唯一标题</h1>', book.documents[0].content)
        self.assertIn('唯一正文。', book.documents[0].content)


if __name__ == '__main__':
    unittest.main()
