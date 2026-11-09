"""校对管线：词典、扫描、复核三层。

拆开是因为三层的失败方式完全不同：词典层依赖 jieba 与 JSON 文件，
扫描层关心 HTML 结构与覆盖率，复核层关心「判断有没有正确落到书里」。
混在一个 660 行的文件里时，出问题很难定位是哪一层。
"""

from . import lexicon, review, scan

__all__ = ['lexicon', 'review', 'scan']
