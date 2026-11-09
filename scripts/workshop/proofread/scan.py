"""校对扫描：把书里的文字抽成段落，再找出可疑的位置。

只管「哪里可能有问题」，不管结果怎么复核、怎么写入。
段落抽取的锚点是「文档 + 节点在 body 里的序号」，
写入阶段用同一套遍历规则找回节点，所以两边必须一致。
"""

import collections
import hashlib
import json
import re

from .. import epub
from .. import text as textutil
from .lexicon import WordIndex, enclosed

# 段落级：整体算一段。段内的行内标签不影响分段，
# 这样「错字被标签切成两半」仍然会被认出来并拒绝写入。
BLOCK_TAGS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li',
              'td', 'th', 'dd', 'dt', 'blockquote', 'pre')
# 容器级：内容要按段落拆开。aside 常用来放脚注，也要算容器，
# 否则脚注会被整块再扫一遍，同一段文字出现两个锚点，
# 写入时判定重叠，整本书的修正都写不出去。
CONTAINER_TAGS = ('div', 'section', 'article', 'aside', 'nav', 'header',
                  'footer', 'figure', 'blockquote', 'ul', 'ol', 'dl', 'table')


def digest(value):
    """段落指纹：确认写入时改的还是同一段文字。"""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def sentence_context(text, start, end):
    """取出疑点前后的整句，供复核者判断。"""
    spans = [(match.start(), match.end())
             for match in re.finditer(r'[^。！？\n]+[。！？\n]*|[。！？\n]+', text)]
    selected = [i for i, (left, right) in enumerate(spans) if left < end and start < right]
    if not selected:
        return text
    left = spans[max(0, selected[0] - 1)][0]
    right = spans[min(len(spans) - 1, selected[-1] + 1)][1]
    return text[left:right]


def _container_parts(element, block_tags):
    """容器里不属于任何段落级块的文字，按出现顺序切开。

    题记常写成 div > aside，aside 不是段落级块，它的文字不属于任何
    已选段落。只取 element.text 会漏掉它，整页因此被判为「没扫干净」。
    这里沿子树收集「不在段落级块里」的文字，每个连续片段算一段。
    """
    parts = []

    def walk(node, inside_block, buffer):
        inside_block = inside_block or node.tag in block_tags
        if not inside_block and node.text and node.text.strip():
            buffer.append(node.text)
        for child in node:
            walk(child, inside_block, buffer)
            if not inside_block and child.tail and child.tail.strip():
                buffer.append(child.tail)
        return buffer

    # 容器的直接文字与各子树里段外的文字分别成段：它们本来就不连续。
    if element.text and element.text.strip():
        parts.append(element.text)
    for child in element:
        if child.tag in block_tags:
            continue
        buffer = []
        walk(child, False, buffer)
        value = ''.join(buffer)
        if value.strip():
            parts.append(value)
        if child.tail and child.tail.strip():
            parts.append(child.tail)
    return parts
    """这一页是不是目录页。

    判据：页面里没有段落级正文块，只有容器和链接。
    目录页常被排版成整页 div 加链接，不是正文，不该进校对范围。
    """
    paragraphs = 0
    links = 0
    for element in body.iter():
        tag = element.tag.split('}')[-1]
        if tag == 'p':
            paragraphs += 1
        elif tag == 'a':
            links += 1
    return paragraphs == 0 and links > 0


def is_navigation_page(body):
    """这一页是不是目录页。

    判据：页面里没有段落级正文块，只有容器和链接。
    目录页常被排版成整页 div 加链接，不是正文，不该进校对范围。
    """
    paragraphs = 0
    links = 0
    for element in body.iter():
        tag = element.tag.split('}')[-1]
        if tag == 'p':
            paragraphs += 1
        elif tag == 'a':
            links += 1
    return paragraphs == 0 and links > 0


def paragraphs(path):
    """把书里的可读文字按段落收集起来。

    锚点是「文档 + 元素在 body 里的序号」。div 这样的容器也参与分段，
    否则正文放在 div 里的书会整本漏扫——漏扫比扫错更危险。
    """
    files, opf, package = epub.load(path)
    _, spine = epub.inventory(files, opf, package)
    rows = []
    seen = set()
    skip_tags = {epub.q(epub.H, 'script'), epub.q(epub.H, 'style')}
    for name, item in spine:
        if name in seen or item.get('media-type') != 'application/xhtml+xml':
            continue
        if 'nav' in item.get('properties', '').split():
            continue
        seen.add(name)
        root = epub.xml(files[name])
        body = root.find(epub.q(epub.H, 'body'))
        if body is None or is_navigation_page(body):
            continue
        elements = list(body.iter())
        parents = {child: parent for parent in elements for child in parent}
        block_tags = {epub.q(epub.H, tag) for tag in BLOCK_TAGS}
        container_tags = {epub.q(epub.H, tag) for tag in CONTAINER_TAGS}
        owner = {}
        for index, element in enumerate(elements):
            if element.tag in skip_tags:
                continue
            parent = parents.get(element)
            block = None
            while parent is not None:
                if parent.tag in block_tags:
                    block = parent
                    break
                parent = parents.get(parent)
            owner[index] = block
        selected = set()
        for index, element in enumerate(elements):
            if element.tag not in block_tags:
                continue
            parent = parents.get(element)
            nested = False
            while parent is not None:
                if parent in selected:
                    nested = True
                    break
                parent = parents.get(parent)
            if nested:
                continue
            if any(child.tag in skip_tags for child in element.iter()):
                continue
            value = ''.join(element.itertext())
            if value.strip():
                selected.add(element)
                rows.append((name, index, value))
        # 容器：没有段落级子块时正文直接写在容器里，按契约中止扫描
        # （那是抽取规则没覆盖的排版，不能当成扫过了）。
        for index, element in enumerate(elements):
            if element.tag not in container_tags:
                continue
            parent = parents.get(element)
            if parent is not None and parent.tag in container_tags:
                continue
            if any(child.tag in skip_tags for child in element.iter()):
                continue
            # 看所有后代，不只看直接子元素：脚注常排成 div > aside > p，
            # 只看一层会误判成「没有段落」，把整段又扫一遍。
            covered = any(desc.tag in block_tags for desc in element.iter() if desc is not element)
            if not covered:
                value = ''.join(element.itertext())
                if value.strip():
                    rows.append((name, index, value))
            else:
                selected.add(element)
                # 容器里有段落，但它自己的文字（题记、题解）不属于任何段落，
                # 只取 element.text 会漏掉包在 aside/span 里的那些。
                for part in _container_parts(element, block_tags):
                    rows.append((name, index, part))

        def leaks(node, inside=False):
            if node.tag in skip_tags:
                return False
            inside = inside or node in selected
            if not inside and (node.text or '').strip():
                return True
            for child in node:
                if leaks(child, inside):
                    return True
                if not inside and (child.tail or '').strip():
                    return True
            return False

        if leaks(body):
            raise ValueError('Uncovered reading text in ' + name
                             + '; expand block extraction before scanning')
        # 收进来的文字必须真的被扫过，否则「查过了」就是假话。
        # visible 只算叶子节点，父节点的 itertext 会把子节点重复算一遍。
        scanned = sum(len(text) for document, _, text in rows if document == name)
        visible = 0
        for element in body.iter():
            if element.tag in skip_tags:
                continue
            if any(child.tag not in skip_tags for child in element):
                continue
            if (element.text or '').strip():
                visible += len(element.text)
        if visible and scanned < visible:
            raise ValueError('Some reading text in %s was not collected for scanning' % name)
    return files, opf, package, rows


def find_candidates(rows, rules, words, protected, confusions, variants,
                    tokenizer, frequencies, fingerprint):
    """在每一段里找疑点，并剔掉已被词典解释的。

    返回 (疑点列表, 豁免数)。疑点是候选，不是结论——
    最终由复核者逐组判断。
    """
    word_index, protected_index = WordIndex(words), WordIndex(protected)
    candidates = []
    exemptions = 0
    for document, paragraph, text in rows:
        findings = {}
        known_spans = word_index.spans(text)
        protected_spans = protected_index.spans(text)
        for old, new in rules.get('replacements', {}).items():
            offset = text.find(old)
            while offset >= 0:
                findings[(offset, offset + len(old), new)] = 'ocr_sequence'
                offset = text.find(old, offset + 1)
        for offset, char in enumerate(text):
            standard = variants.get(char)
            if standard and not any(left <= offset and offset + 1 <= right
                                    for (left, right, _), kind in findings.items()
                                    if kind == 'ocr_sequence'):
                findings[(offset, offset + 1, standard)] = 'variant_character'
        token_spans = list(tokenizer.tokenize(text, HMM=False)) if tokenizer else []

        for match in re.finditer(r'(?<=[㐀-鿿])\|(?=[㐀-鿿])', text):
            findings[(match.start(), match.end(), '')] = 'separator_noise'
        for match in re.finditer(r'“[^”\n]{2,100}(?:他说|她说|上尉对他们说|庭长说|军官说)[，,]“', text):
            # 引号提前闭合：说话人标签前应当是句号/逗号加后引号。
            spoken = re.search(r'(?:他说|她说|上尉对他们说|庭长说|军官说)[，,]“', match.group(0))
            if spoken:
                start = match.start() + spoken.start()
                findings[(start, match.end(), '，”' + spoken.group(0))] = 'quotation_boundary'
        for match in textutil.OCR.finditer(text):
            findings[(match.start(), match.end(), '')] = 'suspicious_character'
        for start in range(len(text)):
            for length in (2, 3, 4):
                old = text[start:start + length]
                if len(old) != length:
                    continue
                for offset, char in enumerate(old):
                    for replacement in confusions.get(char, ''):
                        new = old[:offset] + replacement + old[offset + 1:]
                        if new in words and new != old:
                            findings[(start, start + length, new)] = 'known_alternative'
        for (start, end, replacement), reason in sorted(findings.items()):
            old = text[start:end]
            # 明确的长串候选优先于单字警告。
            if reason == 'suspicious_character' and any(
                left <= start and end <= right and kind == 'ocr_sequence'
                for (left, right, _), kind in findings.items()
            ):
                continue
            ordinary_match = reason == 'known_alternative' and enclosed(start, end, known_spans)
            suspicious = reason in ('suspicious_character', 'ocr_sequence',
                                    'separator_noise', 'quotation_boundary')
            if not suspicious and enclosed(start, end, protected_spans):
                exemptions += 1
                continue
            if reason == 'known_alternative' and tokenizer:
                crossing = any(left < end and start < right and len(token) >= 2
                               and frequencies.get(token, 0) >= 50
                               and not (start <= left and right <= end)
                               for token, left, right in token_spans)
                if crossing and not ordinary_match:
                    continue
            if ordinary_match and tokenizer is None:
                continue
            identity = digest(json.dumps([fingerprint, document, paragraph,
                                          start, end, replacement]))
            candidates.append({'id': identity, 'document': document, 'paragraph': paragraph,
                               'paragraph_sha256': digest(text), 'start': start, 'end': end,
                               'old': old, 'suggestion': replacement, 'reason': reason,
                               'context': sentence_context(text, start, end),
                               'priority': 3 if suspicious else (0 if ordinary_match else 2),
                               'status': 'pending'})
    return candidates, exemptions


def discover_terms(rows, tokenizer, frequencies, provenance):
    """统计反复出现的陌生词，用来补词典。

    同一个词在书里出现多次、又在标准词表里查不到，很可能是
    人名地名或生造词，值得人工确认后加进 book dictionary。
    """
    occurrences = collections.defaultdict(list)
    for document, paragraph, text in rows:
        for term, start, end in tokenizer.tokenize(text, HMM=True):
            if 2 <= len(term) <= 12 and re.fullmatch(r'[㐀-鿿]+', term) \
                    and term not in frequencies:
                occurrences[term].append({'document': document, 'paragraph': paragraph,
                                          'start': start, 'end': end,
                                          'context': sentence_context(text, start, end)})
    ordered = sorted(occurrences, key=lambda term: (-len(occurrences[term]), term))
    chosen = set(ordered[:100])
    chosen.update(term for term in ordered if len(occurrences[term]) >= 5
                  and len({row['document'] for row in occurrences[term]}) >= 3)
    result = []
    for term in ordered:
        if term not in chosen:
            continue
        all_rows = occurrences[term]
        unique = list({row['context']: row for row in all_rows}.values())
        samples = [unique[i] for i in sorted({0, len(unique) // 2, len(unique) - 1})]
        result.append({'term': term, 'count': len(all_rows),
                       'documents': len({row['document'] for row in all_rows}),
                       'samples': samples, 'status': 'pending'})
    # 只差一个字的写法归成一组，方便看出「同一个词被写成了几种样子」。
    masks = collections.defaultdict(set)
    for entry in result:
        term = entry['term']
        for i in range(len(term)):
            masks[(len(term), term[:i] + '*' + term[i + 1:])].add(term)
    for entry in result:
        term = entry['term']
        related = set()
        for i in range(len(term)):
            related.update(masks[(len(term), term[:i] + '*' + term[i + 1:])])
        entry['similar_spellings'] = sorted(related - {term})
    return {'lexicon': provenance, 'selection': 'count>=5 and documents>=3, union top100',
            'terms': result}
