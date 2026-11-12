"""注释体检与规范化：看出注释接没接好，把接不上的接上。

同一本书里的注释做法不一：有的本来就是链接，有的只是正文里的纯文本。
这里按引用侧的形状分出三种形态，只对没接上的那种动手。

- noteref：`<a epub:type="noteref">`，已是 EPUB 3 规范，只核对不重写
- anchored：带链接但没语义标记，补上 `epub:type` 即可
- plain：圈码纯文本，需要配对后换成可跳转的引用

正文文字一个字不改，改的只是标记与容器。
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import epub, names

# 圈码：①-⑳。正文里用作注释标记的常见写法。
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
# 方括号数字：[1] [12]
BRACKETED = re.compile(r'^\[\d+\]$')
# 注释标记的可见文字：圈码、方括号数字，或「注」字。
# 不认 `*` —— 它在正文里常作排印分隔符或强调，误报太多。
# 星号型的脚注引用可以由「带链接 + 上标」这条结构规则认出来。
MARKER = re.compile(r'^(?:[%s]|\[\d+\]|注)$' % CIRCLED)
# 注释正文里以圈码开头的列表项
LEADING_CIRCLE = re.compile(r'^([%s])\s*' % CIRCLED)
# 能装下注释标记的标签
MARKER_TAGS = (epub.q(epub.H, 'sup'), epub.q(epub.H, 'span'), epub.q(epub.H, 'a'),
               epub.q(epub.H, 'small'), epub.q(epub.H, 'em'), epub.q(epub.H, 'b'),
               epub.q(epub.H, 'i'), epub.q(epub.H, 'strong'))
# 星号这类符号只在「确实带链接」时才算注释标记
STAR_MARKER = re.compile(r'^[*＊※]$')

FORM_NOTEREF = 'noteref'
FORM_ANCHORED = 'anchored'
FORM_PLAIN = 'plain'

# 配对置信度
HIGH = 'high'
MEDIUM = 'medium'
LOW = 'low'


def _text(element):
    """元素里的可见文字。"""
    return ''.join(element.itertext())


def _is_marker(text):
    """这段文字是不是一个注释标记。"""
    text = text.strip()
    if not text:
        return False
    if len(text) == 1 and text in CIRCLED:
        return True
    return bool(MARKER.match(text))


def _class_list(element):
    return (element.get('class') or '').split()


def _has_type(element, wanted):
    """元素的 epub:type 属性里有没有某个类型（空格分隔）。"""
    value = element.get(epub.q(epub.EP, 'type')) or ''
    return wanted in value.split()


def _parent_map(element):
    return {child: parent for parent in element.iter() for child in parent}


def _nearest_anchor(element, parents):
    """元素祖先里最近的那个 <a>。"""
    current = element
    while current is not None:
        if current.tag == epub.q(epub.H, 'a'):
            return current
        current = parents.get(current)
    return None


def _descendant_anchor(element):
    """元素内部第一个 <a>。

    标记文字被拆进几个链接时（`[`、`1`、`]` 各是一个 <a>），
    外层容器才是标记的位置。
    """
    for child in element.iter(epub.q(epub.H, 'a')):
        return child
    return None


def _marker_spots(body, parents):
    """正文里注释标记所在的位置，按文档顺序。

    位置取**最内层**含标记文字的元素：圈码常被包成
    <sup><small><span>①</span></small></sup>，最内层才是文字真正待的地方，
    链接加在那里能保住原有的字号与颜色。
    """
    spots = []
    for element in body.iter():
        if element.tag not in MARKER_TAGS:
            continue
        text = _text(element).strip()
        if not _is_marker(text) and not STAR_MARKER.match(text):
            continue
        # 内层也含标记文字时，这个元素只是包装，跳过
        if any(_is_marker(_text(child).strip()) for child in element):
            continue
        anchor = _nearest_anchor(element, parents) or _descendant_anchor(element)
        # 星号没有链接时只是排印分隔符，不算注释标记
        if STAR_MARKER.match(text) and anchor is None:
            continue
        spots.append({'element': element, 'text': text, 'anchor': anchor,
                      'href': (anchor.get('href') or '') if anchor is not None else ''})
    return spots


def _index_documents(files, documents):
    """每份文档的根元素、body、父表，以及全局 id 索引。"""
    parsed = {}
    ids = {}
    for path in documents:
        try:
            root = epub.xml(files[path])
        except ET.ParseError:
            continue
        body = root.find(epub.q(epub.H, 'body'))
        if body is None:
            continue
        parsed[path] = (root, body, _parent_map(body))
        for element in body.iter():
            value = element.get('id')
            if value:
                ids.setdefault(value, []).append((path, element))
    return parsed, ids


def _resolve(document, href):
    """把相对链接解析成 (目标包内路径, 锚点)。解析不了返回 (None, None)。"""
    if not href:
        return None, None
    if href.startswith('#'):
        return document, href[1:]
    target, _, fragment = href.partition('#')
    try:
        path, _ = epub.resolve(document, target)
    except ValueError:
        return None, None
    return path, fragment


def _circular_groups(body):
    """正文里以圈码开头的注释列表：[(列表容器, [(条目, 圈码), ...])]。"""
    groups = []
    for element in body.iter(epub.q(epub.H, 'ul')):
        items = []
        for child in element:
            if child.tag != epub.q(epub.H, 'li'):
                continue
            match = LEADING_CIRCLE.match(_text(child).strip())
            if match:
                items.append((child, match.group(1)))
        if items:
            groups.append((element, items))
    return groups


def _inside(element, container):
    """element 是不是 container 自身的后代（不含 container 自己）。"""
    return any(child is element for child in container.iter()) and element is not container


def _inside_any(element, containers):
    """element 是不是落在这些容器中的某一个里面。"""
    return any(_inside(element, container) for container in containers)


class Survey(object):
    """一本书里注释的清点结果。"""

    def __init__(self, path):
        self.path = path
        self.files, self.opf, self.package = epub.load(path)
        _, spine = epub.inventory(self.files, self.opf, self.package)
        self.documents = [name for name, item in epub.reading_documents(spine)]
        self.parsed, self.id_index = _index_documents(self.files, self.documents)
        self.spots = []          # 每个文档一份
        self.groups = []         # 每个文档一份
        self.targets = {}        # (文档, 锚点) -> [元素]
        self.referenced = set()  # (文档, 锚点)
        self.broken = []
        self.self_referential = []
        self._scan()

    # ---------- 清点 ----------

    def _scan(self):
        for name in self.documents:
            if name not in self.parsed:
                continue
            root, body, parents = self.parsed[name]
            spots = _marker_spots(body, parents)
            # 语义型引用：带 epub:type="noteref" 的 <a>，标记可以是图片，
            # 没有可见文字，_marker_spots 认不出来，这里单独补上。
            for anchor in body.iter(epub.q(epub.H, 'a')):
                if not _has_type(anchor, 'noteref'):
                    continue
                if any(spot['element'] is anchor or _inside(spot['element'], anchor)
                       for spot in spots):
                    continue
                spots.append({'element': anchor, 'text': _text(anchor).strip(),
                              'anchor': anchor, 'href': anchor.get('href') or ''})
            self.spots.append((name, spots))
            self.groups.append((name, _circular_groups(body)))
        # 注释正文候选：所有标记链接指向的元素。
        # 挂在 <body> 上的 id 是整份文档的锚点，不是注释正文，不能拿来判回链。
        for name, spots in self.spots:
            for spot in spots:
                target, fragment = _resolve(name, spot['href'])
                if not fragment or target not in self.files:
                    continue
                for owner, element in self.id_index.get(fragment, []):
                    if element.tag == epub.q(epub.H, 'body'):
                        continue
                    self.targets.setdefault((owner, fragment), []).append(element)
        # 逐个标记定形态，并记下有效引用指向的锚点
        self.referenced = set()
        for name, spots in self.spots:
            for spot in spots:
                spot['form'] = self._form_of(name, spot)
                if spot['form'] in (FORM_NOTEREF, FORM_ANCHORED):
                    target, fragment = _resolve(name, spot['href'])
                    if fragment and target in self.files and fragment in self.id_index:
                        self.referenced.add((target, fragment))

    def _form_of(self, name, spot):
        """标记属于哪种形态；注释正文里的回链不算引用。

        注释正文里常有指回正文的链接，它也长得像注释标记。判据是：
        这个标记的 href 恰好指向**它自己所在的那个注释正文元素**，
        那它就是回链，不是引用。
        """
        anchor = spot['anchor']
        if anchor is None:
            return FORM_PLAIN
        if _inside_any(spot['element'], self.targets.get((name, _resolve(name, spot['href'])[1]), [])):
            return None
        if _has_type(anchor, 'noteref'):
            return FORM_NOTEREF
        return FORM_ANCHORED

    def _check_links(self, name, spot):
        """验一条链接落不落得到实处。"""
        href = spot['href']
        target, fragment = _resolve(name, href)
        if target is None:
            return '链接无法解析'
        if target not in self.files:
            return '目标文件不存在'
        if not fragment:
            return None
        if fragment not in self.id_index:
            return '锚点不存在'
        return None

    # ---------- 配对 ----------

    def pairings(self, name):
        """把圈码注释块与纯文本标记配对。

        规则：一个注释块覆盖**自上一个注释块以来**出现的全部纯文本标记，
        按出现顺序一一对应。圈码序列一致才算高置信。
        """
        spots = [spot for spot in dict(self.spots).get(name, []) if spot['form'] == FORM_PLAIN]
        groups = dict(self.groups).get(name, [])
        result = []
        pending = list(spots)
        for container, items in groups:
            if len(pending) < len(items):
                for item, circle in items:
                    result.append({'container': container, 'item': item, 'circle': circle,
                                   'mark': None, 'confidence': LOW})
                continue
            taken = pending[:len(items)]
            pending = pending[len(items):]
            marks = ''.join(spot['text'] for spot in taken)
            circles = ''.join(circle for _, circle in items)
            if marks == circles:
                confidence = HIGH
            elif sorted(marks) == sorted(circles):
                confidence = MEDIUM
            else:
                confidence = LOW
            for spot, (item, circle) in zip(taken, items):
                result.append({'container': container, 'item': item, 'circle': circle,
                               'mark': spot, 'confidence': confidence})
        for spot in pending:
            result.append({'container': None, 'item': None, 'circle': None,
                           'mark': spot, 'confidence': LOW})
        return result

    # ---------- 报告 ----------

    def report(self):
        references = {'total': 0, 'linked': 0, 'plain': 0}
        notes = {'total': 0, 'semantic': 0, 'plain': 0}
        forms = {FORM_NOTEREF: 0, FORM_ANCHORED: 0, FORM_PLAIN: 0}
        broken = []
        self_referential = []
        orphan_notes = []
        unmarked_notes = []
        confidences = {HIGH: 0, MEDIUM: 0, LOW: 0}

        for name, spots in self.spots:
            for spot in spots:
                form = spot['form']
                if form is None:
                    continue
                references['total'] += 1
                forms[form] += 1
                if form == FORM_PLAIN:
                    references['plain'] += 1
                    continue
                references['linked'] += 1
                reason = self._check_links(name, spot)
                if reason:
                    broken.append({'document': name, 'text': spot['text'],
                                   'href': spot['href'], 'reason': reason})
                    continue
                anchor = spot['anchor']
                target, fragment = _resolve(name, spot['href'])
                if anchor is not None and anchor.get('id') and anchor.get('id') == fragment:
                    self_referential.append({'document': name, 'text': spot['text'],
                                            'href': spot['href'],
                                            'reason': '链接指向自己，跳过去没意义'})

        # 注释正文按 DOM 结构清点，不从链接反推：
        # 引用与回链都长得像注释标记，反推会把正文侧的锚点也算进来。
        for name in self.documents:
            if name not in self.parsed:
                continue
            root, body, parents = self.parsed[name]
            seen = set()
            containers = []
            for element in body.iter(epub.q(epub.H, 'aside')):
                containers.append(element)
            for container, items in _circular_groups(body):
                for item, circle in items:
                    containers.append(item)
            for element in containers:
                if any(_inside(element, other) for other in containers):
                    continue
                if id(element) in seen:
                    continue
                seen.add(id(element))
                notes['total'] += 1
                ident = element.get('id')
                if _has_type(element, 'footnote'):
                    notes['semantic'] += 1
                    continue
                notes['plain'] += 1
                if ident:
                    unmarked_notes.append({'document': name, 'id': ident})
                    if (name, ident) not in self.referenced:
                        orphan_notes.append({'document': name, 'id': ident})
            # 内联注释正文：带注释样式 class 的段落，且没被上面的容器包住
            for element in body.iter():
                if element.tag not in (epub.q(epub.H, 'p'), epub.q(epub.H, 'div')):
                    continue
                if id(element) in seen or any(_inside(element, other) for other in containers):
                    continue
                if not any(c in ('annotation', 'footnote', 'note')
                           or c.startswith('note-') for c in _class_list(element)):
                    continue
                seen.add(id(element))
                notes['total'] += 1
                ident = element.get('id')
                if _has_type(element, 'footnote'):
                    notes['semantic'] += 1
                else:
                    notes['plain'] += 1
                    if ident:
                        unmarked_notes.append({'document': name, 'id': ident})
                        if (name, ident) not in self.referenced:
                            orphan_notes.append({'document': name, 'id': ident})

        for name in self.documents:
            for pair in self.pairings(name):
                confidences[pair['confidence']] += 1

        return {
            'file': Path(self.path).name,
            'documents': len(self.documents),
            'references': references,
            'notes': notes,
            'forms': forms,
            'pairings': confidences,
            'broken_links': broken,
            'self_referential': self_referential,
            'orphan_notes': orphan_notes,
            'unmarked_notes': unmarked_notes,
        }


def _next_note_number(files, stem):
    """在包内挑一个没被用过的注释编号，免得 validate_files 报重复 id。"""
    used = set()
    for name, data in files.items():
        if name.endswith(('.xml', '.opf', '.ncx', '.xhtml', '.html')):
            try:
                root = epub.xml(data)
            except ET.ParseError:
                continue
            for element in root.iter():
                value = element.get('id')
                if value and value.startswith(stem) and value[len(stem):].isdigit():
                    used.add(int(value[len(stem):]))
    index = 1
    while index in used:
        index += 1
    return index


def _ensure_semantics(spot):
    """给已有的链接注释补 noteref 标记。

    已有的 href 与 id 一个都不改，只加属性，所以原有的跳转不会断。
    """
    anchor = spot['anchor']
    if anchor is None or _has_type(anchor, 'noteref'):
        return 0
    anchor.set(epub.q(epub.EP, 'type'), 'noteref')
    return 1


def _mark_note_bodies(survey, files):
    """给已有 id 的注释正文补 epub:type="footnote"。

    只认 aside 与带注释 class 的段落；回链锚点（<a>）不标。
    """
    marked = 0
    for name in survey.documents:
        if name not in survey.parsed:
            continue
        root, body, parents = survey.parsed[name]
        changed = False
        for element in body.iter():
            if element.tag == epub.q(epub.H, 'body'):
                continue
            ident = element.get('id')
            if not ident or _has_type(element, 'footnote'):
                continue
            # 只标「真被注释引用指向」的元素，别把普通带 id 的段落标上
            if (name, ident) not in survey.referenced:
                continue
            classes = _class_list(element)
            is_note = element.tag == epub.q(epub.H, 'aside') or any(
                c in ('annotation', 'footnote', 'note') or c.startswith('note-')
                for c in classes)
            # 内联脚注：被引用指向的 <p>，且段首是回链（指向正文）。
            # 普通段落即便带 id 也不标。
            if not is_note:
                if element.tag != epub.q(epub.H, 'p'):
                    continue
                if not any(child.tag == epub.q(epub.H, 'a') for child in list(element)[:2]):
                    continue
            element.set(epub.q(epub.EP, 'type'), 'footnote')
            marked += 1
            changed = True
        if changed:
            files[name] = epub.dump(root)
    return marked


def _restructure_plain(survey, files, first_id):
    """把圈码纯文本标记变成可跳转引用。

    只处理高置信配对：圈码序列完全一致才动手。注释正文从 <li> 换成
    <aside epub:type="footnote">，圈码文字留在原处不改。
    """
    linked = 0
    note_bodies = 0
    skipped = 0
    ident = first_id
    for name in survey.documents:
        if name not in survey.parsed:
            continue
        root, body, parents = survey.parsed[name]
        root_changed = False
        pairs = survey.pairings(name)
        # 按注释块分组，只在整块都是高置信时才动
        by_container = {}
        for pair in pairs:
            by_container.setdefault(pair['container'], []).append(pair)
        for container, group in by_container.items():
            if container is None:
                skipped += len(group)
                continue
            if any(pair['confidence'] != HIGH or pair['mark'] is None for pair in group):
                skipped += len(group)
                continue
            parent = parents.get(container)
            if parent is None:
                skipped += len(group)
                continue
            # 在 <ul> 原来所在的位置依次插入 aside，保持阅读顺序
            pos = list(parent).index(container)
            note_ids = []
            for pair in group:
                note_id = 'workshop-note-%d' % ident
                ident += 1
                note_ids.append(note_id)
                item = pair['item']
                aside = ET.Element(epub.q(epub.H, 'aside'),
                                   {'id': note_id,
                                    epub.q(epub.EP, 'type'): 'footnote'})
                for child in list(item):
                    aside.append(child)
                if item.text:
                    aside.text = item.text
                pos += 1
                parent.insert(pos, aside)
                note_bodies += 1
            for pair, note_id in zip(group, note_ids):
                mark = pair['mark']
                holder = mark['element']
                owner = parents.get(holder)
                if owner is None:
                    continue
                link = ET.Element(epub.q(epub.H, 'a'),
                                  {'href': '#' + note_id,
                                   epub.q(epub.EP, 'type'): 'noteref'})
                at = list(owner).index(holder)
                owner.remove(holder)
                owner.insert(at, link)
                link.append(holder)
                linked += 1
                root_changed = True
            # 整块的 li 都换成了 aside，原来的 <ul> 留空壳，删掉
            parent.remove(container)
        if root_changed:
            files[name] = epub.dump(root)
    return {'linked': linked, 'note_bodies': note_bodies, 'skipped': skipped}


def notes_fix(args):
    """把接不上的注释接上，补上语义标记；原书不改。"""
    survey = Survey(args.input)
    before = survey.report()
    files = survey.files
    opf = survey.opf
    package = survey.package

    # 已有的链接注释只需补语义标记；改过的文档记下来统一写回
    semantic = 0
    touched = set()
    for name, spots in survey.spots:
        for spot in spots:
            if spot['form'] not in (FORM_NOTEREF, FORM_ANCHORED):
                continue
            if _ensure_semantics(spot):
                semantic += 1
                touched.add(name)
    for name in touched:
        files[name] = epub.dump(survey.parsed[name][0])

    bodies = _mark_note_bodies(survey, files)
    first_id = _next_note_number(files, 'workshop-note-')
    link_result = _restructure_plain(survey, files, first_id)

    # 可见文字不变式：正文文字一个字都不能改
    for name in survey.documents:
        if name not in survey.parsed:
            continue
        original = epub.visible(survey.parsed[name][1])
        updated = epub.xml(files[name]).find(epub.q(epub.H, 'body'))
        if updated is not None and epub.visible(updated) != original:
            raise ValueError('Notes normalization changed visible text: ' + name)

    args.output = names.output_for(args, epub.book_title(package))
    epub.write(files, args.output)
    epub.report({'output': args.output,
                 'references_semantic': semantic,
                 'note_bodies_semantic': bodies,
                 'references_linked': link_result['linked'],
                 'note_bodies_created': link_result['note_bodies'],
                 'left_for_review': link_result['skipped'],
                 'broken_links_before': len(before['broken_links'])})


def notes(args):
    """Report how a book's annotations are wired up; nothing is written."""
    epub.report(Survey(args.input).report())


def register(sub):
    parser = sub.add_parser('notes', help='Report how annotations are wired up')
    parser.add_argument('input')
    parser.set_defaults(run=notes)

    fix = sub.add_parser('notes-fix', help='Make inline annotations clickable and semantic')
    fix.add_argument('input')
    fix.add_argument('--output')
    fix.set_defaults(run=notes_fix)
