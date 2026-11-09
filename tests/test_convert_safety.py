"""原生转换的边界与安全：命令行约定、原子写出、压缩包与路径防护。

这些行为是「不依赖 Calibre」这条路上的护栏：错误的输入不能拖死进程，
失败的写出不能在磁盘上留下半个文件，包内路径不能越出内容目录。
"""

import argparse
import contextlib
import inspect
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from workshop import build_cmd, epub, names, pages_cmd
from workshop.convert import archive, paths, xmlsafe
from workshop.convert import docx_input, epub_output, mobi_writer, odt_input
from workshop.convert.book import Book, Document, Image, TocEntry


def quiet(call, *args, **kwargs):
    """执行命令，吞掉它打印的 JSON 报告。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return call(*args, **kwargs)


def make_docx(path, body, extra=None):
    """写一个最小的 DOCX。"""
    document = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + body + '</w:body></w:document>')
    core = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>测试书名</dc:title><dc:creator>测试作者</dc:creator>'
            '</cp:coreProperties>')
    with zipfile.ZipFile(path, 'w') as handle:
        handle.writestr('word/document.xml', document)
        handle.writestr('docProps/core.xml', core)
        for name, data in (extra or {}).items():
            handle.writestr(name, data)
    return path


def make_odt(path, content, extra=None):
    """写一个最小的 ODT。"""
    with zipfile.ZipFile(path, 'w') as handle:
        handle.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        handle.writestr('content.xml', content)
        for name, data in (extra or {}).items():
            handle.writestr(name, data)
    return path


ODT_CONTENT = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<office:document-content'
               ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
               ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
               ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
               ' xmlns:xlink="http://www.w3.org/1999/xlink">'
               '<office:body><office:text>'
               '<text:h text:outline-level="1">第一章</text:h>'
               '<text:p>正文。</text:p>'
               '</office:text></office:body></office:document-content>')


class PathSafetyTests(unittest.TestCase):
    """包内路径清洗。"""

    def test_traversal_is_refused(self):
        self.assertIsNone(paths.safe_path('../secret'))
        self.assertIsNone(paths.safe_path('a/../../secret'))

    def test_inner_traversal_is_normalised(self):
        self.assertEqual(paths.safe_path('a/../b.txt'), 'b.txt')

    def test_absolute_and_drive_paths_are_refused(self):
        self.assertIsNone(paths.safe_path('/etc/passwd'))
        self.assertIsNone(paths.safe_path('C:/Windows/win.ini'))

    def test_backslashes_become_separators(self):
        self.assertEqual(paths.safe_path('a\\b.txt'), 'a/b.txt')

    def test_relative_to_strips_the_base(self):
        self.assertEqual(paths.relative_to('OEBPS', 'OEBPS/Text/a.xhtml'), 'Text/a.xhtml')

    def test_relative_to_keeps_paths_outside_the_base(self):
        """不在内容目录下的文件原样保留，图片引用才对得上。"""
        self.assertEqual(paths.relative_to('OEBPS', 'META-INF/container.xml'),
                         'META-INF/container.xml')

    def test_relative_to_rejects_escape(self):
        self.assertIsNone(paths.relative_to('OEBPS', '../outside.png'))


class XmlSafetyTests(unittest.TestCase):
    """XML 实体炸弹防护。"""

    def test_plain_doctype_is_accepted(self):
        data = b'<?xml version="1.0"?><!DOCTYPE html><html><body><p>x</p></body></html>'
        self.assertIsNotNone(xmlsafe.parse(data))

    def test_internal_subset_is_refused(self):
        data = (b'<?xml version="1.0"?>'
                b'<!DOCTYPE lol [<!ENTITY lol "lol">]>'
                b'<root>&lol;</root>')
        with self.assertRaises(xmlsafe.XmlError):
            xmlsafe.parse(data)

    def test_entity_declaration_without_subset_is_refused(self):
        with self.assertRaises(xmlsafe.XmlError):
            xmlsafe.parse(b'<!ENTITY x "y"><root/>')

    def test_broken_xml_raises_a_readable_error(self):
        with self.assertRaises(xmlsafe.XmlError):
            xmlsafe.parse(b'<root><unclosed></root>')

    def test_docx_with_entities_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = make_docx(
                Path(temp) / 'a.docx',
                '<w:p><w:r><w:t>正文</w:t></w:r></w:p>',
                extra={'word/document.xml':
                       '<?xml version="1.0"?>'
                       '<!DOCTYPE w:document [<!ENTITY a "b">]>'
                       '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                       '<w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>'})
            with self.assertRaises(ValueError):
                docx_input.read(path)


class ArchiveSafetyTests(unittest.TestCase):
    """压缩包结构防护。"""

    def test_traversal_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.docx'
            with zipfile.ZipFile(path, 'w') as handle:
                handle.writestr('../evil.xml', '<x/>')
            with self.assertRaises(archive.ArchiveError):
                archive.Archive(path)

    def test_absolute_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.docx'
            with zipfile.ZipFile(path, 'w') as handle:
                handle.writestr('/etc/evil.xml', '<x/>')
            with self.assertRaises(archive.ArchiveError):
                archive.Archive(path)

    def test_duplicate_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.docx'
            with zipfile.ZipFile(path, 'w') as handle:
                handle.writestr('word/document.xml', '<a/>')
                handle.writestr('word/document.xml', '<b/>')
            with self.assertRaises(archive.ArchiveError):
                archive.Archive(path)

    def test_oversized_entry_is_refused(self):
        original = archive.MAX_ENTRY
        archive.MAX_ENTRY = 16
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'x.docx'
                with zipfile.ZipFile(path, 'w') as handle:
                    handle.writestr('word/document.xml', 'x' * 64)
                with self.assertRaises(archive.ArchiveError):
                    archive.Archive(path)
        finally:
            archive.MAX_ENTRY = original

    def test_not_a_zip_raises_the_documented_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.docx'
            path.write_bytes(b'not a zip at all')
            with self.assertRaises(archive.ArchiveError):
                archive.Archive(path)


class OdtImageSafetyTests(unittest.TestCase):
    """ODT 里越界的图片路径不能被写进 EPUB。"""

    def test_archive_entry_that_escapes_is_refused(self):
        """包里带 '..' 路径的条目直接拒绝整个文件。"""
        picture = ('<draw:frame><draw:image xlink:href="../outside.png"/></draw:frame>')
        content = ODT_CONTENT.replace('<text:p>正文。</text:p>',
                                      '<text:p>正文。</text:p>' + picture)
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content,
                            extra={'../outside.png': b'\x89PNG\r\n\x1a\n'})
            with self.assertRaises(ValueError):
                odt_input.read(path)

    def test_image_href_must_name_a_real_picture(self):
        """正文引用了包里没有的图片时，不能原样写进结果。"""
        picture = '<draw:frame><draw:image xlink:href="../outside.png"/></draw:frame>'
        content = ODT_CONTENT.replace('<text:p>正文。</text:p>',
                                      '<text:p>正文。' + picture + '</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content)
            reading = odt_input.read(path)
        self.assertNotIn('outside.png', reading.documents[0].content)

    def test_real_picture_uses_the_cleaned_path(self):
        picture = '<draw:frame><draw:image xlink:href="Pictures/p.png"/></draw:frame>'
        content = ODT_CONTENT.replace('<text:p>正文。</text:p>',
                                      '<text:p>正文。' + picture + '</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content,
                            extra={'Pictures/p.png': b'\x89PNG\r\n\x1a\n'})
            reading = odt_input.read(path)
        self.assertIn('<img src="Pictures/p.png"', reading.documents[0].content)
        self.assertEqual(list(reading.images), ['Pictures/p.png'])

    def test_link_that_escapes_is_dropped(self):
        link = '<text:a xlink:href="../outside.html">外链</text:a>'
        content = ODT_CONTENT.replace('<text:p>正文。</text:p>',
                                      '<text:p>' + link + '</text:p>')
        with tempfile.TemporaryDirectory() as temp:
            path = make_odt(Path(temp) / 'a.odt', content)
            reading = odt_input.read(path)
        self.assertNotIn('../outside.html', reading.documents[0].content)


class AtomicWriteTests(unittest.TestCase):
    """写出失败时不能在目标路径留下半个文件。"""

    def sample_book(self):
        book = Book()
        book.title = '书名'
        book.author = '作者'
        book.add_document(Document('c1.xhtml', content='<html><body><p>正文</p></body></html>'))
        book.toc.append(TocEntry('第一章', href='c1.xhtml'))
        return book

    def test_failed_mobi_write_leaves_nothing(self):
        from workshop import convert as convert_module
        original = mobi_writer.build_mobi
        mobi_writer.build_mobi = lambda book, kf8=False: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'out.mobi'
                with self.assertRaises(RuntimeError):
                    convert_module.write(self.sample_book(), target)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(temp).iterdir()), [])
        finally:
            mobi_writer.build_mobi = original

    def test_existing_output_is_never_overwritten(self):
        from workshop import convert as convert_module
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'out.mobi'
            target.write_bytes(b'keep me')
            with self.assertRaises(convert_module.ConversionError):
                convert_module.write(self.sample_book(), target)
            self.assertEqual(target.read_bytes(), b'keep me')

    def test_written_file_is_readable_by_others(self):
        from workshop import convert as convert_module
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'out.mobi'
            convert_module.write(self.sample_book(), target)
            mode = os.stat(str(target)).st_mode & 0o777
        self.assertTrue(mode & 0o044, 'output should not be owner-only: %o' % mode)


class OutputNameTests(unittest.TestCase):
    """默认输出文件名要在各平台上都能建出来。"""

    def test_windows_reserved_names_are_escaped(self):
        """CON、PRN 这类名字在 Windows 上建不出文件。"""
        for title in ('CON', 'con', 'PRN', 'AUX', 'NUL', 'COM1', 'LPT9'):
            name = names.safe_filename(title, '.epub')
            self.assertFalse(name.startswith(title + '.'), name)
            self.assertTrue(name.startswith('_'), name)

    def test_reserved_name_before_the_extension_is_escaped(self):
        self.assertTrue(names.safe_filename('CON.txt', '').startswith('_'))

    def test_forbidden_characters_are_replaced(self):
        self.assertEqual(names.safe_filename('a/b:c*d', ''), 'a_b_c_d')

    def test_normal_titles_are_left_alone(self):
        self.assertEqual(names.safe_filename('杀死一只知更鸟', '.epub'), '杀死一只知更鸟.epub')

    def test_extension_is_not_appended_twice(self):
        """书名自带扩展名时不要重复添加，否则成了「活着.epub.epub」。"""
        self.assertEqual(names.safe_filename('活着.epub', '.epub'), '活着.epub')
        self.assertEqual(names.safe_filename('活着.EPUB', '.epub'), '活着.epub')
        # 别的扩展名不是同一个，仍然要补上。
        self.assertEqual(names.safe_filename('活着.mobi', '.epub'), '活着.mobi.epub')

    def test_long_titles_are_shortened_by_characters(self):
        name = names.safe_filename('长' * 400, '.epub')
        self.assertLessEqual(len(name), names.MAX_NAME_LENGTH + len('.epub'))

    def test_blank_title_yields_nothing(self):
        self.assertEqual(names.safe_filename('   ', ''), '')


class EpubLoadTests(unittest.TestCase):
    """EPUB 结构损坏时给出可读的错误，而不是堆栈。"""

    def make_epub(self, path, files):
        with zipfile.ZipFile(path, 'w') as handle:
            handle.writestr('mimetype', b'application/epub+zip')
            for name, data in files.items():
                handle.writestr(name, data)

    def test_missing_container_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.epub'
            self.make_epub(path, {})
            with self.assertRaises(ValueError) as caught:
                epub.load(path)
        self.assertIn('container.xml', str(caught.exception))

    def test_container_without_rootfile_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.epub'
            self.make_epub(path, {'META-INF/container.xml':
                                  '<?xml version="1.0"?>'
                                  '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                                  ' version="1.0"><rootfiles/></container>'})
            with self.assertRaises(ValueError) as caught:
                epub.load(path)
        self.assertIn('package document', str(caught.exception))

    def test_missing_package_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.epub'
            self.make_epub(path, {'META-INF/container.xml':
                                  '<?xml version="1.0"?>'
                                  '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                                  ' version="1.0"><rootfiles>'
                                  '<rootfile full-path="OEBPS/content.opf"'
                                  ' media-type="application/oebps-package+xml"/>'
                                  '</rootfiles></container>'})
            with self.assertRaises(ValueError) as caught:
                epub.load(path)
        self.assertIn('OEBPS/content.opf', str(caught.exception))


class ConvertCommandTests(unittest.TestCase):
    """convert 子命令的格式与文件名约定。"""

    def make_args(self, **overrides):
        values = {'input': '', 'output': None, 'title': None, 'author': None, 'format': 'epub'}
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_format_mobi_really_produces_mobi(self):
        with tempfile.TemporaryDirectory() as temp:
            source = make_docx(Path(temp) / 'a.docx',
                               '<w:p><w:r><w:t>正文。</w:t></w:r></w:p>')
            target = Path(temp) / 'out' / 'book.mobi'
            args = self.make_args(input=str(source), output=str(target), format='mobi')
            quiet(build_cmd.convert, args)
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes()[60:68], b'BOOKMOBI')

    def test_default_output_uses_the_book_title(self):
        with tempfile.TemporaryDirectory() as temp:
            source = make_docx(Path(temp) / 'ugly-file-name.docx',
                               '<w:p><w:r><w:t>正文。</w:t></w:r></w:p>')
            args = self.make_args(input=str(source), format='mobi')
            quiet(build_cmd.convert, args)
            names = [path.name for path in (Path(temp) / '整理后').iterdir()]
        self.assertEqual(names, ['测试书名.mobi'])

    def test_unsupported_combination_names_the_formats(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.docx'
            source.write_bytes(b'x')
            args = self.make_args(input=str(source), output=str(Path(temp) / 'a.pdf'))
            if build_cmd._calibre_available():
                self.skipTest('Calibre is installed, so the native limits are not reached')
            with self.assertRaises(ValueError) as caught:
                build_cmd.convert(args)
        message = str(caught.exception)
        self.assertIn('.pdf', message)
        self.assertIn('Calibre', message)

    def test_legacy_doc_is_refused_with_advice(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.doc'
            source.write_bytes(b'x')
            args = self.make_args(input=str(source))
            with self.assertRaises(ValueError) as caught:
                build_cmd.convert(args)
        self.assertIn('.docx', str(caught.exception))


class KindleUsbTests(unittest.TestCase):
    """kindle --method usb 一定产出 .azw3。"""

    def test_epub_output_suffix_is_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source = make_docx(Path(temp) / 'a.docx',
                               '<w:p><w:r><w:t>正文。</w:t></w:r></w:p>')
            target = Path(temp) / 'book.epub'
            args = argparse.Namespace(input=str(source), output=str(target), method='usb',
                                      pages=False, format='epub', title=None, author=None,
                                      chars=500, exclude=None)
            quiet(pages_cmd.kindle, args)
            self.assertFalse(target.exists())
            self.assertTrue(target.with_suffix('.azw3').is_file())


class EpubOutputImageTests(unittest.TestCase):
    """EPUB 写出时图片路径与正文引用保持一致。"""

    def test_ncx_identifier_matches_the_package(self):
        """NCX 的 dtb:uid 必须和 OPF 的 identifier 一致，否则 EPUBCheck 报 NCX-001。"""
        book = Book()
        book.title = '书名'
        book.add_document(Document('ch0001.xhtml',
                                   content='<html><body><p>正文</p></body></html>'))
        book.toc.append(TocEntry('第一章', href='ch0001.xhtml'))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'a.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as handle:
                package = epub.xml(handle.read('OEBPS/content.opf'))
                ncx = epub.xml(handle.read('OEBPS/toc.ncx'))
        identifier = package.findtext('.//' + epub.q(epub.D, 'identifier'))
        uid = ncx.find('.//' + epub.q(epub.N, 'meta')).get('content')
        self.assertEqual(identifier, uid)

    def test_styles_and_metadata_survive_the_round_trip(self):
        """转换不该丢掉样式表与出版信息。"""
        book = Book()
        book.title = '书名'
        book.publisher = '某出版社'
        book.metadata['date'] = ['2024-01-01']
        book.styles['Text/book.css'] = 'p{text-indent:2em}'
        book.add_document(Document('Text/ch1.xhtml',
                                   content='<html><body><p>正文</p></body></html>'))
        book.toc.append(TocEntry('第一章', href='Text/ch1.xhtml'))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'a.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as handle:
                names = handle.namelist()
                package = epub.xml(handle.read('OEBPS/content.opf'))
        self.assertIn('OEBPS/Text/book.css', names)
        publishers = [e.text for e in package.findall('.//' + epub.q(epub.D, 'publisher'))]
        self.assertIn('某出版社', publishers)
        dates = [e.text for e in package.findall('.//' + epub.q(epub.D, 'date'))]
        self.assertIn('2024-01-01', dates)

    def test_title_page_is_not_listed_twice(self):
        """读入端自带的 title.xhtml 不能和生成的标题页重复进清单。"""
        book = Book()
        book.title = '书名'
        book.author = '作者'
        book.add_document(Document('title.xhtml',
                                   content='<html><body><h1>书名</h1></body></html>'))
        book.add_document(Document('ch0001.xhtml',
                                   content='<html><body><p>正文</p></body></html>'))
        book.toc.append(TocEntry('第一章', href='ch0001.xhtml'))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'a.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as handle:
                package = epub.xml(handle.read('OEBPS/content.opf'))
        hrefs = [item.get('href') for item in package.findall('.//' + epub.q(epub.O, 'item'))]
        self.assertEqual(len(hrefs), len(set(hrefs)))
        self.assertEqual(hrefs.count('title.xhtml'), 1)

    def test_image_is_written_where_the_document_refers_to_it(self):
        book = Book()
        book.title = '书名'
        book.add_document(Document('Text/ch1.xhtml',
                                   content='<html><body><p><img src="../Images/p.png"/></p></body></html>'))
        book.add_image(Image('Images/p.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 20, 'image/png'))
        book.toc.append(TocEntry('第一章', href='Text/ch1.xhtml'))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'a.epub'
            epub_output.write(book, target)
            with zipfile.ZipFile(target) as handle:
                names = handle.namelist()
                chapter = handle.read('OEBPS/Text/ch1.xhtml')
        self.assertIn('OEBPS/Images/p.png', names)
        self.assertIn(b'../Images/p.png', chapter)


class CommandGuardTests(unittest.TestCase):
    """守护「正文不许被改坏」的检查必须用 raise，不能用 assert。

    python -O 会把 assert 整个移除；这两条检查守护的正是用户文件
    有没有被悄悄改坏，不能依赖 assert。
    """

    def test_pagination_guard_is_a_real_check(self):
        from workshop import pages_cmd
        source = inspect.getsource(pages_cmd.paginate)
        self.assertIn("raise ValueError('Pagination changed visible text", source)

    def test_cover_guard_is_a_real_check(self):
        from workshop import cover_cmd
        source = inspect.getsource(cover_cmd.cover)
        self.assertIn('Replacing the cover changed existing content documents', source)


if __name__ == '__main__':
    unittest.main()
