"""PalmDOC 压缩：MOBI 文本记录使用的记录级压缩算法。

算法本身是公开的 PalmDOC/MOBI 格式规范，这里按规范独立实现：

  0x00-0x08  后续 N 个字节原样输出
  0x09-0x7F  字面字符
  0x80-0xBF  LZ77 引用：距离 = (word & 0x3FFF) >> 3，长度 = (word & 7) + 3
  0xC0-0xFF  空格 + ASCII：输出空格后跟 (byte ^ 0x80)

引用距离最大 2047，匹配长度 3 到 10。匹配时优先取更长的匹配，长度相同时取更近的
出现位置；为避免每个位置都回扫整个缓冲区，按 3 字节前缀建哈希链，
从最近的候选往回找。
"""

MIN_MATCH = 3
MAX_MATCH = 10
MAX_DISTANCE = 2047
HASH_BITS = 16
HASH_SIZE = 1 << HASH_BITS

# 二进制运行：控制字符与高位字节成组输出，每组最多 8 字节。
BINARY_LIMIT = 8


def _hash(data, position):
    """取 3 字节前缀作为哈希键。"""
    value = data[position] << 16 | data[position + 1] << 8 | data[position + 2]
    return (value * 0x1E35A7BD) >> (32 - HASH_BITS) & (HASH_SIZE - 1)


def compress(data):
    """把一段 UTF-8 文本压成 PalmDOC 记录内容。"""
    if not data:
        return b''
    out = bytearray()
    length = len(data)
    heads = [-1] * HASH_SIZE
    prev = [-1] * length

    def register(index):
        """把一个位置挂到哈希链上，供后续位置回溯匹配。"""
        if index < 0 or index + MIN_MATCH > length:
            return
        key = _hash(data, index)
        prev[index] = heads[key]
        heads[key] = index

    position = 0
    indexed = 0
    while position < length:
        match_length = 0
        match_distance = 0

        # 只在两端各留出 10 字节余量时做回溯匹配，这是 PalmDOC 的既定行为。
        if position > 10 and length - position > 10:
            # 先把此前未登记的位置补进哈希链。
            while indexed < position:
                register(indexed)
                indexed += 1
            candidate = heads[_hash(data, position)]
            limit = max(0, position - MAX_DISTANCE)
            while candidate >= limit and candidate >= 0:
                span = 0
                while (span < MAX_MATCH and position + span < length
                       and data[candidate + span] == data[position + span]):
                    span += 1
                # 匹配不能越过当前位置，重复的字节只能引用已写出的部分。
                if span > position - candidate:
                    span = position - candidate
                if span > match_length:
                    match_length = span
                    match_distance = position - candidate
                    if span >= MAX_MATCH:
                        break
                candidate = prev[candidate]
            if match_length >= MIN_MATCH:
                code = 0x8000 | ((match_distance << 3) & 0x3FF8) | (match_length - MIN_MATCH)
                out.append((code >> 8) & 0xFF)
                out.append(code & 0xFF)
                position += match_length
                continue

        byte = data[position]

        # 空格后跟 ASCII 字母数字时压成两个半字节。
        if byte == 0x20 and position + 1 < length:
            following = data[position + 1]
            if 0x40 <= following < 0x80:
                out.append(following ^ 0x80)
                position += 2
                continue

        if byte == 0 or 0x08 < byte < 0x80:
            out.append(byte)
            position += 1
            continue

        # 控制字符与高位字节：成组输出，组首字节是组长度。
        run = bytearray()
        while position < length and len(run) < BINARY_LIMIT:
            current = data[position]
            if current == 0 or 0x08 < current < 0x80:
                break
            run.append(current)
            position += 1
        out.append(len(run))
        out.extend(run)

    return bytes(out)


def decompress(data):
    """解压一段 PalmDOC 记录内容。"""
    out = bytearray()
    position = 0
    length = len(data)
    while position < length:
        byte = data[position]
        position += 1
        if 1 <= byte <= 8:
            out.extend(data[position:position + byte])
            position += byte
        elif byte <= 0x7F:
            out.append(byte)
        elif byte >= 0xC0:
            out.append(0x20)
            out.append(byte ^ 0x80)
        else:
            word = (byte << 8) | data[position]
            position += 1
            distance = (word & 0x3FFF) >> 3
            span = (word & 7) + 3
            if distance and distance <= len(out):
                start = len(out) - distance
                for index in range(span):
                    out.append(out[start + index])
    return bytes(out)
