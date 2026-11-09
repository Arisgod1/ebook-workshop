"""AZW3（KF8）写出：纯 KF8 standalone 的分块、骨架与目录索引。

与 Calibre 的 AZW3Output 一样，.azw3 输出纯 KF8 文件，不写 MOBI 6 兼容段、
不写 BOUNDARY。文件里只有一份 KF8 头（record 0，264 字节头布局），
chunk / skeleton / NCX 三张索引各自成对出现（头记录 + 索引记录）。

Kindle 对 KF8 standalone 的支持最完整，这个结构也避开了联合文件
需要的 EXTH 121、双份 EXTH 与 BOUNDARY 判定。

索引的字节布局逐项对照 Calibre 的 writer8/index.py：
一张索引 = 主索引头（TAGX + geometry + IDXT）+ N 条索引记录 + M 条 CNCX。
主索引头的 IDXT 指向 geometry 块里各索引记录的描述符，
索引记录自己的 IDXT 才指向条目在记录内的偏移。
"""

import struct

from .mobi import VALUES_PER_ENTRY, align_block, build_cncx, encode_number
from .mobi_header import INDEX_HEADER_LENGTH, build_tagx

# 每条 chunk 的最大字节数。
CHUNK_SIZE = 8192

# 单条索引记录的容量上限。kindlegen 用 1048 字节的余量，
# 超过就开下一条索引记录。
RECORD_LIMIT = 0x10000 - INDEX_HEADER_LENGTH - 1048

# skeleton 索引的标签：chunk 数量与几何位置。
SKEL_TAGS = (1, 6, 0)

# chunk 索引的标签。
CHUNK_TAGS = (2, 3, 4, 6, 0)

# KF8 的每张索引有自己的位掩码表，和 MOBI 6 的目录索引不通用。
# 掩码同时决定控制字节里能放下几个值。
SKEL_MASKS = {1: 3, 6: 12}
CHUNK_MASKS = {2: 1, 3: 2, 4: 4, 6: 8}

# KF8 目录索引的标签：offset / length / label / depth / parent /
# first_child / last_child / pos_fid。
NCX_TAGS = (1, 2, 3, 4, 21, 22, 23, 6, 0)
NCX_MASKS = {1: 1, 2: 2, 3: 4, 4: 8, 21: 16, 22: 32, 23: 64, 6: 128}


def split_chunks(text, size=CHUNK_SIZE):
    """把文本流切成 chunk。

    切分点尽量落在标签边界上，避免把一个标签拆到两个 chunk 里，
    否则阅读器拼回去时会出问题。
    """
    chunks = []
    position = 0
    length = len(text)
    while position < length:
        end = min(position + size, length)
        if end < length:
            # 往回退到最近的标签闭合处。
            boundary = text.rfind(b'>', position + size // 2, end)
            if boundary != -1:
                end = boundary + 1
        chunk = text[position:end]
        if chunk:
            chunks.append(chunk)
        position = end
    return chunks or [b'']


def build_chunk_table(chunks, file_number, sequence_start=0):
    """生成 chunk 表。

    每项记录：插入位置、选择器、文件号、序号、起止位置。

    骨架为空时，每个 chunk 都插在骨架开头（insert_pos=0），
    重建结果就是各块顺序拼起来。selector 是这个 chunk 在资源里的
    标识，kindle:pos:fid 与图片引用都用它。
    """
    table = []
    offset = 0
    for index, chunk in enumerate(chunks):
        table.append({'insert_pos': 0,
                      'selector': '%010d' % offset,
                      'file_number': file_number,
                      'sequence_number': sequence_start + index,
                      'start_pos': offset,
                      'length': len(chunk)})
        offset += len(chunk)
    return table


def build_chunk_index(table):
    """构造 chunk 索引记录（INDX）。

    CHUNK_TAGS 有 4 个真实标签，依次是 cncx_offset、file_number、
    sequence_number、geometry（两个值）；条目里的值个数必须一一对应。
    """
    # chunk 索引的 tag 2 是 CNCX 池里的偏移，不能恒写 0：
    # 读端会用 cncx[0] 取串，池是空的就崩。
    selectors = []
    for item in table:
        if item['selector'] not in selectors:
            selectors.append(item['selector'])
    cncx_records, offsets = build_cncx(selectors)

    entries = []
    for item in table:
        entries.append((item['selector'],
                        {2: offsets[item['selector']],
                         3: item['file_number'],
                         4: item['sequence_number'],
                         6: (item['start_pos'], item['length'])}))
    return _build_index(entries, CHUNK_TAGS, CHUNK_MASKS, cncx_records)


def build_skel_index(skeletons):
    """构造 skeleton 索引记录（INDX）。

    一条 skeleton 记它的 chunk 数量与在文本流里的位置。
    读端按 chunk_count 累加去消费 chunk 表，数目必须与 chunk 表对上。
    规范要求 tag1 与 geometry 都写两遍。
    """
    entries = []
    for skel in skeletons:
        entries.append((skel['name'],
                        {1: (skel['chunk_count'], skel['chunk_count']),
                         6: (skel['start_pos'], skel['length'],
                             skel['start_pos'], skel['length'])}))
    return _build_index(entries, SKEL_TAGS, SKEL_MASKS)


def build_ncx_index(toc_items, chunk_table=None, text_length=0):
    """构造 KF8 的 NCX 目录索引（头记录 + 索引记录 + CNCX 记录）。

    目录项的标识是十六进制序号，标题文字放在 CNCX 字符串池里，
    条目的 tag 3 是它在池内的偏移。读者按 头记录号 + count + 1
    定位池，所以 CNCX 记录必须紧跟在索引记录之后。

    条目必须按 (层级, 偏移) 排序：Kindle 靠这个顺序做跨节跳转，
    目录树本身则靠 parent / first_child / last_child 三个字段还原。
    """
    # 父子关系要按读入时的深度优先顺序还原：排序会打乱顺序，
    # 之后再按层级推父子，子项就会被算到错误的父项名下。
    parent_of = {}
    children_of = {}
    stack = []
    for index, item in enumerate(toc_items):
        while stack and toc_items[stack[-1]].depth >= item.depth:
            stack.pop()
        if stack:
            parent_of[index] = stack[-1]
            children_of.setdefault(stack[-1], []).append(index)
        stack.append(index)

    order = sorted(range(len(toc_items)),
                   key=lambda index: (toc_items[index].depth, toc_items[index].offset))
    position = {old: new for new, old in enumerate(order)}
    ordered = [toc_items[index] for index in order]

    # 长度：到下一个「层级不更深、偏移更大」的条目为止；没有就到正文末尾。
    length_until = []
    for item in ordered:
        enders = [other.offset for other in ordered
                  if other.depth <= item.depth and other.offset > item.offset]
        length_until.append(min(enders) if enders else text_length)

    cncx_records, cncx_offsets = build_cncx(item.title for item in ordered)

    entries = []
    for new in range(len(ordered)):
        old = order[new]
        item = ordered[new]
        kids = children_of.get(old, [])
        entries.append((
            '%02X' % new,
            {1: item.offset,
             2: max(0, length_until[new] - item.offset),
             3: cncx_offsets[item.title],
             4: item.depth,
             21: position.get(parent_of.get(old)),
             22: position.get(kids[0]) if kids else None,
             23: position.get(kids[-1]) if kids else None,
             6: _pos_fid(chunk_table or [], item.offset)},
        ))

    return _build_index(entries, NCX_TAGS, NCX_MASKS, cncx_records)


def _pos_fid(chunk_table, offset):
    """把正文偏移换算成 (chunk 序号, chunk 内偏移)。

    阅读器按 chunk 序号找 chunk，再加上偏移定位到具体位置。
    """
    for chunk in chunk_table:
        start = chunk['start_pos']
        if start <= offset < start + chunk['length']:
            return (chunk['sequence_number'], offset - start)
    if chunk_table:
        last = chunk_table[-1]
        return (last['sequence_number'], offset - last['start_pos'])
    return (0, offset)


def control_bits(tag, value, masks):
    """算出这个标签在控制字节里该置的位。

    控制字节里每个标签占一段连续位，写的是「值组数」而不是值的个数：
    值组数 = 值个数 / 每组值数。阅读器按这一段的值决定读几组变长整数。
    置满整段（比如两组用两位掩码）才表示「后面先跟一个字节长度」，
    所以单组必须写 1，写满掩码会让阅读器走错分支、把整条流读乱。
    """
    mask = masks.get(tag, 0)
    if not mask:
        return 0
    count = len(value) if isinstance(value, tuple) else 1
    groups = count // VALUES_PER_ENTRY.get(tag, 1)
    shift = (mask & -mask).bit_length() - 1
    return mask & (groups << shift)


def _build_index(entries, tags, masks, cncx_records=()):
    """构造一张 KF8 索引。

    一张索引是若干条记录：主索引头（装着 TAGX、geometry、IDXT）、
    一条或多条索引记录（每条装着一批条目和自己的 IDXT），
    最后是 CNCX 字符串池（若有）。

    entries: [(标签文本, {标签号: 值或值元组}), ...]
    masks 是这张索引专用的位掩码表。
    """
    tagx = build_tagx(tags, masks=masks)
    real_tags = [tag for tag in tags if tag]

    # 按 RECORD_LIMIT 把条目分到多条索引记录里。
    blocks = [bytearray()]
    block_counts = [0]
    block_last = ['']
    idxt_blocks = [bytearray()]
    for entry in entries:
        label = entry[0]
        raw = bytearray(label.encode('utf-8'))
        raw.insert(0, len(raw))
        control = 0
        values = []
        for tag in real_tags:
            if tag not in entry[1]:
                continue
            value = entry[1][tag]
            # None 表示这条目录项没有这个标签（比如顶层项没有父项）。
            if value is None:
                continue
            control |= control_bits(tag, value, masks)
            if isinstance(value, tuple):
                values.extend(value)
            else:
                values.append(value)
        block = bytearray(raw)
        # 控制字节把各标签的位掩码按位或在一起，
        # 阅读器靠它决定读几个变长整数。
        block.append(control)
        for value in values:
            block.extend(encode_number(value))

        offset = len(blocks[-1])
        idxt_size = len(idxt_blocks[-1]) + 2
        if offset + idxt_size + len(block) + 2 > RECORD_LIMIT:
            blocks.append(bytearray())
            idxt_blocks.append(bytearray())
            block_counts.append(0)
            block_last.append('')
            offset = 0
        idxt_blocks[-1].extend(struct.pack('>H', INDEX_HEADER_LENGTH + offset))
        blocks[-1].extend(block)
        block_counts[-1] += 1
        block_last[-1] = label

    index_records = []
    for body, idxt, count in zip(blocks, idxt_blocks, block_counts):
        body_block = bytes(align_block(bytes(body)))
        idxt_block = bytes(align_block(b'IDXT' + bytes(idxt)))
        head = bytearray()
        head.extend(b'INDX')
        head.extend(struct.pack('>I', INDEX_HEADER_LENGTH))
        head.extend(b'\0' * 4)
        head.extend(struct.pack('>I', 1))           # 记录类型：索引记录
        head.extend(b'\0' * 4)
        head.extend(struct.pack('>I', INDEX_HEADER_LENGTH + len(body_block)))
        head.extend(struct.pack('>I', count))
        head.extend(b'\xff' * 8)
        head.extend(b'\0' * (192 - len(head)))
        assert len(head) == 192, len(head)
        index_records.append(bytes(head) + body_block + idxt_block)

    # geometry：每个索引记录一个描述符，名字取该记录最后一个条目的 ident。
    # 主索引头的 IDXT 给出每个描述符在头记录里的绝对偏移（Calibre 的约定：
    # 192 + TAGX 块长度 + 描述符在 geometry 块内的偏移）。
    tagx_block = align_block(tagx)
    geometry = bytearray()
    geometry_offsets = []
    for last, count in zip(block_last, block_counts):
        encoded = last.encode('utf-8')
        geometry_offsets.append(len(geometry))
        geometry.append(len(encoded))
        geometry.extend(encoded)
        geometry.extend(struct.pack('>H', count))
    geometry_block = bytes(align_block(bytes(geometry)))
    tagx_end = INDEX_HEADER_LENGTH + len(tagx_block)
    main_idxt = bytearray(b'IDXT')
    for offset in geometry_offsets:
        main_idxt.extend(struct.pack('>H', tagx_end + offset))
    main_idxt_block = bytes(align_block(bytes(main_idxt)))

    header = bytearray()
    header.extend(b'INDX')
    header.extend(struct.pack('>I', INDEX_HEADER_LENGTH))    # 头长
    header.extend(b'\0' * 8)                                # 未用
    header.extend(struct.pack('>I', 2))                      # 索引类型：变形词索引
    header.extend(struct.pack('>I', 0))                      # IDXT 偏移，稍后回填
    header.extend(struct.pack('>I', len(index_records)))     # 索引记录数
    header.extend(struct.pack('>I', 65001))                  # UTF-8
    header.extend(struct.pack('>I', 0xFFFFFFFF))             # 未用
    header.extend(struct.pack('>I', sum(block_counts)))      # 条目数
    header.extend(struct.pack('>I', 0))                      # ORDT 偏移
    header.extend(struct.pack('>I', 0))                      # LIGT 偏移
    header.extend(struct.pack('>I', 0))                      # ORDT/LIGT 条目数
    header.extend(struct.pack('>I', len(cncx_records)))      # CNCX 记录数
    header.extend(b'\0' * (180 - len(header)))
    header.extend(struct.pack('>I', INDEX_HEADER_LENGTH))    # TAGX 偏移
    header.extend(b'\0' * (192 - len(header)))
    assert len(header) == 192, len(header)
    header.extend(align_block(tagx))
    header.extend(geometry_block)
    idxt_offset = len(header)
    header.extend(main_idxt_block)
    header[20:24] = struct.pack('>I', idxt_offset)

    records = [bytes(header)]
    records.extend(index_records)
    records.extend(cncx_records)
    return records
