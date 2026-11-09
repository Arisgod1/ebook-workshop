"""MOBI / AZW3 写出：把中间表示的书写成 Kindle 可读的电子书。

两种输出各自独立，不共享联合结构：

  .mobi   纯 MOBI 6，记录 0 是 232 字节头的 MOBI 6 头
  .azw3   纯 KF8 standalone，记录 0 是 264 字节头的 KF8 头

这与 Calibre 的 MOBIOutput / AZW3Output 行为一致。standalone 不需要
BOUNDARY、EXTH 121，也不需要两套 EXTH，Kindle 对它的支持最完整。

实现按公开的 MOBI 格式规范独立编写，不依赖 Calibre。
"""

import struct
import uuid

from . import kf8 as kf8_module
from . import palmdoc
from .mobi import build_exth, build_pdb_header, EOF_RECORD
from .mobi_header import (EXTH_FLAG_BASE, FLIS_RECORD, build_index,
                          build_mobi6_header, build_mobi_header, fcis_record)

# 文本记录固定 4096 字节。
RECORD_SIZE = 4096


class TocItem:
    """写到 MOBI 目录索引里的一项。"""

    def __init__(self, title, offset, length, label_offset, depth=0):
        self.title = title
        self.offset = offset
        self.length = length
        self.label_offset = label_offset
        self.depth = depth


def split_records(text):
    """把正文切成定长记录。

    切分点回退到 UTF-8 字符边界，每条记录都是完整的字节序列，因此
    不需要跨记录的重叠字节——记录尾部只写一个 0 表示「没有重叠」。
    读端按 (计数字节 & 0x3) + 1 剥掉尾部，这个 0 正好让它剥掉自己。

    重叠字节不能随手填：计数字节只有 2 位，(4 & 0x3) + 1 = 1，
    写 4 会被读端当成 1 个字节，剩下的重叠字节就被当正文解压，整段乱掉。
    """
    records = []
    position = 0
    length = len(text)
    while position < length:
        end = min(position + RECORD_SIZE, length)
        # 记录末尾不要停在多字节字符中间。
        while end > position:
            try:
                text[position:end].decode('utf-8')
                break
            except UnicodeDecodeError:
                end -= 1
        if end == position:
            end = min(position + RECORD_SIZE, length)
        records.append((text[position:end], b'', b'\x00'))
        position = end
    return records


def build_text_records(text):
    """把正文压缩成 MOBI 文本记录。

    每条记录都必须压缩：头里声明的压缩方式对全书生效，
    少数记录存原文会让读者解压出错，正文直接损坏。
    """
    records = []
    for chunk, overlap, tail in split_records(text):
        records.append(palmdoc.compress(chunk) + overlap + tail)
    return records


def build_fdst(flows):
    """FDST 记录：正文分成几个 flow，每个 flow 在文本流里的起止位置。

    正文是一整条 flow；样式表、SVG 这些是各自的 flow。
    Kindle 用它把阅读进度换算成百分比。
    """
    entries = []
    for flow in flows:
        entries.append(flow[0])
        entries.append(flow[1])
    if not entries:
        entries = [0, 0]
    return b'FDST' + struct.pack('>LL', 12, len(entries) // 2) + struct.pack('>%dL' % len(entries), *entries)


def _image_record(image):
    """把一张图片写成 MOBI 的资源记录。

    记录就是图片的原始字节，不加任何前缀：
    读端按图片本身的格式识别，多加的魔数会被丢掉。
    """
    return image.data


def _write_images(records, book, serializer):
    """按 recindex 的顺序写出全部图片资源记录。

    返回 (封面在资源列表里的下标, 首条资源记录号, 资源条数)。
    正文里每个 recindex 都必须有对应记录，少写一条图片就全部错位。
    """
    ordered = sorted(serializer.image_index.items(), key=lambda item: item[1])
    # 封面即使正文没引用也要写：EXTH 201 要指向它。
    if book.cover and book.cover in book.images and book.cover not in serializer.image_index:
        ordered.append((book.cover, len(ordered) + 1))
    cover_offset = None
    first_resource = None
    written = 0
    for href, index in ordered:
        image = book.images.get(href)
        if image is None:
            continue
        if first_resource is None:
            first_resource = len(records) + 1        # PDB 里的记录号
        records.append(_image_record(image))
        written += 1
        if href == book.cover:
            cover_offset = index - 1                 # 资源列表 0-based 下标
    return cover_offset, first_resource, written


def build_ncx(entries):
    """生成 NCX 目录（供老设备使用）。

    条目按深度优先顺序排好，层级用嵌套的 navPoint 表示。
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
             '<head><meta name="dtb:uid" content="urn:uuid:%s"/></head>' % uuid.uuid4(),
             '<docTitle><text>%s</text></docTitle>' % _escape(entries['title']),
             '<navMap>']
    open_levels = []
    play_order = 0
    for item in entries['items']:
        # 同层或更深的上一个节点先闭合，剩下的就是这一项的父节点。
        while open_levels and open_levels[-1] >= item.depth:
            lines.append('</navPoint>')
            open_levels.pop()
        play_order += 1
        lines.append('<navPoint id="np%04d" playOrder="%d">' % (play_order, play_order))
        lines.append('<navLabel><text>%s</text></navLabel>' % _escape(item.title))
        lines.append('<content src="filepos=%010d"/>' % item.offset)
        open_levels.append(item.depth)
    while open_levels:
        lines.append('</navPoint>')
        open_levels.pop()
    lines.append('</navMap></ncx>')
    return '\n'.join(lines).encode('utf-8')


def _escape(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def build_mobi(book, kf8=False):
    """把中间表示的书写成 MOBI 6 或纯 KF8 standalone（AZW3）。"""
    from .serializer import Serializer

    documents = [{'href': document.href, 'body': document.content}
                 for document in book.linear_documents()]
    serializer = Serializer(documents, images=book.images, toc=book.toc, kf8=kf8)
    stream = serializer()

    text_records = build_text_records(stream)
    text_length = len(stream)

    # 目录项：标题、在正文流里的偏移、层级，以及到下一项的字节长度。
    toc_items = []
    for entry, depth in book.toc_tree():
        href = entry.href or ''
        offset = serializer.id_offsets.get(href)
        if offset is None and '#' in href:
            # 锚点没落到具体位置时退回文档开头，目录项不至于整条丢掉。
            offset = serializer.id_offsets.get(href.split('#', 1)[0])
        if offset is None:
            continue
        toc_items.append(TocItem(entry.title, offset, 0, offset, depth))
    for index, item in enumerate(toc_items):
        end = (toc_items[index + 1].offset if index + 1 < len(toc_items)
               else len(stream))
        item.length = max(0, end - item.offset)

    metadata = _metadata(book)
    if kf8:
        return _assemble_standalone(book, stream, text_records, text_length,
                                    toc_items, serializer, metadata)
    return _assemble(book, stream, text_records, text_length, toc_items,
                     serializer, metadata)


def _metadata(book):
    from .mobi import Metadata
    return Metadata(title=book.title, author=book.author, language=book.language,
                    identifier=book.identifier, publisher=book.publisher,
                    description=book.description)


def _common_records(text_records):
    """两种格式共用的文本记录与 4 字节对齐填充。

    返回 (records, 首条非文本记录的编号)。records 里只有文本记录与可能的
    对齐填充，不含 record0，所以 PDB 记录号就是下标 + 1。
    """
    records = list(text_records)
    # 文本记录总长要按 4 字节对齐，不够就补一条填充记录。
    # 补的是「还差几个字节到 4 的倍数」，不是余数本身。
    text_size = sum(len(record) for record in text_records)
    if text_size % 4:
        records.append(b'\0' * ((4 - text_size % 4) % 4))
    return records, len(records) + 1


def _tail_records(records, text_length, kf8):
    """追加两种格式共用的尾部：FDST / FLIS / FCIS / EOF。

    返回 (FDST 记录号, FLIS 记录号, FCIS 记录号)。两种格式只有 FCIS
    的常量不同（44 与 52 字节），其余完全一致。
    """
    records.append(build_fdst([(0, text_length)]))
    fdst_record_index = len(records)
    records.append(FLIS_RECORD)
    flis_record_index = len(records)
    records.append(fcis_record(text_length, kf8=kf8))
    fcis_record_index = len(records)
    records.append(EOF_RECORD)
    return fdst_record_index, flis_record_index, fcis_record_index


def _assemble(book, stream, text_records, text_length, toc_items, serializer, metadata):
    """纯 MOBI 6 的记录序列。

    记录 0 是 232 字节头；正文之后是 MOBI 6 的目录索引、NCX、
    图片资源、FDST/FLIS/FCIS，最后是 EOF。
    """
    records, first_non_text = _common_records(text_records)
    last_text_index = len(text_records)

    index_records = []
    if toc_items:
        records.append(build_ncx({'title': book.title, 'items': toc_items}))
        # 标题放进 CNCX 字符串池，条目的 tag 3 写池内偏移。
        # 用标题文本当 ident 会让读端显示 Unknown Text。
        index_records, _ = build_index(
            [('%02X' % index, {1: item.offset, 2: item.length,
                               3: item.title, 4: item.depth})
             for index, item in enumerate(toc_items)],
            cncx_strings=[item.title for item in toc_items])

    primary_index_record = len(records) + 1 if index_records else 0xFFFFFFFF
    records.extend(index_records)

    cover_offset, first_resource_record, resource_count = _write_images(
        records, book, serializer)
    # 末内容记录是最后一条含内容的记录（图片算内容记录），
    # 但不含随后追加的 FDST/FLIS/FCIS/EOF。
    last_content_record = len(records)

    fdst_record_index, flis_record_index, fcis_record_index = _tail_records(
        records, text_length, kf8=False)

    exth = build_exth(metadata, cover_offset=cover_offset,
                      start_offset=serializer.start_offset,
                      num_of_resources=resource_count or None,
                      kf8_unknown_count=0 if cover_offset is not None else None)
    record0 = build_mobi6_header(
        metadata, text_length, last_text_index, first_non_text,
        exth, book.title.encode('utf-8'),
        first_image_record=first_resource_record,
        uid=None, fdst_record=fdst_record_index, fcis_record=fcis_record_index,
        flis_record=flis_record_index,
        last_content_record=last_content_record,
        primary_index_record=primary_index_record,
        extra_data_flags=0b1, exth_flags=EXTH_FLAG_BASE)

    all_records = [record0] + records
    header = build_pdb_header('BOOKMOBI', book.title, [len(r) for r in all_records])
    return header + b''.join(all_records)


def _assemble_standalone(book, stream, text_records, text_length, toc_items,
                         serializer, metadata):
    """纯 KF8 standalone（AZW3）的记录序列。

    记录 0 是 264 字节头的 KF8 头，file_version=8；
    正文之后依次是 CHUNK 索引、SKEL 索引、NCX 索引、图片资源、
    FDST/FLIS/FCIS，最后 EOF。没有 BOUNDARY，也没有 EXTH 121。
    """
    records, first_non_text = _common_records(text_records)
    last_text_index = len(text_records)

    # CHUNK 索引：整条文本流切块，一块一条索引项。
    #
    # SKEL 与 CHUNK 必须成套：读端按每份 skeleton 的 chunk_count 累加去消费
    # chunk 表。骨架少了 chunk 会 IndexError，多了会静默拼错正文。
    # 这里按 KF8 的第一版简化模型：整条流当一个 skeleton、chunks 全归它，
    # 这样三套坐标（skeleton 起点、chunk 插入位置、chunk 内偏移）天然自洽。
    chunks = kf8_module.split_chunks(stream)
    chunk_table = kf8_module.build_chunk_table(chunks, 0)
    chunk_index = len(records) + 1
    records.extend(kf8_module.build_chunk_index(chunk_table))

    # SKEL 索引：一条空骨架，全部内容都放在 chunk 里。
    #
    # 读端按下式重建每份文档：skeleton[:insert_pos] + chunk + skeleton[insert_pos:]。
    # 若骨架本身就装全文、chunk 又放一遍，正文会成倍重复。
    # 所以骨架留空（length=0），每块的 insert_pos 都是 0，
    # 拼起来正好是原始文本流。
    skeletons = [{'name': 'OEBPS/text',
                  'chunk_count': len(chunk_table),
                  'start_pos': 0,
                  'length': 0}]
    skel_index = len(records) + 1
    records.extend(kf8_module.build_skel_index(skeletons))

    # NCX 目录索引。
    ncx_index = 0xFFFFFFFF
    if toc_items:
        ncx_index = len(records) + 1
        records.extend(kf8_module.build_ncx_index(toc_items, chunk_table, text_length))

    cover_offset, first_resource_record, resource_count = _write_images(
        records, book, serializer)

    # records 不含 record0，所以 append 之后的 PDB 记录号就是 len(records)。
    fdst_record_index, flis_record_index, fcis_record_index = _tail_records(
        records, text_length, kf8=True)

    # standalone 的 EXTH 不写 121（那是联合文件专用的 KF8 头位置）。
    exth = build_exth(metadata, cover_offset=cover_offset,
                      start_offset=serializer.start_offset,
                      num_of_resources=resource_count or None,
                      kf8_unknown_count=0 if cover_offset is not None else None)
    record0 = build_mobi_header(
        metadata, text_length, last_text_index, first_non_text,
        exth, book.title.encode('utf-8'),
        first_resource_record=first_resource_record,
        uid=None, file_version=8, book_type=2,
        ncx_index=ncx_index, chunk_index=chunk_index,
        skel_index=skel_index, guide_index=0xFFFFFFFF,
        fdst_record=fdst_record_index, fcis_record=fcis_record_index,
        flis_record=flis_record_index,
        extra_data_flags=0b1, exth_flags=EXTH_FLAG_BASE)

    all_records = [record0] + records
    header = build_pdb_header('BOOKMOBI', book.title, [len(r) for r in all_records])
    return header + b''.join(all_records)
