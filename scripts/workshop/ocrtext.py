"""OCR 逐页文本的清洗：合并硬换行、剔除页眉页脚、归一空白。

这里只有纯字符串变换，不碰文件、不碰参数解析。OCR 引擎在外面跑，
把逐页文字交进来，这一步负责把它变成「一行一段」的普通文本，
好让已有的 build 命令按章号正确分章。

保守原则：拿不准就不合并。多一段只影响排版，粘错一句会改坏正文。
页边界是硬边界，不同页之间绝不合并。
"""

import re
from collections import Counter

# 句子收尾的标点。行尾是这些字时，断行多半是真的段落结束。
_SENTENCE_END = '。！？；：…」』”’）】.!?;:'
# 列表/项目符号开头。这类行是新的段落，不能接到上一行。
_BULLET = re.compile(r'^(?:[•·▪◦*\-—※①-⑳]|[（(]?\d+[.、)）]|[一二三四五六七八九十]+[、.])')
# 整行只有数字，或数字加少量装饰符：页码。
_PAGE_NUMBER = re.compile(
    r'^\s*(?:第\s*)?\d{1,4}\s*(?:页|/|／|\\)?\s*\d{0,4}\s*$'
    r'|^\s*[—\-–=·\s]*\d{1,4}[—\-–=·\s]*$'
    r'|^\s*第\s*\d{1,4}\s*页(?:\s*/?\s*共\s*\d{1,4}\s*页)?\s*$')
# 归一化页眉候选时忽略的装饰字符。
_DECORATION = re.compile(r'[\s\-—–_=·•|/／\\.,，。:：;；()（）\[\]【】<>《》"\'’“”]')


def _lines(page):
    """把一页文本拆成干净的行：统一换行、剥首尾空白、全角空格归一。"""
    text = page.replace('\r\n', '\n').replace('\r', '\n').replace('﻿', '')
    out = []
    for line in text.split('\n'):
        line = line.replace('　', ' ').replace('\xa0', ' ').strip()
        out.append(line)
    return out


def _is_heading(line, expression):
    """这一行是不是章标题。章标题前后都不参与合并，否则 build 认不出章节。"""
    return bool(line and expression.match(line))


def _joins_with_previous(line, expression):
    """这一行该不该接到上一行后面。"""
    if not line or _is_heading(line, expression):
        return False
    if _BULLET.match(line) or _PAGE_NUMBER.match(line):
        return False
    return True


def _join_broken(lines, expression):
    """把 OCR 按物理行切断的句子接回去，返回 (段落列表, 合并掉的行数)。

    空行是段落分隔；章标题独占一段；页边界由调用方控制（一次只传一页）。
    """
    paragraphs = []
    current = ''
    merged = 0
    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
                current = ''
            continue
        if _is_heading(line, expression):
            if current:
                paragraphs.append(current)
                current = ''
            paragraphs.append(line)
            continue
        if not current:
            current = line
            continue
        # 上一行已经收尾，或本行看着像新段落：另起一段。
        if current[-1] in _SENTENCE_END or not _joins_with_previous(line, expression):
            paragraphs.append(current)
            current = line
            continue
        current += line
        merged += 1
    if current:
        paragraphs.append(current)
    return paragraphs, merged


def _normalize_head(line):
    """页眉候选的归一化：数字、装饰符、空白全去掉。

    「—— 2 ——」「第 12 页」「- 12 -」归一化后都是同一个空串/短串，
    才能按跨页重复一起抓到。
    """
    value = re.sub(r'\d+', '', line)
    return _DECORATION.sub('', value)


def _running_heads(pages_lines, expression, min_repeat=3):
    """找出要删的页眉页脚，返回 (归一化模式集合, 具体行集合)。

    只有「去掉数字后几乎不剩内容」的短行才配当页眉候选：页眉的本质是
    「固定文字 + 变化的页码」，比如「第 12 页 / 共 300 页」「—— 2 ——」。
    有实质中文内容的短行（哪怕跨页重复）多半是正文里的叠句或目录项，
    不能因为重复就删掉。

    跨页重复是必要条件：只在至少 min_repeat 个不同页里出现才判为页眉。
    纯页码短行无论重复几次都直接删。
    """
    occurrences = Counter()
    page_numbers = set()
    for lines in pages_lines:
        seen = set()
        for line in lines:
            if not line or _is_heading(line, expression):
                continue
            if _PAGE_NUMBER.match(line):
                page_numbers.add(line)
                continue
            if len(line) > 30:
                continue
            key = _normalize_head(line)
            # 剩下的实质文字不超过 12 字：页眉通常只有书名/章名这类短标识。
            # 中文正文行一般远超这个长度，不会误判。
            if key and len(key) <= 12:
                seen.add(key)
        for key in seen:
            occurrences[key] += 1
    # 页数不够 min_repeat 时，跨页重复这条判据根本没有证据，
    # 只保留纯页码短行的删除（那个不依赖重复）。
    if len(pages_lines) < max(2, min_repeat):
        patterns = set()
    else:
        patterns = {key for key, count in occurrences.items()
                    if count >= min_repeat}
    return patterns, page_numbers


def clean_pages(pages, pattern, join_lines=True, drop_running_heads=True,
                min_repeat=3):
    """把逐页 OCR 文本清洗成一份可交给 build 的 TXT。

    pages 是字符串列表，每个元素是一页。返回
    ``{'text': 全文, 'stats': {页数, 合并行数, 删除行数, 段落数}}``。
    """
    if not pages:
        raise ValueError('No OCR pages supplied')
    if min_repeat < 1:
        raise ValueError('--min-repeat must be at least 1')
    expression = re.compile(pattern)
    pages_lines = [_lines(page) for page in pages]

    patterns, page_numbers = set(), set()
    if drop_running_heads:
        patterns, page_numbers = _running_heads(pages_lines, expression, min_repeat)

    removed = 0
    merged = 0
    paragraphs = []
    for lines in pages_lines:
        kept = []
        for line in lines:
            if not line:
                kept.append(line)
                continue
            if line in page_numbers:
                removed += 1
                continue
            if patterns and len(line) <= 30 and _normalize_head(line) in patterns:
                removed += 1
                continue
            kept.append(line)
        if join_lines:
            page_paragraphs, count = _join_broken(kept, expression)
            merged += count
        else:
            page_paragraphs = [line for line in kept if line]
        paragraphs.extend(page_paragraphs)

    # 段落之间空一行；章标题前后各留一个空行，build 才按章切开。
    blocks = []
    for paragraph in paragraphs:
        blocks.append(paragraph)
    text = '\n\n'.join(blocks) + '\n'
    return {'text': text,
            'stats': {'pages': len(pages), 'lines_merged': merged,
                      'running_heads_removed': removed,
                      'paragraphs': len(paragraphs)}}
