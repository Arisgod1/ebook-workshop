"""KF8 / MOBI 写出的字节级回归。

每条用例都直接解析写出的二进制，断言与 MOBI 规范一致——不是看函数有没有被调用，
而是看读端按规范解析出来的是什么。
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop.convert import book as book_module
from workshop.convert import kf8, mobi, mobi_header, mobi_writer
from workshop.convert.palmdoc import decompress

INDEX_HEADER_LENGTH = 192


def sections(data):
    """把 MOBI/AZW3 文件拆成 PDB 记录列表。"""
    count = struct.unpack('>H', data[76:78])[0]
    offsets = [struct.unpack('>L', data[78 + i * 8:82 + i * 8])[0] for i in range(count)]
    return [data[offsets[i]:offsets[i + 1]] if i + 1 < count else data[offsets[i]:]
            for i in range(count)]


def decint(raw, start=0):
    """读一个变长整数，返回 (值, 消耗字节数)。"""
    value = 0
    consumed = 0
    while start + consumed < len(raw):
        byte = raw[start + consumed]
        consumed += 1
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80:
            return value, consumed
    return value, consumed


def index_header(record):
    """解析 INDX 的 192 字节固定头。

    字段名与 Calibre 的 parse_indx_header 一致：
    ``start`` 是 IDXT 偏移、``count`` 是索引记录数、``total`` 是条目总数。
    """
    words = ('len nul1 type gen start count code lng total ordt ligt nligt ncncx'
             .split())
    words += ['unknown%d' % i for i in range(27)]
    words += 'ocnt oentries ordt1 ordt2 tagx'.split()
    values = struct.unpack('>%dL' % len(words), record[4:4 * (len(words) + 1)])
    return dict(zip(words, values))


def tagx_tags(record):
    """解析头记录里的 TAGX 块，返回 (控制字节数, [(标签, 值数, 掩码)])。"""
    at = record.find(b'TAGX')
    total = struct.unpack_from('>L', record, at + 4)[0]
    control = struct.unpack_from('>L', record, at + 8)[0]
    tags = []
    for position in range(at + 12, at + total, 4):
        tag, values, mask, end = record[position:position + 4]
        if tag:
            tags.append((tag, values, mask))
    return control, tags


def parse_cncx(records):
    """解析 CNCX 字符串池，返回 offset -> 字符串。"""
    strings = {}
    base = 0
    for record in records:
        position = 0
        while position < len(record):
            length, consumed = decint(record, position)
            if length:
                strings[base + position] = (
                    record[position + consumed:position + consumed + length]
                    .decode('utf-8'))
            position += consumed + length
        base += 0x10000
    return strings


def decode_entries(header_record, index_record, count=None):
    """按读端 get_tag_map 的算法解出索引条目。

    返回 [(ident, {标签号: [值...]})]，并顺带返回每个条目剩余的字节数。
    剩余字节应全是 0（IDXT 之前的对齐填充）。
    """
    control_count, tags = tagx_tags(header_record)
    info = index_header(index_record)
    idxt_at = info['start']
    if index_record[idxt_at:idxt_at + 4] != b'IDXT':
        raise AssertionError('索引记录的 IDXT 偏移指错了')
    entry_count = info['count'] if count is None else count
    positions = [struct.unpack_from('>H', index_record, idxt_at + 4 + 2 * j)[0]
                 for j in range(entry_count)]
    positions.append(idxt_at)

    result = []
    for index in range(entry_count):
        record = index_record[positions[index]:positions[index + 1]]
        size = record[0]
        ident = record[1:1 + size].decode('utf-8')
        rest = record[1 + size:]
        control = list(rest[:control_count])
        rest = rest[control_count:]
        values = {}
        for tag, per_entry, mask in tags:
            value = control[0] & mask
            if not value:
                continue
            if value == mask:
                if bin(mask).count('1') > 1:
                    # 整段置满：先跟一个字节长度，再解出块内的值。
                    length, consumed = decint(rest)
                    rest = rest[consumed:]
                    taken = 0
                    while taken < length:
                        item, used = decint(rest)
                        rest = rest[used:]
                        taken += used
                        values.setdefault(tag, []).append(item)
                    continue
                groups = 1
            else:
                shift = (mask & -mask).bit_length() - 1
                groups = value >> shift
            for _ in range(groups * per_entry):
                item, used = decint(rest)
                rest = rest[used:]
                values.setdefault(tag, []).append(item)
        result.append((ident, values, rest))
    return result


def read_index(records, index):
    """读取一套索引：返回 (头, 条目表, CNCX 字符串)。"""
    header = records[index]
    info = index_header(header)
    cncx = {}
    if info['ncncx']:
        start = index + info['count'] + 1
        cncx = parse_cncx(records[start:start + info['ncncx']])
    entries = decode_entries(header, records[index + 1])
    return header, entries, cncx


def build_book(paragraphs=1, documents=1, images=0):
    book = book_module.Book(title='测试书名', author='测试作者', language='zh-CN')
    for doc in range(documents):
        href = 'c%d.xhtml' % doc
        body = ''.join('<h2 id="s%d">第%d节</h2><p>%s</p>'
                       % (i, i, '正文内容。' * paragraphs)
                       for i in range(3))
        book.add_document(book_module.Document(
            href, content='<html><body><h1>第%d章</h1>%s</body></html>' % (doc, body)))
    for i in range(images):
        data = b'\x89PNG\r\n\x1a\n' + bytes([i]) * 24
        book.add_image(book_module.Image('i%d.png' % i, data, 'image/png'))
    book.cover = 'i0.png' if images else None
    book.toc = [book_module.TocEntry('第%d章' % doc, 'c%d.xhtml' % doc,
                                     children=[book_module.TocEntry(
                                         '第%d节' % i, 'c%d.xhtml#s%d' % (doc, i))
                                         for i in range(3)])
                for doc in range(documents)]
    return book


class ChunkIndexTests(unittest.TestCase):
    """CHUNK 索引的 CNCX 池与条目。"""

    def test_chunk_index_has_a_cncx_pool(self):
        """tag 2 是 CNCX 池内偏移，必须真有池且偏移能取到串。

        读端会做 cncx[tag_map[2][0]]，池为空就拿到 None。
        """
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        chunk_index = struct.unpack('>I', records[0][248:252])[0]
        header = records[chunk_index]
        info = index_header(header)
        self.assertEqual(info['ncncx'], 1, 'chunk 索引必须带 CNCX 记录')
        pool = records[chunk_index + info['count'] + 1]
        self.assertGreater(len(pool), 0, 'CNCX 池不能是空的')
        _, entries, cncx = read_index(records, chunk_index)
        self.assertTrue(entries, 'chunk 索引读不出条目')
        for ident, values, leftover in entries:
            self.assertIn(2, values, '条目缺 tag 2')
            self.assertIn(values[2][0], cncx, 'tag 2 的偏移在池里取不到串')
            self.assertTrue(set(leftover) <= {0}, '条目后面有多余的非零字节')

    def test_chunk_index_cncx_selectors_are_decimal(self):
        """chunk 的 selector 是十进制偏移，与 kindle:pos:fid 的编号体系一致。"""
        records = sections(mobi_writer.build_mobi(build_book(paragraphs=2000),
                                                  kf8=True))
        chunk_index = struct.unpack('>I', records[0][248:252])[0]
        _, entries, cncx = read_index(records, chunk_index)
        self.assertGreater(len(entries), 1, '这份正文应该切出多块')
        for ident, values, _ in entries:
            selector = cncx[values[2][0]]
            self.assertTrue(selector.isdigit(), 'selector 应是十进制：%r' % selector)
            self.assertEqual(int(ident), values[6][0], 'ident 应是块在流里的起点')


class Kf8IndexHeaderTests(unittest.TestCase):
    """KF8 主索引头：geometry 与 IDXT 按「索引记录」写。"""

    def test_main_header_idxt_points_into_geometry(self):
        """主索引头的 IDXT 指向 geometry 块里各索引记录的描述符。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        for name, offset in (('ncx', 244), ('chunk', 248), ('skel', 252)):
            index = struct.unpack('>I', records[0][offset:offset + 4])[0]
            header = records[index]
            info = index_header(header)
            idxt_at = info['start']
            self.assertEqual(header[idxt_at:idxt_at + 4], b'IDXT',
                             '%s 的主 IDXT 没有魔数' % name)
            for j in range(info['count']):
                target = struct.unpack_from('>H', header, idxt_at + 4 + 2 * j)[0]
                # 描述符的绝对位置 = 192 + TAGX 对齐后的长度 + IDXT 里的偏移。
                name_len = header[target]
                self.assertGreater(name_len, 0, '%s 的 geometry 名字是空的' % name)
                header[target + 1:target + 1 + name_len].decode('utf-8')

    def test_index_record_idxt_points_at_entries(self):
        """索引记录自己的 IDXT 偏移要指向记录内的条目。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        for name, offset in (('ncx', 244), ('chunk', 248), ('skel', 252)):
            index = struct.unpack('>I', records[0][offset:offset + 4])[0]
            info = index_header(records[index])
            for j in range(info['count']):
                record = records[index + 1 + j]
                record_info = index_header(record)
                at = record_info['start']
                self.assertEqual(record[at:at + 4], b'IDXT',
                                 '%s 索引记录的 IDXT 偏移指错了' % name)
                for k in range(record_info['count']):
                    position = struct.unpack_from('>H', record, at + 4 + 2 * k)[0]
                    self.assertGreaterEqual(position, INDEX_HEADER_LENGTH)

    def test_geometry_names_match_the_index_records(self):
        """geometry 只写每个索引记录一个描述符，名字取该记录最后一个条目。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        for name, offset in (('ncx', 244), ('chunk', 248), ('skel', 252)):
            index = struct.unpack('>I', records[0][offset:offset + 4])[0]
            header = records[index]
            _, entries, _ = read_index(records, index)
            # 单条索引记录时，geometry 只有一个描述符且名字是最后一个条目。
            tagx_at = header.find(b'TAGX')
            tagx_len = struct.unpack_from('>L', header, tagx_at + 4)[0]
            geometry_at = tagx_at + tagx_len
            name_len = header[geometry_at]
            stored = header[geometry_at + 1:geometry_at + 1 + name_len].decode('utf-8')
            self.assertEqual(stored, entries[-1][0],
                             '%s 的 geometry 名字应取最后一个条目' % name)
            self.assertEqual(struct.unpack_from('>H', header,
                                                geometry_at + 1 + name_len)[0],
                             len(entries), '%s 的 geometry 条目数不对' % name)


class CncxTests(unittest.TestCase):
    """CNCX 字符串池。"""

    def test_long_cjk_title_is_truncated_by_characters(self):
        """按字节截断会把一个中文字劈成两半，池里留下坏数据。"""
        title = '中' * 600
        records, _ = mobi.build_cncx([title])
        pool = b''.join(records)
        length, consumed = decint(pool)
        text = pool[consumed:consumed + length].decode('utf-8')
        self.assertEqual(len(text), mobi.CNCX_MAX_STRING_LENGTH)
        self.assertEqual(text, '中' * mobi.CNCX_MAX_STRING_LENGTH)

    def test_same_string_shares_one_offset(self):
        _, offsets = mobi.build_cncx(['同一个标题', '同一个标题', '别的'])
        self.assertEqual(offsets['同一个标题'], 0)
        self.assertGreater(offsets['别的'], 0)

    def test_ncx_titles_survive_in_the_pool(self):
        """KGF 目录标题放进 CNCX，读端按 tag 3 取到的是完整标题。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        ncx_index = struct.unpack('>I', records[0][244:248])[0]
        _, entries, cncx = read_index(records, ncx_index)
        labels = [cncx[values[3][0]] for _, values, _ in entries]
        self.assertIn('第0章', labels)
        self.assertIn('第0节', labels)


class Mobi6IndexTests(unittest.TestCase):
    """MOBI 6 目录索引：ident、CNCX 与 IDXT。"""

    def _index(self, count=9):
        book = book_module.Book(title='书名', author='作者', language='zh-CN')
        body = ''.join('<h2 id="s%d">第%d章</h2><p>正文</p>' % (i, i)
                       for i in range(count))
        book.add_document(book_module.Document(
            'c.xhtml', content='<html><body>%s</body></html>' % body))
        book.toc = [book_module.TocEntry('第%d章' % i, 'c.xhtml#s%d' % i)
                    for i in range(count)]
        records = sections(mobi_writer.build_mobi(book))
        return records, struct.unpack('>I', records[0][244:248])[0]

    def test_ident_is_hex_and_titles_live_in_cncx(self):
        """ident 是十六进制序号，标题在 CNCX 里，tag 3 指向池内偏移。"""
        records, index = self._index()
        header = records[index]
        info = index_header(header)
        self.assertGreater(info['ncncx'], 0, 'MOBI 6 目录索引缺 CNCX 池')
        cncx = parse_cncx(
            records[index + info['count'] + 1:index + info['count'] + 1 + info['ncncx']])
        entries = decode_entries(header, records[index + 1])
        self.assertEqual([ident for ident, _, _ in entries],
                         ['%02X' % i for i in range(9)])
        for ident, values, leftover in entries:
            self.assertIn(3, values, '条目缺 tag 3')
            self.assertIn(values[3][0], cncx, 'tag 3 的偏移在池里取不到串')
            self.assertTrue(set(leftover) <= {0}, '条目后面有多余的非零字节')
        self.assertEqual(cncx[entries[0][1][3][0]], '第0章')

    def test_idxt_offset_accounts_for_alignment_padding(self):
        """IDXT 偏移要按对齐后的 body 长度算，错 1-3 字节就读不出条目。"""
        for count in range(1, 12):
            records, index = self._index(count)
            record = records[index + 1]
            at = index_header(record)['start']
            self.assertEqual(record[at:at + 4], b'IDXT',
                             '%d 条目录项时 IDXT 偏移错位' % count)
            entries = decode_entries(records[index], record)
            self.assertEqual(len(entries), count)
            for _, values, left in entries:
                self.assertTrue(set(left) <= {0})

    def test_entry_lengths_are_nonzero(self):
        """tag 2 是到下一项的真实长度，不是常量 0。"""
        records, index = self._index()
        entries = decode_entries(records[index], records[index + 1])
        for _, values, _ in entries:
            self.assertIn(2, values)
            self.assertGreater(values[2][0], 0)


class NcxTests(unittest.TestCase):
    """KF8 目录索引的标签值。"""

    def test_lengths_and_hierarchy_are_real(self):
        """tag 2 是真实长度，tag 21/22/23 是真实的父子下标。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        ncx_index = struct.unpack('>I', records[0][244:248])[0]
        _, entries, cncx = read_index(records, ncx_index)
        by_label = {cncx[values[3][0]]: (ident, values) for ident, values, _ in entries}
        self.assertIn(1, by_label['第0章'][1], '章缺 tag 1')
        # 父项长度盖住整章，子项各自到下一项为止。
        self.assertGreater(by_label['第0章'][1][2][0],
                           by_label['第0节'][1][2][0])
        self.assertIn(22, by_label['第0章'][1], '父项缺 first_child')
        self.assertIn(23, by_label['第0章'][1], '父项缺 last_child')
        parent_index = by_label['第0章'][0]
        for section in ('第0节', '第1节', '第2节'):
            self.assertEqual(by_label[section][1][21], [int(parent_index, 16)],
                             '%s 的 parent 指错了' % section)

    def test_titles_are_not_truncated_to_zero(self):
        """tag 2 恒为 0 时 Kindle 无法跨节跳转；这里必须是非零的真实长度。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        ncx_index = struct.unpack('>I', records[0][244:248])[0]
        _, entries, _ = read_index(records, ncx_index)
        for _, values, _ in entries:
            self.assertIn(1, values)
            self.assertGreater(values[2][0], 0, '目录项的 length 是 0')


class SkeletonTests(unittest.TestCase):
    """SKEL / CHUNK 自洽。"""

    def test_chunk_count_matches_the_chunk_table(self):
        records = sections(mobi_writer.build_mobi(build_book(paragraphs=2000),
                                                  kf8=True))
        skel_index = struct.unpack('>I', records[0][252:256])[0]
        chunk_index = struct.unpack('>I', records[0][248:252])[0]
        _, chunk_entries, _ = read_index(records, chunk_index)
        _, skel_entries, _ = read_index(records, skel_index)
        self.assertEqual(len(skel_entries), 1)
        declared = skel_entries[0][1][1][0]
        self.assertEqual(declared, len(chunk_entries),
                         'skeleton 说 %d 块，chunk 表里有 %d 块'
                         % (declared, len(chunk_entries)))

    def test_rebuild_reproduces_the_stream(self):
        """按读端 build_parts 的规则重建，结果与序列化输出逐字节相同。"""
        book = build_book(paragraphs=2000)
        records = sections(mobi_writer.build_mobi(book, kf8=True))
        header = records[0]
        last_text = struct.unpack('>H', header[8:10])[0]
        raw = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records[1:last_text + 1])

        skel_index = struct.unpack('>I', header[252:256])[0]
        chunk_index = struct.unpack('>I', header[248:252])[0]
        _, skeletons, _ = read_index(records, skel_index)
        _, chunks, _ = read_index(records, chunk_index)

        rebuilt = []
        base = 0
        for ident, values, _ in skeletons:
            start, length = values[6][0], values[6][1]
            skeleton = raw[start:start + length]
            base = start + length
            for _, chunk_values, _ in chunks:
                part = raw[base:base + chunk_values[6][1]]
                base += chunk_values[6][1]
                rebuilt.append(skeleton + part)
        self.assertEqual(b''.join(rebuilt), raw)


class Mobi6HeaderTests(unittest.TestCase):
    """MOBI 6 头里几处与 KF8 不同的语义。"""

    def test_content_record_pair_is_not_a_fdst_pointer(self):
        """192/194 是首/末内容记录号，196 是 00 00 00 01，不是 FDST。"""
        records = sections(mobi_writer.build_mobi(build_book()))
        header = records[0]
        first, last = struct.unpack_from('>HH', header, 192)
        self.assertEqual(first, 1, '首内容记录应为 1')
        self.assertLessEqual(last, len(records) - 1)
        self.assertEqual(header[196:200], b'\x00\x00\x00\x01')

    def test_reading_192_as_fdst_would_be_wrong(self):
        """把 192 当 FDST 记录号会指到内容记录上，而不是 FDST。"""
        records = sections(mobi_writer.build_mobi(build_book()))
        first_content = struct.unpack_from('>H', records[0], 192)[0]
        self.assertNotEqual(records[first_content][:4], b'FDST')

    def test_mobi6_uses_the_44_byte_fcis(self):
        """MOBI 6 的 FCIS 是 44 字节版本，与 KF8 的 52 字节不同。"""
        records = sections(mobi_writer.build_mobi(build_book()))
        fcis_index = struct.unpack('>I', records[0][200:204])[0]
        fcis = records[fcis_index]
        self.assertEqual(fcis[:4], b'FCIS')
        self.assertEqual(len(fcis), 44, 'MOBI 6 应写 44 字节的 FCIS')
        self.assertEqual(struct.unpack('>I', fcis[12:16])[0], 1)
        self.assertEqual(struct.unpack('>I', fcis[28:32])[0], 0x20)
        self.assertEqual(struct.unpack('>I', fcis[32:36])[0], 8)

    def test_kf8_uses_the_52_byte_fcis(self):
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        fcis_index = struct.unpack('>I', records[0][200:204])[0]
        fcis = records[fcis_index]
        self.assertEqual(len(fcis), 52)
        self.assertEqual(struct.unpack('>I', fcis[12:16])[0], 2)
        self.assertEqual(struct.unpack('>I', fcis[28:32])[0], 0x28)

    def test_record_pointers_have_the_right_magic(self):
        """FDST / FLIS / FCIS / 首条图片记录必须指向真正的记录。"""
        book = build_book(images=2)
        for is_kf8 in (False, True):
            records = sections(mobi_writer.build_mobi(book, kf8=is_kf8))
            header = records[0]
            read = lambda offset: struct.unpack('>I', header[offset:offset + 4])[0]
            self.assertEqual(records[read(200)][:4], b'FCIS', 'kf8=%s' % is_kf8)
            self.assertEqual(records[read(208)][:4], b'FLIS', 'kf8=%s' % is_kf8)
            self.assertEqual(records[read(108)][:2], b'\x89P', 'kf8=%s' % is_kf8)
            if is_kf8:
                self.assertEqual(records[read(192)][:4], b'FDST', 'kf8=%s' % is_kf8)
            else:
                fdst_index = None
                for index, record in enumerate(records):
                    if record[:4] == b'FDST':
                        fdst_index = index
                self.assertIsNotNone(fdst_index, 'MOBI 6 没写 FDST 记录')


class GuideIndexTests(unittest.TestCase):
    """record0 的五个索引槽。"""

    def test_guide_does_not_overwrite_datp(self):
        """256 是 datp 槽、260 是 guide 槽；写错会让读端解析 record 0。"""
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        header = records[0]
        datp, guide = struct.unpack_from('>2L', header, 256)
        self.assertEqual(datp, 0xFFFFFFFF, 'datp 槽必须留 NULL')
        self.assertEqual(guide, 0xFFFFFFFF, '未生成 guide 时该槽应为 NULL')

    def test_all_index_slots_point_at_indx(self):
        records = sections(mobi_writer.build_mobi(build_book(), kf8=True))
        header = records[0]
        for name, offset in (('ncx', 244), ('chunk', 248), ('skel', 252)):
            index = struct.unpack('>I', header[offset:offset + 4])[0]
            self.assertEqual(records[index][:4], b'INDX', name)
            self.assertEqual(records[index + 1][:4], b'INDX', name)


class ImageResourceTests(unittest.TestCase):
    """图片资源记录。"""

    def test_every_referenced_image_is_written(self):
        """正文里每个引用都必须有资源记录，且 recindex 对得上。"""
        book = build_book(images=3)
        book.documents[0].content = (
            '<html><body><h1>章</h1><p>正文</p><img src="i0.png"/>'
            '<img src="i1.png"/><img src="i2.png"/></body></html>')
        records = sections(mobi_writer.build_mobi(book))
        first = struct.unpack('>I', records[0][108:112])[0]
        written = [index for index, record in enumerate(records)
                   if record[:8] == b'\x89PNG\r\n\x1a\n']
        self.assertEqual(len(written), 3, '三张图都该写成资源记录')
        self.assertEqual(written[0], first, '首条资源记录指针错位')
        text = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records[1:2])
        indices = [int(value) for value in
                   __import__('re').findall(rb'recindex="(\d+)"', text)]
        self.assertEqual(indices, [1, 2, 3], 'recindex 应从 1 开始连续编号')

    def test_kf8_uses_kindle_embed_for_every_image(self):
        book = build_book(images=2)
        book.documents[0].content = (
            '<html><body><h1>章</h1><img src="i0.png"/><img src="i1.png"/></body></html>')
        records = sections(mobi_writer.build_mobi(book, kf8=True))
        text = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records[1:2])
        embeds = __import__('re').findall(rb'kindle:embed:([0-9A-Z]+)\?', text)
        self.assertEqual(sorted(set(embeds)), [b'0001', b'0002'])


class TextRecordTests(unittest.TestCase):
    """文本记录的尾部约定。"""

    def test_reader_rule_reproduces_multibyte_text(self):
        """按读端 (尾字节 & 3) + 1 剥离，拼回去必须与源文本逐字节相同。

        构造多字节字符正好跨 4096 字节边界的情况。
        """
        text = ('<html><body><h1>章</h1><p>' + 'a' * (4096 - 34)
                + '。' * 40 + '</p></body></html>').encode('utf-8')
        records = mobi_writer.build_text_records(text)
        self.assertGreater(len(records), 1)
        rebuilt = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records)
        self.assertEqual(rebuilt, text)

    def test_last_record_has_no_phantom_overlap(self):
        """每条记录的尾字节剥掉的总字节数不超过记录本身。"""
        text = ('<html><body>' + '正文。' * 5000 + '</body></html>').encode('utf-8')
        for record in mobi_writer.build_text_records(text):
            trail = (record[-1] & 3) + 1
            self.assertLess(trail, len(record))
            self.assertLessEqual(record[-1] & 3, 3)


class AlignmentTests(unittest.TestCase):
    """4 字节对齐填充。"""

    def test_padding_record_makes_total_a_multiple_of_four(self):
        for size in range(1, 9):
            text = b'x' * size
            records, first_non_text = mobi_writer._common_records(
                mobi_writer.build_text_records(text))
            total = sum(len(record) for record in records)
            self.assertEqual(total % 4, 0, 'size=%d 时总长没对齐' % size)
            self.assertEqual(first_non_text, len(records) + 1)

    def test_text_length_is_uncompressed_stream_length(self):
        book = build_book(paragraphs=500)
        records = sections(mobi_writer.build_mobi(book))
        declared = struct.unpack('>I', records[0][4:8])[0]
        last_text = struct.unpack('>H', records[0][8:10])[0]
        rebuilt = b''.join(
            decompress(record[:len(record) - ((record[-1] & 3) + 1)])
            for record in records[1:last_text + 1])
        self.assertEqual(declared, len(rebuilt))


class PdbTests(unittest.TestCase):
    """PalmDB 记录索引表。"""

    def test_unique_ids_are_twice_the_record_number(self):
        data = mobi_writer.build_mobi(build_book())
        count = struct.unpack('>H', data[76:78])[0]
        for index in range(count):
            entry = data[78 + index * 8:86 + index * 8]
            unique = int.from_bytes(entry[5:8], 'big')
            self.assertEqual(unique, 2 * index, '第 %d 条记录的 uniqueID' % index)

    def test_name_is_ascii_and_padded(self):
        """PDB 名称字段只放 ASCII，非 ASCII 字符换成 _，不足 31 字节补 NUL。"""
        book = book_module.Book(title='中文书名', author='作者')
        book.add_document(book_module.Document(
            'c.xhtml', content='<html><body><p>正文</p></body></html>'))
        data = mobi_writer.build_mobi(book)
        name = data[:32]
        name.decode('ascii')                       # 必须是纯 ASCII
        self.assertFalse(any(byte >= 0x80 for byte in name))
        self.assertEqual(name[31], 0, '名称第 31 字节应是结尾 NUL')
        self.assertIn(b'_', name, '中文应被替换成下划线，而不是丢掉')


class ExthTests(unittest.TestCase):
    """EXTH 内容。"""

    def _codes(self, record0):
        start = 16 + struct.unpack('>I', record0[20:24])[0]
        length = struct.unpack('>I', record0[start + 4:start + 8])[0]
        position = start + 12
        while position + 8 <= start + length:
            code, size = struct.unpack('>II', record0[position:position + 8])
            yield code, record0[position + 8:position + size]
            position += size

    def test_no_joint_or_thumbnail_entries(self):
        """standalone 不写 121；也不写缩略图 202 / 129。"""
        for is_kf8 in (False, True):
            records = sections(mobi_writer.build_mobi(build_book(), kf8=is_kf8))
            codes = {code for code, _ in self._codes(records[0])}
            self.assertNotIn(121, codes, 'kf8=%s' % is_kf8)
            self.assertNotIn(202, codes, 'kf8=%s' % is_kf8)
            self.assertNotIn(129, codes, 'kf8=%s' % is_kf8)

    def test_kindlegen_codes_have_the_documented_values(self):
        records = sections(mobi_writer.build_mobi(build_book()))
        values = {code: struct.unpack('>I', payload)[0]
                  for code, payload in self._codes(records[0])
                  if code in (204, 205, 206, 207)}
        self.assertEqual(values, {204: mobi.KINDLEGEN_PLATFORM,
                                  205: mobi.KINDLEGEN_VERSION,
                                  206: mobi.KINDLEGEN_REVISION,
                                  207: mobi.KINDLEGEN_RESERVED})

    def test_source_does_not_impersonate_calibre(self):
        records = sections(mobi_writer.build_mobi(build_book()))
        source = [payload for code, payload in self._codes(records[0])
                  if code == mobi.EXTH_SOURCE]
        self.assertTrue(source)
        self.assertFalse(source[0].startswith(b'calibre:'))


class IndexOverflowTests(unittest.TestCase):
    """索引记录的 64 KiB 上限。"""

    def test_large_ncx_rolls_over_into_more_index_records(self):
        """几千条目录项会超出单条 INDX 记录的 64 KiB，必须滚动分片。"""
        book = book_module.Book(title='大目录', author='作者')
        body = ''.join('<h2 id="s%04d">章节%d</h2><p>正文%d</p>' % (i, i, i)
                       for i in range(4000))
        book.add_document(book_module.Document(
            'c.xhtml', content='<html><body>%s</body></html>' % body))
        book.toc = [book_module.TocEntry('章节%d' % i, 'c.xhtml#s%04d' % i)
                    for i in range(4000)]
        records = sections(mobi_writer.build_mobi(book, kf8=True))
        index = struct.unpack('>I', records[0][244:248])[0]
        info = index_header(records[index])
        self.assertGreater(info['count'], 1, '4000 条目录应该需要多条索引记录')
        self.assertEqual(info['total'], 4000)
        total = 0
        for j in range(info['count']):
            record = records[index + 1 + j]
            self.assertLessEqual(len(record), 0x10000, '索引记录超过 64 KiB')
            total += index_header(record)['count']
        self.assertEqual(total, 4000)

        # 每个索引记录在 geometry 里各有一个描述符，主 IDXT 指得到。
        header = records[index]
        idxt_at = info['start']
        self.assertEqual(header[idxt_at:idxt_at + 4], b'IDXT')
        for j in range(info['count']):
            target = struct.unpack_from('>H', header, idxt_at + 4 + 2 * j)[0]
            length = header[target]
            name = header[target + 1:target + 1 + length].decode('utf-8')
            self.assertTrue(name)


class DeadCodeTests(unittest.TestCase):
    """清单要求删掉的死代码与无用参数。"""

    def test_boundary_builder_is_gone(self):
        self.assertFalse(hasattr(kf8, 'build_boundary'))

    def test_mobi_module_has_no_duplicate_build_text_records(self):
        self.assertFalse(hasattr(mobi, 'build_text_records'))

    def test_build_mobi_takes_no_images_argument(self):
        import inspect
        parameters = inspect.signature(mobi_writer.build_mobi).parameters
        self.assertNotIn('images', parameters)

    def test_common_records_takes_only_text_records(self):
        import inspect
        parameters = inspect.signature(mobi_writer._common_records).parameters
        self.assertEqual(list(parameters), ['text_records'])

    def test_index_helpers_are_gone(self):
        for name in ('build_index_record', 'build_index_header'):
            self.assertFalse(hasattr(mobi_header, name), name)

    def test_unused_exth_flag_constants_are_gone(self):
        for name in ('EXTH_FLAG_AUTHOR', 'EXTH_FLAG_PUBLISHER',
                     'EXTH_FLAG_DESCRIPTION', 'EXTH_FLAG_TITLE',
                     'EXTH_FLAG_UPDATED_TITLE', 'EXTH_FLAG_KF8_PRESENT'):
            self.assertFalse(hasattr(mobi_header, name), name)



class Mobi6IndexCapacityTests(unittest.TestCase):
    """MOBI 6 的单条索引记录有 64 KiB 上限。"""

    def test_too_many_toc_entries_fails_with_a_readable_message(self):
        from workshop.convert.book import Book, Document, TocEntry
        from workshop.convert import mobi_writer
        book = Book(title='大书', author='a')
        book.add_document(Document('c1.xhtml',
                                   content='<html><body><p>%s</p></body></html>' % ('正文' * 10000)))
        for index in range(6000):
            book.toc.append(TocEntry('第%d节' % index, href='c1.xhtml'))
        with self.assertRaises(ValueError) as caught:
            mobi_writer.build_mobi(book, kf8=False)
        self.assertIn('64 KiB', str(caught.exception))
        self.assertIn('AZW3', str(caught.exception))

if __name__ == '__main__':
    unittest.main()
