"""校对命令：筛查 → 复核 → 写入三步的流程编排。

具体的事在 proofread/ 包里：
  lexicon  词典、分词、变体表
  scan     段落抽取与疑点定位
  review   分组、复核包、按决定写回

这一层只做三件事：解析参数、把各层串起来、打印机读摘要。
"""

import hashlib
import json
import time
from pathlib import Path

from . import epub
from .proofread import lexicon, review, scan
from .proofread.lexicon import VARIANTS_PATH

VERSION = 3


def scan_book(args):
    """筛查：把整本书扫一遍，写出任务文件。"""
    started = time.monotonic()
    rules = lexicon.dictionary(args.dictionary)
    if args.book_dictionary:
        custom = lexicon.dictionary(args.book_dictionary)
        digest_input = hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
        if custom.get('source_sha256') and custom['source_sha256'] != digest_input:
            raise ValueError('Book dictionary belongs to a different source; review it before reuse')
        for key in ('words', 'protected'):
            rules[key] = sorted(set(rules.get(key, []) + custom.get(key, [])))
        rules['confusions'] = dict(rules.get('confusions', {}), **custom.get('confusions', {}))
        rules['replacements'] = dict(rules.get('replacements', {}), **custom.get('replacements', {}))
        rules['protected_evidence'] = custom.get('protected_evidence', {})
    words = set(rules.get('words', []))
    protected = set(rules.get('protected', []))
    confusions = rules.get('confusions', {})
    variants = lexicon.variant_table()
    tokenizer, frequencies, provenance, notes = lexicon.load_lexicon(args.lexicon)
    words.update(word for word in frequencies if len(word) >= 2)
    source = Path(args.input).read_bytes()
    fingerprint = hashlib.sha256(source).hexdigest()
    configuration = {'rules': rules, 'version': VERSION, 'lexicon': provenance,
                     'variants': hashlib.sha256(VARIANTS_PATH.read_bytes()).hexdigest()}
    rules_hash = scan.digest(json.dumps(configuration, sort_keys=True, ensure_ascii=False))
    output = Path(args.job)
    if output.exists():
        previous = json.loads(output.read_text(encoding='utf-8'))
        if previous.get('source_sha256') != fingerprint or previous.get('rules_hash') != rules_hash:
            raise ValueError('Source or dictionary changed; choose a new job path')
        epub.report(review.summary(previous))
        return
    _, _, _, rows = scan.paragraphs(args.input)
    candidates, exemptions = scan.find_candidates(rows, rules, words, protected, confusions,
                                                  variants, tokenizer, frequencies, fingerprint)
    job = {'version': VERSION,
           'source': str(Path(args.input).resolve()),
           'source_sha256': fingerprint,
           'rules_hash': rules_hash,
           'paragraphs_scanned': len(rows),
           'characters_scanned': sum(len(row[2]) for row in rows),
           'exemptions': exemptions,
           'lexicon': provenance,
           'protected_evidence': rules.get('protected_evidence', {}),
           'variant_table_entries': len(variants),
           'enhancements': {'lexicon': 'jieba' if tokenizer else 'starter', 'notes': notes},
           'seconds': time.monotonic() - started,
           'candidates': candidates}
    if args.terms:
        review.save(args.terms,
                    scan.discover_terms(rows, tokenizer, frequencies, provenance) if tokenizer
                    else {'candidates': [], 'note': 'Book-term discovery requires Jieba; '
                                                    'spelling scan completed'})
    review.save(output, job)
    epub.report(review.summary(job))


def review_book(args):
    """复核：导出复核包，或导入判断。"""
    job = json.loads(Path(args.job).read_text(encoding='utf-8'))
    if args.packet_dir and (args.packet or args.decisions):
        raise ValueError('--packet-dir is export-only and cannot be combined with --packet')
    if args.decisions:
        _import_decisions(args, job)
    elif args.packet or args.packet_dir:
        _export_packets(args, job)
    if args.report:
        review.write_report(job, args.report)
    epub.report(review.summary(job))


def _import_decisions(args, job):
    """导入复核结论，并校验包与书对得上、每组都有判断。"""
    decisions = json.loads(Path(args.decisions).read_text(encoding='utf-8'))
    if not isinstance(decisions, list):
        raise ValueError('Decisions must be a list')
    packet = json.loads(Path(args.packet).read_text(encoding='utf-8')) if args.packet else None
    if packet is not None and packet.get('source_sha256') != job['source_sha256']:
        raise ValueError('Packet belongs to a different source')
    covered = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError('Each decision must be an object')
        status = decision.get('status')
        if status not in ('keep', 'suggested', 'approved', 'unresolved'):
            raise ValueError('Invalid or duplicate decision')
        note = decision.get('note')
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Decision requires a reason')
        if 'old' not in decision or 'suggestion' not in decision:
            raise ValueError('Each decision must name the error with old and suggestion')
        changed = review.apply_group_verdict(
            job, decision, status, decision.get('replacement'), note)
        if not changed and not any(
                c['old'] == decision.get('old') and c['suggestion'] == decision.get('suggestion')
                for c in job['candidates']):
            raise ValueError('Group verdict matches no candidate')
        covered.add((decision.get('old'), decision.get('suggestion')))
    if packet:
        expected = {(g['old'], g['suggestion']) for g in packet.get('groups', [])}
        if expected and not expected <= covered:
            raise ValueError('Return a decision for every packet group')
    review.save(args.job, job)


def _export_packets(args, job):
    """导出复核包，可分给多个 worker。"""
    packets = review.make_packets(
        job, args.workers if args.packet_dir else 1, args.groups, args.budget)
    for index, packet in enumerate(packets, 1):
        output = (Path(args.packet_dir) / ('worker-%s.json' % index)
                  if args.packet_dir else args.packet)
        review.save(output, packet)
    if args.packet_dir and args.workers == 1:
        obsolete = Path(args.packet_dir) / 'worker-2.json'
        if obsolete.exists():
            obsolete.unlink()


def apply_book(args):
    """写入：把已批准的修正落进副本。"""
    job = json.loads(Path(args.job).read_text(encoding='utf-8'))
    corrections = review.write_corrections(job, args.output)
    epub.report({'output': args.output, 'corrections': corrections})


def register(sub):
    default = str(Path(__file__).resolve().parents[2] / 'assets' / 'proofread-dictionary.json')
    parser = sub.add_parser('proofread', help='Offline EPUB spelling candidates')
    parser.add_argument('input')
    parser.add_argument('--job', required=True)
    parser.add_argument('--dictionary', default=default)
    parser.add_argument('--book-dictionary')
    parser.add_argument('--lexicon', choices=['auto', 'starter', 'jieba'], default='auto')
    parser.add_argument('--terms', help='Save repeated unknown terms for book dictionary review')
    parser.set_defaults(run=scan_book)

    parser = sub.add_parser('proofread-review', help='Export bounded review packets or import decisions')
    parser.add_argument('--job', required=True)
    parser.add_argument('--packet')
    parser.add_argument('--groups', type=int, default=30)
    parser.add_argument('--budget', type=int, default=20000,
                        help='Maximum UTF-8 bytes of review content')
    parser.add_argument('--decisions')
    parser.add_argument('--report', help='Write concrete corrections and unresolved locations')
    parser.add_argument('--packet-dir', help='Export disjoint packets for host Agent workers')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.set_defaults(run=review_book)

    parser = sub.add_parser('proofread-apply', help='Apply approved, position-anchored corrections')
    parser.add_argument('--job', required=True)
    parser.add_argument('--output', required=True)
    parser.set_defaults(run=apply_book)
