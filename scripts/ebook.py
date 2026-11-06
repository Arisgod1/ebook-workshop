#!/usr/bin/env python3
"""Local, dependency-free EPUB inspection and reversible preparation (Python 3.9+)."""
import argparse
import collections
import datetime
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

H = 'http://www.w3.org/1999/xhtml'
O = 'http://www.idpf.org/2007/opf'
D = 'http://purl.org/dc/elements/1.1/'
N = 'http://www.daisy.org/z3986/2005/ncx/'
EP = 'http://www.idpf.org/2007/ops'
C = 'urn:oasis:names:tc:opendocument:xmlns:container'
for prefix, uri in [('', H), ('opf', O), ('dc', D), ('epub', EP), ('ncx', N)]:
    ET.register_namespace(prefix, uri)
PATTERN = r'^\s*(?:第(?P<number>\d+)章\s*(?P<title>.*)|(?P<pipe_number>\d+)\|(?P<pipe_title>.*)|(?P<section>正文|番外|后记|後記|附录|附錄)(?:[\s：:·（(一二三四五六七八九十0-9].*)?)$'
CSS = '''body{font-family:serif;line-height:1.8;margin:0 5%;overflow-wrap:break-word}
h1{font-size:1.5em;font-weight:normal;line-height:1.6;margin:2em 0 1.5em;text-align:center;break-after:avoid}
p{text-indent:2em;margin:.5em 0;orphans:2;widows:2}nav ol{list-style:none;padding-left:1.2em}
nav li{margin:.35em 0}a{color:inherit}.cover img{display:block;max-width:100%;max-height:95vh;width:auto;height:auto;margin:auto}
.title{text-align:center;margin-top:20%}.title p{text-indent:0}.notice p{text-indent:0}
'''


def q(ns, name):
    return '{' + ns + '}' + name


def xml(data):
    if re.search(br'<!\s*(ENTITY|DOCTYPE[^>]*\[)', data, re.I):
        raise ValueError('XML entity declarations are not supported')
    return ET.fromstring(data)


def dump(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def report(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def load(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP paths')
        for n in names:
            if n.startswith('/') or '..' in n.split('/') or '\\' in n:
                raise ValueError('Unsafe ZIP path: ' + n)
        if sum(i.file_size for i in z.infolist()) > 1024 ** 3:
            raise ValueError('Archive expands beyond 1 GiB; inspect before processing')
        files = {n: z.read(n) for n in names if not n.endswith('/')}
    if files.get('mimetype') != b'application/epub+zip':
        raise ValueError('Missing/incorrect EPUB mimetype')
    container = xml(files['META-INF/container.xml'])
    opf = container.find('.//' + q(C, 'rootfile')).get('full-path')
    package = xml(files[opf])
    if 'META-INF/encryption.xml' in files:
        raise ValueError('Encrypted resources present; inspect separately, do not remove encryption')
    return files, opf, package


def resolve(base, href):
    u = urlsplit(href)
    if u.scheme or u.netloc:
        return None, None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(u.path))) if u.path else base
    if path.startswith('../') or path.startswith('/'):
        raise ValueError('Reference leaves EPUB root: ' + href)
    return path, unquote(u.fragment)


def inventory(files, opf, package):
    items = {e.get('id'): e for e in package.findall('./' + q(O, 'manifest') + '/' + q(O, 'item'))}
    spine = []
    for e in package.findall('./' + q(O, 'spine') + '/' + q(O, 'itemref')):
        if e.get('idref') not in items:
            raise ValueError('Spine item absent from manifest: ' + str(e.get('idref')))
        item = items[e.get('idref')]
        path, _ = resolve(opf, item.get('href'))
        spine.append((path, item))
    return items, spine


def image_info(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
        w, h = struct.unpack('>II', data[16:24])
        return 'image/png', 'png', w, h
    if data.startswith(b'\xff\xd8'):
        i = 2
        while i < len(data):
            if data[i] != 255:
                i += 1
                continue
            while i < len(data) and data[i] == 255:
                i += 1
            marker = data[i]
            i += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            size = struct.unpack('>H', data[i:i+2])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack('>HH', data[i+3:i+7])
                return 'image/jpeg', 'jpg', w, h
            if size < 2:
                break
            i += size
    raise ValueError('Cover must be an intact PNG or JPEG (copy original bytes; no silent conversion)')


def cover_item(package):
    items = package.findall('./' + q(O, 'manifest') + '/' + q(O, 'item'))
    for e in items:
        if 'cover-image' in e.get('properties', '').split():
            return e
    old = next((e.get('content') for e in package.findall('.//' + q(O, 'meta')) if e.get('name') == 'cover'), None)
    return next((e for e in items if e.get('id') == old), None)


def visible(root):
    # Empty page markers deliberately add no characters to this stream.
    return ''.join(root.itertext())


def write(files, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Output exists; choose a new path (source files are never overwritten)')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate before publishing the output file; failed validation leaves no partial result.
    validate_files(files)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.epub', delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        with zipfile.ZipFile(temp, 'w') as z:
            z.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in files.items():
                if name != 'mimetype':
                    z.writestr(name, body, compress_type=zipfile.ZIP_DEFLATED)
        os.link(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def validate_files(files):
    roots = {}
    for name, data in files.items():
        if name.endswith(('.xml', '.opf', '.ncx', '.xhtml', '.html')):
            roots[name] = xml(data)
    rootfile = roots['META-INF/container.xml'].find('.//' + q(C, 'rootfile')).get('full-path')
    package = roots[rootfile]
    items, spine = inventory(files, rootfile, package)
    for item in items.values():
        target, _ = resolve(rootfile, item.get('href'))
        if target not in files:
            raise ValueError('Missing manifest resource: ' + str(target))
    for name, root in roots.items():
        ids = [e.get('id') for e in root.iter() if e.get('id')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate IDs in ' + name)
        for element in root.iter():
            for attr, val in element.attrib.items():
                if attr.split('}')[-1] not in ('href', 'src'):
                    continue
                target, fragment = resolve(name, val)
                if target is None:
                    continue
                if target not in files:
                    raise ValueError('Broken resource reference: ' + name + ' -> ' + val)
                if fragment:
                    if target not in roots:
                        continue
                    if not any(e.get('id') == fragment or e.get(q('http://www.w3.org/XML/1998/namespace', 'id')) == fragment for e in roots[target].iter()):
                        raise ValueError('Broken anchor: ' + name + ' -> ' + val)
    return {'markup_files': len(roots), 'manifest_items': len(items), 'spine_items': len(spine)}


def validate(args):
    files, opf, package = load(args.input)
    result = validate_files(files)
    with zipfile.ZipFile(args.input) as z:
        first = z.infolist()[0]
        if first.filename != 'mimetype' or first.compress_type != zipfile.ZIP_STORED or first.extra:
            raise ValueError('mimetype must be first, uncompressed, without extra ZIP fields')
    result['structural_validation'] = 'passed (not a substitute for EPUBCheck)'
    if args.epubcheck:
        completed = subprocess.run(['java', '-jar', args.epubcheck, args.input], capture_output=True, text=True)
        result['epubcheck_exit_code'] = completed.returncode
        result['epubcheck_output'] = completed.stdout + completed.stderr
        report(result)
        if completed.returncode:
            raise ValueError('EPUBCheck did not pass')
        return
    report(result)


def decode(path, encoding=None):
    data = Path(path).read_bytes()
    if encoding:
        return data.decode(encoding), encoding
    options = ['utf-16'] if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for enc in options:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    raise ValueError('Encoding not identified; supply --encoding. Never decode with errors=ignore/replace')


def split_text(text, pattern):
    rx = re.compile(pattern)
    prefix, chapters, current = [], [], None
    for line in text.splitlines():
        m = rx.match(line)
        if m:
            groups = m.groupdict()
            number = groups.get('number') or groups.get('pipe_number')
            current = {'heading': line.strip(), 'number': int(number) if number else None, 'paragraphs': []}
            chapters.append(current)
        elif line.strip():
            (prefix if current is None else current['paragraphs']).append(line.strip())
    return prefix, chapters


SECTION = re.compile(r'^(?:正文|番外|后记|後記|附录|附錄)(?=$|[\s：:·（(]|[一二三四五六七八九十0-9])')
OCR = re.compile(r'(?<=[\u3400-\u9fff])(?:[A-Za-z][0-9]?|[0-9][A-Za-z])(?=[\u3400-\u9fff])|[�□■\ue000-\uf8ff]')


def section_label(heading):
    match = SECTION.match(heading)
    return {'後記': '后记', '附錄': '附录'}.get(match[0], match[0]) if match else None


def normalized(text):
    return re.sub(r'\s+', '', text)


def snapshot(path, args):
    chapters, groups, records = [], [], []
    semantic_hints = collections.Counter()
    result = {'file': path.name}
    if path.suffix.lower() == '.txt':
        content, encoding = decode(path, args.encoding)
        prefix, chapters = split_text(content, args.pattern)
        result.update(encoding=encoding, frontmatter_paragraphs=len(prefix))
        records = [(None, line) for line in prefix]
        for chapter in chapters:
            records.extend((None, line) for line in chapter['paragraphs'])
            groups.append(chapter['heading'])
    else:
        files, opf, package = load(path)
        _, spine = inventory(files, opf, package)
        result['metadata'] = {tag: [e.text or '' for e in package.findall('.//' + q(D, tag))]
                              for tag in ('title', 'creator', 'language')}
        result['spine_documents'] = len(spine)
        result['reading_documents'] = []
        result['page_markers'] = 0
        for name, item in spine:
            if item.get('media-type') != 'application/xhtml+xml':
                continue
            body = xml(files[name]).find(q(H, 'body'))
            if body is None:
                continue
            result['page_markers'] += sum('pagebreak' in e.get(q(EP, 'type'), '').split() for e in body.iter())
            if 'nav' in item.get('properties', '').split() or any(
                set(e.get(q(EP, 'type'), '').split()) & {'cover', 'titlepage', 'toc'}
                for e in body.iter()
            ):
                continue
            result['reading_documents'].append(name)
            labels = {'bodymatter': '正文', 'afterword': '后记', 'appendix': '附录'}
            semantic_hints.update({label for e in body.iter()
                                   for kind in e.get(q(EP, 'type'), '').split()
                                   if (label := labels.get(kind))})
            current = None
            for element in body.iter():
                value = ''.join(element.itertext()).strip() if element.tag in HEADINGS or element.tag == q(H, 'p') else ''
                if element.tag in HEADINGS:
                    match = re.match(args.pattern, value)
                    number = (match.groupdict().get('number') or match.groupdict().get('pipe_number')) if match else None
                    if match or section_label(value):
                        current = {'heading': value, 'number': int(number) if number else None, 'paragraphs': []}
                        chapters.append(current)
                    if value:
                        groups.append(value)
                elif element.tag == q(H, 'p') and value:
                    if current is not None:
                        current['paragraphs'].append(value)
                    records.append((name, value))
    def hint(heading):
        match = re.match(args.pattern, heading)
        title = (match.groupdict().get('title') or match.groupdict().get('pipe_title')) if match else None
        return section_label(heading) or section_label(title or '')
    section_counts = collections.Counter(filter(None, map(hint, groups)))
    numbers = [c['number'] for c in chapters if c['number'] is not None and not section_label(c['heading'])]
    paras = collections.Counter(normalized(p) for _, p in records)
    candidates = collections.Counter(name for name, paragraph in records if OCR.search(paragraph))
    result.update(chapter_headings_recognized=len(chapters), declared_numbers=numbers,
                  section_heading_hints=dict(section_counts), section_markup_documents=dict(semantic_hints),
                  empty_chapter_candidates=[i for i,c in enumerate(chapters,1) if not c['paragraphs']],
                  short_chapter_candidates=[i for i,c in enumerate(chapters,1) if sum(map(len,c['paragraphs'])) < 800],
                  duplicate_declared_numbers=sorted(n for n,c in collections.Counter(numbers).items() if c>1),
                  numbering_jumps=[(a,b) for a,b in zip(numbers,numbers[1:]) if b != a+1],
                  repeated_paragraphs=sum(c-1 for p,c in paras.items() if c>1),
                  repeated_long_paragraphs=sum(c-1 for p,c in paras.items() if c>1 and len(p)>150),
                  replacement_characters=sum(p.count('�') for _,p in records),
                  suspicious_paragraphs=sum(candidates.values()),
                  suspicious_documents={name: count for name,count in candidates.items() if name is not None},
                  scope='Heuristic; section labels and paragraph counts do not establish completeness')
    if args.show_headings:
        result['headings'] = [c['heading'] for c in chapters]
    return result, paras


HEADINGS = {q(H, 'h'+str(i)) for i in range(1, 7)}


def inspect(args):
    result, _ = snapshot(Path(args.input), args)
    report(result)


def compare(args):
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
    report({'baseline': baseline['file'], 'books': [baseline] + [item for item, _ in others],
            'comparisons': comparisons,
            'scope': 'Exact paragraphs ignoring whitespace; edits and paragraph splitting affect counts; no completeness verdict'})


def book_title(package):
    return (package.findtext('.//' + q(D, 'title')) or '').strip()


def output_for(args, title, extension='.epub'):
    if args.output:
        return args.output
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')
    if not safe:
        raise ValueError('No usable book title; specify --output')
    return str(Path(args.input).parent / '整理后' / (safe + extension))


def correct(args):
    files, opf, package = load(args.input)
    _, spine = inventory(files, opf, package)
    eligible = {name for name, item in spine if item.get('media-type') == 'application/xhtml+xml'
                and 'nav' not in item.get('properties', '').split()}
    edits = json.loads(Path(args.edits).read_text(encoding='utf-8'))
    if not isinstance(edits, list) or not edits:
        raise ValueError('Supply a nonempty JSON list of verified corrections')
    roots = {}
    pending = {}
    for index, edit in enumerate(edits, 1):
        if (not isinstance(edit, dict) or any(not isinstance(edit.get(key), str)
                for key in ('document', 'old', 'new')) or not edit['old'] or edit['old'] == edit['new']):
            raise ValueError(f'Invalid correction #{index}')
        path, old, new = (edit[key] for key in ('document', 'old', 'new'))
        if path not in eligible:
            raise ValueError(f'Correction #{index}: document is not in the reading order')
        if path not in roots:
            roots[path] = xml(files[path])
        body = roots[path].find(q(H, 'body'))
        matches = []
        for para in body.iter(q(H, 'p')) if body is not None else ():
            if any(e.tag in {q(H, 'script'), q(H, 'style')} for e in para.iter()):
                continue
            for element in para.iter():
                for field in ('text', 'tail') if element is not para else ('text',):
                    value = getattr(element, field) or ''
                    start = value.find(old)
                    while start != -1:
                        matches.append((element, field, start))
                        start = value.find(old, start + 1)
        if len(matches) != 1:
            raise ValueError(f'Correction #{index}: expected one match in a single text node, found {len(matches)}')
        element, field, start = matches[0]
        pending.setdefault((element, field), []).append((start, start+len(old), new))
    for (element, field), replacements in pending.items():
        value = getattr(element, field)
        limit = len(value)
        for start, end, new in sorted(replacements, reverse=True):
            if end > limit:
                raise ValueError('Corrections overlap')
            value = value[:start] + new + value[end:]
            limit = start
        setattr(element, field, value)
    for path, root in roots.items():
        files[path] = dump(root)
    args.output = output_for(args, book_title(package))
    write(files, args.output)
    report({'output': args.output, 'corrections_applied': len(edits)})


def convert(args):
    suffix = Path(args.input).suffix.lower()
    if suffix == '.doc':
        raise ValueError('Save the legacy Word .doc as .docx in Word or LibreOffice before converting')
    if args.format == 'epub' and suffix == '.epub':
        raise ValueError('Input is already EPUB; use inspect, correct, layout or cover')
    converter = shutil.which('ebook-convert')
    mac = Path('/Applications/calibre.app/Contents/MacOS/ebook-convert')
    if not converter and mac.is_file():
        converter = str(mac)
    if not converter:
        raise ValueError('Install Calibre and make ebook-convert available to convert this format')
    title = args.title
    if args.format == 'azw3' and Path(args.input).suffix.lower() == '.epub':
        _, _, package = load(args.input)
        title = title or book_title(package)
    with tempfile.TemporaryDirectory() as temp:
        generated = Path(temp) / ('book.' + args.format)
        cmd = [converter, str(Path(args.input).resolve()), str(generated)]
        if args.format == 'epub':
            cmd.extend(['--epub-version', '3'])
        for flag, value in (('--title', title), ('--authors', args.author)):
            if value:
                cmd.extend([flag, value])
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode or not generated.is_file() or not generated.stat().st_size:
            raise ValueError('Calibre conversion failed; check whether the input format and file are supported')
        if args.format == 'epub':
            files, opf, package = load(generated)
            if suffix == '.pdf':
                _, spine = inventory(files, opf, package)
                bodies = (
                    xml(files[name]).find(q(H, 'body')) for name, item in spine
                    if item.get('media-type') == 'application/xhtml+xml'
                    and 'nav' not in item.get('properties', '').split()
                )
                if not any(any(ch.isalnum() for ch in visible(body)) for body in bodies if body is not None):
                    raise ValueError('PDF conversion produced no readable text; run OCR on scanned pages before converting')
            title = book_title(package)
            args.output = output_for(args, title or Path(args.input).stem)
            write(files, args.output)
        else:
            output = Path(output_for(args, title or Path(args.input).stem, '.azw3'))
            if output.resolve() == Path(args.input).resolve():
                raise ValueError('Output would replace the source')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('xb') as dest, generated.open('rb') as src:
                shutil.copyfileobj(src, dest)
            args.output = str(output)
    report({'output': args.output, 'format': args.format,
            'next_step': 'Review metadata and TOC' if args.format == 'epub' else 'Use Calibre Send to device for USB delivery'})

def page(title, body):
    return ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="'+H+'" xmlns:epub="'+EP+'" lang="zh-CN" xml:lang="zh-CN"><head><title>'+html.escape(title)+'</title><meta charset="utf-8"/><link rel="stylesheet" href="style.css"/></head><body>'+body+'</body></html>').encode()


def build(args):
    text, encoding = decode(args.input, args.encoding)
    prefix, chapters = split_text(text, args.pattern)
    if not chapters:
        raise ValueError('No chapter headings found; supply a matching --pattern with named number/title groups')
    fixes = json.loads(Path(args.headings_json).read_text()) if args.headings_json else {}
    if not isinstance(fixes, dict) or any(not str(k).isdigit() or not 1<=int(k)<=len(chapters) or not isinstance(v,str) for k,v in fixes.items()):
        raise ValueError('Heading overrides must map valid 1-based chapter positions to exact verified titles')
    files = {'mimetype': b'application/epub+zip', 'OEBPS/style.css': CSS.encode()}
    files['META-INF/container.xml'] = ('<?xml version="1.0"?><container xmlns="'+C+'" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>').encode()
    files['OEBPS/title.xhtml'] = page(args.title, '<section class="title" epub:type="titlepage"><h1>'+html.escape(args.title)+'</h1><p>'+html.escape(args.author)+'</p></section>')
    spine, nav, originals = ['title'], [], {}
    if prefix:
        files['OEBPS/front.xhtml'] = page('原文件前置内容', ''.join('<p>'+html.escape(p)+'</p>' for p in prefix))
        spine.append('front'); nav.append(('front', '原文件前置内容'))
    for i, chapter in enumerate(chapters, 1):
        ident = 'c%04d' % i
        heading = fixes.get(str(i), chapter['heading'])
        files['OEBPS/'+ident+'.xhtml'] = page(heading, '<section epub:type="chapter"><h1>'+html.escape(heading)+'</h1>'+''.join('<p>'+html.escape(p)+'</p>' for p in chapter['paragraphs'])+'</section>')
        originals[ident] = chapter['paragraphs']
        spine.append(ident)
        nav.append((ident, heading if args.full_toc else '第%d节' % i))
    navbody = '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>'+''.join('<li><a href="'+ident+'.xhtml">'+html.escape(label)+'</a></li>' for ident,label in nav)+'</ol></nav>'
    files['OEBPS/nav.xhtml'] = page('目录', navbody)
    uid = 'urn:uuid:'+str(uuid.uuid4())
    ncx = ET.Element(q(N,'ncx'), {'version':'2005-1'})
    head=ET.SubElement(ncx,q(N,'head'));ET.SubElement(head,q(N,'meta'),{'name':'dtb:uid','content':uid})
    ET.SubElement(ET.SubElement(ncx,q(N,'docTitle')),q(N,'text')).text=args.title
    nm=ET.SubElement(ncx,q(N,'navMap'))
    for i,(ident,label) in enumerate(nav,1):
        point=ET.SubElement(nm,q(N,'navPoint'),{'id':'n%d'%i})
        ET.SubElement(ET.SubElement(point,q(N,'navLabel')),q(N,'text')).text=label
        ET.SubElement(point,q(N,'content'),{'src':ident+'.xhtml'})
    files['OEBPS/toc.ncx']=dump(ncx)
    package=ET.Element(q(O,'package'),{'version':'3.0','unique-identifier':'book-id'})
    md=ET.SubElement(package,q(O,'metadata'))
    for tag,val in [('identifier',uid),('title',args.title),('creator',args.author),('language',args.language)]:
        e=ET.SubElement(md,q(D,tag));e.text=val
        if tag=='identifier':e.set('id','book-id')
    ET.SubElement(md,q(O,'meta'),{'property':'dcterms:modified'}).text=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    mf=ET.SubElement(package,q(O,'manifest'))
    for name in files:
        if not name.startswith('OEBPS/'):continue
        short=name.split('/',1)[1];ident=short.rsplit('.',1)[0]
        mime='application/xhtml+xml' if short.endswith('.xhtml') else 'text/css' if short.endswith('.css') else 'application/x-dtbncx+xml'
        e=ET.SubElement(mf,q(O,'item'),{'id':ident,'href':short,'media-type':mime})
        if ident=='nav':e.set('properties','nav')
    sp=ET.SubElement(package,q(O,'spine'),{'toc':'toc'})
    for ident in spine:ET.SubElement(sp,q(O,'itemref'),{'idref':ident})
    files['OEBPS/content.opf']=dump(package)
    for ident,paras in originals.items():
        root=xml(files['OEBPS/'+ident+'.xhtml'])
        assert [''.join(e.itertext()) for e in root.iter(q(H,'p'))]==paras
    if args.cover:
        files=replace_cover(files,'OEBPS/content.opf',package,Path(args.cover).read_bytes())
    args.output = output_for(args, args.title)
    write(files,args.output)
    report({'output':args.output,'encoding':encoding,'chapters':len(chapters),'preserved_frontmatter':bool(prefix),'heading_overrides':len(fixes),'paragraph_roundtrip':'passed','filename_note':'Use the standard book title as filename; keep workflow labels in reports'})


def replace_cover(files, opf, package, data):
    mime, ext, width, height = image_info(data)
    result = dict(files)
    base=posixpath.dirname(opf)
    # A distinct resource avoids rewriting existing references that might use the old cover elsewhere.
    stem='workshop-cover-'+hashlib.sha256(data).hexdigest()[:12]
    image_path=posixpath.join(base,stem+'.'+ext)
    document_path=posixpath.join(base,stem+'.xhtml')
    result[image_path]=data
    result[document_path]=page('封面','<section class="cover" epub:type="cover"><img src="'+stem+'.'+ext+'" alt="封面" width="'+str(width)+'" height="'+str(height)+'"/></section>')
    # page() assumes stylesheet at the package root. Build provides it; imported books may not.
    if posixpath.join(base,'style.css') not in result:
        result[posixpath.join(base,'style.css')]=CSS.encode()
    mf=package.find(q(O,'manifest'));sp=package.find(q(O,'spine'))
    old=cover_item(package)
    old_image_path=resolve(opf,old.get('href'))[0] if old is not None else None
    if old is not None:
        props=[p for p in old.get('properties','').split() if p!='cover-image']
        if props:old.set('properties',' '.join(props))
        else:old.attrib.pop('properties',None)
    if stem+'-image' in {e.get('id') for e in mf}:
        raise ValueError('This exact cover is already present; inspect instead of replacing it again')
    removed_cover_paths = set()
    # Remove previous cover reading entry by metadata guide, semantic markup, or image-only content.
    items={e.get('id'):e for e in mf}
    for ref in list(sp):
        item=items.get(ref.get('idref'))
        if item is None or item.get('media-type')!='application/xhtml+xml':continue
        path,_=resolve(opf,item.get('href'));root=xml(result[path]);body=root.find(q(H,'body'))
        is_cover=any('cover' in e.get(q(EP,'type'),'').split() for e in root.iter())
        linked_images=[resolve(path,e.get('src') or e.get(q('http://www.w3.org/1999/xlink','href')) or e.get('href'))[0] for e in body.iter() if e.tag in (q(H,'img'),q('http://www.w3.org/2000/svg','image')) and (e.get('src') or e.get(q('http://www.w3.org/1999/xlink','href')) or e.get('href'))] if body is not None else []
        image_only=body is not None and not ''.join(body.itertext()).strip() and old_image_path is not None and linked_images==[old_image_path]
        if is_cover or (image_only and ref is list(sp)[0]):
            sp.remove(ref)
            removed_cover_paths.add(path)
    for suffix,href,media,props in [('image',stem+'.'+ext,mime,'cover-image'),('page',stem+'.xhtml','application/xhtml+xml','')]:
        attrs={'id':stem+'-'+suffix,'href':href,'media-type':media}
        if props:attrs['properties']=props
        ET.SubElement(mf,q(O,'item'),attrs)
    if not any(e.get('href')=='style.css' for e in mf):ET.SubElement(mf,q(O,'item'),{'id':stem+'-style','href':'style.css','media-type':'text/css'})
    sp.insert(0,ET.Element(q(O,'itemref'),{'idref':stem+'-page'}))
    md=package.find(q(O,'metadata'))
    for e in list(md):
        if e.tag==q(O,'meta') and e.get('name')=='cover':md.remove(e)
    ET.SubElement(md,q(O,'meta'),{'name':'cover','content':stem+'-image'})
    guide=package.find(q(O,'guide'))
    if guide is not None:
        for e in guide:
            if e.get('type')=='cover':e.set('href',stem+'.xhtml')
    # Make Go To Cover and NCX cover entries target the replacement rather than the retained old resource.
    for item in list(mf):
        if 'nav' not in item.get('properties','').split() and item.get('media-type')!='application/x-dtbncx+xml':
            continue
        path,_=resolve(opf,item.get('href'));root=xml(result[path]);changed=False
        for e in root.iter():
            for attr in ('href','src'):
                if attr not in e.attrib:continue
                target,_=resolve(path,e.get(attr))
                if target in removed_cover_paths:
                    e.set(attr,posixpath.relpath(document_path,posixpath.dirname(path)));changed=True
        if changed:result[path]=dump(root)
    result[opf]=dump(package)
    return result


def cover(args):
    files,opf,package=load(args.input)
    if not package.get('version','').startswith('3'):
        raise ValueError('Convert EPUB 2 to EPUB 3 before editing with this helper')
    if args.from_epub:
        source,sopf,spackage=load(args.from_epub);item=cover_item(spackage)
        if item is None:raise ValueError('Source EPUB has no declared cover')
        path,_=resolve(sopf,item.get('href'));data=source[path]
    else:data=Path(args.image).read_bytes()
    result=replace_cover(files,opf,package,data)
    # Existing content documents are copied unchanged; only package and new cover resources differ.
    navigation={resolve(opf,e.get('href'))[0] for e in package.findall('./'+q(O,'manifest')+'/'+q(O,'item')) if 'nav' in e.get('properties','').split() or e.get('media-type')=='application/x-dtbncx+xml'}
    assert all(result[n]==b for n,b in files.items() if n!=opf and n not in navigation)
    args.output = output_for(args, book_title(package))
    write(result,args.output)
    mime,ext,w,h=image_info(data)
    report({'output':args.output,'cover_pixels':[w,h],'cover_sha256':hashlib.sha256(data).hexdigest(),'original_image_bytes_preserved':True,'original_content_documents_unchanged':True,'cover_navigation_updated':True,'note':'Review edition/cover provenance statements in any existing colophon separately'})


def paginate(args):
    if args.chars<1:raise ValueError('--chars must be positive')
    files,opf,package=load(args.input)
    if not package.get('version','').startswith('3'):raise ValueError('Pagination helper requires EPUB 3')
    items,spine=inventory(files,opf,package)
    nav=next((resolve(opf,e.get('href'))[0] for e in items.values() if 'nav' in e.get('properties','').split()),None)
    if nav is None:raise ValueError('EPUB 3 navigation document missing')
    if any('page-list' in e.get(q(EP,'type'),'').split() for e in xml(files[nav]).iter()):raise ValueError('Existing page-list: retain it or inspect before replacing; no automatic overwrite')
    markers=[];counter=0;processed=[]
    skip=set(args.exclude or [])
    for path,item in spine:
        if path in skip or item.get('media-type')!='application/xhtml+xml' or path==nav:continue
        root=xml(files[path]);body=root.find(q(H,'body'))
        if body is None:continue
        if any('pagebreak' in e.get(q(EP,'type'),'').split() for e in body.iter()):raise ValueError('Existing page markers: '+path)
        if any(set(e.get(q(EP,'type'),'').split()) & {'cover','titlepage','toc'} for e in body.iter()):continue
        original=visible(body);blocks=[e for e in body.iter() if e.tag in (q(H,'p'),q(H,'h1'),q(H,'h2'),q(H,'h3')) and ''.join(e.itertext()).strip()]
        if not blocks:continue
        parents={c:p for p in body.iter() for c in p};count=0;first=True
        for block in blocks:
            if first or count>=args.chars:
                first=False;count=0;counter+=1;ident='workshop-page-%d'%counter
                if any(e.get('id')==ident for e in root.iter()):raise ValueError('Page marker ID already exists')
                marker=ET.Element(q(H,'span'),{'id':ident,q(EP,'type'):'pagebreak','role':'doc-pagebreak','aria-label':str(counter),'title':str(counter)})
                parent=parents[block];parent.insert(list(parent).index(block),marker)
                markers.append((counter,path,ident))
            count+=len(re.sub(r'\s','',''.join(block.itertext())))
        assert visible(body)==original,'Pagination changed visible text'
        files[path]=dump(root);processed.append(path)
    if not markers:raise ValueError('No eligible content to paginate')
    nr=xml(files[nav]);body=nr.find(q(H,'body'));pn=ET.SubElement(body,q(H,'nav'),{q(EP,'type'):'page-list','hidden':'hidden','id':'workshop-page-list'})
    ET.SubElement(pn,q(H,'h2')).text='估算页码';ol=ET.SubElement(pn,q(H,'ol'))
    for number,path,ident in markers:
        ET.SubElement(ET.SubElement(ol,q(H,'li')),q(H,'a'),{'href':posixpath.relpath(path,posixpath.dirname(nav))+'#'+ident}).text=str(number)
    files[nav]=dump(nr)
    ncx_id=package.find(q(O,'spine')).get('toc')
    if ncx_id and ncx_id in items:
        ncx=resolve(opf,items[ncx_id].get('href'))[0];root=xml(files[ncx])
        if root.find(q(N,'pageList')) is not None:raise ValueError('Existing NCX pageList requires inspection')
        # EPUB NCX does not require playOrder. Omit it throughout to avoid conflicting orders.
        for e in root.iter():e.attrib.pop('playOrder',None)
        pl=ET.SubElement(root,q(N,'pageList'));ET.SubElement(ET.SubElement(pl,q(N,'navLabel')),q(N,'text')).text='估算页码'
        for number,path,ident in markers:
            t=ET.SubElement(pl,q(N,'pageTarget'),{'id':'workshop-pt%d'%number,'type':'normal','value':str(number)})
            ET.SubElement(ET.SubElement(t,q(N,'navLabel')),q(N,'text')).text=str(number)
            ET.SubElement(t,q(N,'content'),{'src':posixpath.relpath(path,posixpath.dirname(ncx))+'#'+ident})
        files[ncx]=dump(root)
    args.output = output_for(args, book_title(package))
    write(files,args.output)
    report({'output':args.output,'synthetic_pages':counter,'target_nonwhite_characters_per_page':args.chars,'documents_paginated':len(processed),'excluded':sorted(skip),'text_roundtrip':'passed','meaning':'Estimated fixed reference pages, not paper pages or screen turns','device_validation':'Not tested; Send to Kindle can alter/discard page information'})


def kindle(args):
    if args.method == 'usb':
        if args.pages:
            raise ValueError('For USB, generate estimated pages in Calibre when sending to the device')
        args.format, args.title, args.author = 'azw3', None, None
        return convert(args)
    files, opf, package = load(args.input)
    items, _ = inventory(files, opf, package)
    has_pages = any(
        any('page-list' in e.get(q(EP, 'type'), '').split() or e.tag == q(N, 'pageList')
            for e in xml(files[resolve(opf, item.get('href'))[0]]).iter())
        for item in items.values()
        if 'nav' in item.get('properties', '').split() or item.get('media-type') == 'application/x-dtbncx+xml'
    )
    if args.pages and not has_pages:
        return paginate(args)
    args.output = output_for(args, book_title(package))
    write(files, args.output)
    report({'output': args.output, 'existing_page_list_preserved': has_pages,
            'next_step': 'Send EPUB via Send to Kindle; page display depends on Amazon conversion and the device'})


def layout_style(args):
    style = (Path(__file__).resolve().parent.parent / 'assets' / 'cn-novel.css').read_text(encoding='utf-8')
    custom = False
    for attr, lower, upper, original, template in (
        ('margin', 0, 25, 'margin: 0 5%;', 'margin: 0 %g%%;'),
        ('line_height', 1, 3, 'line-height: 1.8;', 'line-height: %g;'),
        ('indent', 0, 6, 'text-indent: 2em;', 'text-indent: %gem;'),
        ('paragraph_gap', 0, 3, 'margin: .5em 0;', 'margin: %gem 0;'),
    ):
        value = getattr(args, attr)
        if value is None:
            continue
        if not lower <= value <= upper:
            raise ValueError(f'{attr} must be between {lower} and {upper}')
        style = style.replace(original, template % value, 1)
        custom = True
    style = style.encode('utf-8')
    stem = 'workshop-cn-novel'
    if custom:
        stem += '-' + hashlib.sha256(style).hexdigest()[:12]
    return stem, style


def layout(args):
    files, opf, package = load(args.input)
    if not package.get('version', '').startswith('3'):
        raise ValueError('Layout preset requires EPUB 3')
    if any(
        meta.get('property') == 'rendition:layout'
        and (meta.text or '').strip() == 'pre-paginated'
        for meta in package.iter(q(O, 'meta'))
    ) or any(
        'rendition:layout-pre-paginated' in ref.get('properties', '').split()
        for ref in package.iter(q(O, 'itemref'))
    ):
        raise ValueError('Layout preset requires a reflowable EPUB')
    _, spine = inventory(files, opf, package)
    eligible = {
        path for path, item in spine
        if path and item.get('media-type') == 'application/xhtml+xml'
        and 'nav' not in item.get('properties', '').split()
    }
    style_id, style = layout_style(args)
    style_path = posixpath.join(posixpath.dirname(opf), style_id + '.css')
    manifest = package.find(q(O, 'manifest'))
    registered = [
        item for item in manifest if resolve(opf, item.get('href'))[0] == style_path
    ]
    if style_path in files or registered:
        if (files.get(style_path) != style or len(registered) != 1
                or registered[0].get('media-type') != 'text/css'):
            raise ValueError('Existing layout stylesheet conflicts with the preset')
    else:
        while any(item.get('id') == style_id for item in manifest):
            style_id += '-style'
        ET.SubElement(manifest, q(O, 'item'), {
            'id': style_id, 'href': posixpath.basename(style_path), 'media-type': 'text/css',
        })
    for path in dict.fromkeys(args.document):
        if path not in eligible:
            raise ValueError('Select a reading document from the EPUB spine: ' + path)
        root = xml(files[path])
        head, body = root.find(q(H, 'head')), root.find(q(H, 'body'))
        if head is None or body is None or not visible(body).strip():
            raise ValueError('Selected document has no readable body: ' + path)
        if any(
            set(e.get(q(EP, 'type'), '').split()) & {'cover', 'titlepage', 'toc'}
            for e in body.iter()
        ):
            raise ValueError('Select a prose chapter instead of front matter: ' + path)
        original_text = visible(body)
        classes = body.get('class', '').split()
        if 'workshop-cn-novel' not in classes:
            body.set('class', ' '.join(classes + ['workshop-cn-novel']))
        workshop_styles = {
            resolve(opf, item.get('href'))[0] for item in manifest
            if item.get('media-type') == 'text/css'
            and re.fullmatch(r'workshop-cn-novel(?:-[0-9a-f]{12})?\.css', item.get('href', ''))
            and item.get('id', '').startswith('workshop-cn-novel')
        }
        linked = False
        for link in head.findall(q(H, 'link')):
            if 'stylesheet' not in link.get('rel', '').split():
                continue
            target = resolve(path, link.get('href', ''))[0]
            if target == style_path and not linked:
                linked = True
            elif target in workshop_styles:
                head.remove(link)
        if not linked:
            href = quote(posixpath.relpath(style_path, posixpath.dirname(path)))
            ET.SubElement(head, q(H, 'link'), {'rel': 'stylesheet', 'type': 'text/css', 'href': href})
        if visible(body) != original_text:
            raise ValueError('Layout changed body text: ' + path)
        files[path] = dump(root)
    files[style_path] = style
    files[opf] = dump(package)
    args.output = output_for(args, book_title(package))
    write(files, args.output)
    report({'output': args.output, 'preset': args.preset, 'documents': list(dict.fromkeys(args.document))})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    a=sub.add_parser('inspect');a.add_argument('input');a.add_argument('--encoding');a.add_argument('--pattern',default=PATTERN);a.add_argument('--show-headings',action='store_true');a.set_defaults(run=inspect)
    a=sub.add_parser('compare', help='Compare two or more TXT/EPUB editions without quoting text')
    a.add_argument('inputs', nargs='+')
    a.add_argument('--encoding')
    a.add_argument('--pattern', default=PATTERN)
    a.set_defaults(run=compare, show_headings=False)
    a=sub.add_parser('correct', help='Apply verified exact corrections from JSON')
    a.add_argument('input'); a.add_argument('--edits', required=True); a.add_argument('--output')
    a.set_defaults(run=correct)
    a=sub.add_parser('convert', help='Convert with Calibre')
    a.add_argument('input'); a.add_argument('--output'); a.add_argument('--title'); a.add_argument('--author')
    a.add_argument('--format', choices=['epub', 'azw3'], default='epub')
    a.set_defaults(run=convert)
    a=sub.add_parser('build');a.add_argument('input');a.add_argument('--output');a.add_argument('--title',required=True);a.add_argument('--author',required=True);a.add_argument('--language',default='zh-CN');a.add_argument('--encoding');a.add_argument('--pattern',default=PATTERN);a.add_argument('--headings-json');a.add_argument('--cover');a.add_argument('--full-toc',action='store_true');a.set_defaults(run=build)
    a=sub.add_parser('cover');a.add_argument('input');a.add_argument('--output');g=a.add_mutually_exclusive_group(required=True);g.add_argument('--image');g.add_argument('--from-epub');a.set_defaults(run=cover)
    a=sub.add_parser('paginate');a.add_argument('input');a.add_argument('--output');a.add_argument('--chars',type=int,default=500,help='Approximate non-whitespace characters per synthetic page; 500 is a Chinese reading convention, not an industry standard');a.add_argument('--exclude',action='append',help='Exact archive path of a document to leave unnumbered, e.g. OEBPS/notes.xhtml');a.set_defaults(run=paginate)
    a=sub.add_parser('kindle', help='Prepare an EPUB for email or convert to AZW3 for USB')
    a.add_argument('input'); a.add_argument('--output')
    a.add_argument('--method', choices=['email', 'usb'], required=True)
    a.add_argument('--pages', action='store_true', help='Add estimated pages to EPUB for email, if absent')
    a.add_argument('--chars', type=int, default=500); a.add_argument('--exclude', action='append')
    a.set_defaults(run=kindle)
    layout_parser = sub.add_parser('layout', help='Apply a reading preset to selected EPUB 3 chapters')
    layout_parser.add_argument('input')
    layout_parser.add_argument('--output')
    layout_parser.add_argument('--preset', choices=['cn-novel'], default='cn-novel')
    layout_parser.add_argument('--document', action='append', required=True,
                               help='Exact chapter path inside the EPUB. Repeat for each chapter.')
    layout_parser.add_argument('--margin', type=float, help='Left/right margin in percent')
    layout_parser.add_argument('--line-height', type=float, help='Line height multiplier')
    layout_parser.add_argument('--indent', type=float, help='First-line indent in em')
    layout_parser.add_argument('--paragraph-gap', type=float, help='Vertical paragraph margin in em')
    layout_parser.set_defaults(run=layout)
    a=sub.add_parser('validate');a.add_argument('input');a.add_argument('--epubcheck',help='Path to an externally installed official EPUBCheck JAR');a.set_defaults(run=validate)
    import importlib.util
    spec = importlib.util.spec_from_file_location('workshop_proofread', Path(__file__).with_name('proofread.py'))
    proofread = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proofread)
    proofread.register(sub, argparse.Namespace(**globals()))
    args=parser.parse_args()
    if args.command == 'compare' and len(args.inputs) < 2:
        parser.error('compare requires at least two files')
    try:args.run(args)
    except (ValueError,KeyError,ET.ParseError,zipfile.BadZipFile,OSError,UnicodeError,struct.error,re.error) as e:
        print('Error: '+str(e),file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
