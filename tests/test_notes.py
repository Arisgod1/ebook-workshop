"""注释体检与规范化的功能测试：跑真 CLI，读回真实产物逐项验。

样本在测试里现场生成，覆盖三种注释形态：纯文本圈码、带链接但没语义、
已是 EPUB 3 语义脚注。检查的是用户能看到的现象：注释能不能点、
正文有没有被改坏、断链会不会被报出来。
"""

import unittest
from pathlib import Path

from tests.support import epub_entries, epub_text, run, run_failing
from tests.test_fix_epub import make_epub, package, xhtml


def plain_book(body):
    """一本只有一章的书，正文由调用方给。"""
    manifest = ('<item id="d1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"'
                ' properties="nav"/>')
    spine = '<itemref idref="d1"/>'
    nav = ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"'
           ' xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>'
           '<body><nav epub:type="toc" id="toc"><ol><li><a href="c1.xhtml">第一章</a>'
           '</li></ol></nav></body></html>')
    return {
        'OEBPS/content.opf': package(manifest, spine),
        'OEBPS/c1.xhtml': xhtml('第一章', body, xmlns_epub=True),
        'OEBPS/nav.xhtml': nav,
    }


CIRCLED_BODY = (
    '<p>开头一句正文，这里有<sup class="m"><span>①</span></sup>一个注释，'
    '后面还有<sup class="m"><span>②</span></sup>另一个。</p>'
    '<ul class="notes"><li>① 第一条注释的正文。</li>'
    '<li>② 第二条注释的正文。</li></ul>'
    '<p>这一章的结尾。</p>'
)

ANCHORED_BODY = (
    '<p>开头一句正文，这里有<sup><a href="c1.xhtml#n1">[1]</a></sup>一个注释。</p>'
    '<aside id="n1"><p>[1] 第一条注释的正文。</p></aside>'
    '<p>这一章的结尾。</p>'
)

SEMANTIC_BODY = (
    '<p>开头一句正文，这里有<sup><a epub:type="noteref" href="c1.xhtml#n1">[1]</a>'
    '</sup>一个注释。</p>'
    '<aside epub:type="footnote" id="n1"><p>[1] 第一条注释的正文。</p></aside>'
    '<p>这一章的结尾。</p>'
)


class NotesCommandTests(unittest.TestCase):
    """notes / notes-fix 的端到端行为。"""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def book(self, body, name='book.epub'):
        path = self.root / name
        return make_epub(path, plain_book(body))

    def test_notes_reports_plain_circled_references(self):
        """体检能认出圈码纯文本注释，报告它一个都点不了。"""
        book = self.book(CIRCLED_BODY)
        report = run('notes', book)
        self.assertEqual(report['references']['total'], 2)
        self.assertEqual(report['references']['plain'], 2)
        self.assertEqual(report['forms']['plain'], 2)
        self.assertEqual(report['pairings']['high'], 2)

    def test_notes_reports_broken_anchor(self):
        """注释引用指向不存在的锚点时，体检把断链列出来。"""
        body = ANCHORED_BODY.replace('href="c1.xhtml#n1"', 'href="c1.xhtml#gone"')
        book = self.book(body)
        report = run('notes', book)
        self.assertEqual(len(report['broken_links']), 1)
        self.assertEqual(report['broken_links'][0]['reason'], '锚点不存在')

    def test_notes_reports_semantic_notes_as_compliant(self):
        """已是 EPUB 3 语义脚注的引用，体检认成 noteref 且没有断链。"""
        book = self.book(SEMANTIC_BODY)
        report = run('notes', book)
        self.assertEqual(report['forms']['noteref'], 1)
        self.assertEqual(report['notes']['semantic'], 1)
        self.assertEqual(report['broken_links'], [])

    def test_notes_writes_no_file(self):
        """体检只报告，绝不写文件，原书字节不变。"""
        book = self.book(CIRCLED_BODY)
        before = book.read_bytes()
        run('notes', book)
        self.assertEqual(book.read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name != 'book.epub'], [])

    def test_notes_fix_links_circled_notes(self):
        """圈码注释被换成可跳转引用，注释正文换成 aside 语义脚注。"""
        book = self.book(CIRCLED_BODY)
        output = self.root / 'fixed.epub'
        result = run('notes-fix', book, '--output', output)
        self.assertEqual(result['references_linked'], 2)
        self.assertEqual(result['note_bodies_created'], 2)
        entries = epub_entries(output)
        body = entries['OEBPS/c1.xhtml'].decode('utf-8')
        self.assertIn('epub:type="noteref"', body)
        self.assertIn('epub:type="footnote"', body)
        self.assertIn('href="#workshop-note-', body)

    def test_notes_fix_preserves_visible_text(self):
        """规范化一字不改正文，圈码和注释正文都原样留着。"""
        book = self.book(CIRCLED_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        self.assertEqual(epub_text(book, 'OEBPS/c1.xhtml'),
                         epub_text(output, 'OEBPS/c1.xhtml'))

    def test_notes_fix_output_passes_validate(self):
        """规范化产物的链接和锚点都落得到实处。"""
        book = self.book(CIRCLED_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        report = run('validate', output)
        self.assertEqual(report['structural_validation'],
                         'passed (not a substitute for EPUBCheck)')

    def test_notes_fix_then_notes_shows_linked(self):
        """改完再体检，两条引用都成了 noteref，注释正文也带语义了。"""
        book = self.book(CIRCLED_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        report = run('notes', output)
        self.assertEqual(report['forms']['noteref'], 2)
        self.assertEqual(report['references']['linked'], 2)
        self.assertEqual(report['notes']['semantic'], 2)
        self.assertEqual(report['broken_links'], [])

    def test_notes_fix_adds_semantics_to_anchored(self):
        """已有链接但没语义的注释，只补标记，不动 href 和 id。"""
        book = self.book(ANCHORED_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        body = epub_entries(output)['OEBPS/c1.xhtml'].decode('utf-8')
        self.assertIn('epub:type="noteref"', body)
        self.assertIn('epub:type="footnote"', body)
        self.assertIn('href="c1.xhtml#n1"', body)
        self.assertIn('id="n1"', body)

    def test_notes_fix_leaves_semantic_book_unchanged_in_text(self):
        """已经合规的书跑一遍，正文文字不变，连通断链依然为零。"""
        book = self.book(SEMANTIC_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        self.assertEqual(epub_text(book, 'OEBPS/c1.xhtml'),
                         epub_text(output, 'OEBPS/c1.xhtml'))
        report = run('notes', output)
        self.assertEqual(report['broken_links'], [])

    def test_notes_fix_refuses_to_overwrite(self):
        """输出已存在时报错退出，不覆盖用户文件。"""
        book = self.book(CIRCLED_BODY)
        output = self.root / 'fixed.epub'
        run('notes-fix', book, '--output', output)
        before = output.read_bytes()
        code, error = run_failing('notes-fix', book, '--output', output)
        self.assertEqual(code, 1)
        self.assertIn('Output exists', error)
        self.assertEqual(output.read_bytes(), before)

    def test_notes_fix_skips_mismatched_circles(self):
        """底本圈码写错时那组不改，报告里留给人判断。"""
        body = CIRCLED_BODY.replace('<li>② 第二条注释的正文。</li>',
                                    '<li>③ 第二条注释的正文。</li>')
        book = self.book(body)
        output = self.root / 'fixed.epub'
        result = run('notes-fix', book, '--output', output)
        self.assertEqual(result['references_linked'], 0)
        self.assertEqual(result['left_for_review'], 2)

    def test_notes_fix_is_idempotent(self):
        """改完再改一遍不会重复加链接或换 id。"""
        book = self.book(CIRCLED_BODY)
        first = self.root / 'first.epub'
        second = self.root / 'second.epub'
        run('notes-fix', book, '--output', first)
        result = run('notes-fix', first, '--output', second)
        self.assertEqual(result['references_linked'], 0)
        self.assertEqual(result['note_bodies_created'], 0)
        self.assertEqual(epub_text(first, 'OEBPS/c1.xhtml'),
                         epub_text(second, 'OEBPS/c1.xhtml'))

    def test_notes_fix_default_output_location(self):
        """不给 --output 时写到原书旁的「整理后/书名.epub」。"""
        book = self.book(CIRCLED_BODY)
        result = run('notes-fix', book)
        self.assertIn('整理后', result['output'])
        self.assertTrue(Path(result['output']).exists())


if __name__ == '__main__':
    unittest.main()
