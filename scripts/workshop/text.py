"""Plain-text handling: encoding, chapter splitting and the patterns used to spot noise."""
import re
from pathlib import Path

# 章号里的中文数字。中文小说写「第N章」是常态，只认阿拉伯数字会整本失明。
CHINESE_NUMERALS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
                     '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

PATTERN = (r'^\s*(?:第(?P<number>\d+)章\s*(?P<title>.*)'
           r'|第(?P<cn_number>[零〇一二两三四五六七八九十百千]+)[章回]\s*(?P<cn_title>.*)'
           r'|(?P<pipe_number>\d+)\|(?P<pipe_title>.*)'
           r'|(?P<section>正文|番外|后记|後記|附录|附錄)(?:[\s：:·（(一二三四五六七八九十0-9].*)?)$')


def chinese_number(value):
    """把中文数字转成整数；不是合法数字就返回 None。

    支持到千位：十、十五、二十、二十一、一百、一百零八。
    """
    if not value:
        return None
    if value.isdigit():
        return int(value)
    total = 0
    section = 0
    current = 0
    seen = False
    for char in value:
        if char in CHINESE_NUMERALS:
            current = CHINESE_NUMERALS[char]
            seen = True
        elif char == '十':
            section += (current or 1) * 10
            current = 0
            seen = True
        elif char == '百':
            section += (current or 1) * 100
            current = 0
            seen = True
        elif char == '千':
            section += (current or 1) * 1000
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + section + current


def to_number(value):
    """把章号文字转成整数；转不了返回 None。

    支持阿拉伯数字与中文数字。用户自定义 --pattern 时，
    number 组里可能是「十五」这样的写法，不能直接 int()。
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
    return chinese_number(text_value)


def split_text(text, pattern):
    """Split plain text into front matter and chapters, one paragraph per line."""
    expression = re.compile(pattern)
    prefix, chapters, current = [], [], None
    for line in text.splitlines():
        match = expression.match(line)
        if match:
            groups = match.groupdict()
            # 章号可能是阿拉伯数字，也可能是「十五」这样的中文数字，
            # 还可能是用户自己 pattern 里的任意写法——转不了就当没有章号。
            number = to_number(groups.get('number')) or to_number(groups.get('pipe_number'))
            if number is None:
                number = chinese_number(groups.get('cn_number'))
            current = {'heading': line.strip(), 'number': number, 'paragraphs': []}
            chapters.append(current)
        elif line.strip():
            (prefix if current is None else current['paragraphs']).append(line.strip())
    return prefix, chapters
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




def section_label(heading):
    """The 正文/番外/后记/附录 label of a heading, if it has one."""
    match = SECTION.match(heading)
    return {'後記': '后记', '附錄': '附录'}.get(match[0], match[0]) if match else None


def normalized(text):
    """Text without whitespace, for comparing passages between editions."""
    return re.sub(r'\s+', '', text)
