"""格式转换：借助 Calibre 把 Word、PDF 等转成 EPUB。"""
import os
import stat
import tempfile
from pathlib import Path

from tests.support import Workspace, epub_text, run, run_failing


class FakeCalibre:
    """A stand-in for ebook-convert that writes the expected output file."""

    def __init__(self, directory):
        self.script = Path(directory) / 'ebook-convert'
        self.script.write_text(
            '#!/bin/sh\n'
            'out="$2"\n'
            'case "$out" in\n'
            '  *.epub) cp "$FAKE_EPUB" "$out" ;;\n'
            '  *) printf "fake azw3" > "$out" ;;\n'
            'esac\n')
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def path(self):
        return str(self.script)


class ConvertTests(Workspace):
    def setUp(self):
        super().setUp()
        self.converted = self.build('converted.epub')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calibre = FakeCalibre(self.temp.name)
        self.previous_path = os.environ.get('PATH', '')
        os.environ['PATH'] = self.temp.name + os.pathsep + self.previous_path
        self.addCleanup(lambda: os.environ.__setitem__('PATH', self.previous_path))
        self.previous_fixture = os.environ.get('FAKE_EPUB')
        os.environ['FAKE_EPUB'] = str(self.converted)
        self.addCleanup(lambda: os.environ.__setitem__('FAKE_EPUB', self.previous_fixture or ''))

    def test_word_file_is_converted_and_keeps_metadata(self):
        """Word 转成 EPUB 后书名作者正确，正文完整。"""
        source = self.root / 'source.docx'
        source.write_bytes(b'synthetic source')
        output = self.root / 'out.epub'
        report = run('convert', source, '--title', '示例书名', '--author', '测试作者', '--output', output)
        self.assertEqual(Path(report['output']).name, 'out.epub')
        self.assertIn('示例书名', epub_text(output, 'OEBPS/title.xhtml'))
        self.assertTrue(run('validate', output)['structural_validation'].startswith('passed'))

    def test_source_file_is_never_modified(self):
        """转换只读取源文件，不动它。"""
        source = self.root / 'source.docx'
        source.write_bytes(b'synthetic source')
        run('convert', source, '--title', '示例书名', '--author', '测试作者', '--output', self.root / 'out.epub')
        self.assertEqual(source.read_bytes(), b'synthetic source')

    def test_already_epub_is_not_converted_again(self):
        """已经是 EPUB 的文件直接提示用其他命令。"""
        code, error = run_failing('convert', self.converted, '--output', self.root / 'out.epub')
        self.assertEqual(code, 1)
        self.assertIn('already EPUB', error)

    def test_legacy_doc_is_refused_with_advice(self):
        """旧版 .doc 先让用户在 Word 或 LibreOffice 里另存为 .docx。"""
        source = self.root / 'source.doc'
        source.write_bytes(b'legacy')
        code, error = run_failing('convert', source, '--output', self.root / 'out.epub')
        self.assertEqual(code, 1)
        self.assertIn('.docx', error)
