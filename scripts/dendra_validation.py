"""Private, bounded reuse inside one Python operation; never loaded from disk.

Every reuse hashes the complete actual candidate and validator sources again.
Separate commands/callbacks always start cold. Reports and candidate receipts are
outputs only: neither can populate this object's completed-validation records.
"""
import copy
import hashlib
import json
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
import dendra_candidate as dc

SOURCES = ('dendra_validation.py', 'dendra_candidate.py', 'dendra_archive.py',
           'dendra_state.py', 'dendra_publisher.py', 'main_publisher.py',
           'build_dendra_daily.R', 'dendra/core.R', 'dendra/integrated.R',
           'dendra/archive.R', 'dendra/semantic.R',
           '../data/input/dendra/pilot_catalog.json')


def source_binding():
    base = Path(__file__).resolve().parent
    return tuple((name, dc.sha(base / name)) for name in SOURCES)


def content_binding(root):
    root = Path(root)
    paths = dc.shared._candidate_inventory(root)
    dc.require(0 < len(paths) <= 20000, 'Validation inventory bound')
    dc.shared._validate_desired_inventory(paths, fixed_paths=dc.FIXED, owned_roots=dc.OWNED)
    records = []
    for name in paths:
        p = root / name
        s = p.stat()
        dc.require(stat.S_ISREG(s.st_mode) and 0 < s.st_size <= 32000000,
                   'Validation regular-file/byte bound')
        records.append((name, s.st_size, dc.shared._sha256(p)))
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


class ValidationOperation:
    """Created by the trusted caller, used once, then discarded (maximum 30 min)."""
    def __init__(self):
        self.__sources = source_binding()
        self.__started = time.monotonic()
        self.__wall = time.time()
        self.__last_wall = self.__wall
        self.__completed = {}

    def _check(self):
        wall = time.time()
        dc.require(wall >= self.__last_wall - 1, 'Validation operation clock moved backwards')
        dc.require(time.monotonic() - self.__started <= 1800 and wall - self.__wall <= 1800,
                   'Validation operation expired')
        self.__last_wall = wall
        dc.require(source_binding() == self.__sources, 'Validator source changed during operation')

    def validate(self, root, now=None):
        self._check()
        key = content_binding(root)
        instant = now or datetime.now(timezone.utc)
        if key not in self.__completed:
            # No caller-supplied result, semantic=False, disk receipt, or filename
            # claim can enter this completed set.
            result = dc.validate(root, now=instant)
            dc.require(result['semantic']['status'] == 'passed', 'Full R validation required')
            self._check()
            dc.require(content_binding(root) == key, 'Candidate changed during validation')
            dc.require(len(self.__completed) < 4, 'Validation operation generation bound')
            self.__completed[key] = copy.deepcopy(result)
            reused = False
        else:
            result = copy.deepcopy(self.__completed[key])
            reused = True
        # Exact index hashing binds identities, policy, dates, health, ownership,
        # and lineage. Time-dependent checks are not inherited from that hash.
        index = dc.load(Path(root) / dc.FIXED[0])
        dc.require(datetime.fromisoformat(index['generated_at_utc'].replace('Z', '+00:00')) <= instant,
                   'Build timestamp is in the future')
        result['freshness'] = dc.freshness(index, now=instant)
        result['operation_reuse'] = reused
        result['content_sha256'] = key
        self._check()
        return result


def operation(value=None):
    dc.require(value is None or type(value) is ValidationOperation,
               'Only a trusted in-process validation operation is accepted')
    return value if value is not None else ValidationOperation()
