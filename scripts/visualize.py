#!/usr/bin/env python3
"""
Generate tb/visualize.html from existing tb/vectors/ files.

Usage:
    python scripts/visualize.py [--vec-dir tb/vectors] [--out tb/visualize.html]

This is a thin wrapper around gen_tb.gen_html(). Run it when you want to
regenerate the HTML visualiser without regenerating the testbench or vectors.
To regenerate everything in one pass, use gen_tb.py --html instead.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_tb import gen_html


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vec-dir', default=os.path.join(repo_root, 'tb', 'vectors'))
    ap.add_argument('--out',     default=os.path.join(repo_root, 'tb', 'visualize.html'))
    args = ap.parse_args()
    gen_html(args.vec_dir, args.out)


if __name__ == '__main__':
    main()
