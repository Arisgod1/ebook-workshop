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


def main():
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
