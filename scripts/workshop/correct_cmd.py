"""应用已核实的勘误：按文件和原文精确替换，位置不唯一时整批不写入。"""
import json
from pathlib import Path

from . import epub, names


def correct(args):
    """Apply a verified correction list; every match must be unique in one text node."""
    files, opf, package = epub.load(args.input)
    _, spine = epub.inventory(files, opf, package)
    eligible = {name for name, item in epub.reading_documents(spine)}
    edits = json.loads(Path(args.edits).read_text(encoding='utf-8'))
    if not isinstance(edits, list) or not edits:
        raise ValueError('Supply a nonempty JSON list of verified corrections')
    roots = {}
    changes = {}
    for index, edit in enumerate(edits, 1):
        if (not isinstance(edit, dict) or any(not isinstance(edit.get(key), str)
                for key in ('document', 'old', 'new')) or not edit['old'] or edit['old'] == edit['new']):
            raise ValueError(f'Invalid correction #{index}')
        path, old, new = (edit[key] for key in ('document', 'old', 'new'))
        if path not in eligible:
            raise ValueError(f'Correction #{index}: document is not in the reading order')
        if path not in roots:
            roots[path] = epub.xml(files[path])
        body = roots[path].find(epub.q(epub.H, 'body'))
        # 在整个 body 里找，不只是 <p>：错字同样可能出现在标题、列表项、
        # 甚至直接排版在 div 里的正文里。只认 <p> 的话这些地方改不了。
        skip = {epub.q(epub.H, 'script'), epub.q(epub.H, 'style')}
        matches = epub.find_text_positions(body, old, skip) if body is not None else []
        if len(matches) != 1:
            raise ValueError(f'Correction #{index}: expected one match in a single text node, found {len(matches)}')
        element, field, start = matches[0]
        changes.setdefault((element, field), []).append((start, start + len(old), new))
    epub.apply_text_edits(changes)
    for path, root in roots.items():
        files[path] = epub.dump(root)
    args.output = names.output_for(args, epub.book_title(package))
    epub.write(files, args.output)
    epub.report({'output': args.output, 'corrections_applied': len(edits)})


def register(sub):
    parser = sub.add_parser('correct', help='Apply verified exact corrections from JSON')
    parser.add_argument('input')
    parser.add_argument('--edits', required=True)
    parser.add_argument('--output')
    parser.set_defaults(run=correct)
