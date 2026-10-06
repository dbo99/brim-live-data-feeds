#!/usr/bin/env python3
"""Explicit offline export/validation of the reviewed frozen Candidate 3."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dendra.history_acquisition.candidate3_delivery import ArrowReader, export, validate_delivery
from dendra.history_acquisition.safety import encode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--validate-manifest-sha256", help="Validate an existing export; write nothing")
    args = parser.parse_args(argv)
    if args.validate_manifest_sha256:
        result = validate_delivery(args.source_root, args.output_root,
                                   manifest_sha256=args.validate_manifest_sha256, reader=ArrowReader())
    else:
        result = export(args.source_root, args.output_root)
    sys.stdout.buffer.write(encode(result))


if __name__ == "__main__":
    main()
