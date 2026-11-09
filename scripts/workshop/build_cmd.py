"""转换：把 TXT 或其他格式整理成 EPUB，并填写书名作者。"""
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import epub, names, text
from . import convert as convert_module
from .convert import epub_output
from .convert import htmltext
from .convert.book import Book, Document, Image, TocEntry


def build(args):
    """把 TXT 整理成 EPUB，保留前置内容、番外与后记。"""
    content, encoding = text.decode(args.input, args.encoding)
    prefix, chapters = text.split_text(content, args.pattern)
    if not chapters:
        raise ValueError('No chapter headings found; supply a matching --pattern with named number/title groups')
    fixes = json.loads(Path(args.headings_json).read_text(encoding='utf-8')) if args.headings_json else {}
    if not isinstance(fixes, dict) or any(not str(k).isdigit() or not 1 <= int(k) <= len(chapters) or not isinstance(v, str)
                                         for k, v in fixes.items()):
        raise ValueError('Heading overrides must map valid 1-based chapter positions to exact verified titles')

    # 和别的读入端一样，先摊成中间表示，再由唯一的 EPUB 写出端落地。
    # 以前这里手写了一套 PKG 组装，和 convert 的写出端各改各的，
    # 封面的 manifest 条目、dcterms:modified、样式表这些都两处维护。
    book = Book(title=args.title, author=args.author, language=args.language)
    book.identifier = 'urn:uuid:' + str(uuid.uuid4())
    book.add_document(Document('title.xhtml', content=htmltext.title_page(book),
                               properties=['titlepage']))
    book.toc.append(TocEntry(args.title, href='title.xhtml'))
    if prefix:
        body = ''.join(htmltext.paragraph_html(paragraph) for paragraph in prefix)
        book.add_document(Document('front.xhtml', content=htmltext.document('原文件前置内容', 1, body)))
        book.toc.append(TocEntry('原文件前置内容', href='front.xhtml'))
    originals = {}
    for index, chapter in enumerate(chapters, 1):
        heading = fixes.get(str(index), chapter['heading'])
        body = ''.join(htmltext.paragraph_html(paragraph) for paragraph in chapter['paragraphs'])
        href = 'c%04d.xhtml' % index
        book.add_document(Document(href, content=htmltext.document(heading, 1, body)))
        book.toc.append(TocEntry(heading if args.full_toc else '第%d节' % index, href=href))
        originals[href] = (heading, chapter['paragraphs'])
    if args.cover:
        data = Path(args.cover).read_bytes()
        mime, extension, *_ = names.image_info(data)
        book.cover = 'cover.' + extension
        book.add_document(Document('cover.xhtml', content=htmltext.document(
            '封面', 1, '<img src="%s" alt="封面"/>' % book.cover)))
        book.add_image(Image(book.cover, data, mime))
        book.toc.insert(1, TocEntry('封面', href='cover.xhtml'))

    args.output = names.output_for(args, args.title)
    epub_output.write(book, args.output)
    # 写出的 EPUB 必须能原样读回同样的正文，否则排版就丢了东西。
    written, written_opf, written_package = epub.load(args.output)
    documents = dict(epub.reading_documents(epub.inventory(written, written_opf, written_package)[1]))
    for href, (heading, paragraphs) in originals.items():
        body = epub.xml(written[epub.resolve(written_opf, href)[0]]).find(epub.q(epub.H, 'body'))
        got = [''.join(element.itertext()).strip() for element in body.iter(epub.q(epub.H, 'p'))]
        if got != [paragraph.strip() for paragraph in paragraphs]:
            raise ValueError('Written EPUB does not preserve the chapter text of ' + href)
    epub.report({'output': args.output, 'encoding': encoding, 'chapters': len(chapters),
                 'preserved_frontmatter': bool(prefix), 'heading_overrides': len(fixes),
                 'paragraph_roundtrip': 'passed',
                 'filename_note': 'Use the standard book title as filename; keep workflow labels in reports'})


def convert(args):
    """Convert between ebook formats.

    The native pipeline handles EPUB, DOCX, ODT and HTML input, and EPUB,
    MOBI and AZW3 output, without needing Calibre installed. Calibre is used
    only when the requested combination falls outside native support.
    """
    suffix = Path(args.input).suffix.lower()
    if suffix == '.doc':
        raise ValueError('Save the legacy Word .doc as .docx in Word or LibreOffice before converting')

    # 没给 --output 时，目标扩展名由 --format 决定，先用它判断能否原生写出。
    planned = args.output or ('output' + _target_suffix(args))

    if convert_module.supported_input(args.input) and convert_module.supported_output(planned):
        try:
            result = _convert_native(args, args.output)
        except convert_module.ConversionError:
            # 输出已存在这类问题不该交给 Calibre 重试一遍。
            raise
        except Exception as error:
            # 原生读取不了这个文件（比如内容损坏或结构特殊），
            # 有 Calibre 就交给它处理。这里放宽到所有异常：解析坏文件时
            # 可能冒出结构错误、索引越界等各种意料之外的异常。
            if not _calibre_available():
                raise ValueError('Native conversion failed: %s. Install Calibre to convert files '
                                 'the native pipeline cannot read.' % error) from error
            result = _convert_with_calibre(args, planned)
            result['native_error'] = str(error)
        epub.report(result)
        return

    if not _calibre_available():
        raise ValueError(
            'Cannot convert %s to %s: the native pipeline reads %s and writes %s, and Calibre '
            'is not installed for other combinations.'
            % (suffix or 'this file', Path(planned).suffix.lower() or 'this file',
               ', '.join(sorted(convert_module.READERS)),
               ', '.join(sorted(convert_module.WRITERS))))

    result = _convert_with_calibre(args, planned)
    epub.report(result)


# --format 取值到输出扩展名的对应。
FORMAT_SUFFIXES = {'epub': '.epub', 'mobi': '.mobi', 'azw3': '.azw3'}


def _target_suffix(args):
    """根据 --format 决定输出扩展名。"""
    return FORMAT_SUFFIXES.get(args.format, '.epub')


def _find_calibre():
    """找出 ebook-convert 的路径；没有就返回 None。

    PATH 上有就用它（Windows 的 Calibre 安装包会加进 PATH）。
    macOS 的 .app 包不在 PATH 里，单独找一下。
    """
    found = shutil.which('ebook-convert')
    if found:
        return found
    for candidate in ('/Applications/calibre.app/Contents/MacOS/ebook-convert',
                      r'C:\Program Files\Calibre2\ebook-convert.exe',
                      r'C:\Program Files (x86)\Calibre2\ebook-convert.exe'):
        if Path(candidate).is_file():
            return candidate
    return None


def _calibre_available():
    return _find_calibre() is not None


def _convert_native(args, output):
    """用原生管线转换。

    没指定 --output 时，读完书才知道书名，默认文件名按书名取，
    而不是按输入文件的文件名取。
    """
    book = convert_module.read(args.input)
    if args.title:
        book.title = args.title
    if args.author:
        book.author = args.author
    if output is None:
        output = names.output_for(args, book.title or Path(args.input).stem, _target_suffix(args))
    result = convert_module.write(book, output)
    result['engine'] = 'native'
    result['next_step'] = ('Review metadata and TOC' if Path(output).suffix.lower() == '.epub'
                           else 'Send to Kindle by USB or e-mail')
    return result


def _convert_with_calibre(args, planned):
    """回退到 Calibre 转换。"""
    suffix = Path(args.input).suffix.lower()
    converter = _find_calibre()
    fmt = Path(planned).suffix.lower().lstrip('.') or 'epub'
    title = args.title
    if fmt == 'azw3' and suffix == '.epub' and not title:
        # 书名只是为了写进元数据。读不出书名（EPUB 本身有问题）时
        # 不能连累转换，交给 Calibre 继续。
        try:
            _, _, package = epub.load(args.input)
            title = epub.book_title(package)
        except Exception:
            title = None
    with tempfile.TemporaryDirectory() as temp:
        generated = Path(temp) / ('book.' + fmt)
        command = [converter, str(Path(args.input).resolve()), str(generated)]
        if fmt == 'epub':
            command.extend(['--epub-version', '3'])
        for flag, value in (('--title', title), ('--authors', args.author)):
            if value:
                command.extend([flag, value])
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode or not generated.is_file() or not generated.stat().st_size:
            raise ValueError('Calibre conversion failed; check whether the input format and file are supported')
        if fmt == 'epub':
            files, opf, package = epub.load(generated)
            if suffix == '.pdf':
                _, spine = epub.inventory(files, opf, package)
                bodies = (epub.xml(files[name]).find(epub.q(epub.H, 'body')) for name, item in spine
                          if item.get('media-type') == 'application/xhtml+xml'
                          and 'nav' not in item.get('properties', '').split())
                if not any(any(ch.isalnum() for ch in epub.visible(body)) for body in bodies if body is not None):
                    raise ValueError('PDF conversion produced no readable text; run OCR on scanned pages before converting')
            title = epub.book_title(package)
            output = names.output_for(args, title or Path(args.input).stem)
            epub.write(files, output)
        else:
            output = Path(names.output_for(args, title or Path(args.input).stem, '.' + fmt))
            if output.resolve() == Path(args.input).resolve():
                raise ValueError('Output would replace the source')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('xb') as destination, generated.open('rb') as source:
                shutil.copyfileobj(source, destination)
            output = str(output)
    return {'output': output, 'format': fmt, 'engine': 'calibre',
            'next_step': 'Review metadata and TOC' if fmt == 'epub'
            else 'Use Calibre Send to device for USB delivery'}


def register(sub):
    parser = sub.add_parser('build')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--title', required=True)
    parser.add_argument('--author', required=True)
    parser.add_argument('--language', default='zh-CN')
    parser.add_argument('--encoding')
    parser.add_argument('--pattern', default=text.PATTERN)
    parser.add_argument('--headings-json')
    parser.add_argument('--cover')
    parser.add_argument('--full-toc', action='store_true')
    parser.set_defaults(run=build)
    parser = sub.add_parser('convert', help='Convert between ebook formats (native, no Calibre needed)')
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--title')
    parser.add_argument('--author')
    parser.add_argument('--format', choices=['epub', 'azw3', 'mobi'], default='epub',
                        help='Target format; an explicit --output path takes precedence')
    parser.set_defaults(run=convert)
