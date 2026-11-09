"""校对的细节场景：脚注、专名豁免边界、重复句定位、跨标签保护。"""
import json
import zipfile

from tests.support import Workspace, epub_entries, epub_text, run, run_failing


class ProofreadDetailTests(Workspace):
    def setUp(self):
        super().setUp()
        self.source.write_text(
            '第1章 开篇\n'
            '但千活时的专注令她镇静。\n'
            '但千活时的专注令她镇静。\n'
            '不如i兑是为了清静。\n', encoding='utf-8')
        self.book = self.build()
        self.job = self.root / 'job.json'

    def scan(self, *extra):
        return run('proofread', self.book, '--job', self.job, *extra)

    def verdict(self, old, replacement, status='approved', suggestion=None):
        """A decision for one error kind, the way a reviewer answers a packet."""
        candidate = next(c for c in json.loads(self.job.read_text(encoding='utf-8'))['candidates'] if c['old'] == old)
        return {'old': old, 'suggestion': suggestion if suggestion is not None else candidate['suggestion'],
                'reason': candidate['reason'], 'status': status,
                'replacement': replacement, 'note': '确认'}

    def rewrite(self, transform):
        """Replace one document inside the book, as an imported edition might differ."""
        entries = epub_entries(self.book)
        entries['OEBPS/c0001.xhtml'] = transform(entries['OEBPS/c0001.xhtml'].decode()).encode()
        with zipfile.ZipFile(self.book, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in entries.items():
                if name != 'mimetype':
                    archive.writestr(name, body)

    def test_footnotes_are_scanned_once_each(self):
        """脚注和列表里的文字同样会被检查，嵌套段落不重复计算。"""
        self.rewrite(lambda text: text.replace('</body>', '<ul><li>脚注千活。</li><li><p>脚注千活。</p></li></ul></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        self.assertEqual(sum(c['old'] == '千活' for c in candidates), 4)

    def test_uncovered_text_stops_the_scan(self):
        """有正文落在检查范围之外时中止扫描，避免漏检被误认为查过了。"""
        self.rewrite(lambda text: text.replace('</body>', '<div>尚未覆盖的正文</div></body>'))
        code, error = run_failing('proofread', self.book, '--job', self.job)
        self.assertEqual(code, 1)
        self.assertIn('Uncovered reading text', error)
        self.assertFalse(self.job.exists())

    def test_text_in_a_div_is_scanned(self):
        """正文放在 div 里的书也要扫到，不能整本漏掉。"""
        self.rewrite(lambda text: text.replace(
            '</body>', '<div><p>但千活时的专注令她镇静。</p></div></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        self.assertTrue(any(c['old'] == '千活' for c in candidates))

    def test_epigraph_beside_paragraphs_is_scanned(self):
        """容器里既有段落又有不带段落的文字（题记）时，题记也要扫到。"""
        self.rewrite(lambda text: text.replace(
            '</body>', '<div><aside>题记：千活是她的信条。</aside>'
                       '<p>但千活时的专注令她镇静。</p></div></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        self.assertTrue(any('题记' in c['context'] and c['old'] == '千活' for c in candidates))

    def test_navigation_page_is_skipped(self):
        """整页都是链接的目录页不是正文，不进校对范围。"""
        entries = epub_entries(self.book)
        entries['OEBPS/c0002.xhtml'] = (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
            '<title>目录</title></head><body>'
            '<div class="toc-title">目录</div>'
            '<div class="toc-1"><a href="c0001.xhtml">第一章</a></div>'
            '<div class="toc-1"><a href="c0003.xhtml">第二章</a></div>'
            '</body></html>').encode()
        with zipfile.ZipFile(self.book, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for name, body in entries.items():
                if name != 'mimetype':
                    archive.writestr(name, body)
        self.scan()
        job = json.loads(self.job.read_text(encoding='utf-8'))
        self.assertGreater(job['paragraphs_scanned'], 0)

    def test_protected_term_does_not_hide_neighbouring_errors(self):
        """豁免只覆盖词语本身，旁边的错字和夹杂的异常字符照样报。"""
        self.rewrite(lambda text: text.replace('</body>', '<p>专名千活i兑</p></body>'))
        dictionary = self.write_json('book.json', {'protected': ['专名', 'i兑']})
        self.scan('--book-dictionary', dictionary)
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        self.assertEqual(sum(c['old'] == '千活' for c in candidates), 3)
        self.assertEqual(sum(c['old'] == 'i兑' for c in candidates), 2)

    def test_book_dictionary_must_match_the_source(self):
        """本书词典来自另一底本时拒绝套用。"""
        dictionary = self.write_json('book.json', {'source_sha256': 'different', 'protected': ['千活']})
        code, error = run_failing('proofread', self.book, '--job', self.job, '--book-dictionary', dictionary)
        self.assertEqual(code, 1)
        self.assertIn('different source', error)

    def test_quote_and_separator_noise_is_reported(self):
        """引号边界和字间竖线这类排版噪声会被标出来。"""
        self.rewrite(lambda text: text.replace('</body>', '<p>“我很荣幸他说，“我们出发。”</p><p>种|马</p></body>'))
        self.scan()
        reasons = {c['reason'] for c in json.loads(self.job.read_text(encoding='utf-8'))['candidates']}
        self.assertIn('quotation_boundary', reasons)
        self.assertIn('separator_noise', reasons)

    def test_traditional_characters_in_simplified_text_are_reported(self):
        """简体正文里混入的繁体字、异体字会被标出来并给出规范写法。"""
        self.rewrite(lambda text: text.replace('</body>', '<p>奧雷里亚诺有些 HIGGS 髙昂</p></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        found = {c['old']: c['suggestion'] for c in candidates if c['reason'] == 'variant_character'}
        self.assertEqual(found.get('奧'), '奥')
        self.assertEqual(found.get('髙'), '高')
        self.assertNotIn('HIGGS', found)

    def test_one_verdict_covers_every_occurrence_of_the_same_error(self):
        """同一种错字只判断一次，判定结果套用到所有出现的位置。"""
        self.rewrite(lambda text: text.replace('</body>', '<p>但千活时的专注。另有一处千活时更专注。</p></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        repeated = [c for c in candidates if c['old'] == '千活']
        self.assertGreaterEqual(len(repeated), 2)
        decisions = self.write_json('decisions.json', [self.verdict('千活', '干活')])
        run('proofread-review', '--job', self.job, '--decisions', decisions)
        output = self.root / 'fixed.epub'
        run('proofread-apply', '--job', self.job, '--output', output)
        body = epub_text(output, 'OEBPS/c0001.xhtml')
        self.assertNotIn('千活', body)
        self.assertEqual(body.count('干活'), len(repeated))

    def test_rules_hitting_one_spot_do_not_block_each_other(self):
        """多条规则指向同一处时，取范围更广的那条，其余不再重复写入。"""
        self.rewrite(lambda text: text.replace('</body>', '<p>但千活时的专注。</p></body>'))
        self.scan()
        candidates = json.loads(self.job.read_text(encoding='utf-8'))['candidates']
        target = next(c for c in candidates if c['old'] == '千活')
        target.update(status='approved', replacement='干活')
        data = json.loads(self.job.read_text(encoding='utf-8'))
        data['candidates'].append(dict(target, id='overlapping',
                                        start=target['start'] + 1, end=target['end'],
                                        old=target['old'][1:], suggestion='活'))
        self.job.write_text(json.dumps(data), encoding='utf-8')
        output = self.root / 'fixed.epub'
        run('proofread-apply', '--job', self.job, '--output', output)
        self.assertIn('干活', epub_text(output, 'OEBPS/c0001.xhtml'))

    def test_a_verdict_reaches_every_occurrence(self):
        """同一错字出现多处时，一次判定全部改掉。"""
        self.scan()
        candidates = [c for c in json.loads(self.job.read_text(encoding='utf-8'))['candidates'] if c['old'] == '千活']
        decisions = self.write_json('decisions.json', [self.verdict('千活', '干活')])
        run('proofread-review', '--job', self.job, '--decisions', decisions)
        output = self.root / 'fixed.epub'
        run('proofread-apply', '--job', self.job, '--output', output)
        body = epub_text(output, 'OEBPS/c0001.xhtml')
        self.assertNotIn('千活', body)
        self.assertEqual(body.count('干活'), len(candidates))

    def test_inline_markup_around_the_typo_is_kept(self):
        """错字外面包着标签时，只换文字，标签原样保留。"""
        self.rewrite(lambda text: text.replace('但千活时', '但<em>千活</em>时', 1))
        self.scan()
        decisions = self.write_json('decisions.json', [self.verdict('千活', '干活')])
        run('proofread-review', '--job', self.job, '--decisions', decisions)
        output = self.root / 'fixed.epub'
        run('proofread-apply', '--job', self.job, '--output', output)
        self.assertIn('<em>干活</em>', epub_entries(output)['OEBPS/c0001.xhtml'].decode())

    def test_correction_spanning_tags_is_refused(self):
        """错字被标签切成两半时无法安全替换，拒绝写入。"""
        self.rewrite(lambda text: text.replace('但千活时', '但<em>千</em>活时', 1))
        self.scan()
        decisions = self.write_json('decisions.json', [self.verdict('千活', '干活')])
        run('proofread-review', '--job', self.job, '--decisions', decisions)
        code, error = run_failing('proofread-apply', '--job', self.job, '--output', self.root / 'fixed.epub')
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'fixed.epub').exists())

    def test_overlapping_approved_changes_are_refused(self):
        """同一处被批准了两种不同的改法时拒绝写入。"""
        self.scan()
        data = json.loads(self.job.read_text(encoding='utf-8'))
        candidate = next(c for c in data['candidates'] if c['old'] == '千活')
        candidate.update(status='approved', replacement='干活')
        data['candidates'].append(dict(candidate, id='overlapping',
                                        replacement='干活用'))
        self.job.write_text(json.dumps(data), encoding='utf-8')
        code, _ = run_failing('proofread-apply', '--job', self.job, '--output', self.root / 'fixed.epub')
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'fixed.epub').exists())
