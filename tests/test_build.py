"""转换：把 TXT 或其他格式做成能正常阅读的 EPUB。"""
from tests.support import Workspace, epub_entries, epub_text, run, run_failing


class BuildTests(Workspace):
    def test_text_becomes_readable_epub_with_correct_metadata(self):
        """TXT 转出的 EPUB 带正确书名作者，目录可跳转，正文一字不差。"""
        book = self.build()
        entries = epub_entries(book)
        self.assertIn('OEBPS/nav.xhtml', entries)
        self.assertIn('OEBPS/toc.ncx', entries)
        self.assertIn('示例书名', epub_text(book, 'OEBPS/title.xhtml'))
        self.assertIn('测试作者', epub_text(book, 'OEBPS/title.xhtml'))
        self.assertIn('这是一份自造的测试文字 & < >。', epub_text(book, 'OEBPS/c0001.xhtml'))
        self.assertTrue(run('validate', book)['structural_validation'].startswith('passed'))

    def test_front_matter_and_afterword_are_kept(self):
        """作者资料和作者留言按原位置保留，不被当成正文丢掉。"""
        book = self.build()
        self.assertIn('作者资料：测试作者', epub_text(book, 'OEBPS/front.xhtml'))
        self.assertIn('这也是需要保留的内容。', epub_text(book, 'OEBPS/c0003.xhtml'))

    def test_chapter_numbers_are_not_rewritten(self):
        """原文的乱号章节保留原样，不擅自改成连续编号。"""
        book = self.build()
        report = run('inspect', book)
        self.assertEqual(report['declared_numbers'], [1, 5, 3])

    def test_verified_heading_override_applies(self):
        """核实过的标题可以替换，只改标题不动正文。"""
        fixes = self.write_json('fixes.json', {'2': '第2章 核实后的标题'})
        book = self.build(extra=('--headings-json', fixes))
        self.assertIn('第2章 核实后的标题', epub_text(book, 'OEBPS/c0002.xhtml'))
        self.assertEqual(run('inspect', book)['declared_numbers'], [1, 2, 3])

    def test_invalid_heading_override_leaves_no_output(self):
        """指向不存在章节的标题修正会被拒绝，不生成半成品。"""
        fixes = self.write_json('bad.json', {'99': '第99章 不存在'})
        output = self.root / 'bad.epub'
        code, error = run_failing('build', self.source, '--title', '示例', '--author', '某人',
                                  '--headings-json', fixes, '--output', output)
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())

    def test_existing_file_is_never_overwritten(self):
        """同名输出已存在时拒绝写入，保护用户已有文件。"""
        book = self.build()
        before = book.read_bytes()
        code, _ = run_failing('build', self.source, '--title', '另一个名字', '--author', '某人', '--output', book)
        self.assertEqual(code, 1)
        self.assertEqual(book.read_bytes(), before)

    def test_cover_can_be_added_at_build_time(self):
        """转换时可以同时带上封面。"""
        book = self.build(extra=('--cover', self.cover))
        entries = epub_entries(book)
        self.assertTrue(any(name.endswith('.png') for name in entries))
