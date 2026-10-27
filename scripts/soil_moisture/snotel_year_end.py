"""Manual, no-provider export of one explicitly selected committed generation.

This creates a provisional copy, never finalizes a season or establishes an
approved real archive destination. Callers must bind that authority separately.
"""
from __future__ import annotations
import argparse
import ctypes
import errno
import os
import stat
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import soil_network_workflow as workflow
from soil_moisture import snotel_snapshot as s

MANIFEST = 'provisional-export.json'
STATUS = 'PROVISIONAL — NOT FINALIZED'


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def _closure(root, expected):
    root = workflow.snotel_plain(root)
    allowed_dirs = {str(p) for n in expected for p in Path(n).parents if str(p) != '.'}
    files, dirs = set(), set()
    for p in root.rglob('*'):
        mode = p.lstat().st_mode
        s.require(not stat.S_ISLNK(mode), 'Export member symlink')
        rel = p.relative_to(root).as_posix()
        if stat.S_ISDIR(mode): dirs.add(rel)
        else:
            s.require(stat.S_ISREG(mode), 'Nonregular export member')
            files.add(rel)
            s.require(rel in expected and p.stat().st_size == len(expected[rel])
                      and p.read_bytes() == expected[rel], 'Conflicting or corrupt export')
    s.require(files == set(expected) and dirs == allowed_dirs, 'Partial/extra export closure')


def _expose(stage, destination):
    """Atomic no-replace directory exposure; never check-then-overwrite rename."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(stage), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise ValueError('Atomic no-replace export unavailable; destination held')
    if result:
        code = ctypes.get_errno()
        raise OSError(code or errno.EIO, 'Export exposure failed; original destination and stage retained')
    fd = os.open(destination.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def export_generation(reader, *, commit, generation, index_sha256, product_sha256,
                      expected_year, destination, approved_parent, protected_roots,
                      as_of, expose=_expose):
    """No implicit commit/destination/year or blanket authority for private data."""
    s.require(type(expected_year) is int and expected_year < s.water_year(as_of),
              'Expected completed water year required')
    s.require(isinstance(protected_roots, (list, tuple)) and len(protected_roots) > 0,
              'Explicit protected evidence roots required')
    parent = workflow.snotel_plain(approved_parent)
    dest = workflow.snotel_plain(destination)
    s.require(parent.is_dir() and parent.stat().st_uid == os.getuid()
              and not parent.stat().st_mode & 0o077, 'Private owned existing export parent required')
    s.require(dest.parent == parent and dest.name not in ('', '.', '..'), 'Destination must be direct child of approved parent')
    roots = [reader.repo, *(workflow.snotel_plain(p) for p in protected_roots)]
    s.require(all(p.exists() for p in roots), 'Bound protected roots must exist')
    s.require(all(not _overlap(dest, p) and not _overlap(parent, p) for p in roots),
              'Export overlaps source/live/protected evidence')
    trust = dict(commit=commit, generation=generation, index_sha256=index_sha256,
                 product_sha256=product_sha256, water_year=expected_year)
    files, binding = reader.product(commit, expected=trust)
    d = s.import_product(files, trusted_binding=binding)
    manifest = dict(version='snotel-provisional-export-1', status=STATUS,
        source=binding, native_contract=dict(version=d['version'], duration='DAILY', period_ref='END',
                                            unit_native='pct', source_clock='fixed GMT-08'),
        coverage=d['coverage'], annual_maintenance=d['annual_maintenance'],
        start=d['start'], end=d['end'], source_as_of=d['as_of'], built_at=d['built_at'],
        files={p:dict(bytes=len(b), sha256=s.h.sha256(b)) for p,b in files.items()})
    expected = dict(files, **{MANIFEST:s.h.canonical(manifest)})
    if dest.exists():
        s.require(dest.is_dir(), 'Export destination is not a directory')
        _closure(dest, expected)
        return dict(status='NO_OP', manifest_sha256=s.h.sha256(expected[MANIFEST]), source=binding)
    stage = Path(tempfile.mkdtemp(prefix='.snotel-export-stage-', dir=parent))
    for name, raw in expected.items(): workflow.snotel_write(stage/name, raw)
    _closure(stage, expected)
    for directory in sorted([stage, *(p for p in stage.rglob('*') if p.is_dir())],
                            key=lambda p: len(p.parts), reverse=True):
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    expose(stage, dest)
    _closure(dest, expected)
    return dict(status='EXPORTED_PROVISIONAL', manifest_sha256=s.h.sha256(expected[MANIFEST]),
                source=binding, bytes=sum(map(len, expected.values())))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'commit', 'generation', 'index-sha256', 'product-sha256',
                 'destination', 'approved-parent'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--expected-year', required=True, type=int)
    p.add_argument('--protected-root', action='append', required=True)
    a = p.parse_args()
    result = export_generation(workflow.SnotelGitReader(a.repo), commit=a.commit, generation=a.generation,
        index_sha256=a.index_sha256, product_sha256=a.product_sha256, expected_year=a.expected_year,
        destination=a.destination, approved_parent=a.approved_parent,
        protected_roots=a.protected_root, as_of=workflow.now())
    print(s.h.canonical(result).decode(), end='')


if __name__ == '__main__':
    main()
