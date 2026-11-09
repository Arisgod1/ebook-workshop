"""转换中间表示：各种电子书格式读入后统一成这一份结构，再由输出格式写出。

设计参考 Calibre 的 OEBBook，但只保留本项目需要的能力：文档列表、目录、
图片资源和元数据。这样新增一种输入或输出格式时，只需实现一端。
"""


class Document:
    """书内的一份内容文档。"""

    def __init__(self, href, media_type='application/xhtml+xml', content='', properties=()):
        self.href = href
        self.media_type = media_type
        self.content = content
        self.properties = list(properties)

    @property
    def is_linear(self):
        """线性阅读的文档按 spine 顺序排列，非线性的（如注释）排在后面。"""
        return 'non-linear' not in self.properties

    def __repr__(self):
        return '<Document %s>' % self.href


class TocEntry:
    """目录树的一个节点。"""

    def __init__(self, title, href=None, kind='toc', children=None):
        self.title = title
        self.href = href
        self.kind = kind
        self.children = list(children or [])

    def walk(self):
        """深度优先遍历本节点及其所有后代。"""
        yield self
        for child in self.children:
            for item in child.walk():
                yield item

    def __repr__(self):
        return '<TocEntry %r -> %s>' % (self.title, self.href)


class Image:
    """书内的一张图片资源。"""

    def __init__(self, href, data, media_type='image/jpeg'):
        self.href = href
        self.data = data
        self.media_type = media_type

    def __repr__(self):
        return '<Image %s %d bytes>' % (self.href, len(self.data))


class Book:
    """一本待转换的书。

    这是 input plugin 的产出、output plugin 的输入。格式之间互转时，
    中间只经过这一份结构，因此新增格式不会牵动其他格式。
    """

    def __init__(self, title='', author='', language='zh-CN', identifier=None):
        self.title = title
        self.author = author
        self.language = language
        self.identifier = identifier
        self.documents = []
        self.images = {}
        self.toc = []
        self.cover = None
        self.description = ''
        self.publisher = ''
        self.comments = ''
        # 其余元数据原样保留：转换不该悄悄丢掉出版信息。
        # 键是 OPF 里的 dc 元素名（date、rights、subject…）。
        self.metadata = {}
        # 样式表与文档的对应关系：href -> CSS 文字。
        # 正文的排版靠它，丢了书就变成没有样式的纯文本。
        self.styles = {}

    def add_document(self, document):
        self.documents.append(document)
        return document

    def add_image(self, image):
        self.images[image.href] = image
        return image

    def linear_documents(self):
        return [d for d in self.documents if d.is_linear]

    def image_for(self, href):
        """按书内路径找图片，找不到时按文件名兜底匹配。"""
        if href in self.images:
            return self.images[href]
        name = href.rsplit('/', 1)[-1]
        for key, image in self.images.items():
            if key.rsplit('/', 1)[-1] == name:
                return image
        return None

    def toc_entries(self):
        """按阅读顺序展平整棵目录，跳过没有链接的分组标题。

        子章节也是目录项：只取顶层会把整棵子树丢掉，
        MOBI/AZW3 的目录因此比原书少一大截。展平按深度优先，
        与 toc_tree() 的顺序一致。
        """
        return [entry for entry in self.walk_toc() if entry.href]

    def walk_toc(self):
        """深度优先遍历目录树的全部节点（含没有链接的分组标题）。"""
        def walk(entries):
            for entry in entries:
                yield entry
                for item in walk(entry.children):
                    yield item
        return walk(self.toc)

    def toc_tree(self):
        """深度优先展平整棵目录树，给出 (目录项, 层级)。

        层级从 0 开始，子项比父项深一层。MOBI/AZW3 的目录索引
        需要层级信息才能还原目录结构。
        """
        def walk(entries, depth):
            for entry in entries:
                yield entry, depth
                for item in walk(entry.children, depth + 1):
                    yield item
        return walk(self.toc, 0)

    def summary(self):
        return {'title': self.title, 'author': self.author, 'language': self.language,
                'documents': len(self.documents), 'images': len(self.images),
                'toc_entries': len(self.toc_entries())}
