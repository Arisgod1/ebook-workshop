"""校验：检查书内文件、跳转和目录是否完整。"""
import subprocess
import zipfile

from . import epub


def validate(args):
    """Check that the archive is internally consistent."""
    files, opf, package = epub.load(args.input)
    result = epub.validate_files(files)
    with zipfile.ZipFile(args.input) as archive:
        first = archive.infolist()[0]
        if first.filename != 'mimetype' or first.compress_type != zipfile.ZIP_STORED or first.extra:
            raise ValueError('mimetype must be first, uncompressed, without extra ZIP fields')
    result['structural_validation'] = 'passed (not a substitute for EPUBCheck)'
    if args.epubcheck:
        completed = subprocess.run(['java', '-jar', args.epubcheck, args.input], capture_output=True, text=True)
        result['epubcheck_exit_code'] = completed.returncode
        result['epubcheck_output'] = completed.stdout + completed.stderr
        epub.report(result)
        if completed.returncode:
            raise ValueError('EPUBCheck did not pass')
        return
    epub.report(result)


def register(sub):
    parser = sub.add_parser('validate')
    parser.add_argument('input')
    parser.add_argument('--epubcheck', help='Path to an externally installed official EPUBCheck JAR')
    parser.set_defaults(run=validate)
