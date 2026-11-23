"""One bounded private JSON transaction index and immutable body store.

Single writer, fsync + atomic replacement. A reserved attempt remains charged
and blocks its session after interruption. Range replay requires a new explicit
offline run; it never refunds an old charge. Independent fixture lineages only.
"""
from copy import deepcopy
from datetime import date
import fcntl
import os
from pathlib import Path
import uuid

from .history_acquisition.safety import encode, decode, sha, require
from .transport import parse_utc
from .live_sampling import SUPPORTED_WATER_YEARS

MAX_INDEX = 8 * 1024**2
P1 = 'dendra-offline-range-series-2'
SEASON = dict(attempts=20000, revisions=20000, runs=512, cells=732,
              charged_bytes=128*1024**2, physical_bytes=512*1024**2,
              files=100000, record_bytes=256*1024, expanded_bytes=256*1024**2)


def season_totals(data):
    """WY-scoped charges survive relocation; UNKNOWN always spends its full cap."""
    attempts=data['attempts']
    return dict(attempts=len(attempts),revisions=len(data['revisions']),
        runs=len(data.get('range_runs',[])),cells=len(data['cells']),
        consumed_bytes=sum(a['bytes'] for a in attempts),
        charged_bytes=sum(a['bytes'] if a['status']=='RECEIVED' else a['cap'] for a in attempts),
        charged_rows=sum((a['rows'] or 0) if a['status']=='RECEIVED' else
                         a.get('context',{}).get('row_cap',0) for a in attempts))


def check_season(data):
    year=data['binding']['water_year']
    require(data['binding']['lineage']==P1 and type(year) is int and year in SUPPORTED_WATER_YEARS and
            data['binding']['season_limits']==SEASON, 'Exact P1 season contract')
    for key in data['cells']:
        try:
            stamp=date.fromisoformat(key.rsplit('/',1)[1])
        except (ValueError,IndexError,AttributeError) as exc:
            raise ValueError('Invalid P1 source-day key') from exc
        require(stamp.year+(stamp.month>=10)==year, 'Cross-year P1 cell')
    require(all(type(a['cap']) is int and 0<a['cap']<=MAX_INDEX and
                type(a['bytes']) is int and 0<=a['bytes']<=a['cap'] and
                a['status'] in ('RECEIVED','RESERVED','AMBIGUOUS') and
                (a['rows'] is None or type(a['rows']) is int and 0<=a['rows']<=2016)
                for a in data['attempts']), 'P1 charge shape')
    totals=season_totals(data)
    require(all(totals[k]<=SEASON[k] for k in ('attempts','revisions','runs','cells','charged_bytes')) and
            totals['charged_rows']<=SEASON['attempts']*2016, 'Finite P1 season exhausted')
    require(data.get('season_totals')==totals, 'Season accounting mismatch')
    return totals


def compact(value, put):
    """Same store, immutable references; no second ledger or receipt deletion."""
    def record(v):
        b=encode(walk(v));require(len(b)<=SEASON['record_bytes'],'P1 logical record bound')
        return {'$p1':put(b)}
    def walk(v):
        if type(v) is list:return [walk(x) for x in v]
        if type(v) is not dict:return v
        result={}
        for k,x in v.items():
            if k in ('attempts','revisions','range_runs') and type(x) is list:
                result[k]=[record(y) for y in x]
            elif type(x) is dict and ('sample_status' in x and 'candidate_normalized_eligible' in x or
                                     'ordinal' in x and 'body_sha256' in x):
                result[k]=record(x)
            elif type(x) is list and k=='pages':result[k]=[record(y) for y in x]
            else:result[k]=walk(x)
        return result
    return walk(value)


def expand(root, value):
    """Hash-check every referenced logical object with a finite expansion budget."""
    cache={};spent=0
    def walk(v,depth=0):
        nonlocal spent
        require(depth<=48,'P1 reference depth')
        if type(v) is list:return [walk(x,depth+1) for x in v]
        if type(v) is not dict:return v
        if '$p1' in v:
            pin=v['$p1'];require(set(v)=={'$p1'} and type(pin) is str and len(pin)==64 and
                all(c in '0123456789abcdef' for c in pin),'P1 reference shape')
            if pin not in cache:
                b=read(Path(root)/'objects'/pin,SEASON['record_bytes']);require(sha(b)==pin,'P1 object hash')
                cache[pin]=(decode(b),len(b))
            item,size=cache[pin];spent+=size
            require(spent<=SEASON['expanded_bytes'],'P1 expansion bound')
            return walk(item,depth+1)
        return {k:walk(x,depth+1) for k,x in v.items()}
    return walk(value)


def read(path, limit=MAX_INDEX):
    path = Path(path)
    require(path.absolute() == path.resolve() and path.is_file(), 'Unsafe/missing file')
    with path.open('rb') as f:
        b = f.read(limit+1)
    require(len(b) <= limit, 'File bound')
    return b


def durable(path, body, *, replace=False, fault=lambda: None):
    path = Path(path)
    require(len(body) <= MAX_INDEX and path.parent.resolve() == path.parent.absolute(), 'Write boundary')
    if not replace and path.exists():
        require(read(path) == body, 'Immutable collision')
        return
    temp = path.parent / ('.pending-'+uuid.uuid4().hex)
    with temp.open('xb') as f:
        os.chmod(temp, 0o600)
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
        if not replace:
            os.fchmod(f.fileno(), 0o400)
    fault()  # A torn/interrupted write leaves private evidence; old pointer survives.
    if replace:
        os.replace(temp, path)
    else:
        os.link(temp, path)  # Exclusive immutable publication, never clobber.
        temp.unlink()
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class State:
    def __init__(self, root, binding, *, create=False):
        self.root = Path(root).absolute()
        require(self.root == self.root.resolve(), 'Symlink state root')
        if create:
            self.root.mkdir(mode=0o700)
            (self.root/'objects').mkdir(mode=0o700)
            (self.root/'products').mkdir(mode=0o700)
        require(self.root.is_dir(), 'Missing private state')
        for name in ('objects', 'products'):
            require((self.root/name).is_dir() and (self.root/name).resolve() == self.root/name,
                    'State directory boundary')
        require(not (self.root/'writer.lock').is_symlink(), 'Lock boundary')
        self.lock = (self.root/'writer.lock').open('a+b')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.binding = deepcopy(binding)
            self.p1 = binding.get('lineage') == P1
            self._known={};self._physical=0;self._files=0
            if self.p1:
                self.usage()
            if create:
                self.data = dict(version='dendra-offline-state-2' if self.p1 else 'dendra-offline-state-1', binding=binding,
                                 attempts=[], cells={}, revisions=[])
                self.commit()
            else:
                self.data = self.load_index()
                require(self.data['binding'] == binding and self.data['version'] ==
                        ('dendra-offline-state-2' if self.p1 else 'dendra-offline-state-1'),
                        'State binding mismatch')
                require(len(self.data['attempts']) <= self.attempt_limit(), 'Attempt bound')
                for i, a in enumerate(self.data['attempts']):
                    require(a['ordinal'] == i and a['status'] in ('RESERVED','RECEIVED','AMBIGUOUS'),
                            'Invalid attempt state')
                    if a['body_sha256'] is not None:
                        b = read(self.root/'objects'/a['body_sha256'])
                        require(sha(b) == a['body_sha256'] and len(b) == a['bytes'], 'Receipt body mismatch')
                if self.p1:
                    check_season(self.data)
                    if self.data.get('range_runs'):
                        from .live_export import validate_snapshot,verify_range
                        validate_snapshot(self.root,self.data)
                        if (self.root/'accepted.json').exists():
                            p=decode(read(self.root/'accepted.json'))
                            verify_range(self.root,p['manifest_path'],p['manifest_sha256'],self.binding)
        except BaseException:
            self.lock.close()
            raise

    def close(self):
        self.lock.close()

    def load_index(self):
        value=decode(read(self.root/'index.json'))
        return expand(self.root,value) if self.p1 else value

    def usage(self):
        self._physical=0;self._files=0
        for directory in (self.root,self.root/'objects',self.root/'products'):
            for p in directory.iterdir():
                require(not p.is_symlink(),'P1 storage alias')
                if p.is_file():self._physical+=p.stat().st_size;self._files+=1
                else:require(p in (self.root/'objects',self.root/'products'),'P1 unexpected directory')
        require(self._physical<=SEASON['physical_bytes'] and self._files<=SEASON['files'],'P1 physical store bound')

    def put(self,path,body,*,replace=False):
        path=Path(path)
        if not self.p1:return durable(path,body,replace=replace)
        require(path.parent in (self.root,self.root/'objects',self.root/'products'),'P1 write path')
        if not replace and path in self._known:
            require(self._known[path]==sha(body),'P1 immutable collision');return
        exists=path.exists();old=path.stat().st_size if exists else 0
        if exists and not replace:
            require(read(path)==body,'P1 immutable collision');self._known[path]=sha(body);return
        require(self._physical+len(body)<=SEASON['physical_bytes'] and
                self._files+(1 if replace else 2)<=SEASON['files'],
                'P1 physical reservation unavailable')
        try:durable(path,body,replace=replace)
        except BaseException:
            self.usage()  # Retain and charge any torn temporary file.
            raise
        self._physical+=len(body)-old;self._files+=int(not exists)
        if not replace:self._known[path]=sha(body)

    def packed(self,value):
        if not self.p1:return deepcopy(value)
        def put(body):
            pin=sha(body);self.put(self.root/'objects'/pin,body);return pin
        return compact(value,put)

    def commit(self):
        try:
            if self.p1:
                self.data['season_totals']=season_totals(self.data);check_season(self.data)
            self.put(self.root/'index.json', encode(self.packed(self.data)), replace=True)
        except Exception:
            # Roll back the working projection to the last durable transaction.
            if (self.root/'index.json').exists():
                self.data = self.load_index()
            raise

    def reserve(self, task, url, clock, cap, *, context=None):
        attempts = self.data['attempts']
        start = self.data.get('provider',{}).get('start_ordinal',0) if self.is_range() else 0
        require(not any(a['status'] in ('RESERVED', 'AMBIGUOUS') for a in attempts[start:]), 'Unresolved charge')
        require(len(attempts) < self.attempt_limit() and 0 < cap <= MAX_INDEX, 'Attempt/body bound')
        require(sum(a['cap'] if self.is_range() and a['status'] != 'RECEIVED' else a['bytes']
                    for a in attempts)+cap <= (SEASON['charged_bytes'] if self.p1 else 16*1024**2), 'Aggregate body bound')
        if self.p1:
            # Refuse dispatch unless the entire body, receipt transitions and
            # an index replacement fit. Unknowns still spend their full cap.
            require(self._physical+cap+3*SEASON['record_bytes']+MAX_INDEX<=SEASON['physical_bytes'] and
                    self._files+6<=SEASON['files'], 'P1 response custody reservation unavailable')
        ordinal = len(attempts)
        attempts.append(dict(ordinal=ordinal, task=task, query=url, attempted_at=clock,
                             clock_kind='OFFLINE_REPLAY', provider_request=False,
                             status='RESERVED', cap=cap, bytes=0, body_sha256=None, rows=None))
        if context is not None:
            attempts[-1]['context'] = deepcopy(context)
            attempts[-1].update(clock_kind='LIVE_PROVIDER' if context['live'] else 'OFFLINE_DRY_RUN',
                                provider_request=context['live'])
        self.commit()
        return ordinal

    def receive(self, ordinal, body, *, status, rows=None, details=None):
        a = self.data['attempts'][ordinal]
        require(a['status'] == 'RESERVED' and len(body) <= a['cap'] and
                status in ('RECEIVED','AMBIGUOUS'), 'Receipt boundary')
        pin = sha(body)
        self.put(self.root/'objects'/pin, body)
        a.update(status=status, bytes=len(body), body_sha256=pin, rows=rows)
        if details is not None:
            a['details'] = deepcopy(details)
        self.commit()

    def outcome(self, key, clock, result=None, status='FAILED'):
        require(len(self.data['revisions']) < 64, 'Revision bound')
        cell = self.data['cells'].setdefault(key, dict(current=None, last_accepted=None, latest_attempt=None))
        if cell['latest_attempt']:
            require(parse_utc(clock) > parse_utc(cell['latest_attempt']['clock']), 'Replayed/reversed revision')
        if result is not None:
            cell['current'] = deepcopy(result)
            if result['status'] == 'AVAILABLE':
                cell['last_accepted'] = deepcopy(result)
            status = result['status']
        else:
            require(status in ('FAILED','INACTIVE','HELD','NOT_QUERIED'), 'Failure status')
        cell['latest_attempt'] = dict(clock=clock, clock_kind='OFFLINE_REPLAY', status=status)
        self.data['revisions'].append(dict(key=key, clock=clock, status=status,
                                           result=deepcopy(result)))
        self.commit()

    def cell(self, key):
        return deepcopy(self.data['cells'].get(key, dict(current=None, last_accepted=None,
                                                        latest_attempt=dict(status='NOT_QUERIED', clock=None))))

    def is_range(self):
        return self.binding.get('lineage') in ('dendra-offline-range-series-1',P1)

    def attempt_limit(self):
        if not self.is_range():
            return 64
        limit = self.binding['attempt_limit']
        require(type(limit) is int and 1 <= limit <= (SEASON['attempts'] if self.p1 else 4096), 'Finite range ledger capacity')
        return limit

    def range_outcome(self, key, clock, result=None, status='UNQUERIED'):
        from .live_product import candidate_cell_v2
        from .history_acquisition.safety import digest
        require(self.is_range(), 'Range lineage required')
        sid, day = key.split('/')
        require(sid in self.binding['roster'] and int(day[:4])+(day[5:7]>='10') == self.binding['water_year'],
                'Range identity/year')
        if result is not None:
            require(result['identity'] == self.binding['roster'][sid]['identity'] and
                    result['window']['target_date'] == day, 'Exact range result key')
        previous = self.data['cells'].get(key)
        cell = candidate_cell_v2(previous,result=result,clock=clock,outcome=status)
        if self.data.get('provider',{}).get('live'):
            cell['latest_attempt']['clock_kind']='LIVE_PROVIDER'
        # Receipt clocks/ordinals change on rechecks; source evidence does not.
        evidence = None if result is None else dict(
            bodies=[p['body_sha256'] for p in result['evidence']['pages']],
            metadata=result['admission']['source_pins'],configuration=result['admission']['metadata']['configuration_sha256'])
        signature = digest(dict(outcome=cell['latest_attempt']['outcome'],evidence=evidence))
        signatures = self.data.setdefault('range_signatures',{})
        if signatures.get(key) != signature:
            require(len(self.data['revisions']) < self.attempt_limit(), 'Finite range revision capacity')
            self.data['revisions'].append(dict(key=key,signature=signature,clock=clock,result=deepcopy(result),
                                               outcome=cell['latest_attempt']['outcome']))
        signatures[key] = signature
        self.data.setdefault('range_results',{})[key] = deepcopy(result)
        self.data['cells'][key] = cell
        self.commit()
