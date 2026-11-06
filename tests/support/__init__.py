"""Shared fixtures and helpers for the feature tests."""
import base64
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'scripts' / 'ebook.py'
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH1kAAAAASUVORK5CYII=')

# A small book with front matter, out-of-order chapter numbers, an afterword and a repeated paragraph.
BOOK_TEXT = (
    '作者资料：测试作者\n\n'
    '第1章 开篇\n'
    '这是一份自造的测试文字 & < >。\n'
    '第5章 原有乱号\n'
    '第二节保留源文件的编号，不自动猜改。\n'
    '第3章 番外\n'
    '最后一段应完整保留。\n'
    '作者有话要说：\n'
    '这也是需要保留的内容。\n'
)


def run(*args, ok=True):
    """Run the command line tool and return its parsed report."""
    result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True)
    if ok and result.returncode:
        raise AssertionError(f'command failed: {args}\n{result.stderr}')
    if not ok:
        return result
    return json.loads(result.stdout) if result.stdout.strip() else {}


def run_failing(*args):
    """Run a command that is expected to fail; returns (returncode, stderr)."""
    result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True)
    return result.returncode, result.stderr


def epub_entries(path):
    """Read the files inside an EPUB."""
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def epub_text(path, document):
    """The visible text of one document inside an EPUB."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(epub_entries(path)[document])
    return ''.join(root.itertext())


class Workspace(unittest.TestCase):
    """Base class giving each test a private directory and a ready-made book."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.txt'
        self.source.write_text(BOOK_TEXT, encoding='utf-8')
        self.cover = self.root / 'cover.png'
        self.cover.write_bytes(PNG)

    def build(self, name='book.epub', extra=()):
        """Create an EPUB from the sample text and return its path."""
        output = self.root / name
        run('build', self.source, '--title', '示例书名', '--author', '测试作者', '--output', output, *extra)
        return output

    def write_json(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path
