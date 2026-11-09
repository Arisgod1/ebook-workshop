"""MOBI 记录头与索引记录的构造。

MOBI 头（record0）是一段定长结构，末尾跟着 EXTH 和全书标题。
索引记录用 INDX / TAGX / IDXT 四件套拼成。

KF8 与 MOBI 6 是两套布局：本模块对两者分别提供一个构造函数，
字段偏移相同而语义不同的位置（192/194、索引槽、头长）各自处理。
"""

import struct

from .mobi import (FLAT_BOOK_TAGS, INDEX_HEADER_LENGTH, TAG_BITMASKS,
                   VALUES_PER_ENTRY, align_block, build_cncx, encode_number)

# MOBI 头的固定字段偏移。头总长 264 字节，后接 EXTH 与标题。
MOBI_HEADER_LENGTH = 264
MOBI_HEADER_PADDING = 8192   # 给亚马逊转换服务预留的空间

# MOBI 6 的头长：字段里写 232，实际头部数据占 248 字节。
MOBI6_HEADER_LENGTH = 248

# MOBI 头里的关键偏移，写入时用来定位其他字段。
OFFSET_COMPRESSION = 0
OFFSET_TEXT_LENGTH = 4
OFFSET_LAST_TEXT_RECORD = 8
OFFSET_RECORD_SIZE = 10
OFFSET_ENCRYPTION = 12
OFFSET_IDENT = 16
OFFSET_HEADER_LENGTH = 20
OFFSET_BOOK_TYPE = 24
OFFSET_ENCODING = 28
OFFSET_UID = 32
OFFSET_FILE_VERSION = 36
OFFSET_FIRST_NON_TEXT = 80
OFFSET_TITLE_OFFSET = 84
OFFSET_TITLE_LENGTH = 88
OFFSET_LANGUAGE = 92
OFFSET_MIN_VERSION = 104
OFFSET_FIRST_RESOURCE = 108
OFFSET_EXTH_FLAGS = 128
OFFSET_FDST = 192
OFFSET_FDST_COUNT = 196
OFFSET_FCIS = 200
OFFSET_FCIS_COUNT = 204
OFFSET_FLIS = 208
OFFSET_FLIS_COUNT = 212
OFFSET_EXTRA_FLAGS = 240
OFFSET_UNKNOWN4 = 232
OFFSET_UNKNOWN5 = 264
# 244 起是五个索引槽，顺序不能变：读端按 >4L 从 0xF8 一次读四个。
OFFSET_NCX_INDEX = 244
OFFSET_CHUNK_INDEX = 248
OFFSET_SKEL_INDEX = 252
OFFSET_DATP_INDEX = 256      # 未使用，必须留 NULL
OFFSET_GUIDE_INDEX = 260     # 写成 256 会让读端去解析 record 0 然后崩溃
OFFSET_EXTH = 280

# MOBI 6 的 192/194 不是 FDST 记录号，而是首/末内容记录号（u16 对）。
OFFSET_FIRST_CONTENT = 192
OFFSET_LAST_CONTENT = 194
OFFSET_MOBI6_UNKNOWN = 196

# 阅读器用来判断这本书是哪种书的标志位。
# bit6 是「有 EXTH」的唯一硬判据，bit4 是 Calibre 恒置的未知位；
# 其余位（作者/出版者/描述/标题等）在纯 MOBI 6 与纯 KF8 里都不置。
EXTH_FLAG_EXTH_PRESENT = 0b1000000
EXTH_FLAG_BASE = EXTH_FLAG_EXTH_PRESENT | 0b0010000

FLIS_RECORD = (b'FLIS\0\0\0\x08\0\x41\0\0\0\0\0\0\xff\xff\xff\xff\0\x01\0\x03\0\0\0\x03\0\0\0\x01'
               + b'\xff' * 4)


def fcis_record(text_length, kf8=True):
    """FCIS 记录：记录文本流长度，供阅读器做进度换算。

    KF8 与 MOBI 6 是两个不同的常量：偏移 0x0C 分别是 2 与 1，
    0x1C 分别是 0x28 与 0x20，0x20 分别是 0 与 8。混用会让读端
    把 52 字节的记录按 44 字节解析。
    """
    if kf8:
        return (b'FCIS\x00\x00\x00\x14\x00\x00\x00\x10\x00\x00\x00\x02\x00\x00\x00\x00'
                + struct.pack('>L', text_length)
                + b'\x00\x00\x00\x00\x00\x00\x00\x28\x00\x00\x00\x00\x00\x00\x00'
                + b'\x28\x00\x00\x00\x08\x00\x01\x00\x01\x00\x00\x00\x00')
    return (b'FCIS\x00\x00\x00\x14\x00\x00\x00\x10\x00\x00\x00\x01\x00\x00\x00\x00'
            + struct.pack('>L', text_length)
            + b'\x00\x00\x00\x00\x00\x00\x00\x20\x00\x00\x00\x08\x00\x01\x00\x01'
            + b'\x00\x00\x00\x00')


def build_mobi_header(metadata, text_length, last_text_record, first_non_text_record,
                      exth, full_title, first_resource_record=None, uid=None,
                      file_version=8, book_type=2, ncx_index=0xFFFFFFFF,
                      chunk_index=0xFFFFFFFF, skel_index=0xFFFFFFFF,
                      guide_index=0xFFFFFFFF, fdst_record=0xFFFFFFFF,
                      fcis_record=0xFFFFFFFF, flis_record=0xFFFFFFFF,
                      extra_data_flags=0b1, exth_flags=None):
    """构造 KF8 的 MOBI 头（record0 的头部部分）。

    字段顺序与偏移按 KF8 规范排布；末尾补 8192 字节余量。
    """
    if uid is None:
        uid = int.from_bytes(uuid_bytes()[:4], 'big')
    if exth_flags is None:
        exth_flags = EXTH_FLAG_BASE

    # 固定字段占 264 字节，接着 16 字节保留区，然后才是 EXTH 和全书标题。
    # 阅读器按 16 + header_length 定位 EXTH，所以 EXTH 落在偏移 280。
    head = bytearray(MOBI_HEADER_LENGTH + 16)
    title = full_title.encode('utf-8') if isinstance(full_title, str) else full_title
    # 264-280 是四个未知字段，规范里的取值是 0xFF 0x00 0xFF 0x00。
    head[OFFSET_UNKNOWN5:OFFSET_UNKNOWN5 + 16] = b'\xff\xff\xff\xff' + b'\0' * 4 \
        + b'\xff\xff\xff\xff' + b'\0' * 4

    def put(offset, value, fmt='>I'):
        struct.pack_into(fmt, head, offset, value)

    put(OFFSET_COMPRESSION, 2, '>H')            # 2 = PalmDOC 压缩
    put(OFFSET_TEXT_LENGTH, text_length)
    put(OFFSET_LAST_TEXT_RECORD, last_text_record, '>H')
    put(OFFSET_RECORD_SIZE, 4096, '>H')
    put(OFFSET_ENCRYPTION, 0, '>H')
    head[OFFSET_IDENT:OFFSET_IDENT + 4] = b'MOBI'
    put(OFFSET_HEADER_LENGTH, MOBI_HEADER_LENGTH)
    put(OFFSET_BOOK_TYPE, book_type)
    put(OFFSET_ENCODING, 65001)                 # UTF-8
    put(OFFSET_UID, uid)
    put(OFFSET_FILE_VERSION, file_version)
    put(OFFSET_FIRST_NON_TEXT, first_non_text_record)
    put(OFFSET_TITLE_OFFSET, MOBI_HEADER_LENGTH + 16 + len(exth))
    put(OFFSET_TITLE_LENGTH, len(title))
    put(OFFSET_LANGUAGE, language_code(metadata.language), '>4s')
    put(OFFSET_MIN_VERSION, file_version)
    put(OFFSET_FIRST_RESOURCE, first_resource_record if first_resource_record is not None else 0xFFFFFFFF)
    put(OFFSET_EXTH_FLAGS, exth_flags)
    put(OFFSET_FDST, fdst_record)
    put(OFFSET_FDST_COUNT, 1 if fdst_record != 0xFFFFFFFF else 0)
    put(OFFSET_FCIS, fcis_record)
    put(OFFSET_FCIS_COUNT, 1)
    put(OFFSET_FLIS, flis_record)
    put(OFFSET_FLIS_COUNT, 1)
    put(OFFSET_EXTRA_FLAGS, extra_data_flags)
    put(OFFSET_NCX_INDEX, ncx_index)
    put(OFFSET_CHUNK_INDEX, chunk_index)
    put(OFFSET_SKEL_INDEX, skel_index)
    put(OFFSET_DATP_INDEX, 0xFFFFFFFF)      # 未使用的槽，必须留 NULL
    put(OFFSET_GUIDE_INDEX, guide_index)
    # 偏移 232 起的 8 字节在规范里是未知字段，写全 0xFF。
    head[OFFSET_UNKNOWN4:OFFSET_UNKNOWN4 + 8] = b'\xff' * 8

    out = bytearray(head)
    out.extend(exth)
    out.extend(title)
    out.extend(b'\0' * MOBI_HEADER_PADDING)
    return bytes(out)


def build_mobi6_header(metadata, text_length, last_text_record, first_non_text_record,
                       exth, full_title, first_image_record=None, uid=None,
                       fdst_record=0xFFFFFFFF, fcis_record=0xFFFFFFFF,
                       flis_record=0xFFFFFFFF, last_content_record=1,
                       primary_index_record=0xFFFFFFFF, extra_data_flags=0b1,
                       exth_flags=None, book_type=2):
    """构造纯 MOBI 6 的记录 0。

    字段偏移与 KF8 相同，但有几处语义不同：

    - 头长字段写 232，实际头部数据占 248 字节
    - EXTH 跟在头部之后，不是第 280 字节
    - 192/194 是首/末内容记录号（u16 对），不是 FDST 记录号

    阅读器按 16 + 头长 定位 EXTH，两处必须自洽。
    """
    if uid is None:
        uid = int.from_bytes(uuid_bytes()[:4], 'big')
    if exth_flags is None:
        exth_flags = EXTH_FLAG_BASE

    title = full_title.encode('utf-8') if isinstance(full_title, str) else full_title
    head = bytearray(MOBI6_HEADER_LENGTH)

    def put(offset, value, fmt='>I'):
        struct.pack_into(fmt, head, offset, value)

    put(OFFSET_COMPRESSION, 2, '>H')            # 2 = PalmDOC 压缩
    put(OFFSET_TEXT_LENGTH, text_length)
    put(OFFSET_LAST_TEXT_RECORD, last_text_record, '>H')
    put(OFFSET_RECORD_SIZE, 4096, '>H')
    put(OFFSET_ENCRYPTION, 0, '>H')
    head[OFFSET_IDENT:OFFSET_IDENT + 4] = b'MOBI'
    put(OFFSET_HEADER_LENGTH, 232)               # MOBI 6 的头长字段
    put(OFFSET_BOOK_TYPE, book_type)
    put(OFFSET_ENCODING, 65001)                 # UTF-8
    put(OFFSET_UID, uid)
    put(OFFSET_FILE_VERSION, 6)
    put(OFFSET_MIN_VERSION, 6)
    put(OFFSET_FIRST_NON_TEXT, first_non_text_record)
    # 标题紧跟在 EXTH 之后。EXTH 声明长度不含尾部对齐填充，
    # 所以要按实际占位（对齐到 4 字节）来算标题位置。
    exth_size = (len(exth) + 3) // 4 * 4
    put(OFFSET_TITLE_OFFSET, MOBI6_HEADER_LENGTH + exth_size)
    put(OFFSET_TITLE_LENGTH, len(title))
    put(OFFSET_LANGUAGE, language_code(metadata.language), '>4s')
    put(OFFSET_MIN_VERSION, 6)
    put(OFFSET_FIRST_RESOURCE, first_image_record if first_image_record is not None
        else 0xFFFFFFFF)
    put(OFFSET_EXTH_FLAGS, exth_flags)
    # 192/194 是首/末内容记录号。首内容记录恒为 1，末内容记录是
    # 最后一条含内容的记录（图片、FDST 等也算内容记录）。
    put(OFFSET_FIRST_CONTENT, 1, '>H')
    put(OFFSET_LAST_CONTENT, last_content_record, '>H')
    put(OFFSET_MOBI6_UNKNOWN, 1)
    # 200/208 是 FCIS / FLIS 记录号，204/212 各写 1（条数）。
    # 偏移 216 起的 8 字节是未知区，留 0。
    put(OFFSET_FCIS, fcis_record)
    put(OFFSET_FCIS_COUNT, 1)
    put(OFFSET_FLIS, flis_record)
    put(OFFSET_FLIS_COUNT, 1)
    put(OFFSET_EXTRA_FLAGS, extra_data_flags)
    put(OFFSET_NCX_INDEX, primary_index_record)
    # 230 起 2 字节 + 232 起 8 字节是未知区：前两个字节留 0，
    # 后 8 字节按规范写全 0xFF。
    head[OFFSET_UNKNOWN4:OFFSET_UNKNOWN4 + 8] = b'\xff' * 8

    out = bytearray(head)
    out.extend(exth)
    out.extend(title)
    # 尾部预留空间，亚马逊的出版流水线要在这里追加加密信息。
    out.extend(b'\0' * (1024 * 8))
    return bytes(align_block(bytes(out)))


def language_code(language):
    """把 ISO 语言名转成 MOBI 的 locale 字段（4 字节）。"""
    from .languages import locale_code
    return locale_code(language)


def uuid_bytes():
    """给 MOBI 头用的随机唯一标识。"""
    import uuid
    return uuid.uuid4().bytes


def build_tagx(tags=FLAT_BOOK_TAGS, control_byte_count=1, masks=None):
    """构造 TAGX 块：声明这个索引里有哪些标签及其位掩码。

    每个标签 4 字节：标签号、每条目的值数、位掩码、结束标记。
    值数写在标签定义里，不能从掩码推：掩码只说明控制字节占几位。
    masks 可以传自己的掩码表，KF8 的几张索引各用各的。
    """
    table = TAG_BITMASKS if masks is None else masks
    body = bytearray()
    for tag in tags:
        if tag:
            mask = table.get(tag, 0)
            values = VALUES_PER_ENTRY.get(tag, 1)
            body.append(tag)
            body.append(values)
            body.append(mask)
            body.append(0)
        else:
            body.extend(b'\0\0\0\x01')            # 结束标记
    return b'TAGX' + struct.pack('>II', 12 + len(body), control_byte_count) + bytes(body)


# CDX 条目使用的标签号，与 FLAT_BOOK_TAGS 对应。
TAG_OFFSET = 1
TAG_SIZE = 2
TAG_LABEL_OFFSET = 3
TAG_DEPTH = 4


def build_index(entries, tags=None, masks=None, cncx_strings=()):
    """构造一套 MOBI 6 索引记录：头记录 + 若干索引记录 + CNCX。

    entries: [(ident, {标签号: 值或值元组}), ...]
    tags/masks 缺省用 MOBI 6 平装书的标签表。
    cncx_strings 是要放进字符串池的标题，条目的 tag 3 应写池内偏移。

    返回 (records, cncx_offsets)。records 的第一条是主索引头，
    其后是索引记录，最后是 CNCX 记录。主索引头 @52 写 CNCX 记录数，
    读者按「头记录号 + 记录数 + 1」定位池。
    """
    tagx = build_tagx(FLAT_BOOK_TAGS if tags is None else tags, masks=masks)
    cncx_records, cncx_offsets = build_cncx(cncx_strings)

    body = bytearray()
    positions = []
    for ident, values in entries:
        raw = bytearray(ident.encode('utf-8'))
        raw.insert(0, len(raw))
        control = 0
        flat = []
        for tag in (FLAT_BOOK_TAGS if tags is None else tags):
            if not tag or tag not in values:
                continue
            value = values[tag]
            # tag 3 是 CNCX 池内偏移；调用方可以直接给标题文字，
            # 这里换成池里的实际偏移（池里查不到就是编码 bug）。
            if isinstance(value, str):
                value = cncx_offsets[value]
            control |= _control_bits(tag, value, masks)
            if isinstance(value, tuple):
                flat.extend(value)
            else:
                flat.append(value)
        block = bytearray(raw)
        block.append(control)
        for value in flat:
            block.extend(encode_number(value))
        positions.append(len(body))
        body.extend(block)

    body_block = bytes(align_block(bytes(body)))
    # IDXT 的条目偏移是 u16，单条索引记录最多 64 KiB - 头长。
    # 目录特别多的书会撞到这个上限；此时要明确说出来，而不是让
    # struct.pack 抛一个用户看不懂的 'H' format requires ...。
    if positions and INDEX_HEADER_LENGTH + positions[-1] > 0xFFFF:
        raise ValueError(
            'MOBI 6 目录条目过多（%d 条），单条索引记录超过 64 KiB。'
            '这种书请改用 AZW3，或先合并目录层级。' % len(entries))
    idxt = bytearray(b'IDXT')
    for position in positions:
        idxt.extend(struct.pack('>H', INDEX_HEADER_LENGTH + position))
    idxt_block = bytes(align_block(bytes(idxt)))

    header = bytearray()
    header.extend(b'INDX')
    header.extend(struct.pack('>I', INDEX_HEADER_LENGTH))
    header.extend(b'\0' * 8)
    header.extend(struct.pack('>I', 2))           # 索引类型：变形词索引
    header.extend(struct.pack('>I', 0))           # IDXT 偏移，稍后回填
    header.extend(struct.pack('>I', 1))           # 索引记录数
    header.extend(struct.pack('>I', 65001))       # UTF-8
    header.extend(struct.pack('>I', 0xFFFFFFFF))
    header.extend(struct.pack('>I', len(entries)))  # 条目数
    header.extend(struct.pack('>I', 0))           # ORDT 偏移
    header.extend(struct.pack('>I', 0))           # LIGT 偏移
    header.extend(struct.pack('>I', 0))           # ORDT/LIGT 条目数
    header.extend(struct.pack('>I', len(cncx_records)))
    header.extend(b'\0' * (180 - len(header)))
    header.extend(struct.pack('>I', INDEX_HEADER_LENGTH))
    header.extend(b'\0' * (192 - len(header)))
    assert len(header) == 192, len(header)
    header.extend(align_block(tagx))
    # geometry：本索引只有一个索引记录，名字取该记录最后一个条目的 ident。
    last_ident = entries[-1][0].encode('utf-8') if entries else b''
    header.append(len(last_ident))
    header.extend(last_ident)
    header.extend(struct.pack('>H', len(entries)))
    header.extend(b'\0' * ((4 - len(header) % 4) % 4))
    idxt_offset = len(header)
    header.extend(idxt_block)
    header[20:24] = struct.pack('>I', idxt_offset)

    index_record = bytearray()
    index_record.extend(b'INDX')
    index_record.extend(struct.pack('>I', INDEX_HEADER_LENGTH))
    index_record.extend(b'\0' * 4)
    index_record.extend(struct.pack('>I', 1))     # 记录类型：索引记录
    index_record.extend(b'\0' * 4)
    index_record.extend(struct.pack('>I', INDEX_HEADER_LENGTH + len(body_block)))
    index_record.extend(struct.pack('>I', len(entries)))
    index_record.extend(b'\xff' * 8)
    index_record.extend(b'\0' * (192 - len(index_record)))
    assert len(index_record) == 192, len(index_record)
    index_record.extend(body_block)
    index_record.extend(idxt_block)

    records = [bytes(align_block(bytes(header))), bytes(index_record)]
    records.extend(cncx_records)
    return records, cncx_offsets


def _control_bits(tag, value, masks):
    """算出这个标签在控制字节里该置的位。

    控制字节里每个标签占一段连续位，写的是「值组数」而不是值的个数：
    值组数 = 值个数 / 每组值数。置满整段（两位掩码时是 3）会被读成
    「后面先跟一个字节长度」，所以单组只写 1。
    """
    table = TAG_BITMASKS if masks is None else masks
    mask = table.get(tag, 0)
    if not mask:
        return 0
    count = len(value) if isinstance(value, tuple) else 1
    groups = count // VALUES_PER_ENTRY.get(tag, 1)
    shift = (mask & -mask).bit_length() - 1
    return mask & (groups << shift)
