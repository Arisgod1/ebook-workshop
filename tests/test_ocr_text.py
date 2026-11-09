"""OCR 文本清洗的回归用例：按用户能看到的结果测。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from tests.support import run, run_failing
from workshop import ocrtext, text


class OcrCleanTests(unittest.TestCase):
    """清洗本身：合并、页眉、章标题、页边界。"""

    def clean(self, pages, **kwargs):
        return ocrtext.clean_pages(pages, text.PATTERN, **kwargs)

    def test_broken_chinese_line_is_joined(self):
        """OCR 把一个句子从中间切断时，要接回去。"""
        result = self.clean(['第一章 开篇\n这是第一段，OCR 可\n能会把一行断开。'])
        self.assertIn('这是第一段，OCR 可能会把一行断开。', result['text'])
        self.assertEqual(result['stats']['lines_merged'], 1)

    def test_sentence_end_starts_a_new_paragraph(self):
        """句末标点后的行是新段落，不能粘上去。"""
        result = self.clean(['第一章 开篇\n第一句。\n第二句。'])
        self.assertIn('第一句。\n\n第二句。', result['text'])
        self.assertEqual(result['stats']['lines_merged'], 0)

    def test_heading_is_never_merged_into_body(self):
        """章标题前后都不合并，否则 build 认不出章节。"""
        result = self.clean(['正文尾巴\n第二章 继续\n下一章开头。'])
        prefix, chapters = text.split_text(result['text'], text.PATTERN)
        self.assertEqual([c['heading'] for c in chapters], ['第二章 继续'])

    def test_page_boundary_is_never_joined(self):
        """页末和下一页首绝不粘死。"""
        result = self.clean(['第一章 开篇\n页末没有标点', '下一页开头。'])
        self.assertIn('页末没有标点\n\n下一页开头。', result['text'])

    def test_page_numbers_and_running_heads_are_removed(self):
        """跨页重复的页眉和变化的页码一起删掉。"""
        pages = ['我的电子书\n第一章 开篇\n正文。\n—— 1 ——',
                 '我的电子书\n这是第二段。\n—— 2 ——',
                 '我的电子书\n第三章 结束\n结尾。\n—— 3 ——']
        result = self.clean(pages, min_repeat=3)
        self.assertNotIn('我的电子书', result['text'])
        self.assertNotIn('—— 1 ——', result['text'])
        self.assertIn('正文。', result['text'])
        self.assertGreaterEqual(result['stats']['running_heads_removed'], 4)

    def test_short_body_text_is_not_mistaken_for_a_running_head(self):
        """单页或短正文不能因为「重复」被删掉。"""
        raw = '第一章 开篇\n第一段文字，OCR 可\n能会把一行断开。\n1\n'
        for repeat in (1, 2, 3):
            result = self.clean([raw], min_repeat=repeat)
            self.assertIn('第一段文字，OCR 可能会把一行断开。', result['text'])
        self.assertNotIn('\n1\n', result['text'])

    def test_no_join_keeps_every_physical_line(self):
        """诗歌/剧本用 --no-join 时只归一空白，不合并任何行。"""
        result = self.clean(['第一章 开篇\n第一行没有标点\n第二行也没有'],
                            join_lines=False)
        self.assertIn('第一行没有标点\n\n第二行也没有', result['text'])
        self.assertEqual(result['stats']['lines_merged'], 0)

    def test_full_width_spaces_are_normalized(self):
        result = self.clean(['第一章 开篇\n有　全角空格的一段。'])
        self.assertIn('有 全角空格的一段。', result['text'])


class OcrCommandTests(unittest.TestCase):
    """真命令：排序、默认路径、不覆盖、端到端接 build。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pages = self.root / 'page'
        self.pages.mkdir()

    def write(self, name, body):
        path = self.pages / name
        path.write_text(body, encoding='utf-8')
        return str(path)

    def test_files_are_sorted_by_natural_page_number(self):
        """page2.txt 要排在 page10.txt 前面。"""
        self.write('page1.txt', '第一章 开篇\n第一页。')
        self.write('page2.txt', '第二页。')
        self.write('page10.txt', '第十页。')
        output = self.root / 'clean.txt'
        report = run('ocr-text', self.pages / 'page1.txt',
                     self.pages / 'page10.txt', self.pages / 'page2.txt',
                     '--output', output)
        self.assertEqual(report['pages'], 3)
        content = output.read_text(encoding='utf-8')
        self.assertLess(content.index('第二页'), content.index('第十页'))

    def test_output_is_not_overwritten(self):
        source = self.write('page1.txt', '第一章 开篇\n正文。')
        output = self.root / 'clean.txt'
        output.write_text('keep me', encoding='utf-8')
        code, error = run_failing('ocr-text', source, '--output', output)
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding='utf-8'), 'keep me')

    def test_default_output_goes_to_the_整理后_folder(self):
        source = self.write('page1.txt', '第一章 开篇\n正文。')
        report = run('ocr-text', source)
        self.assertTrue(Path(report['output']).is_file())
        self.assertEqual(Path(report['output']).parent.name, '整理后')
        self.assertTrue(report['output'].endswith('.txt'))

    def test_cleaned_text_builds_into_a_readable_epub(self):
        """清洗 → build → proofread 全链路。"""
        self.write('page1.txt', '我的电子书\n第一章 开篇\n这是第一段，OCR 可\n能会把一行断开。\n—— 1 ——')
        self.write('page2.txt', '我的电子书\n还有 i兑 这样的错字。\n—— 2 ——')
        self.write('page3.txt', '我的电子书\n第二章 继续\n最后一段。\n—— 3 ——')
        cleaned = self.root / 'clean.txt'
        run('ocr-text', self.pages / 'page1.txt', self.pages / 'page2.txt',
            self.pages / 'page3.txt', '--output', cleaned)
        book = self.root / 'book.epub'
        built = run('build', cleaned, '--title', '整理后', '--author', '某人',
                    '--output', book)
        self.assertEqual(built['chapters'], 2)
        with zipfile.ZipFile(book) as archive:
            body = archive.read('OEBPS/c0001.xhtml').decode()
        self.assertIn('这是第一段，OCR 可能会把一行断开。', body)
        self.assertNotIn('我的电子书', body)
        job = self.root / 'job.json'
        run('proofread', book, '--job', job, '--lexicon', 'starter')
        candidates = json.loads(job.read_text(encoding='utf-8'))['candidates']
        self.assertTrue(any(c['old'] == 'i兑' and c['suggestion'] == '说'
                            for c in candidates))


if __name__ == '__main__':
    unittest.main()
