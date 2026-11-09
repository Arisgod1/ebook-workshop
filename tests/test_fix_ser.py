"""序列化器与中间表示的缺陷回归测试。

每条用例都对着用户能看到的现象：正文里的 &amp; 字面量、
写进正文中间的 filepos 数字、图片消失、带片段的链接跳错位置、
MOBI 目录只剩顶层条目。
"""

import re
import struct
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from workshop.convert import book as book_module  # noqa: E402
from workshop.convert import docx_input, html_input, mobi_writer  # noqa: E402
from workshop.convert.palmdoc import decompress  # noqa: E402
from workshop.convert.serializer import Serializer, escape  # noqa: E402

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
       b'\x01\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82')


def stream_of(book, kf8=False):
    """把一本书交给序列化器，返回 (序列化器, 文本流)。"""
    documents = [{'href': document.href, 'body': document.content}
                 for document in book.linear_documents()]
    serializer = Serializer(documents, images=book.images, toc=book.toc, kf8=kf8)
    return serializer, serializer()


def split_records(data):
    """把 MOBI/AZW3 文件拆成记录列表。"""
    count = struct.unpack('>H', data[76:78])[0]
    offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
    return [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
            for i in range(count)]


def read_text_record(record):
    """按读端的规则解出一条文本记录。"""
    trail = (record[-1] & 0x3) + 1
    return decompress(record[:len(record) - trail])


def text_stream_of(data):
    """把 MOBI/AZW3 的正文记录解压拼回一条文本流。"""
    records = split_records(data)
    last = struct.unpack('>H', records[0][8:10])[0]
    return b''.join(read_text_record(record) for record in records[1:last + 1])


def simple_book(chapter_bodies, toc=None):
    """按 (href, 正文 HTML) 组一本书。"""
    book = book_module.Book(title='测试书名', author='测试作者', language='zh-CN')
    for href, body in chapter_bodies:
        book.add_document(book_module.Document(href, content=body))
    book.toc = list(toc or [])
    return book


class EntityEscapingTests(unittest.TestCase):
    """正文里的实体只转义一次。"""

    def test_escaped_text_is_not_escaped_again(self):
        """L&amp;N 不能变成 L&amp;amp;N。

        读入端（EPUB/DOCX/ODT/HTML）交给序列化器的 content 已经是
        HTML 片段，文字里的 & 与 < 都以实体形式存在。序列化器再转一次，
        读者会在正文里看到字面的 &amp;。
        """
        book = simple_book([('c1.xhtml', (
            '<html><body><h1>第一章</h1>'
            '<p>L&amp;N 铁路公司，5 &lt; 6，a &amp; b</p></body></html>'))])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertNotIn('&amp;amp;', text)
        self.assertNotIn('&amp;lt;', text)
        self.assertIn('L&amp;N', text)
        self.assertIn('5 &lt; 6', text)

    def test_bare_ampersand_is_escaped_once(self):
        """裸 & 仍要转成 &amp;——它不是实体的一部分。"""
        self.assertEqual(escape('a & b'), 'a &amp; b')
        self.assertEqual(escape('L&amp;N'), 'L&amp;N')
        self.assertEqual(escape('&lt;'), '&lt;')

    def test_docx_output_keeps_the_source_entities(self):
        """从真实 DOCX 读出来的 &amp; 在 MOBI 文本流里原样保留。"""
        path = ROOT / 'tests' / 'support' / 'tmp_entity.docx'
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                    '<w:r><w:t>第一章</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>AT&amp;T 与 5 &lt; 6</w:t></w:r></w:p>'
                    '</w:body></w:document>')
        styles = ('<?xml version="1.0"?><w:styles xmlns:w="http://schemas.'
                  'openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:style w:type="paragraph" w:styleId="Heading1">'
                  '<w:name w:val="heading 1"/></w:style></w:styles>')
        try:
            with zipfile.ZipFile(str(path), 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr('word/document.xml', document)
                archive.writestr('word/styles.xml', styles)
            book = docx_input.read(path)
            _, stream = stream_of(book)
        finally:
            if path.exists():
                path.unlink()
        text = stream.decode('utf-8')
        self.assertIn('AT&amp;T 与 5 &lt; 6', text)
        self.assertNotIn('&amp;amp;', text)


class FileposOffsetTests(unittest.TestCase):
    """filepos 回填的是整条文本流里的绝对偏移。"""

    def test_cross_document_link_points_at_the_target_document(self):
        """跨文档链接要落在目标文档开头，不是片段内的位置。

        按片段内位置回填会把 10 位数字写进正文中间，后文整段被盖掉。
        """
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>第一章</h1>'
                         '<p><a href="c2.xhtml">跳到第二章</a>后面的文字。</p></body></html>'),
            ('c2.xhtml', '<html><body><h1>第二章</h1><p>正文二。</p></body></html>'),
        ])
        serializer, stream = stream_of(book)
        stream.decode('utf-8')                       # 必须是合法 UTF-8
        targets = [int(value) for value in re.findall(rb'filepos=(\d{10})', stream)]
        self.assertTrue(targets)
        for target in targets:
            self.assertLess(target, len(stream))
        # 链接指向的正是第二份文档的起点。
        self.assertIn(serializer.id_offsets['c2.xhtml'], targets)
        at_target = stream[serializer.id_offsets['c2.xhtml']:]
        self.assertTrue(at_target.startswith(b'<h1>\xe7\xac\xac\xe4\xba\x8c\xe7\xab\xa0</h1>'))
        # 正文没有被数字盖住。
        self.assertIn('后面的文字。'.encode('utf-8'), stream)

    def test_guide_filepos_points_into_the_body(self):
        """guide 的 filepos 也是全局偏移，解析后必须落在 <body> 之内。"""
        book = simple_book(
            [('c1.xhtml', '<html><body><h1>第一章</h1><p>正文。</p></body></html>')],
            [book_module.TocEntry('第一章', 'c1.xhtml')])
        serializer, stream = stream_of(book)
        for target in re.findall(rb'filepos=(\d{10})', stream):
            offset = int(target)
            self.assertGreaterEqual(offset, serializer.body_start)
            self.assertLess(offset, len(stream))
        self.assertGreaterEqual(serializer.start_offset, serializer.body_start)

    def test_missing_link_target_is_not_redirected_to_the_book_start(self):
        """找不到目标的链接不该静默指向书首。

        指向 body_start 的话，读者点「坏链接」会跳到第一章，
        看起来像链接生效了，实际去了错的地方。宁可保留占位符
        （读端会当成悬空锚点），也不要伪造一个位置。
        """
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>第一章</h1>'
                         '<p><a href="missing.xhtml">坏链接</a></p></body></html>'),
        ])
        serializer, stream = stream_of(book)
        targets = [int(value) for value in re.findall(rb'filepos=(\d{10})', stream)]
        self.assertNotIn(serializer.body_start, targets,
                         '坏链接被回填成书首位置')
        self.assertTrue(all(target == 0 for target in targets),
                        '坏链接的占位符应原样保留')

    def test_no_mojibake_after_fixup(self):
        """回填之后整条流仍是干净的中文，没有被写坏的标签。"""
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>第一章</h1>'
                         '<p><a href="c2.xhtml">下一章</a></p></body></html>'),
            ('c2.xhtml', '<html><body><h1>第二章</h1><p>正文。</p></body></html>'),
        ])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('<h1>第一章</h1>', text)
        self.assertIn('<h1>第二章</h1>', text)
        # 每个 10 位数字都在 filepos= 之后，不出现在标签名里。
        self.assertEqual(text.count('filepos='), len(re.findall(r'filepos=\d{10}', text)))


class FragmentLinkTests(unittest.TestCase):
    """带 #片段 的链接要落到目标锚点。"""

    def test_id_with_a_quote_is_escaped_in_the_output(self):
        """id 里含引号时要转义，不能生成畸形标记。"""
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>一</h1>'
                         "<h2 id='a\"b'>节</h2></body></html>"),
        ])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('<h2 id="a&quot;b">', text)
        # 属性里的引号必须转义掉，否则标记会被截断。
        self.assertNotIn('id="a"', text)

    def test_cross_document_fragment_resolves_to_the_anchor(self):
        """chapter.xhtml#id 指向锚点，不是一个不存在的文件。"""
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>一</h1>'
                         '<p><a href="c2.xhtml#sec2">跳</a></p></body></html>'),
            ('c2.xhtml', '<html><body><h1>二</h1><h2 id="sec2">节</h2></body></html>'),
        ])
        serializer, stream = stream_of(book)
        target = serializer.id_offsets['c2.xhtml#sec2']
        self.assertIn(target, [int(v) for v in re.findall(rb'filepos=(\d{10})', stream)])
        self.assertTrue(stream[target:].startswith(b'<h2 id="sec2">'))

    def test_same_document_fragment_resolves_to_the_anchor(self):
        """#id 指向本文档里的锚点，而不是被当成外部链接丢掉。"""
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>一</h1>'
                         '<p><a href="#sec1">跳</a></p><h2 id="sec1">节</h2></body></html>'),
        ])
        serializer, stream = stream_of(book)
        self.assertIn('<a filepos=', stream.decode('utf-8'))
        target = serializer.id_offsets['c1.xhtml#sec1']
        self.assertIn(target, [int(v) for v in re.findall(rb'filepos=(\d{10})', stream)])
        self.assertTrue(stream[target:].startswith(b'<h2 id="sec1">'))

    def test_fragment_only_anchor_does_not_invent_a_path(self):
        """resolve 只对路径部分做归一化，片段不参与拼接。"""
        serializer = Serializer([])
        self.assertEqual(serializer.resolve('Text/a.xhtml', 'b.xhtml#x'),
                         'Text/b.xhtml#x')
        self.assertEqual(serializer.resolve('Text/a.xhtml', '#x'), 'Text/a.xhtml#x')
        self.assertEqual(serializer.resolve('Text/a.xhtml', '../Images/i.png'),
                         'Images/i.png')

    def test_target_document_without_the_anchor_falls_back_to_its_start(self):
        """锚点没落到位置时退到文档开头，而不是书首。"""
        book = simple_book([
            ('c1.xhtml', '<html><body><h1>一</h1>'
                         '<p><a href="c2.xhtml#nope">跳</a></p></body></html>'),
            ('c2.xhtml', '<html><body><h1>二</h1></body></html>'),
        ])
        serializer, stream = stream_of(book)
        targets = [int(v) for v in re.findall(rb'filepos=(\d{10})', stream)]
        self.assertIn(serializer.id_offsets['c2.xhtml'], targets)


class ImageReferenceTests(unittest.TestCase):
    """图片在子目录、被多次引用时也要正确落到资源记录。"""

    def test_subdirectory_image_is_kept(self):
        """文档在 Text/、图片在 Images/ 时，图片不能被丢掉。"""
        book = simple_book([('Text/a.xhtml', (
            '<html><body><p><img src="../Images/pic.png" alt=""/>文字</p></body></html>'))])
        book.add_image(book_module.Image('Images/pic.png', PNG, 'image/png'))
        serializer, stream = stream_of(book, kf8=True)
        text = stream.decode('utf-8')
        self.assertIn('kindle:embed:', text)
        self.assertNotIn('<img/>', text, '图片被整个丢弃')
        self.assertEqual(serializer.image_index, {'Images/pic.png': 1})

    def test_same_image_referenced_twice_uses_one_resource(self):
        """同一张图引用两次只占一个资源槽。

        否则资源列表条目数比正文引用多，读端按序号取记录会全部错位。
        """
        book = simple_book([('a.xhtml', (
            '<html><body><p><img src="pic.png"/></p>'
            '<p><img src="pic.png"/></p></body></html>'))])
        book.add_image(book_module.Image('Images/pic.png', PNG, 'image/png'))
        serializer, stream = stream_of(book)
        self.assertEqual(len(serializer.image_index), 1)
        text = stream.decode('utf-8')
        self.assertEqual(text.count('recindex="00001"'), 2)
        self.assertNotIn('recindex="00002"', text)

    def test_basename_fallback_does_not_allocate_a_second_slot(self):
        """路径对不上、靠文件名兜底时，也要复用同一个序号。"""
        book = simple_book([('a.xhtml', (
            '<html><body><p><img src="wrong/pic.png"/></p>'
            '<p><img src="wrong/pic.png"/></p></body></html>'))])
        book.add_image(book_module.Image('Images/pic.png', PNG, 'image/png'))
        serializer, stream = stream_of(book)
        self.assertEqual(serializer.image_index, {'Images/pic.png': 1})
        text = stream.decode('utf-8')
        self.assertEqual(text.count('recindex="00001"'), 2)

    def test_kf8_image_tag_keeps_the_real_media_type(self):
        """KF8 的 kindle:embed 要带图片自己的 mime，不能一律写 jpeg。"""
        book = simple_book([('a.xhtml', (
            '<html><body><p><img src="pic.png"/></p></body></html>'))])
        book.add_image(book_module.Image('Images/pic.png', PNG, 'image/png'))
        _, stream = stream_of(book, kf8=True)
        self.assertIn('mime=image/png', stream.decode('utf-8'))

    def test_unknown_image_is_skipped_without_breaking_text(self):
        """找不到的图片只是不写 img，正文文字照常保留。"""
        book = simple_book([('a.xhtml', (
            '<html><body><p>前文<img src="nope.png"/>后文</p></body></html>'))])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('前文', text)
        self.assertIn('后文', text)
        self.assertNotIn('recindex', text)


class InlineTocTests(unittest.TestCase):
    """MOBI 6 的内联目录：Calibre 的读端靠它还原目录。"""

    def build(self):
        return simple_book([
            ('c1.xhtml', '<html><body><h1>第一章</h1><p>正文一</p></body></html>'),
            ('c2.xhtml', '<html><body><h2 id="s2">第一节</h2><p>正文二</p>'
                         '<h1>第二章</h1><p>正文三</p></body></html>'),
        ], [book_module.TocEntry('第一章', 'c1.xhtml',
                                 children=[book_module.TocEntry('第一节', 'c2.xhtml#s2')]),
            book_module.TocEntry('第二章', 'c2.xhtml')])

    def test_mobi6_writes_an_inline_toc_with_every_entry(self):
        """MOBI 6 的正文里要有一份列出全部条目的内联目录。"""
        _, stream = stream_of(self.build())
        text = stream.decode('utf-8')
        self.assertIn('<b>目录</b>', text)
        self.assertIn('第一章', text)
        self.assertIn('第一节', text)
        self.assertIn('第二章', text)
        # 读端扫到分页标记就停止收集目录项。
        self.assertIn('<div class="mbp_pagebreak"></div>', text)

    def test_guide_toc_points_at_the_inline_toc(self):
        """guide 的 toc 指向内联目录，不是第一章。"""
        serializer, stream = stream_of(self.build())
        text = stream.decode('utf-8')
        reference = re.search(r'<reference type="toc"[^>]*filepos=(\d{10})', text)
        self.assertIsNotNone(reference)
        offset = int(reference.group(1))
        self.assertLess(offset, serializer.id_offsets['c1.xhtml'])
        self.assertTrue(stream[offset:offset + 12].startswith(b'<div id="mbp'))

    def test_start_reference_points_at_the_first_chapter(self):
        """start 仍指向第一章，打开书停在那里，不是停在内联目录。"""
        serializer, stream = stream_of(self.build())
        text = stream.decode('utf-8')
        reference = re.search(r'<reference type="start"[^>]*filepos=(\d{10})', text)
        self.assertIsNotNone(reference)
        self.assertEqual(int(reference.group(1)), serializer.id_offsets['c1.xhtml'])

    def test_inline_toc_nests_child_entries(self):
        """子条目要比父条目更深一层：读端靠缩进还原层级。"""
        _, stream = stream_of(self.build())
        text = stream.decode('utf-8')
        # 父条目在一层 blockquote 里，子条目在两层里。
        self.assertIn('<blockquote><p><a filepos=', text)
        self.assertIn('<blockquote><blockquote><p><a filepos=', text)

    def test_kf8_has_no_inline_toc(self):
        """KF8 的目录走 NCX 索引，正文里不该多一份重复目录。"""
        _, stream = stream_of(self.build(), kf8=True)
        text = stream.decode('utf-8')
        self.assertNotIn('<b>目录</b>', text)
        self.assertNotIn('mobi_toc', text)

    def test_start_reference_survives_a_linkless_first_entry(self):
        """顶层第一项是没有链接的分组标题时，仍要写出 start 引用。

        DOCX 里很常见：「第一部」只是分组，链接在它的子章节上。
        拿顶层第一项去找 href 会一个都找不到，Kindle 于是随便挑位置打开。
        """
        book = simple_book([
            ('c1.xhtml', '<html><body><h2 id="s1">第一章</h2><p>正文一</p></body></html>'),
        ], [book_module.TocEntry('第一部', None, children=[
            book_module.TocEntry('第一章', 'c1.xhtml#s1')])])
        serializer, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('type="start"', text)
        self.assertIsNotNone(serializer.start_offset)
        self.assertEqual(serializer.start_offset, serializer.id_offsets['c1.xhtml#s1'])

    def test_inline_toc_does_not_claim_a_body_anchor(self):
        """目录容器不占用正文里的锚点名。

        目录容器需要一个 id —— Calibre 导入 MOBI 时按 guide 里 toc 引用的
        片段找它，找不到就没有目录。但这个 id 必须足够特别，
        不能和正文里存在的锚点（比如 <h1 id="toc">）撞名。
        """
        book = simple_book(
            [('c1.xhtml', '<html><body><h1 id="toc">第一章</h1></body></html>')],
            [book_module.TocEntry('第一章', 'c1.xhtml')])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('<b>目录</b>', text)
        # 目录容器的 id 不能是正文用到的那个。
        self.assertIn('<div id="mbp-toc">', text, '目录容器要有自己的 id')
        self.assertIn('<h1 id="toc">', text, '正文的锚点不能被抢走')
        # guide 的 toc 指向的正是目录容器。
        self.assertIn('href="#mbp-toc"', text)

    def test_inline_toc_links_reach_the_chapters_in_the_mobi_file(self):
        """写进 MOBI 后，内联目录里的链接仍指向真实章节。"""
        book = self.build()
        stream = text_stream_of(mobi_writer.build_mobi(book))
        text = stream.decode('utf-8')
        self.assertIn('<b>目录</b>', text)
        for target in re.findall(r'filepos=(\d{10})', text):
            self.assertLess(int(target), len(stream))
        # 目录里的第一章条目与正文标题落在同一位置。
        chapter = stream.index('<h1>第一章</h1>'.encode('utf-8'))
        self.assertIn(b'<a filepos=%010d>' % chapter, text.encode('utf-8'))


class TocEntriesTests(unittest.TestCase):
    """目录展平要包含全部层级。"""

    def test_toc_entries_walks_the_whole_tree(self):
        """子章节不能被静默丢掉。"""
        book = book_module.Book()
        book.toc = [
            book_module.TocEntry('A', 'a.xhtml', children=[
                book_module.TocEntry('A1', 'a.xhtml#s1'),
                book_module.TocEntry('A2', 'a.xhtml#s2', children=[
                    book_module.TocEntry('A2a', 'a.xhtml#s3')]),
            ]),
            book_module.TocEntry('B', 'b.xhtml'),
        ]
        self.assertEqual([entry.title for entry in book.toc_entries()],
                         ['A', 'A1', 'A2', 'A2a', 'B'])

    def test_toc_entries_skips_group_headers_without_links(self):
        """没有链接的分组标题不进目录，但它的子项要保留。"""
        book = book_module.Book()
        book.toc = [book_module.TocEntry('分组', None, children=[
            book_module.TocEntry('A', 'a.xhtml')])]
        self.assertEqual([entry.title for entry in book.toc_entries()], ['A'])

    def test_toc_entries_matches_the_tree(self):
        """toc_entries 与 toc_tree 的条目集合一致（只差没有链接的分组）。"""
        book = book_module.Book()
        book.toc = [book_module.TocEntry('A', 'a.xhtml', children=[
            book_module.TocEntry('A1', 'a.xhtml#s1')]), book_module.TocEntry('B', 'b.xhtml')]
        self.assertEqual([entry.title for entry in book.toc_entries()],
                         [entry.title for entry, _ in book.toc_tree()])

    def test_summary_counts_nested_entries(self):
        """summary 里的目录条目数要按整棵树算。"""
        book = book_module.Book()
        book.toc = [book_module.TocEntry('A', 'a.xhtml', children=[
            book_module.TocEntry('A1', 'a.xhtml#s1')])]
        self.assertEqual(book.summary()['toc_entries'], 2)

    def test_html_input_child_chapters_are_in_toc_entries(self):
        """HTML 输入的子章节也在 toc_entries 里。"""
        book = html_input.read_text(
            '<html><body><h1>第一章</h1><p>a</p><h2>小节</h2><p>b</p>'
            '<h1>第二章</h1><p>c</p></body></html>')
        self.assertEqual([entry.title for entry in book.toc_entries()],
                         ['第一章', '小节', '第二章'])

    def test_two_level_docx_headings_reach_mobi_and_azw3(self):
        """DOCX 的两级标题在 MOBI 与 AZW3 的目录里都要在。

        MOBI 6 的目录写在 NCX 记录里；AZW3 的目录写在 KF8 的 NCX 索引里，
        条目数记在头记录的 36 字节处。少了子章节，两边都会缺项。
        """
        path = ROOT / 'tests' / 'support' / 'tmp_two_level.docx'
        document = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                    '<w:r><w:t>第一部</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>正文。</w:t></w:r></w:p>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                    '<w:r><w:t>第一章</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>章内容。</w:t></w:r></w:p>'
                    '</w:body></w:document>')
        styles = ('<?xml version="1.0"?><w:styles xmlns:w="http://schemas.'
                  'openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:style w:type="paragraph" w:styleId="Heading1">'
                  '<w:name w:val="heading 1"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Heading2">'
                  '<w:name w:val="heading 2"/></w:style></w:styles>')
        try:
            with zipfile.ZipFile(str(path), 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr('word/document.xml', document)
                archive.writestr('word/styles.xml', styles)
            book = docx_input.read(path)

            # MOBI 6：NCX 记录里有父章和子章两个标题。
            records = split_records(mobi_writer.build_mobi(book, kf8=False))
            ncx = next(record for record in records if b'<navMap>' in record)
            labels = re.findall(r'<navLabel><text>([^<]*)</text>',
                                ncx.decode('utf-8'))
            self.assertEqual(labels, ['第一部', '第一章'])

            # AZW3：NCX 索引的条目数是 2，说明两级标题都进了 KF8 目录。
            records = split_records(mobi_writer.build_mobi(book, kf8=True))
            ncx_index = struct.unpack('>I', records[0][244:248])[0]
            count = struct.unpack('>I', records[ncx_index][36:40])[0]
            self.assertEqual(count, 2)
        finally:
            if path.exists():
                path.unlink()


class AllowedTagsTests(unittest.TestCase):
    """定义了却没人用的白名单不能留在模块里骗人。"""

    def test_allowed_tags_is_gone(self):
        import workshop.convert.serializer as serializer_module
        self.assertFalse(hasattr(serializer_module, 'ALLOWED_TAGS'))

    def test_unsupported_tags_keep_their_text(self):
        """白名单外的标签（如 aside）只是去掉标签，文字要留下。"""
        book = simple_book([('a.xhtml', (
            '<html><body><aside><p>旁注文字</p></aside></body></html>'))])
        _, stream = stream_of(book)
        text = stream.decode('utf-8')
        self.assertIn('旁注文字', text)
        self.assertNotIn('<aside>', text)


if __name__ == '__main__':
    unittest.main()
