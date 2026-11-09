"""Kindle 准备：邮件传书可加估算页码，已有页码不会被覆盖。"""
import struct

from tests.support import Workspace, epub_entries, epub_text, run, run_failing


class KindleTests(Workspace):
    def test_email_delivery_can_add_estimated_pages(self):
        """邮件传书加上估算页码，正文文字不变。"""
        book = self.build()
        output = self.root / 'paged.epub'
        report = run('kindle', book, '--method', 'email', '--pages', '--output', output)
        self.assertGreater(report['synthetic_pages'], 0)
        self.assertEqual(epub_text(book, 'OEBPS/c0001.xhtml'), epub_text(output, 'OEBPS/c0001.xhtml'))

    def test_existing_page_list_is_kept(self):
        """已有页码的书再准备一次，不会被重复分页。"""
        book = self.build()
        paged = self.root / 'paged.epub'
        run('kindle', book, '--method', 'email', '--pages', '--output', paged)
        again = self.root / 'again.epub'
        report = run('kindle', paged, '--method', 'email', '--pages', '--output', again)
        self.assertTrue(report['existing_page_list_preserved'])
        self.assertEqual(epub_entries(paged), epub_entries(again))

    def test_pagination_can_skip_a_document(self):
        """附录、报告等可以不参与分页。"""
        book = self.build()
        output = self.root / 'paged.epub'
        report = run('paginate', book, '--chars', '100', '--exclude', 'OEBPS/front.xhtml', '--output', output)
        self.assertIn('OEBPS/front.xhtml', report['excluded'])
        self.assertTrue(run('validate', output)['structural_validation'].startswith('passed'))

    def test_paginating_twice_is_refused(self):
        """已经有页码标记的书不会再次分页。"""
        book = self.build()
        paged = self.root / 'paged.epub'
        run('paginate', book, '--output', paged)
        code, error = run_failing('paginate', paged, '--output', self.root / 'twice.epub')
        self.assertEqual(code, 1)
        self.assertIn('Existing page-list', error)

    def test_usb_delivery_rejects_page_option(self):
        """USB 方式由 Calibre 处理页码，不接受 --pages。"""
        book = self.build()
        code, error = run_failing('kindle', book, '--method', 'usb', '--pages')
        self.assertEqual(code, 1)
        self.assertIn('Calibre', error)

    def test_usb_delivery_produces_azw3(self):
        """USB 传书产出 AZW3，且是有 MOBI 头的真文件。"""
        book = self.build()
        output = self.root / 'kindle.azw3'
        report = run('kindle', book, '--method', 'usb', '--output', output)
        self.assertEqual(report['format'], 'azw3')
        self.assertTrue(output.is_file())
        data = output.read_bytes()
        self.assertEqual(data[60:68], b'BOOKMOBI')
        record0 = data[struct.unpack('>L', data[78:82])[0]:]
        self.assertEqual(record0[16:20], b'MOBI')
        self.assertEqual(struct.unpack('>I', record0[36:40])[0], 8, '应该是 KF8')

    def test_usb_delivery_rewrites_the_output_extension(self):
        """输出路径写错了扩展名，也要按 USB 的约定产出 .azw3。"""
        book = self.build()
        wrong = self.root / 'kindle.epub'
        run('kindle', book, '--method', 'usb', '--output', wrong)
        self.assertFalse(wrong.exists(), '不该产出 .epub')
        self.assertTrue(wrong.with_suffix('.azw3').is_file())
