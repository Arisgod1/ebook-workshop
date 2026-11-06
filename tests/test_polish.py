"""精修：换封面、调排版、应用已核实的勘误。"""
from tests.support import Workspace, epub_entries, epub_text, run, run_failing


class PolishTests(Workspace):
    def test_cover_image_is_copied_verbatim(self):
        """换封面使用原图字节，正文内容完全不变。"""
        source = self.build('withcover.epub', extra=('--cover', self.cover))
        target = self.build('target.epub')
        output = self.root / 'new.epub'
        report = run('cover', target, '--from-epub', source, '--output', output)
        self.assertEqual(report['cover_pixels'], [1, 1])
        entries = epub_entries(output)
        self.assertIn(self.cover.read_bytes(), entries.values())
        for name, body in epub_entries(target).items():
            if name.startswith('OEBPS/c') and name.endswith('.xhtml'):
                self.assertEqual(entries[name], body)

    def test_cover_can_come_from_a_plain_image(self):
        """也可以直接给一张图片作为封面。"""
        book = self.build()
        output = self.root / 'covered.epub'
        run('cover', book, '--image', self.cover, '--output', output)
        self.assertTrue(run('validate', output)['structural_validation'].startswith('passed'))

    def test_layout_changes_look_not_words(self):
        """排版只改样式，选中的章节文字不变，未选的章节原样保留。"""
        book = self.build()
        output = self.root / 'styled.epub'
        run('layout', book, '--document', 'OEBPS/c0001.xhtml', '--output', output)
        before, after = epub_entries(book), epub_entries(output)
        self.assertIn('workshop-cn-novel', after['OEBPS/c0001.xhtml'].decode())
        self.assertEqual(epub_text(book, 'OEBPS/c0001.xhtml'), epub_text(output, 'OEBPS/c0001.xhtml'))
        self.assertEqual(before['OEBPS/c0002.xhtml'], after['OEBPS/c0002.xhtml'])

    def test_layout_numbers_can_be_adjusted(self):
        """边距、缩进等数值可以按需调整。"""
        book = self.build()
        output = self.root / 'custom.epub'
        run('layout', book, '--document', 'OEBPS/c0001.xhtml', '--margin', 8, '--indent', 0, '--output', output)
        styles = [body for name, body in epub_entries(output).items() if name.endswith('.css')]
        self.assertTrue(any(b'margin: 0 8%' in body for body in styles))
        self.assertTrue(any(b'text-indent: 0em' in body for body in styles))

    def test_layout_can_be_tried_on_one_chapter_first(self):
        """先试排一章，确认效果后再应用到全书，目录不受影响。"""
        book = self.build()
        trial = self.root / 'trial.epub'
        run('layout', book, '--document', 'OEBPS/c0001.xhtml', '--output', trial)
        before, after = epub_entries(book), epub_entries(trial)
        self.assertEqual(before['OEBPS/nav.xhtml'], after['OEBPS/nav.xhtml'])
        self.assertEqual(before['OEBPS/toc.ncx'], after['OEBPS/toc.ncx'])

    def test_layout_rejects_a_non_chapter(self):
        """不能用排版命令改目录或封面页。"""
        book = self.build()
        code, error = run_failing('layout', book, '--document', 'OEBPS/nav.xhtml', '--output', self.root / 'x.epub')
        self.assertEqual(code, 1)
        self.assertIn('reading document', error)

    def test_verified_correction_is_applied(self):
        """核实过的勘误清单可以写入，其余内容不受影响。"""
        book = self.build()
        edits = self.write_json('edits.json', [{'document': 'OEBPS/c0001.xhtml', 'old': '自造', 'new': '编写'}])
        output = self.root / 'corrected.epub'
        report = run('correct', book, '--edits', edits, '--output', output)
        self.assertEqual(report['corrections_applied'], 1)
        self.assertIn('编写', epub_text(output, 'OEBPS/c0001.xhtml'))

    def test_ambiguous_correction_is_refused(self):
        """同一段文字出现多次时无法定位，整批不写入。"""
        book = self.build()
        edits = self.write_json('edits.json', [{'document': 'OEBPS/c0001.xhtml', 'old': '第', 'new': '个'}])
        output = self.root / 'corrected.epub'
        code, error = run_failing('correct', book, '--edits', edits, '--output', output)
        self.assertEqual(code, 1)
        self.assertIn('expected one match', error)
        self.assertFalse(output.exists())
