"""输出文件名与图片格式识别。

这两件事跟 EPUB 容器本身无关，但每个命令都要用：输出路径默认取原书旁的
「整理后/书名.epub」，图片则要按字节判断格式与尺寸。
放在 epub.py 里时，容器层被迫依赖 pathlib 和 struct 的细节。
"""

import re
import struct
from pathlib import Path

# Windows 保留的设备名，叫这些名字的文件建不出来。
RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL'} | {
    'COM%d' % i for i in range(1, 10)} | {'LPT%d' % i for i in range(1, 10)}

# Windows 的单个文件名上限是 255 个字符；给扩展名留出余地。
MAX_NAME_LENGTH = 120


def safe_filename(title, extension=''):
    """把书名变成各平台都能用的文件名。"""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')
    if not safe:
        return ''
    # 保留名要改掉，否则 Windows 上连文件都建不出来。
    if safe.upper() in RESERVED_NAMES or safe.split('.')[0].upper() in RESERVED_NAMES:
        safe = '_' + safe
    # 中文标题可能很长，截断时按字符切，不会劈坏 UTF-8。
    if len(safe) > MAX_NAME_LENGTH:
        safe = safe[:MAX_NAME_LENGTH].rstrip(' .')
    return safe + extension


def output_for(args, title, extension='.epub'):
    """默认输出位置：原书旁的「整理后」文件夹，文件名用书名。"""
    if args.output:
        return args.output
    name = safe_filename(title, extension)
    if not name:
        raise ValueError('No usable book title; specify --output')
    return str(Path(args.input).parent / '整理后' / name)


def image_info(data):
    """读 PNG 或 JPEG 的尺寸，不解码、不重新编码。"""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'image/png', 'png', width, height
    if data.startswith(b'\xff\xd8'):
        index = 2
        while index < len(data):
            if data[index] != 255:
                index += 1
                continue
            while index < len(data) and data[index] == 255:
                index += 1
            marker = data[index]
            index += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            size = struct.unpack('>H', data[index:index + 2])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack('>HH', data[index + 3:index + 7])
                return 'image/jpeg', 'jpg', width, height
            if size < 2:
                break
            index += size
    raise ValueError('Cover must be an intact PNG or JPEG (copy original bytes; no silent conversion)')
