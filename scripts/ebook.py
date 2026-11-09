#!/usr/bin/env python3
"""Local, dependency-free EPUB inspection and reversible preparation (Python 3.9+)."""
import argparse
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workshop import (build_cmd, correct_cmd, cover_cmd, inspect_cmd, layout_cmd,
                      pages_cmd, proofread_cmd, validate_cmd)

MODULES = (inspect_cmd, build_cmd, correct_cmd, cover_cmd, layout_cmd, pages_cmd,
           proofread_cmd, validate_cmd)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for module in MODULES:
        module.register(sub)
    return parser


def _use_utf8_output():
    """让中文输出在任何终端上都能打出来。

    Windows 的控制台默认是 GBK/CP936，直接 print 中文会抛
    UnicodeEncodeError，用户看到的是堆栈而不是结果。这里把标准输出
    和错误输出都切成 UTF-8；命令行重定向到文件时同样适用。
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, 'encoding', '') or '').lower()
        if encoding.replace('-', '') in ('utf8', 'utf8sig'):
            continue
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass


def main():
    _use_utf8_output()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == 'compare' and len(args.inputs) < 2:
        parser.error('compare requires at least two files')
    try:
        args.run(args)
    except (ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, OSError, UnicodeError, struct.error, re.error) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
