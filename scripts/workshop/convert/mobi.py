"""MOBI 写出：把中间表示的书写成 Kindle 可读的 MOBI / AZW3。

文件结构（自上而下）：

  PalmDB 头      78 字节 + 每记录 8 字节索引
  PalmDB 记录 0  MOBI 头（record0）+ EXTH + 全书标题
  PalmDB 记录 1… 文本记录、索引、目录、图片等

MOBI 6 与 KF8（AZW3）可以合并在同一个文件里：MOBI 6 部分在前，
KF8 部分在后，靠 EXTH 里的边界偏移区分。Kindle 读到 KF8 边界后
按 KF8 解析，读不到就退回 MOBI 6。

实现按公开的 MOBI 格式规范独立编写，不依赖 Calibre。
"""

import struct
import time
import uuid


# PalmDB 头固定 78 字节，之后是每记录 8 字节的索引表。
PDB_HEADER_SIZE = 78
PDB_RECORD_INFO_SIZE = 8

# EXTH 记录类型。阅读器真正会读的是少数几个，其余用于兼容既有工具。
EXTH_CREATOR = 100
EXTH_PUBLISHER = 101
EXTH_DESCRIPTION = 103
EXTH_SUBJECT = 105
EXTH_PUBDATE = 106
EXTH_RIGHTS = 109
EXTH_SOURCE = 112
EXTH_STARTREADING = 116
EXTH_NUM_OF_RESOURCES = 125
EXTH_KF8_UNKNOWN_COUNT = 131
EXTH_COVEROFFSET = 201
EXTH_HASFAKECOVER = 203
EXTH_TITLE = 503
EXTH_LANGUAGE = 524
EXTH_PRIMARY_WRITING_MODE = 525
EXTH_PAGE_PROGRESSION_DIRECTION = 527
EXTH_OVERRIDE_KINDLE_FONTS = 528

# EXTH 里的固定值，用来让阅读器把文件当成正规出版的书处理。
CDETYPE_BOOK = b'EBOK'
# 204-207 是 kindlegen 的伪装字段，四个位置的含义固定：
# 平台号（201 表示 kindlegen 的通用平台）、版本号、修订号、保留字段。
KINDLEGEN_PLATFORM = 201
KINDLEGEN_VERSION = 2
KINDLEGEN_REVISION = 9
KINDLEGEN_RESERVED = 0
KINDLEGEN2_REVISION = b'0730-890adc2'

# CNCX 字符串池：单串最多 500 个字符（是字符不是字节，中文编码后可能更长），
# 单条记录最多 0x10000 - 1024 字节，与 kindlegen 对齐。
CNCX_MAX_STRING_LENGTH = 500
CNCX_RECORD_LIMIT = 0x10000 - 1024

# MOBI 索引的标签号与位掩码。位掩码是标签在控制字节里的位置。
TAG_OFFSET = 1
TAG_SIZE = 2
TAG_LABEL_OFFSET = 3
TAG_DEPTH = 4
# 每条目的值数。KF8 的标签定义里直接给出，不从掩码推：
# 掩码只说明控制字节里占几位。
VALUES_PER_ENTRY = {6: 2, 11: 3}

TAG_BITMASKS = {11: 0b1}
TAG_BITMASKS.update({x: (1 << i) for i, x in enumerate([1, 2, 3, 4, 5, 21, 22, 23])})
TAG_BITMASKS.update({x: (1 << i) for i, x in enumerate([69, 70, 71, 72, 73])})

# 目录索引用的标签顺序。末位 0 是结束标记。
FLAT_BOOK_TAGS = (TAG_OFFSET, TAG_SIZE, TAG_LABEL_OFFSET, TAG_DEPTH, 0)

# 索引头固定长度。
INDEX_HEADER_LENGTH = 192

# 记录末尾标记。
EOF_RECORD = b'\xe9\x8e\r\n'


def align_block(raw, multiple=4):
    """补齐到指定长度倍数。MOBI 的索引与块记录都要求 4 字节对齐。"""
    extra = len(raw) % multiple
    return raw if extra == 0 else raw + b'\0' * (multiple - extra)


def encode_number(value):
    """MOBI 的变长整数：大端，每字节 7 位有效，首字节高位标记结束。"""
    if value < 0:
        raise ValueError('Cannot encode negative numbers')
    out = bytearray()
    while True:
        out.append(value & 0x7F)
        value >>= 7
        if value == 0:
            break
    out[0] |= 0x80
    out.reverse()
    return bytes(out)


def decode_number(data):
    """读一个变长整数，返回 (值, 消耗的字节数)。"""
    value = 0
    for index, byte in enumerate(data):
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80:
            return value, index + 1
    raise ValueError('Truncated variable-width integer')


def to_base(number, base=32, min_digits=None):
    """把数字转成指定进制的字符串。"""
    # 大写字母：读端的 kindle:pos:fid 正则只认大写。
    digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    if number == 0:
        value = '0'
    else:
        value = ''
        while number:
            number, remainder = divmod(number, base)
            value = digits[remainder] + value
    return value.rjust(min_digits, '0') if min_digits else value


class Metadata:
    """写出 MOBI 时用的元数据。"""

    def __init__(self, title='', author='', language='zh-CN', identifier=None,
                 publisher='', description='', rights='All rights reserved', pubdate=None):
        self.title = title
        self.author = author
        self.language = language
        self.identifier = identifier
        self.publisher = publisher
        self.description = description
        self.rights = rights
        self.pubdate = pubdate or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def build_exth(metadata, cover_offset=None, start_offset=None,
               num_of_resources=None, kf8_unknown_count=0,
               primary_writing_mode=None, page_progression_direction=None):
    """构造 EXTH 头。

    每条记录是 4 字节类型、4 字节总长度（含这 8 字节）加负载。
    头部本身是 'EXTH' 加总长度和记录数。

    缩略图（202 / 129）与 KF8 头位置（121）一律不写：前者 Kindle 会自己
    从封面生成，后者只有联合文件才需要；本项目的 .azw3 是纯 KF8 standalone。
    """
    body = bytearray()
    count = 0

    def text(code, value):
        nonlocal count
        if not value:
            return
        data = value.encode('utf-8')
        body.extend(struct.pack('>II', code, len(data) + 8))
        body.extend(data)
        count += 1

    def number(code, value):
        nonlocal count
        body.extend(struct.pack('>III', code, 12, value))
        count += 1

    text(EXTH_CREATOR, metadata.author)
    text(EXTH_PUBLISHER, metadata.publisher)
    text(EXTH_DESCRIPTION, metadata.description)
    text(EXTH_SUBJECT, 'ebook')
    text(EXTH_RIGHTS, metadata.rights)
    text(EXTH_TITLE, metadata.title)
    text(EXTH_LANGUAGE, metadata.language)
    text(EXTH_PUBDATE, metadata.pubdate)
    # EXTH 112 是「生成工具」标识。不要冒用 calibre: 前缀：
    # 那会让下游按 Calibre 的行为推断这本书的来历。
    text(EXTH_SOURCE, 'ebook-workshop:' + (metadata.identifier or str(uuid.uuid4())))

    body.extend(struct.pack('>II', 501, 12))
    body.extend(CDETYPE_BOOK)
    count += 1

    for code, value in zip((204, 205, 206, 207),
                           (KINDLEGEN_PLATFORM, KINDLEGEN_VERSION,
                            KINDLEGEN_REVISION, KINDLEGEN_RESERVED)):
        number(code, value)

    text(535, KINDLEGEN2_REVISION.decode())

    if cover_offset is not None:
        number(EXTH_COVEROFFSET, cover_offset)
        number(EXTH_HASFAKECOVER, 0)
    if start_offset is not None:
        number(EXTH_STARTREADING, start_offset)
    if num_of_resources is not None:
        number(EXTH_NUM_OF_RESOURCES, num_of_resources)
    if kf8_unknown_count is not None:
        number(EXTH_KF8_UNKNOWN_COUNT, kf8_unknown_count)
    if primary_writing_mode:
        text(EXTH_PRIMARY_WRITING_MODE, primary_writing_mode)
    if page_progression_direction in ('rtl', 'ltr', 'default'):
        text(EXTH_PAGE_PROGRESSION_DIRECTION, page_progression_direction)
    text(EXTH_OVERRIDE_KINDLE_FONTS, 'true')

    # 长度字段只算记录本身，不含尾部的对齐填充。
    # 填充是为了让后面的数据 4 字节对齐，阅读器按长度字段截取后忽略填充。
    body = bytes(body)
    padded = align_block(body)
    return b''.join([b'EXTH', struct.pack('>II', len(body) + 12, count), padded])


def build_pdb_header(identity, title, record_lengths):
    """构造 PalmDB 头与记录索引表。

    名称字段只放 ASCII：老阅读器按字节读这个名字，中文会读成乱码。
    每条记录索引的 3 字节 uniqueID 写 2 * 记录号（Calibre 的约定），
    全 0 会被某些读端当成无效记录。
    """
    name = ''.join(c if c.isascii() and (c.isalnum() or c in ' -') else '_'
                   for c in title).encode('ascii', 'replace')
    name = name[:31].ljust(31, b'\0')
    now = int(time.time())
    count = len(record_lengths)

    out = bytearray()
    out.extend(name + b'\0')
    out.extend(struct.pack('>HH', 0, 0))              # 属性与版本
    out.extend(struct.pack('>II', now, now))          # 创建与修改时间
    out.extend(struct.pack('>IIII', 0, 0, 0, 0))      # 唯一 id 与两个序号
    out.extend(identity.ljust(3, '\0')[:8].encode('utf-8'))
    out.extend(struct.pack('>II', count, 0))
    out.extend(struct.pack('>H', count))

    offset = PDB_HEADER_SIZE + PDB_RECORD_INFO_SIZE * count + 2
    for index, length in enumerate(record_lengths):
        unique = 2 * index
        out.extend(struct.pack('>LBBBB', offset, 0,
                               (unique >> 16) & 0xFF, (unique >> 8) & 0xFF, unique & 0xFF))
        offset += length
    out.extend(b'\0\0')
    return bytes(out)


def build_cncx(strings):
    """把一组字符串编码成 CNCX 记录列表。

    CNCX 是索引用的字符串池，每条记录是若干个
    ``<变长整数 字节数><UTF-8 文字>``。返回 ``(records, offsets)``：
    records 是要追加在索引记录之后的记录列表，offsets 是
    ``原文 -> 池内偏移`` 的映射。

    偏移的算法与 Calibre 一致：跨记录时加上 ``记录号 * 0x10000``，
    单条记录超过 ``CNCX_RECORD_LIMIT`` 就开下一条。

    标题先按**字符**截断到 500。按字节截会把一个多字节字符劈成两半，
    池里留下坏数据，读端整条标题都解不出来。
    """
    offsets = {}
    records = []
    pool = bytearray()
    base = 0
    for text in strings:
        if text in offsets:
            continue
        encoded = text[:CNCX_MAX_STRING_LENGTH].encode('utf-8')
        raw = encode_number(len(encoded)) + encoded
        if pool and len(pool) + len(raw) > CNCX_RECORD_LIMIT:
            records.append(bytes(align_block(bytes(pool))))
            pool = bytearray()
            base = len(records) * 0x10000
        if pool:
            offsets[text] = base + len(pool)
        else:
            offsets[text] = base
        pool.extend(raw)
    if pool:
        records.append(bytes(align_block(bytes(pool))))
    return records, offsets
