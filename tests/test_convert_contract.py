"""命令行契约：格式选择、默认文件名、错误提示、降级行为。

这些是用户直接感知的约定：--format 到底产出什么、文件叫什么、
出错时看到什么话。改坏了不会有异常，只会让用户拿到不对的东西。
"""

import struct
import unittest
from pathlib import Path

from tests.support import Workspace, run, run_failing


class ConvertContractTests(Workspace):
    """convert 子命令的对外约定。"""

    def setUp(self):
        super().setUp()
        self.book = self.build()

    def convert(self, *args):
        return run('convert', *args)

    def test_format_epub_produces_an_epub(self):
        """--format epub 产出 EPUB，扩展名要对。"""
        report = self.convert(str(self.book), '--format', 'epub',
                              '--output', str(self.root / 'out.epub'))
        self.assertEqual(report['format'], 'epub')

    def test_format_mobi_produces_a_mobi(self):
        """--format mobi 产出 MOBI，不能静默变成 EPUB。"""
        output = self.root / 'out.mobi'
        report = self.convert(str(self.book), '--format', 'mobi', '--output', str(output))
        self.assertEqual(report['format'], 'mobi')
        self.assertTrue(output.is_file())
        data = output.read_bytes()
        self.assertEqual(data[60:68], b'BOOKMOBI')

    def test_format_azw3_produces_an_azw3(self):
        """--format azw3 产出纯 KF8 的 AZW3。"""
        output = self.root / 'out.azw3'
        report = self.convert(str(self.book), '--format', 'azw3', '--output', str(output))
        self.assertEqual(report['format'], 'azw3')
        data = output.read_bytes()
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(struct.unpack('>I', record0[36:40])[0], 8, '应该是 KF8')

    def test_output_extension_decides_the_format(self):
        """给了 --output 就按它的扩展名走，--format 只在没有 --output 时生效。"""
        report = self.convert(str(self.book), '--format', 'mobi',
                              '--output', str(self.root / 'x.azw3'))
        self.assertEqual(report['format'], 'azw3')

    def test_default_output_uses_the_book_title(self):
        """不给 --output 时，默认文件名用书名而不是输入文件名。"""
        report = self.convert(str(self.book))
        name = Path(report['output']).name
        self.assertTrue(name.endswith('.epub'), name)
        self.assertNotIn('book', name.split('.')[0].lower()[:4])

    def test_existing_output_is_not_overwritten(self):
        """目标已存在时报错退出，已有文件不动。"""
        target = self.root / 'taken.epub'
        target.write_bytes(b'keep me')
        code, error = run_failing('convert', str(self.book), '--output', str(target))
        self.assertEqual(code, 1)
        self.assertEqual(target.read_bytes(), b'keep me')

    def test_unsupported_format_says_what_is_supported(self):
        """不认识的格式要说清支持哪些，而不是抛堆栈。"""
        source = self.root / 'a.pdf'
        source.write_bytes(b'%PDF-1.4 not really a pdf')
        code, error = run_failing('convert', str(source), '--output', str(self.root / 'a.epub'))
        self.assertEqual(code, 1)
        self.assertIn('Error', error)
        self.assertNotIn('Traceback', error)

    def test_malformed_input_reports_one_line(self):
        """坏文件要报一行 Error，不能吐堆栈。"""
        source = self.root / 'broken.epub'
        source.write_bytes(b'not a zip file at all')
        code, error = run_failing('convert', str(source), '--output', str(self.root / 'b.epub'))
        self.assertEqual(code, 1)
        self.assertTrue(error.startswith('Error:'), error)
        self.assertNotIn('Traceback', error)
        self.assertFalse((self.root / 'b.epub').exists(), '失败时不该留下文件')


if __name__ == '__main__':
    unittest.main()
