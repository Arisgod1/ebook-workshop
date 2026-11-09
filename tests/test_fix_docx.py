"""DOCX 读入的回归测试：图片、标题级别、内容控件、元数据、大小上限。

这些用例都针对用户在真实 Word 文件上能看到的症状：
图片丢失、本地化标题不进目录、内容控件里的正文整段消失、
Strict 文档的书名作者为空、小压缩包把转换拖死。
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop.convert import docx_input

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
DC = 'http://purl.org/dc/elements/1.1/'
DCTERMS = 'http://purl.org/dc/terms/'
CP = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
APP = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'

PNG = (b'\x89PNG\r\n\x1a\n'
       b'\x00\x00\x00\rIHDR\x00\x00\x00\x06\x00\x00\x00\x06'
       b'\x08\x02\x00\x00\x00\x83\x85\x15\xac\x00\x00\x00\x00IEND\xaeB`\x82')
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'


def heading_styles(*entries):
    """由 (styleId, styleName) 生成一个最小的 styles.xml。"""
    body = ''.join(
        '<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/></w:style>'
        % (style_id, name) for style_id, name in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<w:styles xmlns:w="%s">%s</w:styles>' % (W, body))


def core_properties(title='测试书名', creator='测试作者', namespace=DC):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<cp:coreProperties xmlns:cp="%s" xmlns:dc="%s">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator>'
            '</cp:coreProperties>' % (CP, namespace, title, creator))


def write_docx(path, body, styles=None, core=None, extra=None, namespaces=''):
    """写一个最小的 DOCX；body 里的 w:/r:/a: 前缀总是有定义。"""
    document = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:a="%s"%s>'
                '<w:body>%s</w:body></w:document>'
                % (W, R, A, namespaces, body))
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', document)
        if styles is not None:
            archive.writestr('word/styles.xml', styles)
        if core is not None:
            archive.writestr('docProps/core.xml', core)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def paragraph(text, style=None, properties=''):
    style_xml = '<w:pStyle w:val="%s"/>' % style if style else ''
    return ('<w:p><w:pPr>%s%s</w:pPr><w:r><w:t>%s</w:t></w:r></w:p>'
            % (style_xml, properties, text))


IMAGE_RELS = ('<?xml version="1.0"?>'
              '<Relationships xmlns="%s">'
              '<Relationship Id="rId5" Type="image" Target="media/image1.png"/>'
              '</Relationships>' % PR)


def image_paragraph(identifier='rId5'):
    return ('<w:p><w:r><w:t>图前</w:t></w:r>'
            '<w:r><w:drawing><a:graphic><a:graphicData>'
            '<a:blip r:embed="%s"/>'
            '</a:graphicData></a:graphic></w:drawing></w:r>'
            '<w:r><w:t>图后</w:t></w:r></w:p>' % identifier)


class DocxImageTests(unittest.TestCase):
    """图片必须真的读出来，而且字节正确。"""

    def test_picture_bytes_match_the_archive(self):
        """正文引用的图片带真实字节，不是空 Image。"""
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', image_paragraph(),
                styles=heading_styles(),
                core=core_properties(),
                extra={'word/_rels/document.xml.rels': IMAGE_RELS,
                       'word/media/image1.png': PNG})
            book = docx_input.read(path)
        self.assertEqual(list(book.images), ['media/image1.png'])
        image = book.images['media/image1.png']
        self.assertEqual(image.data, PNG)
        self.assertEqual(image.media_type, 'image/png')

    def test_media_type_follows_the_bytes_not_the_suffix(self):
        """扩展名写错时按文件头判定类型，MOBI 才不会带错 mime。"""
        rels = IMAGE_RELS.replace('image1.png', 'image1.bin')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', image_paragraph(),
                styles=heading_styles(),
                core=core_properties(),
                extra={'word/_rels/document.xml.rels': rels,
                       'word/media/image1.bin': JPEG})
            book = docx_input.read(path)
        image = book.images['media/image1.bin']
        self.assertEqual(image.media_type, 'image/jpeg')

    def test_missing_target_is_skipped_not_emptied(self):
        """关系指向的图片不在包里时不生成空图片。"""
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', image_paragraph(),
                styles=heading_styles(),
                core=core_properties(),
                extra={'word/_rels/document.xml.rels': IMAGE_RELS})
            book = docx_input.read(path)
        self.assertEqual(book.images, {})
        self.assertNotIn('<img', book.documents[0].content)


class DocxHeadingTests(unittest.TestCase):
    """标题级别：段落 outlineLvl、本地化样式名、basedOn 链。"""

    def test_paragraph_outline_level_becomes_a_heading(self):
        """段落自己的 outlineLvl 进目录，直接格式优先于样式。"""
        body = (paragraph('大纲标题', properties='<w:outlineLvl w:val="0"/>')
                + paragraph('正文内容'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body,
                              styles=heading_styles(), core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['大纲标题'])
        self.assertIn('正文内容', book.documents[0].content)

    def test_outline_level_outranks_the_style(self):
        """段落 outlineLvl=1 让二级样式变成二级标题。"""
        body = (paragraph('一级', style='Heading1')
                + paragraph('二级', style='Heading1',
                            properties='<w:outlineLvl w:val="1"/>'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('Heading1', 'heading 1')),
                core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['一级'])
        self.assertEqual([child.title for child in book.toc[0].children], ['二级'])

    def test_outline_level_nine_is_body_text(self):
        """outlineLvl=9 是「正文」，不能当标题。"""
        body = (paragraph('真标题', style='Heading1')
                + paragraph('普通段落', properties='<w:outlineLvl w:val="9"/>'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('Heading1', 'heading 1')),
                core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['真标题'])

    def test_localised_heading_name_is_recognised(self):
        """德语 Word 的「Überschrift 1」也要认成一级标题。"""
        body = paragraph('Erster Abschnitt', style='berschrift1')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('berschrift1', 'Überschrift 1')),
                core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['Erster Abschnitt'])

    def test_based_on_chain_is_followed(self):
        """自定义样式只写 basedOn="Heading1" 时也能认出级别。"""
        styles = ('<?xml version="1.0" encoding="UTF-8"?>'
                  '<w:styles xmlns:w="%s">'
                  '<w:style w:type="paragraph" w:styleId="Heading1">'
                  '<w:name w:val="heading 1"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="MyHeading">'
                  '<w:name w:val="My Heading"/><w:basedOn w:val="Heading1"/>'
                  '</w:style></w:styles>' % W)
        body = paragraph('基于标题一', style='MyHeading')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body,
                              styles=styles, core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['基于标题一'])


class DocxContentControlTests(unittest.TestCase):
    """内容控件与嵌套容器里的正文不能整段丢。"""

    def test_content_control_paragraphs_are_read(self):
        """w:sdt/w:sdtContent 里的段落照常读出来。"""
        body = ('<w:sdt><w:sdtPr><w:alias w:val="x"/></w:sdtPr><w:sdtContent>'
                + paragraph('控件里的正文')
                + '</w:sdtContent></w:sdt>')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body,
                              styles=heading_styles(), core=core_properties())
            book = docx_input.read(path)
        self.assertIn('控件里的正文', book.documents[0].content)

    def test_heading_inside_a_content_control_enters_the_toc(self):
        """内容控件里包着的标题也要进目录。"""
        body = ('<w:sdt><w:sdtContent>'
                + paragraph('控件里的标题', style='Heading1')
                + '</w:sdtContent></w:sdt>'
                + paragraph('正文'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('Heading1', 'heading 1')),
                core=core_properties())
            book = docx_input.read(path)
        self.assertEqual([entry.title for entry in book.toc], ['控件里的标题'])

    def test_paragraphs_before_the_first_heading_are_kept(self):
        """第一个标题之前的前言/版权段落不能丢。"""
        body = (paragraph('前言：版权说明')
                + paragraph('第一章', style='Heading1')
                + paragraph('正文'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('Heading1', 'heading 1')),
                core=core_properties())
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('前言：版权说明', content)
        self.assertLess(content.index('前言：版权说明'), content.index('正文'))

    def test_list_items_before_the_first_heading_are_kept(self):
        """第一个标题之前的列表项同样不能丢。"""
        body = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>'
                '<w:r><w:t>前置列表项</w:t></w:r></w:p>'
                + paragraph('第一章', style='Heading1')
                + paragraph('正文'))
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', body,
                styles=heading_styles(('Heading1', 'heading 1')),
                core=core_properties())
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('<li>前置列表项</li>', content)
        self.assertIn('正文', content)

    def test_deeply_nested_content_controls_are_read(self):
        """内容控件套内容控件时也要一递归到底。"""
        body = ('<w:sdt><w:sdtContent><w:sdt><w:sdtContent>'
                + paragraph('深层正文')
                + '</w:sdtContent></w:sdt></w:sdtContent></w:sdt>')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body,
                              styles=heading_styles(), core=core_properties())
            book = docx_input.read(path)
        self.assertIn('深层正文', book.documents[0].content)

    def test_nested_table_cells_are_read(self):
        """表格单元格里嵌套的表格内容不丢。"""
        body = ('<w:tbl><w:tr><w:tc><w:p><w:r><w:t>外层</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>内层</w:t></w:r></w:p>'
                '</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>')
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body,
                              styles=heading_styles(), core=core_properties())
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertIn('外层', content)
        self.assertIn('内层', content)


class DocxMetadataTests(unittest.TestCase):
    """core.xml 的两种命名空间与 app.xml 的出版信息。"""

    def test_dcterms_core_properties_are_read(self):
        """dcterms 命名空间的 title/creator 也读得出来。"""
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', paragraph('正文'),
                styles=heading_styles(),
                core=core_properties('Strict 书名', 'Strict 作者',
                                     namespace=DCTERMS))
            book = docx_input.read(path)
        self.assertEqual(book.title, 'Strict 书名')
        self.assertEqual(book.author, 'Strict 作者')

    def test_app_company_becomes_the_publisher(self):
        """app.xml 的 Company 是出版社。"""
        app = ('<?xml version="1.0"?><Properties xmlns="%s">'
               '<Company>示例出版社</Company>'
               '<Application>Microsoft Office Word</Application>'
               '</Properties>' % APP)
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(
                Path(temp) / 'a.docx', paragraph('正文'),
                styles=heading_styles(), core=core_properties(),
                extra={'docProps/app.xml': app})
            book = docx_input.read(path)
        self.assertEqual(book.publisher, '示例出版社')

    def test_missing_core_properties_does_not_blank_the_title(self):
        """没有 core.xml 时保持默认值，不是把书名清空。"""
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', paragraph('正文'),
                              styles=heading_styles())
            book = docx_input.read(path)
        self.assertEqual(book.title, '')
        self.assertIn('正文', book.documents[0].content)


class DocxNoteTests(unittest.TestCase):
    """脚注与尾注：文字在别的部件里，不读就整个消失。"""

    FOOTNOTES = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<w:footnotes xmlns:w="%s">'
                 '<w:footnote w:type="separator" w:id="-1">'
                 '<w:p><w:r><w:t>分隔线不该出现</w:t></w:r></w:p></w:footnote>'
                 '<w:footnote w:id="1"><w:p><w:r><w:t>这是一条脚注内容</w:t></w:r></w:p></w:footnote>'
                 '<w:footnote w:id="2"><w:p><w:r><w:t>第二条脚注</w:t></w:r></w:p></w:footnote>'
                 '</w:footnotes>' % W)

    def _read(self, body, extra):
        with tempfile.TemporaryDirectory() as temp:
            path = write_docx(Path(temp) / 'a.docx', body, extra=extra)
            return docx_input.read(path)

    def test_footnote_text_is_kept(self):
        """脚注文字在 footnotes.xml 里，必须一起读出来。"""
        body = ('<w:p><w:r><w:t>正文带脚注引用</w:t></w:r>'
                '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>')
        book = self._read(body, {'word/footnotes.xml': self.FOOTNOTES})
        text = '\n'.join(document.content for document in book.documents)
        self.assertIn('这是一条脚注内容', text)

    def test_footnote_reference_links_to_the_note(self):
        """正文里的上标引用要链到注释页，对应关系不能错。"""
        body = ('<w:p><w:r><w:t>正文</w:t></w:r>'
                '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>')
        book = self._read(body, {'word/footnotes.xml': self.FOOTNOTES})
        notes = next(d for d in book.documents if d.href == 'notes.xhtml')
        self.assertIn('id="note2"', notes.content)
        self.assertIn('第二条脚注', notes.content)
        self.assertIn('href="notes.xhtml#note2"', book.documents[0].content)

    def test_separator_is_not_treated_as_a_note(self):
        """id 为 -1 的分隔符是排版用的，不是注释。"""
        body = '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
        book = self._read(body, {'word/footnotes.xml': self.FOOTNOTES})
        text = '\n'.join(document.content for document in book.documents)
        self.assertNotIn('分隔线不该出现', text)


class DocxLimitTests(unittest.TestCase):
    """小压缩包的解压后大小必须有上限。"""

    def test_archive_that_expands_too_much_is_refused(self):
        """压缩后很小、解压后超限的 DOCX 直接拒绝，不拖死转换。"""
        from workshop.convert import archive
        original_entry = archive.MAX_ENTRY
        original_total = archive.MAX_UNCOMPRESSED
        archive.MAX_ENTRY = 64 * 1024
        archive.MAX_UNCOMPRESSED = 64 * 1024
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'bomb.docx'
                with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as handle:
                    handle.writestr('word/document.xml', paragraph('正文'))
                    # 多个小条目累积到超过总上限：单条不超限，总量超限。
                    for index in range(4):
                        handle.writestr('word/media/part%d.bin' % index,
                                        b'\x00' * (24 * 1024))
                self.assertLess(path.stat().st_size, 64 * 1024)
                with self.assertRaises(ValueError) as caught:
                    docx_input.read(path)
        finally:
            archive.MAX_ENTRY = original_entry
            archive.MAX_UNCOMPRESSED = original_total
        message = str(caught.exception)
        self.assertIn('Not a readable DOCX', message)
        self.assertIn('refusing to process', message)


if __name__ == '__main__':
    unittest.main()


class DocxAttributeSafetyTests(unittest.TestCase):
    """图片路径进 HTML 属性时，引号必须转义。"""

    def test_quote_in_image_href_cannot_inject_an_attribute(self):
        """路径里的引号不能提前闭合 src 并塞进 onload。"""
        evil = 'media/x.png" onload="alert(1)'
        doc = ('<?xml version="1.0"?><w:document xmlns:w="%s" xmlns:r="%s" xmlns:a="%s">'
               '<w:body><w:p><w:r><w:drawing><a:graphic><a:graphicData>'
               '<a:blip r:embed="rId5"/></a:graphicData></a:graphic></w:drawing>'
               '</w:r></w:p></w:body></w:document>' % (W, R, A))
        rels = ('<?xml version="1.0"?><Relationships xmlns="%s">'
                '<Relationship Id="rId5" Type="image" Target="%s"/></Relationships>'
                % (PR, evil.replace('"', '&quot;')))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', doc)
                archive.writestr('word/_rels/document.xml.rels', rels)
                archive.writestr('word/' + evil, PNG)
            book = docx_input.read(path)
        content = book.documents[0].content
        self.assertNotIn('onload="alert(1)"', content)
        self.assertIn('&quot;', content)
        import xml.etree.ElementTree as ET
        ET.fromstring(content)      # 整份文档必须仍是合法 XML


class DocxListLevelTests(unittest.TestCase):
    """列表层级一次跳多级时不能写出不匹配的标签。"""

    def test_level_jump_opens_every_level(self):
        items = [{'parts': [('text', '甲')], 'level': 0},
                 {'parts': [('text', '乙')], 'level': 3}]
        html = ''.join(docx_input._list_html(items))
        import xml.etree.ElementTree as ET
        ET.fromstring('<div>' + html + '</div>')   # 之前这里报 mismatched tag
