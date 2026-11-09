"""检查与比较：用户想知道结构有什么问题、不同版本差在哪。"""
from tests.support import Workspace, run, run_failing


class InspectTests(Workspace):
    def test_reports_structure_problems_without_quoting_the_book(self):
        """检查只报问题类型和位置，不展示章节名和正文。"""
        source = self.root / 'book.txt'
        source.write_text('正文\n第1章 私密标题\n相同段落。\n第3章\n番外：不展示\n中文i兑字。\n后记\n相同段落。',
                          encoding='utf-8')
        report = run('inspect', source)
        self.assertEqual(report['section_heading_hints'], {'正文': 1, '番外': 1, '后记': 1})
        self.assertEqual(report['empty_chapter_candidates'], [1, 3])
        self.assertEqual(report['numbering_jumps'], [[1, 3]])
        self.assertEqual(report['suspicious_paragraphs'], 1)
        self.assertNotIn('私密标题', str(report))
        self.assertNotIn('中文i兑字', str(report))

    def test_detects_repeated_and_missing_chapters(self):
        """重复段落和空章会被标出来，供人工判断。"""
        source = self.root / 'book.txt'
        source.write_text('第1章 甲\n重复的句子。\n第2章 乙\n重复的句子。\n第3章 丙\n', encoding='utf-8')
        report = run('inspect', source)
        self.assertEqual(report['repeated_paragraphs'], 1)
        self.assertEqual(report['empty_chapter_candidates'], [3])

    def test_show_headings_is_opt_in(self):
        """默认不列章名，用户明确要求时才显示。"""
        book = self.build()
        self.assertNotIn('headings', run('inspect', book))
        self.assertIn('headings', run('inspect', book, '--show-headings'))

    def test_compare_reports_matching_paragraphs(self):
        """比较版本时按段落重合度给出线索。"""
        first = self.root / 'first.txt'
        second = self.root / 'second.txt'
        first.write_text('第1章\n相同段落。\n独有的一段。\n', encoding='utf-8')
        second.write_text('第1章\n相 同 段 落。\n', encoding='utf-8')
        report = run('compare', first, second)
        self.assertEqual(report['comparisons'][0]['matching_paragraphs'], 1)
        self.assertEqual(report['comparisons'][0]['other_only_paragraphs'], 0)

    def test_compare_needs_two_files(self):
        """只给一个文件时提示用法错误。"""
        book = self.build()
        code, error = run_failing('compare', book)
        self.assertEqual(code, 2)
        self.assertIn('at least two files', error)

    def test_chinese_chapter_numbers_are_recognized(self):
        """「第一章」这类中文章号要能识别，编号才算得出来。"""
        path = self.root / 'cn.txt'
        path.write_text('第一章 甲\n正文。\n第二章 乙\n更多。\n第五章 丙\n末段。\n', encoding='utf-8')
        report = run('inspect', path)
        self.assertEqual(report['chapter_headings_recognized'], 3)
        self.assertEqual(report['declared_numbers'], [1, 2, 5])
        self.assertEqual(report['numbering_jumps'], [[2, 5]])

    def test_custom_pattern_with_chinese_numbers_does_not_crash(self):
        """自定义 pattern 把中文数字放进 number 组时不能崩。"""
        path = self.root / 'cn2.txt'
        path.write_text('第一章 甲\n正文。\n第二章 乙\n', encoding='utf-8')
        pattern = r'^第(?P<number>[一二三四五六七八九十]+)章\s*(?P<title>.*)$'
        report = run('inspect', path, '--pattern', pattern)
        self.assertEqual(report['declared_numbers'], [1, 2])
