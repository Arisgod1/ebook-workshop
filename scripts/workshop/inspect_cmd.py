"""检查与比较：看出结构问题、比对不同版本，不展示正文内容。"""
import collections
import re
from pathlib import Path

from . import epub, text

HEADINGS = {epub.q(epub.H, 'h' + str(level)) for level in range(1, 7)}


def _as_int(value):
    """章号转成整数；转不了就当作没有章号，不能崩。

    用户自定义 --pattern 时，number 组里可能放的是「十五」这样的中文数字。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    if text_value.isdigit():
        return int(text_value)
    return text.chinese_number(text_value)


def snapshot(path, args):
    """Collect structural facts about one TXT or EPUB, without quoting its text."""
    chapters, groups, records = [], [], []
    semantic_hints = collections.Counter()
    result = {'file': path.name}
    if path.suffix.lower() == '.txt':
        content, encoding = text.decode(path, args.encoding)
        prefix, chapters = text.split_text(content, args.pattern)
        result.update(encoding=encoding, frontmatter_paragraphs=len(prefix))
        records = [(None, line) for line in prefix]
        for chapter in chapters:
            records.extend((None, line) for line in chapter['paragraphs'])
            groups.append(chapter['heading'])
    else:
        files, opf, package = epub.load(path)
        _, spine = epub.inventory(files, opf, package)
        result['metadata'] = {tag: [e.text or '' for e in package.findall('.//' + epub.q(epub.D, tag))]
                              for tag in ('title', 'creator', 'language')}
        result['spine_documents'] = len(spine)
        result['reading_documents'] = []
        result['page_markers'] = 0
        for name, item in spine:
            if item.get('media-type') != 'application/xhtml+xml':
                continue
            body = epub.xml(files[name]).find(epub.q(epub.H, 'body'))
            if body is None:
                continue
            result['page_markers'] += sum('pagebreak' in e.get(epub.q(epub.EP, 'type'), '').split() for e in body.iter())
            if 'nav' in item.get('properties', '').split() or any(
                set(e.get(epub.q(epub.EP, 'type'), '').split()) & {'cover', 'titlepage', 'toc'}
                for e in body.iter()
            ):
                continue
            result['reading_documents'].append(name)
            labels = {'bodymatter': '正文', 'afterword': '后记', 'appendix': '附录'}
            semantic_hints.update({label for e in body.iter()
                                   for kind in e.get(epub.q(epub.EP, 'type'), '').split()
                                   if (label := labels.get(kind))})
            current = None
            for element in body.iter():
                value = ''.join(element.itertext()).strip() if element.tag in HEADINGS or element.tag == epub.q(epub.H, 'p') else ''
                if element.tag in HEADINGS:
                    match = re.match(args.pattern, value)
                    number = None
                    if match:
                        groups_of = match.groupdict()
                        number = groups_of.get('number') or groups_of.get('pipe_number')
                        if not number:
                            # 中文章号：第N章 里的 N 可能是「十五」这样的中文数字。
                            number = text.chinese_number(groups_of.get('cn_number'))
                    if match or text.section_label(value):
                        current = {'heading': value, 'number': _as_int(number), 'paragraphs': []}
                        chapters.append(current)
                    if value:
                        groups.append(value)
                elif element.tag == epub.q(epub.H, 'p') and value:
                    if current is not None:
                        current['paragraphs'].append(value)
                    records.append((name, value))

    def hint(heading):
        match = re.match(args.pattern, heading)
        title = (match.groupdict().get('title') or match.groupdict().get('pipe_title')) if match else None
        return text.section_label(heading) or text.section_label(title or '')

    section_counts = collections.Counter(filter(None, map(hint, groups)))
    numbers = [c['number'] for c in chapters if c['number'] is not None and not text.section_label(c['heading'])]
    paragraphs = collections.Counter(text.normalized(p) for _, p in records)
    suspicious = collections.Counter(name for name, paragraph in records if text.OCR.search(paragraph))
    result.update(chapter_headings_recognized=len(chapters), declared_numbers=numbers,
                  section_heading_hints=dict(section_counts), section_markup_documents=dict(semantic_hints),
                  empty_chapter_candidates=[i for i, c in enumerate(chapters, 1) if not c['paragraphs']],
                  short_chapter_candidates=[i for i, c in enumerate(chapters, 1) if sum(map(len, c['paragraphs'])) < 800],
                  duplicate_declared_numbers=sorted(n for n, c in collections.Counter(numbers).items() if c > 1),
                  numbering_jumps=[(a, b) for a, b in zip(numbers, numbers[1:]) if b != a + 1],
                  repeated_paragraphs=sum(c - 1 for p, c in paragraphs.items() if c > 1),
                  repeated_long_paragraphs=sum(c - 1 for p, c in paragraphs.items() if c > 1 and len(p) > 150),
                  replacement_characters=sum(p.count('�') for _, p in records),
                  suspicious_paragraphs=sum(suspicious.values()),
                  suspicious_documents={name: count for name, count in suspicious.items() if name is not None},
                  scope='Heuristic; section labels and paragraph counts do not establish completeness')
    if args.show_headings:
        result['headings'] = [c['heading'] for c in chapters]
    return result, paragraphs


def inspect(args):
    result, _ = snapshot(Path(args.input), args)
    epub.report(result)


def compare(args):
    """Compare editions by matching paragraphs; counts are clues, not a completeness verdict."""
    first, *others = [snapshot(Path(path), args) for path in args.inputs]
    baseline, original = first
    for info, _ in [first] + others:
        info.pop('reading_documents', None)
        info.pop('suspicious_documents', None)
    comparisons = []
    for other, paragraphs in others:
        comparisons.append({'file': other['file'],
                            'matching_paragraphs': sum((original & paragraphs).values()),
                            'baseline_only_paragraphs': sum((original - paragraphs).values()),
                            'other_only_paragraphs': sum((paragraphs - original).values())})
    epub.report({'baseline': baseline['file'], 'books': [baseline] + [item for item, _ in others],
                 'comparisons': comparisons,
                 'scope': 'Exact paragraphs ignoring whitespace; edits and paragraph splitting affect counts; no completeness verdict'})


def register(sub):
    parser = sub.add_parser('inspect')
    parser.add_argument('input')
    parser.add_argument('--encoding')
    parser.add_argument('--pattern', default=text.PATTERN)
    parser.add_argument('--show-headings', action='store_true')
    parser.set_defaults(run=inspect)
    parser = sub.add_parser('compare', help='Compare two or more TXT/EPUB editions without quoting text')
    parser.add_argument('inputs', nargs='+')
    parser.add_argument('--encoding')
    parser.add_argument('--pattern', default=text.PATTERN)
    parser.set_defaults(run=compare, show_headings=False)
