"""HTML 读入端缺陷回归测试。

每条用例都对着真实可见的输出：目录里有没有条目、XHTML 里有没有
原文本该有的文字/标签/图片、EPUB 包里有没有图片资源。
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from workshop.convert import epub_output, html_input  # noqa: E402

# 1x1 PNG，用来当磁盘上相邻的图片文件。
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
       b'\x01\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82')


def content(book):
    return '\n'.join(document.content for document in book.documents)


def toc_titles(book):
    return [(entry.title, [child.title for child in entry.children])
            for entry in book.toc]


class InlineMarkupTests(unittest.TestCase):
    def test_img_and_inline_tags_survive_in_order(self):
        """img / b / a 这些行内标签不能丢，且要按原文顺序出现。

        链接目标是同一本书里的另一章，所以保留成 <a>；
        指向包外的相对链接会降级成 <span>（否则 EPUB 写出会失败）。
        """
        book = html_input.read_text(
            '<html><body><h1>一</h1><p>文<b>粗</b><i>斜</i>'
            '<a href="ch0002.xhtml">链</a>图</p><h1>二</h1><p>第二章</p>'
            '</body></html>')
        page = book.documents[0].content
        self.assertIn('<p>文<b>粗</b><i>斜</i>', page)
        self.assertIn('<a href="ch0002.xhtml">链</a>', page)
        # 标签本身不能被转义成可见文字。
        self.assertNotIn('&lt;img', page)
        self.assertNotIn('&lt;b&gt;', page)
        # 文字里的特殊字符仍要转义，XHTML 才合法。
        book = html_input.read_text('<html><body><p>a &amp; b &lt; c</p></body></html>')
        self.assertIn('a &amp; b &lt; c', book.documents[0].content)

    def test_inline_link_href_keeps_entities_and_is_escaped(self):
        """convert_charrefs 解出的 & 在 href 里要重新转义，不能破坏 XHTML。"""
        book = html_input.read_text(
            '<html><body><h1>一</h1><p><a href="ch0002.xhtml">链接</a>'
            '<a href="javascript:alert(1)">不安全</a>'
            '<a href="../../etc/passwd">越界</a></p><h1>二</h1><p>二</p>'
            '</body></html>')
        page = book.documents[0].content
        # 章节链接保留并正确转义
        self.assertIn('<a href="ch0002.xhtml">链接</a>', page)
        # 危险协议与越界路径的链接降级成文字，标签本身不出现
        self.assertNotIn('javascript:', page)
        self.assertNotIn('../../etc/passwd', page)
        self.assertIn('不安全', page)
        self.assertIn('越界', page)

    def test_img_from_disk_is_added_to_book_images_and_epub(self):
        """read() 要读出同目录的图片文件，写 EPUB 时资源才不悬空。"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'cover.png').write_bytes(PNG)
            source = folder / 'book.html'
            source.write_text(
                '<html><body><h1>第一章</h1><p>前<img src="cover.png"/>后</p>'
                '</body></html>', encoding='utf-8')
            book = html_input.read(source)
            self.assertEqual(len(book.images), 1)
            href = next(iter(book.images))
            self.assertEqual(book.images[href].data, PNG)
            self.assertIn('<img src="%s" alt=""/>' % href, book.documents[0].content)

            target = folder / 'out.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.endswith('/' + href) for name in names))
                self.assertEqual(archive.read(
                    next(name for name in names if name.endswith('/' + href))), PNG)
                opf = archive.read(next(
                    name for name in names if name.endswith('/content.opf'))).decode('utf-8')
                self.assertIn(href, opf)

    def test_remote_and_missing_images_do_not_create_dangling_refs(self):
        """外链与磁盘上找不到的图片不该写出悬空的 img 引用。"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'book.html'
            source.write_text(
                '<html><body><p>前<img src="https://example.com/a.png"/>'
                '<img src="lost.png"/>后</p></body></html>', encoding='utf-8')
            book = html_input.read(source)
            self.assertEqual(book.images, {})
            self.assertNotIn('<img', book.documents[0].content)
            self.assertIn('前后', book.documents[0].content)


class ContainerTests(unittest.TestCase):
    def test_container_h2_h3_titles_become_subsections_with_anchors(self):
        """容器里的 h2-h6 不再是废文字，而是章内子章节 + 目录锚点。"""
        book = html_input.read_text(
            '<html><body><div class="chapter">'
            '<h2>第一章</h2><p>正文一。</p>'
            '<h3>1.1 小节</h3><p>小节内容。</p>'
            '<h4>1.1.1 细节</h4><p>细节内容。</p>'
            '</div></body></html>')
        self.assertEqual([t.title for t in book.toc], ['第一章'])
        children = book.toc[0].children
        self.assertEqual(children[0].title, '1.1 小节')
        self.assertEqual(children[0].children[0].title, '1.1.1 细节')
        self.assertTrue(children[0].href.endswith('#s0002'))
        page = book.documents[0].content
        self.assertIn('<h3 id="s0002">1.1 小节</h3>', page)
        self.assertIn('<p>小节内容。</p>', page)
        self.assertIn('<h4 id="s0003">1.1.1 细节</h4>', page)
        self.assertIn('<p>细节内容。</p>', page)

    def test_multiple_class_names_still_mark_a_chapter(self):
        """class="note chapter" 要认成章节容器；span 上的 class 不算容器。"""
        book = html_input.read_text(
            '<html><body><div class="note chapter"><h2>一</h2><p>正文一</p></div>'
            '<section class="chapter beta"><h2>二</h2><p>正文二</p></section>'
            '</body></html>')
        self.assertEqual([t.title for t in book.toc], ['一', '二'])
        self.assertIn('正文一', book.documents[0].content)
        self.assertNotIn('正文二', book.documents[0].content)
        self.assertIn('正文二', book.documents[1].content)

        # span 不是章节容器，不能凭 class 把行内元素当成一章。
        inline = html_input.read_text(
            '<html><body><p><span class="chapter">前缀</span></p>'
            '<h1>一</h1><p>正文</p></body></html>')
        self.assertEqual([t.title for t in inline.toc], ['一'])
        self.assertIn('<span>前缀</span>', content(inline))

    def test_inner_block_end_does_not_close_chapter_container(self):
        """容器压栈与块级标签栈分开：内层 </div> 不能提前关掉章节。"""
        book = html_input.read_text(
            '<html><body><div class="chapter"><h2>第一章</h2>'
            '<div><p>内层段落。</p></div><p>外层段落。</p></div>'
            '<div class="chapter"><h2>第二章</h2><p>第二章正文。</p></div>'
            '</body></html>')
        self.assertEqual([t.title for t in book.toc], ['第一章', '第二章'])
        first = book.documents[0].content
        self.assertIn('内层段落。', first)
        self.assertIn('外层段落。', first)
        self.assertIn('第二章正文。', book.documents[1].content)


    def test_container_without_heading_keeps_its_paragraphs(self):
        """_container_text 的死回退去掉后，无标题容器的正文仍要留住。"""
        book = html_input.read_text(
            '<html><body><div class="chapter"><p>只有正文没有标题</p></div>'
            '</body></html>')
        self.assertIn('只有正文没有标题', content(book))
        self.assertEqual(len(book.toc), 1)

    def test_empty_container_heading_falls_back_to_a_readable_title(self):
        """容器里的标题是空的，不能往目录里塞空条目。"""
        book = html_input.read_text(
            '<html><body><div class="chapter"><h2></h2><p>x</p></div></body></html>')
        self.assertTrue(all(entry.title.strip() for entry in book.toc))
        self.assertIn('正文', content(book))

    def test_non_image_file_is_not_registered_as_an_image(self):
        """<img src="notes.txt"> 指向的普通文本文件不能进图片清单。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'a.html').write_text(
                '<html><body><h1>章</h1><p><img src="notes.txt"/></p></body></html>',
                encoding='utf-8')
            (root / 'notes.txt').write_text('hello', encoding='utf-8')
            book = html_input.read(root / 'a.html')
        self.assertEqual(list(book.images), [])

    def test_dead_container_text_fallback_is_gone(self):
        """源码里不再有从未填充过的 _container_text 回退分支。"""
        source = Path(html_input.__file__).read_text(encoding='utf-8')
        self.assertNotIn('_container_text', source)


class ChapterTreeTests(unittest.TestCase):
    def test_document_starting_at_h2_is_not_dropped(self):
        """以 h2 开头的文档，h2 就是顶层章节，正文不能整块消失。"""
        book = html_input.read_text(
            '<html><body><h2>甲</h2><p>甲正文。</p>'
            '<h3>甲小节</h3><p>小节正文。</p>'
            '<h2>乙</h2><p>乙正文。</p></body></html>')
        self.assertEqual(toc_titles(book),
                         [('甲', ['甲小节']), ('乙', [])])
        self.assertIn('甲正文。', book.documents[0].content)
        self.assertIn('小节正文。', book.documents[0].content)
        self.assertIn('乙正文。', book.documents[1].content)

    def test_deeper_first_chapter_keeps_its_body(self):
        """h3 开头也不能丢：顶层取没有更小 level 祖先的章节。"""
        book = html_input.read_text(
            '<html><body><h3>序章</h3><p>序章正文。</p>'
            '<h1>正章</h1><p>正章正文。</p></body></html>')
        self.assertEqual([t.title for t in book.toc], ['序章', '正章'])
        self.assertIn('序章正文。', content(book))
        self.assertIn('正章正文。', content(book))

    def test_preamble_before_first_heading_is_kept(self):
        """标题之前的前言、版权、序言要进第一章节的正文。"""
        book = html_input.read_text(
            '<html><head><title>书名</title></head><body>'
            '<p>前言：这本书写给谁。</p>'
            '<p>版权所有，翻印必究。</p>'
            '<h1>第一章</h1><p>正文开始。</p>'
            '<h1>第二章</h1><p>第二章正文。</p></body></html>')
        first = book.documents[0].content
        self.assertIn('前言：这本书写给谁。', first)
        self.assertIn('版权所有，翻印必究。', first)
        self.assertIn('正文开始。', first)
        self.assertNotIn('前言：这本书写给谁。', book.documents[1].content)
        self.assertEqual([t.title for t in book.toc], ['第一章', '第二章'])

    def test_leading_h2_with_preamble_keeps_both(self):
        """以 h2 开头且带前言时，两者都不能消失。"""
        book = html_input.read_text(
            '<html><body><p>写在前面。</p><h2>第一章</h2><p>正文。</p>'
            '</body></html>')
        self.assertIn('写在前面。', book.documents[0].content)
        self.assertIn('正文。', book.documents[0].content)
        self.assertEqual([t.title for t in book.toc], ['第一章'])


if __name__ == '__main__':
    unittest.main()
