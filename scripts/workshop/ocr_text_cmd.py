"""把批量 OCR 的逐页文本清洗成一份可转 EPUB 的 TXT。

OCR 引擎在外面跑，这里只接它产出的文字：合并被扫描件硬换行切断的句子、
去掉反复出现的页眉页码，再交给已有的 build 命令按章号分章。
"""

import os
import re
import tempfile
from pathlib import Path

from . import epub, names, text
from . import ocrtext


def _natural_key(path):
    """按连续数字段排序：page2.txt 要排在 page10.txt 前面。"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', path.name)]


def _default_output(paths):
    """默认写到第一个输入旁的「整理后」，书名去掉末尾的页号。"""
    first = paths[0]
    stem = first.stem
    cleaned = re.sub(r'(?:[-_ 　]?)(?:第)?\d{1,5}\s*页?$', '', stem).strip('-_ 　')
    title = cleaned or stem or '整理文本'
    if not names.safe_filename(title, '.txt'):
        raise ValueError('No usable book title; specify --output')
    return str(first.parent / '整理后' / (title + '.txt'))


def _write_atomic(path, content):
    """原子写 TXT：先写临时文件再改名，不覆盖已有输出。"""
    path = Path(path)
    if path.exists():
        raise ValueError('Output exists; choose a new path (source files are never overwritten)')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix='.part')
    os.close(handle)
    try:
        if os.name != 'nt':
            os.chmod(temporary, epub.default_file_mode())
        Path(temporary).write_text(content, encoding='utf-8')
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run(args):
    paths = sorted((Path(p) for p in args.inputs), key=_natural_key)
    if not paths:
        raise ValueError('Supply one or more OCR text files')
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise ValueError('OCR text file not found: ' + ', '.join(missing[:3]))

    pages = []
    encoding_used = None
    for path in paths:
        value, encoding = text.decode(path, args.encoding)
        pages.append(value)
        if encoding_used is None:
            encoding_used = encoding
        elif encoding_used != encoding:
            raise ValueError('OCR pages use mixed encodings; supply --encoding')

    result = ocrtext.clean_pages(
        pages, args.pattern,
        join_lines=not args.no_join,
        drop_running_heads=not args.keep_running_heads,
        min_repeat=args.min_repeat)
    output = args.output or _default_output(paths)
    _write_atomic(output, result['text'])

    report = dict(result['stats'])
    report.update({'output': output, 'encoding': encoding_used,
                   'inputs': [str(p) for p in paths]})
    epub.report(report)


def register(sub):
    parser = sub.add_parser(
        'ocr-text', help='Clean per-page OCR text into a chapter-ready TXT')
    parser.add_argument('inputs', nargs='+', help='Per-page OCR text files')
    parser.add_argument('--output', help='Destination TXT (default: 整理后/书名.txt)')
    parser.add_argument('--encoding', help='Force an input encoding')
    parser.add_argument('--pattern', default=text.PATTERN,
                        help='Chapter heading regex; headings never get merged')
    parser.add_argument('--no-join', action='store_true',
                        help='Keep every physical line (for poetry or scripts)')
    parser.add_argument('--keep-running-heads', action='store_true',
                        help='Do not remove repeated headers or page numbers')
    parser.add_argument('--min-repeat', type=int, default=3,
                        help='Pages a line must appear in to count as a running head')
    parser.set_defaults(run=run)
