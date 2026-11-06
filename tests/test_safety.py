"""文件安全：损坏或有问题的电子书会被挡下，原书不会被改坏。"""
import zipfile

from tests.support import Workspace, epub_entries, run, run_failing


class SafetyTests(Workspace):
    def test_zip_slip_is_rejected(self):
        """包含越界路径的压缩包直接拒绝处理。"""
        malicious = self.root / 'bad.epub'
        with zipfile.ZipFile(malicious, 'w') as archive:
            archive.writestr('../outside.txt', 'not extracted')
        code, error = run_failing('inspect', malicious)
        self.assertEqual(code, 1)
        self.assertIn('Unsafe ZIP', error)

    def test_broken_anchor_is_caught(self):
        """目录指向不存在的锚点时校验失败。"""
        book = self.build()
        entries = epub_entries(book)
        entries['OEBPS/nav.xhtml'] = entries['OEBPS/nav.xhtml'].replace(b'c0001.xhtml', b'c0001.xhtml#missing')
        broken = self.root / 'broken.epub'
        with zipfile.ZipFile(broken, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in entries.items():
                if name != 'mimetype':
                    archive.writestr(name, body)
        code, error = run_failing('validate', broken)
        self.assertEqual(code, 1)
        self.assertIn('Broken anchor', error)

    def test_duplicate_ids_are_caught(self):
        """同一个文件里出现重复 id 时校验失败。"""
        book = self.build()
        entries = epub_entries(book)
        entries['OEBPS/c0001.xhtml'] = entries['OEBPS/c0001.xhtml'].replace(
            b'<h1>', b'<span id="duplicate"/><span id="duplicate"/><h1>')
        broken = self.root / 'broken.epub'
        with zipfile.ZipFile(broken, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in entries.items():
                if name != 'mimetype':
                    archive.writestr(name, body)
        code, error = run_failing('validate', broken)
        self.assertEqual(code, 1)
        self.assertIn('Duplicate IDs', error)

    def test_encoded_text_files_are_detected(self):
        """UTF-16 和 GB18030 都能正确读出，乱码文件直接拒绝。"""
        for encoding in ('utf-16', 'gb18030'):
            path = self.root / ('source-' + encoding + '.txt')
            path.write_bytes('第1章 甲\n正文内容。\n'.encode(encoding))
            report = run('inspect', path)
            self.assertEqual(report['encoding'], encoding)
        broken = self.root / 'invalid.txt'
        broken.write_bytes(b'\xff')
        code, error = run_failing('inspect', broken)
        self.assertEqual(code, 1)
        self.assertIn('Encoding', error)
