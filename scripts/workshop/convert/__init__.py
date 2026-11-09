"""格式互转的入口：按扩展名挑选读入与写出格式。

支持原生读入：EPUB、DOCX、ODT、HTML。
支持原生写出：EPUB、MOBI、AZW3。

Calibre 存在时仍可作为更高保真度的选项，由调用方指定。
"""

import os
import tempfile
from pathlib import Path

from .. import epub
from . import docx_input, epub_input, epub_output, html_input, mobi_writer, odt_input

# 输入格式：扩展名 -> 读取函数。
READERS = {
    '.epub': epub_input.read,
    '.docx': docx_input.read,
    '.odt': odt_input.read,
    '.htm': html_input.read,
    '.html': html_input.read,
}

# 输出格式：扩展名 -> 写出函数。
WRITERS = {
    '.epub': lambda book, path: epub_output.write(book, path),
    '.mobi': lambda book, path: mobi_writer.build_mobi(book, kf8=False),
    '.azw3': lambda book, path: mobi_writer.build_mobi(book, kf8=True),
}


class ConversionError(ValueError):
    """无法完成转换。"""


def supported_input(path):
    """这个文件能不能原生读入。"""
    return Path(path).suffix.lower() in READERS


def supported_output(path):
    """这个目标能不能原生写出。"""
    return Path(path).suffix.lower() in WRITERS


def read(path):
    """按扩展名读入一个文件，返回中间表示。"""
    suffix = Path(path).suffix.lower()
    reader = READERS.get(suffix)
    if reader is None:
        raise ConversionError(
            'Cannot read %s natively; supported: %s'
            % (suffix or 'this file', ', '.join(sorted(READERS))))
    book = reader(path)
    # 书里没写书名时用文件名顶上：EPUB 的 dc:title 是必填项，
    # 输出文件名也要有个依据。Calibre 的做法一致。
    if not book.title:
        book.title = Path(path).stem
    return book


def write(book, path):
    """把中间表示写成目标文件。

    所有格式走同一条路径：先写临时文件再改名，中途失败不会在目标路径
    留下半个文件。EPUB 写出内部也自己落位，因此直接调用即可。
    """
    suffix = Path(path).suffix.lower()
    writer = WRITERS.get(suffix)
    if writer is None:
        raise ConversionError(
            'Cannot write %s natively; supported: %s'
            % (suffix or 'this file', ', '.join(sorted(WRITERS))))
    output = Path(path)
    if output.exists():
        raise ConversionError('Output exists; choose a new path (source files are never overwritten)')
    output.parent.mkdir(parents=True, exist_ok=True)
    if suffix == '.epub':
        epub_output.write(book, output)
    else:
        handle, temporary = tempfile.mkstemp(dir=str(output.parent), suffix='.part')
        os.close(handle)
        try:
            # mkstemp 建的是 0600，改回普通新建文件的权限。
            # Windows 上没有 POSIX 权限位，跳过。
            if os.name != 'nt':
                os.chmod(temporary, epub.default_file_mode())
            Path(temporary).write_bytes(writer(book, output))
            os.replace(temporary, str(output))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    return {'output': str(output), 'bytes': output.stat().st_size,
            'format': suffix.lstrip('.')}


def convert(source, target):
    """把一个文件转成另一个格式。"""
    book = read(source)
    result = write(book, target)
    result.update(book.summary())
    result['source'] = str(source)
    return result
