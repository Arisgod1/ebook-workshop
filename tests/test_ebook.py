"""Synthetic fixtures only: no third-party books, covers, accounts, or user paths."""
import base64
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ebook', ROOT/'scripts'/'ebook.py')
ebook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ebook)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH1kAAAAASUVORK5CYII=')
TEXT = ('作者资料：测试作者\n\n第1章 开篇\n这是一份自造的测试文字 & < >。\n'+
        ('一段用于验证字符分页且不涉及实际作品的示例。'*8+'\n')*4+
        '第5章 原有乱号\n第二节保留源文件的编号，不自动猜改。\n第3章 番外\n最后一段应完整保留。\n作者有话要说：\n这也是需要保留的内容。\n')


class EbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.txt=self.root/'source.txt';self.txt.write_text(TEXT,encoding='utf8')
        self.cover=self.root/'cover.png';self.cover.write_bytes(PNG)

    def cli(self,*args,ok=True):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=ebook.main_args_for_test(args) if hasattr(ebook,'main_args_for_test') else self.run_main(args)
        self.assertEqual(code,0 if ok else 1,err.getvalue())
        return json.loads(out.getvalue()) if ok and out.getvalue().strip() else err.getvalue()

    @staticmethod
    def run_main(args):
        import sys
        previous=sys.argv
        try:
            sys.argv=['ebook.py']+list(map(str,args));return ebook.main()
        finally:sys.argv=previous

    def build(self,name='book.epub',extra=()):
        out=self.root/name
        self.cli('build',self.txt,'--title','示例书名','--author','测试作者','--output',out,*extra)
        return out

    def test_compare_reports_structure_and_noise_without_excerpts(self):
        first = self.root / 'first.txt'
        second = self.root / 'second.txt'
        first.write_text('正文\n第1章 私密标题\n相同段落。\n第3章\n番外：不展示\n中文i兑字。\n后记\n相同段落。', encoding='utf8')
        second.write_text('第1章\n相 同 段 落。\n', encoding='utf8')
        third = self.build()
        result = self.cli('compare', first, second, third)
        self.assertEqual(result['books'][0]['section_heading_hints'], {'正文': 1, '番外': 1, '后记': 1})
        self.assertEqual(result['books'][0]['empty_chapter_candidates'], [1, 3])
        self.assertEqual(result['books'][0]['numbering_jumps'], [[1, 3]])
        self.assertEqual(result['books'][0]['suspicious_paragraphs'], 1)
        self.assertEqual(result['comparisons'][0]['matching_paragraphs'], 1)
        self.assertNotIn('私密标题', str(result))
        self.assertNotIn('中文i兑字', str(result))

    def test_correct_refuses_ambiguous_edits_and_preserves_navigation(self):
        book = self.build()
        edits = self.root / 'edits.json'
        document = 'OEBPS/c0001.xhtml'
        fixes = [{'document': document, 'old': '自造', 'new': '编写'}]
        edits.write_text(json.dumps(fixes + [{'document': document, 'old': '用于验证', 'new': '另写'}]))
        self.cli('correct', book, '--edits', edits, ok=False)
        output = self.root / '整理后' / '示例书名.epub'
        self.assertFalse(output.exists())
        edits.write_text(json.dumps(fixes))
        self.assertEqual(self.cli('correct', book, '--edits', edits)['output'], str(output))
        before, _, _ = ebook.load(book)
        after, _, _ = ebook.load(output)
        self.assertEqual([name for name in before if before[name] != after[name]], [document])
        self.assertIn('编写', ebook.visible(ebook.xml(after[document])))
        self.cli('correct', book, '--edits', edits, ok=False)

    def test_custom_layout_replaces_preset_in_selected_chapter_only(self):
        book = self.build()
        preset = self.root / 'preset.epub'
        first, second = 'OEBPS/c0001.xhtml', 'OEBPS/c0002.xhtml'
        self.cli('layout', book, '--document', first, '--document', second, '--output', preset)
        result = self.cli('layout', preset, '--document', first, '--margin', 8, '--indent', 0)
        files, _, _ = ebook.load(result['output'])
        old, _, _ = ebook.load(preset)
        self.assertEqual(files[second], old[second])
        self.assertNotIn(b'href="workshop-cn-novel.css"', files[first])
        custom, = [data for name, data in files.items() if 'workshop-cn-novel-' in name]
        self.assertIn(b'margin: 0 8%', custom)
        self.assertIn(b'text-indent: 0em', custom)
        self.assertEqual(ebook.visible(ebook.xml(files[first])), ebook.visible(ebook.xml(old[first])))

    def test_kindle_email_adds_pages_and_preserves_existing_page_list(self):
        book = self.build()
        paged = self.cli('kindle', book, '--method', 'email', '--pages')
        self.assertGreater(paged['synthetic_pages'], 0)
        copy = self.root / 'email.epub'
        info = self.cli('kindle', paged['output'], '--method', 'email', '--pages', '--output', copy)
        self.assertTrue(info['existing_page_list_preserved'])
        self.assertEqual(ebook.load(copy)[0], ebook.load(paged['output'])[0])

    def test_calibre_command_uses_epub3_and_preserves_output_metadata(self):
        book = self.build()
        source = self.root / 'source.mobi'
        source.write_bytes(b'synthetic source')
        def simulate(argv, **kwargs):
            self.assertEqual(argv[1], str(source.resolve()))
            self.assertEqual(argv[3:], ['--epub-version', '3', '--title', '示例书名', '--authors', '测试作者'])
            Path(argv[2]).write_bytes(book.read_bytes())
            return type('Completed', (), {'returncode': 0})()
        with patch.object(ebook.shutil, 'which', return_value='/mock/ebook-convert'), patch.object(ebook.subprocess, 'run', side_effect=simulate):
            output = self.cli('convert', source, '--title', '示例书名', '--author', '测试作者')['output']
        self.assertEqual(Path(output).name, '示例书名.epub')
        self.cli('validate', output)
        self.assertEqual(source.read_bytes(), b'synthetic source')

    def test_build_preserves_content_and_does_not_infer_chapter_fixes(self):
        book=self.build(extra=('--cover',self.cover))
        info=self.cli('inspect',book)
        self.assertEqual(info['declared_numbers'],[1,5,3])
        files,opf,package=ebook.load(book)
        self.assertEqual(package.find('.//'+ebook.q(ebook.D,'title')).text,'示例书名')
        _,chapters=ebook.split_text(TEXT,ebook.PATTERN)
        for i,c in enumerate(chapters,1):
            r=ebook.xml(files['OEBPS/c%04d.xhtml'%i])
            self.assertEqual([''.join(e.itertext()) for e in r.iter(ebook.q(ebook.H,'p'))],c['paragraphs'])
        self.assertIn('OEBPS/front.xhtml',files)
        self.cli('validate',book)

    def test_layout_can_extend_trial_without_changing_existing_navigation(self):
        book = self.build()
        paged = self.root / 'paged.epub'
        self.cli('paginate', book, '--output', paged)
        trial = self.root / 'trial.epub'
        final = self.root / 'final.epub'
        first, second = 'OEBPS/c0001.xhtml', 'OEBPS/c0002.xhtml'
        self.cli('layout', paged, '--document', first, '--output', trial)
        self.cli('layout', trial, '--document', first, '--document', second, '--output', final)
        original, _, _ = ebook.load(paged)
        preview, _, _ = ebook.load(trial)
        result, _, _ = ebook.load(final)
        self.assertEqual(preview[second], original[second])
        self.assertEqual(result[first], preview[first])
        for name in (first, second):
            old, new = [ebook.xml(f[name]).find(ebook.q(ebook.H, 'body'))
                        for f in (original, result)]
            self.assertEqual(list(old.itertext()), list(new.itertext()))
            self.assertEqual([e.get('id') for e in old.iter()], [e.get('id') for e in new.iter()])
            self.assertIn('workshop-cn-novel', new.get('class').split())
        for name in ('OEBPS/nav.xhtml', 'OEBPS/toc.ncx'):
            self.assertEqual(result[name], original[name])
        self.cli('validate', final)

    def test_explicit_title_override_changes_no_body_paragraphs(self):
        fixes=self.root/'fixes.json';fixes.write_text(json.dumps({'2':'第2章 核实后的标题'}))
        book=self.build(extra=('--headings-json',fixes))
        info=self.cli('inspect',book)
        self.assertEqual(info['declared_numbers'],[1,2,3])
        self.assertEqual(self.txt.read_text(encoding='utf8'),TEXT)
        self.assertNotIn('headings',info)

    def test_invalid_override_fails_without_output(self):
        fixes=self.root/'bad.json';fixes.write_text('{"99":"第99章 不存在"}')
        output=self.root/'bad.epub'
        self.cli('build',self.txt,'--title','示例','--author','某人','--headings-json',fixes,'--output',output,ok=False)
        self.assertFalse(output.exists())

    def test_output_overwrite_refused(self):
        book=self.build();before=book.read_bytes()
        self.cli('build',self.txt,'--title','另一个名字','--author','某人','--output',book,ok=False)
        self.assertEqual(before,book.read_bytes())

    def test_migrate_cover_keeps_exact_image_and_content(self):
        source=self.build('source.epub',extra=('--cover',self.cover))
        target=self.build('target.epub');out=self.root/'new.epub'
        result=self.cli('cover',target,'--from-epub',source,'--output',out)
        self.assertEqual(result['cover_pixels'],[1,1])
        f,opf,pkg=ebook.load(out);item=ebook.cover_item(pkg)
        image,_=ebook.resolve(opf,item.get('href'))
        self.assertEqual(f[image],PNG)
        original,_,_=ebook.load(target)
        for n,b in original.items():
            if n.startswith('OEBPS/c') and n.endswith('.xhtml'):self.assertEqual(f[n],b)
        self.cli('validate',out)

    def test_cover_navigation_points_to_new_cover(self):
        book=self.build('initial.epub',extra=('--cover',self.cover))
        files,opf,pkg=ebook.load(book)
        _,spine=ebook.inventory(files,opf,pkg);old_cover=spine[0][0]
        nav=ebook.xml(files['OEBPS/nav.xhtml']);body=nav.find(ebook.q(ebook.H,'body'))
        landmarks=ET.SubElement(body,ebook.q(ebook.H,'nav'),{ebook.q(ebook.EP,'type'):'landmarks'})
        a=ET.SubElement(landmarks,ebook.q(ebook.H,'a'),{ebook.q(ebook.EP,'type'):'cover','href':Path(old_cover).name});a.text='封面'
        files['OEBPS/nav.xhtml']=ebook.dump(nav)
        # Change PNG bytes without changing image geometry, simulating a different source image.
        alternate=PNG+b'new-image-source'
        changed=ebook.replace_cover(files,opf,pkg,alternate)
        root=ebook.xml(changed['OEBPS/nav.xhtml'])
        href=next(e.get('href') for e in root.iter() if e.get(ebook.q(ebook.EP,'type'))=='cover')
        self.assertNotEqual(href,Path(old_cover).name)
        ebook.validate_files(changed)

    def test_paginate_preserves_text_and_has_valid_continuous_targets(self):
        book=self.build(extra=('--cover',self.cover));out=self.root/'paged.epub'
        before,_,_=ebook.load(book)
        result=self.cli('paginate',book,'--chars','100','--exclude','OEBPS/front.xhtml','--output',out)
        self.assertGreater(result['synthetic_pages'],3)
        files,_,_=ebook.load(out)
        for n,b in before.items():
            if n.startswith('OEBPS/c') and n.endswith('.xhtml'):
                self.assertEqual(ebook.visible(ebook.xml(files[n])),ebook.visible(ebook.xml(b)))
        nav=ebook.xml(files['OEBPS/nav.xhtml']);pn=next(e for e in nav.iter() if e.get(ebook.q(ebook.EP,'type'))=='page-list')
        labels=[int(e.text) for e in pn.iter(ebook.q(ebook.H,'a'))]
        self.assertEqual(labels,list(range(1,result['synthetic_pages']+1)))
        self.cli('validate',out)
        second=self.root/'twice.epub';self.cli('paginate',out,'--output',second,ok=False);self.assertFalse(second.exists())

    def test_missing_anchor_is_caught(self):
        book=self.build();files,_,_=ebook.load(book)
        files['OEBPS/nav.xhtml']=files['OEBPS/nav.xhtml'].replace(b'c0001.xhtml',b'c0001.xhtml#missing')
        with self.assertRaisesRegex(ValueError,'Broken anchor'):ebook.validate_files(files)

    def test_duplicate_ids_caught(self):
        book=self.build();files,_,_=ebook.load(book)
        files['OEBPS/c0001.xhtml']=files['OEBPS/c0001.xhtml'].replace(b'<h1>',b'<span id="duplicate"/><span id="duplicate"/><h1>')
        with self.assertRaisesRegex(ValueError,'Duplicate IDs'):ebook.validate_files(files)

    def test_archive_traversal_and_entities_rejected(self):
        malicious=self.root/'bad.epub'
        with zipfile.ZipFile(malicious,'w') as z:z.writestr('../outside.txt','not extracted')
        with self.assertRaisesRegex(ValueError,'Unsafe ZIP'):ebook.load(malicious)
        with self.assertRaises(ValueError):ebook.xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><a>&x;</a>')

    def test_utf16_and_legacy_encoding_are_strict(self):
        for enc in ['utf-16','gb18030']:
            path=self.root/('source-'+enc+'.txt');path.write_bytes(TEXT.encode(enc))
            decoded,found=ebook.decode(path)
            self.assertEqual(decoded,TEXT);self.assertEqual(found,enc)
        path=self.root/'invalid.txt';path.write_bytes(b'\xff')
        with self.assertRaises(ValueError):ebook.decode(path)

    def test_skill_package_has_required_metadata_and_no_scaffold(self):
        skill=(ROOT/'SKILL.md').read_text(encoding='utf8')
        self.assertTrue(skill.startswith('---\nname: ebook-workshop\n'))
        self.assertIn('\ndescription: ',skill)
        self.assertNotIn('[TODO',skill)
        for ref in ['commands.md','kindle-pages.md']:self.assertTrue((ROOT/'references'/ref).exists())

if __name__=='__main__':unittest.main()
