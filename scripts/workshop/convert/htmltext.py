"""HTML 转义与文本渲染的公共工具。

三个读入端（DOCX / ODT / HTML）各自都写过一份转义函数，
而且行为不一致：有的转引号有的不转，`_escape_attr` 一个转单引号
一个不转。这类重复最危险——改了其中一处，另外两处不会跟着改。

这里只放真正通用、且语义明确的几件事。
"""

from .book import TocEntry

# XHTML 的默认命名空间。三个读入端生成的文档都要带它，
# 否则写出 EPUB 再读回来时找不到 body。
XHTML_NS = 'http://www.w3.org/1999/xhtml'


def escape(text):
    """转义元素内容：& < >。

    属性值另有 escape_attr，因为那里还要处理引号。
    """
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def escape_attr(text):
    """转义属性值：在 escape 的基础上处理引号。

    引号必须转，否则 href 或 id 里的引号能提前闭合属性、
    注入别的属性，生成的标记就是畸形的。
    """
    return escape(text).replace('"', '&quot;').replace("'", '&apos;')


def paragraph_html(text):
    """一段纯文字转成 <p>，换行变成 <br/>。"""
    if not text:
        return ''
    return '<p>' + escape(text).replace('\n', '<br/>') + '</p>'


def title_page(book):
    """按书名作者生成标题页文档。"""
    return ('<html xmlns="%s"><head><title>%s</title></head><body><h1>%s</h1>'
            '<p>%s</p></body></html>'
            % (XHTML_NS, escape(book.title), escape(book.title), escape(book.author)))


def document(title, level, body):
    """拼一份完整的 XHTML 文档。

    title 是 <title> 里的文字，level 是正文标题的层级（1-6），
    body 是已经渲染好的内容片段。
    """
    level = min(max(level, 1), 6)
    return ('<html xmlns="%s"><head><title>%s</title></head><body>'
            '<h%d>%s</h%d>%s</body></html>'
            % (XHTML_NS, escape(title), level, escape(title), level, body))


def section_anchor(index):
    """子章节标题的锚点。

    目录里指向它的链接用 '%s#%s' % (文档路径, anchor) 拼，
    锚点是这一节自己的 id，不是父章节锚点的延伸。
    """
    return 's%04d' % index


def section_heading(chapter):
    """渲染一个子章节的标题，带锚点以便目录跳转。"""
    level = min(max(chapter['level'], 1), 6)
    return ('<h%d id="%s">%s</h%d>'
            % (level, section_anchor(chapter['index']),
               escape(chapter['title']), level))


def toc_entry(chapter, href, toc_class=None):
    """把章节树转成目录树。

    子章节的内容内嵌在父章文件里，所以链接指向父章加锚点。
    """
    toc_class = toc_class or TocEntry
    entry = toc_class(chapter['title'], href=href)
    base = href.split('#', 1)[0]
    for child in chapter.get('children', []):
        entry.children.append(
            toc_entry(child, '%s#%s' % (base, section_anchor(child['index'])), toc_class))
    return entry


def chapter_document(chapter, render_body, render_extra=None):
    """把一棵章节树渲染成一份 XHTML 文档。

    三个读入端的骨架完全一样：标题、自己的段落、子章节依次接在后面。
    差别只在「一段怎么渲染」，由 render_body 交进来；
    章节末尾若还有别的块（DOCX 的列表），用 render_extra 补。
    """
    body = ''.join(render_body(paragraph) for paragraph in chapter.get('paragraphs', []))
    if render_extra is not None:
        body += render_extra(chapter)
    for child in chapter.get('children', []):
        body += _child_sections(child, render_body, render_extra)
    return document(chapter['title'], chapter['level'], body)


def _child_sections(chapter, render_body, render_extra=None):
    """递归渲染子章节，标题带锚点。"""
    parts = [section_heading(chapter)]
    parts.extend(render_body(paragraph) for paragraph in chapter.get('paragraphs', []))
    if render_extra is not None:
        parts.append(render_extra(chapter))
    for child in chapter.get('children', []):
        parts.append(_child_sections(child, render_body, render_extra))
    return ''.join(parts)
