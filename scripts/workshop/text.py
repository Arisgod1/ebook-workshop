"""Plain-text handling: encoding, chapter splitting and the patterns used to spot noise."""
import re
from pathlib import Path

PATTERN = r'^\s*(?:第(?P<number>\d+)章\s*(?P<title>.*)|(?P<pipe_number>\d+)\|(?P<pipe_title>.*)|(?P<section>正文|番外|后记|後記|附录|附錄)(?:[\s：:·（(一二三四五六七八九十0-9].*)?)$'
SECTION = re.compile(r'^(?:正文|番外|后记|後記|附录|附錄)(?=$|[\s：:·（(]|[一二三四五六七八九十0-9])')
OCR = re.compile(r'(?<=[\u3400-\u9fff])(?:[A-Za-z][0-9]?|[0-9][A-Za-z])(?=[\u3400-\u9fff])|[�□■\ue000-\uf8ff]')


def decode(path, encoding=None):
    """Read a text file, detecting UTF-16, UTF-8 or GB18030. Never guess with replacement."""
    data = Path(path).read_bytes()
    if encoding:
        return data.decode(encoding), encoding
    options = ['utf-16'] if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for candidate in options:
        try:
            return data.decode(candidate), candidate
        except UnicodeDecodeError:
            pass
    raise ValueError('Encoding not identified; supply --encoding. Never decode with errors=ignore/replace')


def split_text(text, pattern):
    """Split plain text into front matter and chapters, one paragraph per line."""
    expression = re.compile(pattern)
    prefix, chapters, current = [], [], None
    for line in text.splitlines():
        match = expression.match(line)
        if match:
            groups = match.groupdict()
            number = groups.get('number') or groups.get('pipe_number')
            current = {'heading': line.strip(), 'number': int(number) if number else None, 'paragraphs': []}
            chapters.append(current)
        elif line.strip():
            (prefix if current is None else current['paragraphs']).append(line.strip())
    return prefix, chapters


def section_label(heading):
    """The 正文/番外/后记/附录 label of a heading, if it has one."""
    match = SECTION.match(heading)
    return {'後記': '后记', '附錄': '附录'}.get(match[0], match[0]) if match else None


def normalized(text):
    """Text without whitespace, for comparing passages between editions."""
    return re.sub(r'\s+', '', text)
