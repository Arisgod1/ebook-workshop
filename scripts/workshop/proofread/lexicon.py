"""校对用的词典、分词与字符变体表。

这一层只回答「哪些字词是已知的」，不关心一本书怎么扫、结果怎么复核。
拆出来的理由是它有三个外部依赖（jieba、词典 JSON、变体表），
混在扫描逻辑里时出问题很难定位是哪一层。
"""

import importlib.metadata
import json
import logging
from pathlib import Path

VARIANTS_PATH = Path(__file__).resolve().parents[3] / 'assets' / 'proofread-variants.json'


def dictionary(path):
    """读入并校验一份词典。

    结构：words / protected / confusions / replacements 四类，
    每种都有固定的形状要求，写错了要立刻报出来而不是静默忽略。
    """
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Dictionary must be an object')
    for key in ('words', 'protected'):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(word, str) or not word for word in values):
            raise ValueError(key + ' must contain nonempty strings')
    pairs = data.get('confusions', {})
    if not isinstance(pairs, dict) or any(len(k) != 1 or not isinstance(v, str) or not v
                                          for k, v in pairs.items()):
        raise ValueError('confusions must map single characters to alternative characters')
    replacements = data.get('replacements', {})
    if not isinstance(replacements, dict) or any(not k or not isinstance(v, str)
                                                  for k, v in replacements.items()):
        raise ValueError('replacements must map nonempty strings')
    return data


class WordIndex:
    """把词典词表做成前缀树，用来在句子里找已知词的位置。

    找已知词是为了「豁免」：落在词典词里的字不算错字。
    """

    def __init__(self, words):
        self.root = {}
        for word in words:
            node = self.root
            for char in word:
                node = node.setdefault(char, {})
            node[None] = word

    def spans(self, text):
        """返回所有已知词的 (起, 止, 词)。"""
        matches = []
        for start in range(len(text)):
            node = self.root
            for end in range(start, len(text)):
                node = node.get(text[end])
                if node is None:
                    break
                if None in node:
                    matches.append((start, end + 1, node[None]))
        return matches


def enclosed(start, end, spans):
    """这段位置是否落在某个已知词的区间里。"""
    return any(left <= start and end <= right for left, right, _ in spans)


def jieba_resources():
    """加载 jieba 的分词器与词频表。

    词频用来判断「这个词在中文里常不常见」，是筛疑点的依据之一。
    """
    try:
        import jieba
    except ImportError as exc:
        raise ValueError('Install jieba in this Python environment') from exc
    version = importlib.metadata.version('jieba')
    path = Path(jieba.__file__).with_name('dict.txt')
    if not path.is_file():
        path = Path(jieba.get_default_dict())
    frequencies = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        word, frequency, *_ = line.split()
        frequencies[word] = int(frequency)
    jieba.setLogLevel(logging.ERROR)
    tokenizer = jieba.Tokenizer(str(path))
    return tokenizer, frequencies, {'name': 'jieba', 'version': version,
                                    'entries': len(frequencies),
                                    'url': 'https://github.com/fxsjy/jieba/tree/v0.42.1',
                                    'license': 'MIT'}


def variant_table():
    """繁体与异体字到标准字形的映射。"""
    data = json.loads(VARIANTS_PATH.read_text(encoding='utf-8'))
    variants = data.get('variants', {})
    if not isinstance(variants, dict) or any(len(k) != 1 or not isinstance(v, str) or not v
                                             for k, v in variants.items()):
        raise ValueError('Variant table must map single characters to standard characters')
    return variants


def load_lexicon(lexicon):
    """按选项准备词表。

    返回 (分词器, 词频, 来源信息, 说明)。
    lexicon 是 'starter' 时不加载 jieba；加载失败也降级，把原因记进说明。
    """
    tokenizer, frequencies, provenance = None, {}, None
    notes = []
    if lexicon != 'starter':
        try:
            tokenizer, frequencies, provenance = jieba_resources()
        except (ImportError, OSError, ValueError) as exc:
            notes.append('Jieba unavailable; using starter dictionary: ' + str(exc))
    return tokenizer, frequencies, provenance, notes
