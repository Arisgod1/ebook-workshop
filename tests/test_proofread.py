"""Exercise the real command entry point with synthetic books."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProofreadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.txt'
        self.source.write_text('第1章 测试\n但千活时的专注令她镇静。\n千万人以经验为依据。\n但千活时的专注令她镇静。\n不如i兑是为了清静。\n', encoding='utf-8')
        self.book = self.root / 'book.epub'
        self.job = self.root / 'job.json'
        self.run_cli('build', self.source, '--title', '测试', '--author', '测试', '--output', self.book)
        self.original = self.book.read_bytes()

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/ebook.py'), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def scan(self, *args):
        return self.run_cli('proofread', self.book, '--job', self.job, *args)

    def read_job(self):
        return json.loads(self.job.read_text())

    def decide(self, status='approved'):
        candidate = next(c for c in self.read_job()['candidates'] if c['old'] == '千活')
        path = self.root / 'decisions.json'
        path.write_text(json.dumps([{'id': candidate['id'], 'status': status, 'replacement': '干活', 'reason': 'Synthetic fixture ground truth'}]))
        self.run_cli('proofread-review', '--job', self.job, '--decisions', path)
        return candidate

    def test_detection_and_normal_phrases(self):
        self.scan()
        candidates = self.read_job()['candidates']
        self.assertEqual(sum(c['old'] == '千活' for c in candidates), 2)
        self.assertTrue(any(c['old'] == 'i兑' for c in candidates))
        self.assertFalse(any(c['old'] in ('千万', '以经') for c in candidates))
        self.assertEqual(len({c['id'] for c in candidates}), len(candidates))
        self.assertEqual(self.book.read_bytes(), self.original)

    def test_resume_preserves_review(self):
        self.scan()
        self.decide('keep')
        before = self.job.read_bytes()
        self.scan()
        self.assertEqual(self.job.read_bytes(), before)

    def test_book_dictionary_exempts_specific_phrase(self):
        dictionary = self.root / 'dictionary.json'
        dictionary.write_text(json.dumps({'protected': ['千活']}))
        self.scan('--book-dictionary', dictionary)
        self.assertFalse(any(c['old'] == '千活' for c in self.read_job()['candidates']))
        self.assertTrue(any(c['old'] == 'i兑' for c in self.read_job()['candidates']))

    def test_changed_dictionary_refuses_resume(self):
        self.scan()
        dictionary = self.root / 'dictionary.json'
        dictionary.write_text(json.dumps({'protected': ['千活']}))
        self.run_cli('proofread', self.book, '--job', self.job, '--book-dictionary', dictionary, ok=False)

    def test_packet_budget_and_reviewed_exclusion(self):
        self.scan()
        candidate = self.decide('keep')
        packet = self.root / 'packet.json'
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet, '--budget', 1200)
        data = json.loads(packet.read_text())
        self.assertLessEqual(data['accounting']['content_bytes'], 1200)
        self.assertNotIn(candidate['id'], [c['id'] for c in data['items']])

    def test_approved_position_changes_only_one_repeated_sentence(self):
        self.scan()
        self.decide()
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output)
        import zipfile
        with zipfile.ZipFile(output) as archive:
            body = archive.read('OEBPS/c0001.xhtml').decode()
        self.assertEqual(body.count('千活'), 1)
        self.assertEqual(body.count('干活'), 1)
        self.run_cli('validate', output)
        self.assertEqual(self.book.read_bytes(), self.original)

    def test_suggestions_cannot_be_applied(self):
        self.scan()
        self.decide('suggested')
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output, ok=False)
        self.assertFalse(output.exists())

    def test_source_change_blocks_apply(self):
        self.scan()
        self.decide()
        self.book.write_bytes(self.original + b'changed')
        self.run_cli('proofread-apply', '--job', self.job, '--output', self.root / 'fixed.epub', ok=False)

    def test_invalid_batch_does_not_save_partial_decisions(self):
        self.scan()
        original = self.job.read_bytes()
        decisions = self.root / 'bad.json'
        ident = self.read_job()['candidates'][0]['id']
        decisions.write_text(json.dumps([{'id': ident, 'status': 'keep', 'reason': 'valid'},
                                        {'id': 'missing', 'status': 'keep', 'reason': 'invalid'}]))
        self.run_cli('proofread-review', '--job', self.job, '--decisions', decisions, ok=False)
        self.assertEqual(self.job.read_bytes(), original)

    def test_too_small_budget_does_not_write_packet(self):
        self.scan()
        packet = self.root / 'packet.json'
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet, '--budget', 0, ok=False)
        self.assertFalse(packet.exists())

    def test_overlapping_approved_edits_fail_without_output(self):
        self.scan()
        data = self.read_job()
        candidate = next(c for c in data['candidates'] if c['old'] == '千活')
        candidate.update(status='approved', replacement='干活')
        data['candidates'].append(dict(candidate, id='duplicate'))
        self.job.write_text(json.dumps(data))
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output, ok=False)
        self.assertFalse(output.exists())

    def rewrite_book(self, transform):
        import zipfile
        with zipfile.ZipFile(self.book) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files['OEBPS/c0001.xhtml'] = transform(files['OEBPS/c0001.xhtml'].decode()).encode()
        with zipfile.ZipFile(self.book, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)

    def test_list_footnotes_are_scanned_without_duplicate_nested_paragraphs(self):
        self.rewrite_book(lambda text: text.replace('</body>', '<ul><li>脚注千活。</li><li><p>脚注千活。</p></li></ul></body>'))
        self.scan()
        self.assertEqual(sum(c['old'] == '千活' for c in self.read_job()['candidates']), 4)

    def test_uncovered_body_text_blocks_claim_of_full_scan(self):
        self.rewrite_book(lambda text: text.replace('</body>', '<div>尚未覆盖的正文</div></body>'))
        self.run_cli('proofread', self.book, '--job', self.job, ok=False)
        self.assertFalse(self.job.exists())

    def test_protected_range_does_not_hide_adjacent_or_embedded_ocr(self):
        self.rewrite_book(lambda text: text.replace('</body>', '<p>专名千活i兑</p></body>'))
        dictionary = self.root / 'dictionary.json'
        dictionary.write_text(json.dumps({'protected': ['专名', 'i兑']}))
        self.scan('--book-dictionary', dictionary)
        self.assertEqual(sum(c['old'] == '千活' for c in self.read_job()['candidates']), 3)
        self.assertEqual(sum(c['old'] == 'i兑' for c in self.read_job()['candidates']), 2)

    def test_same_paragraph_quote_boundary_and_separator_candidates(self):
        self.rewrite_book(lambda text: text.replace('</body>', '<p>“我很荣幸他说，“我们出发。”</p><p>种|马</p></body>'))
        self.scan()
        reasons = {c['reason'] for c in self.read_job()['candidates']}
        self.assertIn('quotation_boundary', reasons)
        self.assertIn('separator_noise', reasons)

    def test_book_dictionary_source_fingerprint(self):
        dictionary = self.root / 'dictionary.json'
        dictionary.write_text(json.dumps({'source_sha256': 'different', 'protected': ['千活']}))
        self.run_cli('proofread', self.book, '--job', self.job, '--book-dictionary', dictionary, ok=False)

    def test_unresolved_decisions_can_be_recorded(self):
        self.scan()
        ident = self.read_job()['candidates'][0]['id']
        path = self.root / 'decisions.json'
        path.write_text(json.dumps([{'id': ident, 'status': 'unresolved', 'reason': 'Need edition source'}]))
        result = self.run_cli('proofread-review', '--job', self.job, '--decisions', path)
        self.assertEqual(result['statuses']['unresolved'], 1)

    def test_inline_markup_is_preserved_for_single_node_edit(self):
        self.rewrite_book(lambda text: text.replace('但千活时', '但<em>千活</em>时', 1))
        self.scan()
        self.decide()
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output)
        import zipfile
        with zipfile.ZipFile(output) as archive:
            self.assertIn('<em>干活</em>', archive.read('OEBPS/c0001.xhtml').decode())

    def test_jieba_dictionary_metadata_and_large_lexicon(self):
        try:
            import jieba
        except ImportError:
            self.skipTest('Install optional pinned Jieba to run integration test')
        result = self.scan('--lexicon', 'jieba')
        job = self.read_job()
        self.assertEqual(job['lexicon']['version'], '0.42.1')
        self.assertGreater(job['lexicon']['entries'], 100000)
        self.assertTrue(any(c['old'] == '千活' for c in job['candidates']))
        self.assertEqual(job['coverage']['uncovered_text'], 0)

    def test_custom_dictionary_resume_is_stable_across_processes(self):
        dictionary = self.root / 'dictionary.json'
        dictionary.write_text(json.dumps({'protected': ['名词甲', '名词乙', '名词丙'], 'words': ['知识', '经验']}))
        self.scan('--book-dictionary', dictionary)
        previous = self.job.read_bytes()
        self.scan('--book-dictionary', dictionary)
        self.assertEqual(self.job.read_bytes(), previous)

    def test_cross_inline_replacement_refused(self):
        self.rewrite_book(lambda text: text.replace('但千活时', '但<em>千</em>活时', 1))
        self.scan()
        self.decide()
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output, ok=False)
        self.assertFalse(output.exists())

    def review_packets(self, workers):
        directory = self.root / 'packets'
        rounds = 0
        while any(c['status'] == 'pending' for c in self.read_job()['candidates']):
            rounds += 1
            self.assertLess(rounds, 10)
            self.run_cli('proofread-review', '--job', self.job, '--packet-dir', directory,
                         '--workers', workers, '--groups', 1)
            seen = set()
            for path in sorted(directory.glob('worker-*.json')):
                packet = json.loads(path.read_text())
                ids = {item['id'] for item in packet['items']}
                self.assertFalse(ids & seen)
                seen.update(ids)
                decisions = self.root / 'decisions.json'
                decisions.write_text(json.dumps([
                    dict(id=item['id'], status='approved', replacement=item['suggestion'],
                         reason='Synthetic fixture ground truth') for item in packet['items']]))
                self.run_cli('proofread-review', '--job', self.job, '--packet', path,
                             '--decisions', decisions)
        return [(c['id'], c['status'], c['replacement']) for c in self.read_job()['candidates']]

    def test_sequential_and_two_workers_have_same_result(self):
        self.scan()
        original = self.job.read_bytes()
        sequential = self.review_packets(1)
        self.job.write_bytes(original)
        parallel = self.review_packets(2)
        self.assertEqual(sequential, parallel)
        self.job.write_bytes(original)
        self.assertEqual(sequential, self.review_packets(1))
        self.assertFalse((self.root / 'packets' / 'worker-2.json').exists())
        output = self.root / 'fixed.epub'
        self.run_cli('proofread-apply', '--job', self.job, '--output', output)
        self.run_cli('validate', output)

    def test_packet_requires_complete_results_and_allows_reimport(self):
        self.scan()
        packet = self.root / 'packet.json'
        decisions = self.root / 'decisions.json'
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet)
        original = self.job.read_bytes()
        decisions.write_text('[]')
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet,
                     '--decisions', decisions, ok=False)
        self.assertEqual(self.job.read_bytes(), original)
        items = json.loads(packet.read_text())['items']
        decisions.write_text(json.dumps([dict(id=i['id'], status='keep', reason='fixture') for i in items]))
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet, '--decisions', decisions)
        reviewed = self.job.read_bytes()
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet, '--decisions', decisions)
        self.assertEqual(self.job.read_bytes(), reviewed)

    def test_wrong_source_packet_rejected(self):
        self.scan()
        packet = self.root / 'packet.json'
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet)
        data = json.loads(packet.read_text())
        data['source_sha256'] = 'wrong'
        packet.write_text(json.dumps(data))
        decisions = self.root / 'decisions.json'
        decisions.write_text(json.dumps([dict(id=i['id'], status='keep', reason='fixture') for i in data['items']]))
        original = self.job.read_bytes()
        self.run_cli('proofread-review', '--job', self.job, '--packet', packet, '--decisions', decisions, ok=False)
        self.assertEqual(self.job.read_bytes(), original)

    def test_oversized_context_fails_explicitly(self):
        self.scan()
        data = self.read_job()
        for c in data['candidates']:
            c['context'] = '长' * 3000
        self.job.write_text(json.dumps(data))
        self.assertIn('increase --budget', self.run_cli('proofread-review', '--job', self.job,
                      '--packet', self.root / 'packet.json', '--budget', 1200, ok=False))

    def test_report_omits_normal_content(self):
        self.scan()
        candidate = self.decide('keep')
        report = self.root / 'report.md'
        self.run_cli('proofread-review', '--job', self.job, '--report', report)
        self.assertNotIn('状态：keep', report.read_text())
        self.assertIn('依据', report.read_text())


if __name__ == '__main__':
    unittest.main()
