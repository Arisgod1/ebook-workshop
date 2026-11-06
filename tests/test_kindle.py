"""Kindle 准备：邮件传书可加估算页码，已有页码不会被覆盖。"""
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
