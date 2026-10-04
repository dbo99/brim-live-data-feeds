"""Versioned exact-family native archives; no scientific/product promotion API.

Uses the existing serial provider boundary, paginator and immutable Journal.
Version 1--4 jobs and their scientific admission rules are not reinterpreted.
"""
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
from urllib.parse import parse_qsl, urlsplit

from . import local_job as legacy, native_snapshot as ns
from .journal import Journal
from .model import source_binding, PAGE_BYTES
from .observation_quality import binding as quality_binding
from .provider_adapter import CampaignAdapter, CampaignRequestSpec, MemoryResponse
from .safety import Root, Hold, decode, digest, encode, require, sha
from ..transport import DendraFetcher, FetchError, parse_utc, format_utc

VERSION = 'dendra-local-native-job-5'
MODE = 'program_native_archive_adapter'
ENVELOPE = 'dendra-native-only-envelope-1'
MAX_TASKS = 400
FIELDS = {'version', 'family', 'snapshot', 'streams', 'station_ids', 'stream_scopes',
          'science_states', 'reuse', 'sources', 'limits', 'execution_window_seconds',
          'reserve_bytes', 'root', 'enabled', 'fixture'}


def chunk_seconds(row):
    """A query-size policy, never a fabricated cadence or observed POR claim."""
    cadence = row.get('cadence_seconds')
    if cadence is None:
        require(row['science_status'] != 'SCIENCE_READY', 'Cadence-free quarantine only')
        return 30 * 86400
    require(type(cadence) in (int, float) and 0 < cadence < float('inf'),
            'Invalid source cadence')
    return min(365 * 86400, 4030 * cadence)


def plan_intervals(row, intervals):
    """Split already reviewed/subtracted intervals; never discover or fill gaps."""
    result = []
    prior = None
    for scope in intervals:
        start, end = ns.interval(scope)
        require(parse_utc(row['query_start']) <= start and (prior is None or prior <= start),
                'Invalid/overlapping planner input')
        while start < end:
            stop = min(end, start + timedelta(seconds=chunk_seconds(row)))
            require(start < stop, 'Nonadvancing bounded chunk')
            result.append(dict(start=format_utc(start), end=format_utc(stop)))
            start = stop
        prior = end
    return result


class Metrics:
    """Invocation diagnostics only; never authority or attempt accounting."""
    def __init__(self):
        self.values = {}
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()

    def add(self, name, value):
        self.values[name] = self.values.get(name, 0) + value

    @contextmanager
    def measure(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - start)

    def report(self):
        return dict(schema='dendra-native-throughput-1', scope='this_acquire_process',
                    wall_seconds=time.perf_counter()-self.started,
                    cpu_seconds=time.process_time()-self.cpu_started, **self.values)


def measured(name):
    def decorate(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            with self.metrics.measure(name):
                return method(self, *args, **kwargs)
        return call
    return decorate


class TimedResponse:
    """Delegate response behavior; measure provider body I/O, not JSON parsing."""
    def __init__(self, response, metrics, header_seconds):
        self.response, self.metrics = response, metrics
        self.provider_seconds = header_seconds

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        self.io(self.response.__enter__)
        return self

    def __exit__(self, *args):
        try:
            return self.io(self.response.__exit__, *args)
        finally:
            samples = self.metrics.values.setdefault('provider_request_seconds', [])
            if len(samples) < 1500:
                samples.append(self.provider_seconds)

    def read(self, *args):
        return self.io(self.response.read, *args)

    def io(self, operation, *args):
        start = time.perf_counter()
        try:
            return operation(*args)
        finally:
            seconds = time.perf_counter()-start
            self.provider_seconds += seconds
            self.metrics.add('provider_io_seconds', seconds)


def config(path, *, verify_reuse=False):
    path = Path(path).absolute()
    c = legacy.read(path)
    require(set(c) == FIELDS and c['version'] == VERSION and path.read_bytes() == encode(c),
            'Canonical version-5 exact configuration required')
    require(c['sources'] == legacy.sources(), 'Native job source/checkpoint changed; rebind before initialization')
    require(type(c['enabled']) is bool and set(c['family']) ==
            {'provider', 'organization_id', 'organization_name', 'subprovider_label'} and
            c['family']['provider'] == 'Dendra' and ns.ID.fullmatch(c['family']['organization_id']),
            'Exact explicit family binding')
    require(isinstance(c['streams'], list) and 0 < len(c['streams']) <= 28 and
            c['streams'] == sorted(set(c['streams'])), 'Exact sorted bounded stream roster')
    require(set(c['stream_scopes']) == set(c['science_states']) == set(c['streams']),
            'Exact per-stream scope/science roster')
    require(set(c['limits']) == {'attempts', 'bytes'} and
            type(c['limits']['attempts']) is int and 0 < c['limits']['attempts'] <= 1500 and
            type(c['limits']['bytes']) is int and 0 < c['limits']['bytes'] <= 1073741824 and
            type(c['execution_window_seconds']) is int and 0 < c['execution_window_seconds'] <= 12600 and
            type(c['reserve_bytes']) is int and c['reserve_bytes'] >= legacy.BODY,
            'Native job cumulative resource ceilings')
    root = Path(c['root'])
    require(root.is_absolute() and root == root.resolve() and root.parent.is_dir() and
            legacy.REPO/'.l01-soil-integration' in root.parents,
            'Fresh private task-owned job destination required')
    snapshot = ns.Snapshot(c['snapshot'])
    tasks = []
    for sid in c['streams']:
        r = snapshot.validate(sid, c['family'])
        require(c['science_states'][sid] == r['science_status'], 'Bound science state changed')
        scopes = c['stream_scopes'][sid]
        require(isinstance(scopes, list) and scopes, 'Explicit nonempty per-stream interval list')
        prior = None
        for scope in scopes:
            a, b = ns.interval(scope)
            require(parse_utc(r['query_start']) <= a and (prior is None or prior <= a) and
                    b <= parse_utc(snapshot.document['fixed_cutoff']) and
                    (b-a).total_seconds() <= chunk_seconds(r),
                    'Task outside exact query bound/cutoff or overlapping interval')
            tasks.append(dict(stream_id=sid, **scope))
            prior = b
    require(c['station_ids'] == sorted({snapshot.rows[s]['station_id'] for s in c['streams']}),
            'Exact station roster required')
    require(0 < len(tasks) <= MAX_TASKS and 3*len(tasks) <= c['limits']['attempts'],
            'Bounded tasks and full three-page attempt reserve required')
    if c['fixture'] is not None:
        ns.reference(c['fixture'])
    reuse = ns.reference(c['reuse'])
    require(isinstance(reuse.get('references'), list) and len(reuse['references']) <= 10000,
            'Bounded original archive index')
    selected = [e for e in reuse['references'] if e['stream_id'] in c['streams']]
    for e in selected:
        require(e['station_id'] == snapshot.rows[e['stream_id']]['station_id'] and
                e['state'] in ('complete_nonempty', 'complete_empty'), 'Exact sealed reuse identity')
        a, b = ns.interval({k: e[k] for k in ('start', 'end')})
        require(all(t['stream_id'] != e['stream_id'] or parse_utc(t['end']) <= a or
                    parse_utc(t['start']) >= b for t in tasks), 'Planned duplicate of retained sealed coverage')
    if verify_reuse:
        from .native_reuse import validate
        validate(selected, snapshot)
    return c, snapshot, tasks


def task_binding(config_path, c, snapshot, task, *, collector_sources=None):
    sid = task['stream_id']
    initial = CampaignRequestSpec(sid, task['start'], task['end'], task['start'])
    item = dict(identity=snapshot.identity(sid), start=task['start'], end=task['end'],
                native_task=dict(request=dict(method='GET', url=initial.url())))
    key = digest(item)
    policy = dict(logical_requests=3, http_attempts=3, total_bytes=3*legacy.BODY,
                  wall_seconds=300)
    b = dict(version=VERSION, mode=MODE, campaign_id='native-'+key,
             collector_sources=source_binding() if collector_sources is None else collector_sources,
             config_ref=dict(path=str(config_path),
             sha256=sha(Path(config_path).read_bytes())), configuration_sha256=digest(c),
             task=task, roster={sid:snapshot.identity(sid)}, selected_ids=[sid],
             quality_policy=quality_binding(), request_policy=policy,
             budgets=dict(logical_requests=3, attempts=3, response_bytes=3*legacy.BODY,
                          source_rows=3*2016, intervals=1, sessions=16, elapsed_ms=300000))
    require(len(encode(b))+4096 <= PAGE_BYTES, 'Native child header bound')
    return decode(encode(b)), {key:item}


def validate_binding(binding, tasks, *, inventory=None):
    c = ns.reference(binding['config_ref'])
    require(digest(c) == binding['configuration_sha256'], 'Native child config hash changed')
    checked, snapshot, plan = config(binding['config_ref']['path'])
    require(c == checked and binding['task'] in plan and
            (binding, tasks) == task_binding(binding['config_ref']['path'], c, snapshot, binding['task']),
            'Exact native snapshot/family/roster/task binding changed')


def authorize(journal, key):
    require(not journal.inspect_only and not journal.damage and journal.lock is not None and
            key in journal.tasks, 'Native writer/task required')
    validate_binding(journal.binding, journal.tasks)


def raw_envelope(journal, key, run, attempts):
    """Independent completeness check from retained raw receipts; no normalization."""
    state = journal.snapshot(); task = journal.tasks[key]
    require(run == state['intervals'][key]['runs'] and 0 < len(attempts) <= 3 and
            len(set(attempts)) == len(attempts), 'Native seal run/receipt closure')
    all_run = [a['attempt_key'] for a in state['attempts'].values()
               if a['interval_key'] == key and a['run'] == run]
    require(all_run == attempts, 'Native seal cannot omit a charged page')
    cursor, end = parse_utc(task['start']), parse_utc(task['end'])
    rows, pages = [], []
    for n, aid in enumerate(attempts):
        a = state['attempts'][aid]
        require(a['state'] == 'received' and a['status'] == 200 and len(a['objects']) == 1 and
                a['details']['error_code'] is None and parse_utc(a['cursor']) == cursor,
                'Native receipt is incomplete/failed or wrong cursor')
        raw = decode(journal.read_object(a['objects'][0]))
        from .provider_adapter import observation_shape
        observation_shape(encode(raw), task['identity']['stream_id'],
                          quality_policy=journal.binding['quality_policy'])
        previous = cursor
        for row in raw['data']:
            stamp = parse_utc(row['t'])
            require(previous <= stamp < end and row.get('datastream_id', task['identity']['stream_id']) ==
                    task['identity']['stream_id'], 'Native time/stream ordering mismatch')
            previous = stamp
        if n < len(attempts)-1:
            require(len(raw['data']) == raw['limit'] and previous > cursor, 'Nonadvancing native page')
            cursor = previous
        else:
            require(len(raw['data']) < raw['limit'], 'Full final page is not complete')
        rows.extend(raw['data'])
        pages.append(dict(attempt_key=aid, object=a['objects'][0], cursor=a['cursor'],
                          requested_at=a['reserved_at'], retrieved_at=a['at']))
    identity = task['identity']
    return dict(schema_version=ENVELOPE, product_eligible=False,
                acquisition_status='ACQUIRED_SCIENCE_READY' if identity['science_status']=='SCIENCE_READY'
                else 'NATIVE_ONLY_UNRESOLVED_METADATA', identity=identity,
                query_complete=True, requested_interval=dict(start=task['start'], end=task['end']),
                rows=rows, row_semantics='Original source rows including pagination duplicates; no conversion or grouping',
                pages=pages, config_ref=journal.binding['config_ref'],
                configuration_sha256=journal.binding['configuration_sha256'])


def seal(journal, key, run, envelope, attempts):
    authorize(journal, key)
    expected = raw_envelope(journal, key, run, attempts)
    require(envelope == expected, 'Native envelope differs from original pages')
    journal.check_budget()
    obj = journal.put_object(encode(envelope))
    result = dict(interval_key=key, run=run,
                  state='complete_nonempty' if envelope['rows'] else 'complete_empty',
                  checked_at=envelope['pages'][-1]['retrieved_at'],
                  latest_source_observation=envelope['rows'][-1]['t'] if envelope['rows'] else None,
                  attempt_keys=attempts, objects=[obj], acquisition_status=envelope['acquisition_status'],
                  product_eligible=False)
    journal._append('sealed', result)
    return result


class NativeAdapter(CampaignAdapter):
    def __init__(self, journal, metrics=None):
        self.metrics = metrics if metrics is not None else Metrics()
        require(journal.binding['mode'] == MODE and not journal.inspect_only and
                journal.lock is not None and not journal.damage, 'Writable native Journal required')
        with self.metrics.measure('validation_seconds'):
            validate_binding(journal.binding, journal.tasks)
        self._initialize(journal, None)
        self.last_dispatch_mono = None

    def _page_permission(self, spec, interval_key):
        with self.metrics.measure('validation_seconds'):
            authorize(self.journal, interval_key)

    def _execute(self, request, *, timeout, interval_key):
        with self.metrics.measure('validation_seconds'):
            authorize(self.journal, interval_key)
        self.last_dispatch_mono = self.journal.monotonic()
        local_before = sum(self.metrics.values.get(k, 0) for k in ('validation_seconds', 'pacing_seconds'))
        start = time.perf_counter()
        self.metrics.add('executor_calls', 1)
        try:
            try:
                response = self.executor(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
        finally:
            local_after = sum(self.metrics.values.get(k, 0) for k in ('validation_seconds', 'pacing_seconds'))
            seconds = max(0, time.perf_counter()-start-local_after+local_before)
            self.metrics.add('provider_io_seconds', seconds)
        return TimedResponse(response, self.metrics, seconds)

    @measured('parse_seconds')
    def _decode_response(self, body):
        return super()._decode_response(body)

    @measured('parse_seconds')
    def _observation_response(self, body, stream_id):
        return super()._observation_response(body, stream_id)

    @measured('journal_and_seal_seconds')
    def _persist(self, operation, *args, **kwargs):
        return super()._persist(operation, *args, **kwargs)

    def observations(self, key, *, recheck=False):
        require(key in self.journal.tasks and not recheck, 'Exact immutable native task required')
        saved = self.journal.completed(key)
        if saved is not None:
            return dict(cache_hit=True, envelope=saved)
        require(self.active and not any(a['interval_key']==key for a in
                self.journal.snapshot()['attempts'].values()), 'Spent native task cannot replay')
        self._traffic_guard()
        with self.metrics.measure('validation_seconds'):
            authorize(self.journal, key)
        task = self.journal.tasks[key]; sid = task['identity']['stream_id']
        run = self.journal.snapshot()['intervals'][key]['runs'] or self.journal.start_run(key)
        successful = []; self.current_interval = key
        def open_page(request, timeout):
            cursor = dict(parse_qsl(urlsplit(request.full_url).query))['time[$gte]']
            spec = CampaignRequestSpec(sid, task['start'], task['end'], cursor)
            body, unused, receipt = self.exchange(request, spec, interval_key=key, run=run)
            successful.append(receipt)
            return MemoryResponse(body)
        fetcher = DendraFetcher(opener=open_page, timeout=25, max_attempts=1, max_pages=3,
            page_size=2016, max_retry_delay=0, now_fn=lambda:parse_utc(self.journal.now()), sleep_fn=self.pause)
        try:
            fetcher.fetch_interval(sid, task['start'], task['end'])
            envelope = raw_envelope(self.journal, key, run, successful)
            self._persist(self.journal.seal, key, run, envelope, successful)
            return dict(cache_hit=False, envelope=envelope)
        except Exception:
            if not self.journal.damage and not self.halted:
                self._persist(self.journal.hold, key, 'transport_or_parse')
            raise
        finally:
            self.current_interval = None


class ProgramJob(legacy.Job):
    def __init__(self, c, snapshot, tasks, *, clock, fixture=None, config_path):
        super().__init__(c, snapshot, clock=clock, fixture=fixture)
        self.tasks = tasks
        self.config_path = Path(config_path)
        self.metrics = Metrics()
        # Clock is owned by this job. Include both adapter and cross-child waits.
        self._clock_wait = self.clock.wait
        self.clock.wait = self.wait
        self.acquiring = False
        # Only small semantic verification tokens, not decoded rows or counters.
        self.verified_seals = set()

    def pins(self):
        return dict(configuration_sha256=digest(self.c), snapshot=self.c['snapshot'],
                    reuse=self.c['reuse'], plan_sha256=digest(self.tasks), sources=self.c['sources'])

    def series_catalog(self, reviews=None):
        return dict(version=VERSION, snapshot=self.c['snapshot'], family=self.c['family'],
                    streams={s:self.inventory.rows[s] for s in self.c['streams']},
                    product_eligible=False, promotion='Separate reviewed mapping and product adapter required')

    def asset_map(self):
        return dict(reuse=self.c['reuse'], metadata='catalog.json',
                    new_assets_reference='status.json/accounting/assets', product_eligible=False)

    @contextmanager
    def open(self, create=False):
        if create:
            config(self.config_path, verify_reuse=True)
            require(not self.root.exists(), 'Never recreate existing native job/accounting')
            self.root.mkdir(mode=0o700)
            with Root(self.root) as fs:fs.write_new('writer.lock', b'', 0)
        with Root(self.root) as fs:
            self.fs = fs; fd = fs.lock_fd('writer.lock')
            try:
                try:fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:raise Hold('Native job already has a writer') from exc
                self.lock = fd
                if create:
                    self.storage(self.c['limits']['bytes'])
                    self.put('job.json', dict(version=VERSION, configuration=self.c, job_id=digest(self.c)))
                    self.put('catalog.json', self.series_catalog())
                    self.put('plan.json', dict(tasks=self.tasks, pins=self.pins()))
                    self.put('scope-binding.json', self.pins())
                    self.put('asset-map.json', self.asset_map())
                self.verify_scope()
                yield self
            finally:
                self.lock=None; self.fs=None; os.close(fd)

    @measured('validation_seconds')
    def verify_scope(self):
        c, unused, tasks = config(self.config_path)
        require(c==self.c and tasks==self.tasks and self.get('job.json')==
                dict(version=VERSION, configuration=c, job_id=digest(c)) and
                self.get('scope-binding.json')==self.pins() and
                self.get('plan.json')==dict(tasks=tasks,pins=self.pins()) and
                self.get('catalog.json')==self.series_catalog() and
                self.get('asset-map.json')==self.asset_map(), 'Native job snapshot/config/plan mutation')
        return dict(outcome='VALIDATED_NATIVE_SCOPE_NO_DISPATCH', job_id=digest(c), streams=len(c['streams']),
                    tasks=len(tasks), window=self.execution_window(), science_states=c['science_states'])

    def child(self, root, cid):
        saved = legacy.read(Path(root)/'prepared.json')
        return Journal(root,saved['binding'],saved['tasks'],inventory=self.inventory,
                       inspect_only=True,now=self.clock.now,monotonic=self.clock.monotonic)

    def window_records(self):
        if not self.has('continuation-windows'):return []
        names=self.fs.list('continuation-windows');require(0<len(names)<=16 and
            names==[f'{i:04d}.json' for i in range(1,len(names)+1)],'Native continuation sequence')
        prior=self.get('window.json');records=[]
        for n in names:
            r=self.get('continuation-windows/'+n)
            require(set(r)=={'ordinal','pins','previous_sha256','opened_at','deadline','accounting_before','record_sha256'} and
                    r['record_sha256']==digest({k:v for k,v in r.items() if k!='record_sha256'}) and
                    r['ordinal']==len(records)+1 and r['pins']==self.pins() and
                    r['previous_sha256']==digest(prior) and
                    parse_utc(prior['deadline'])<=parse_utc(r['opened_at']) and
                    parse_utc(r['deadline'])==parse_utc(r['opened_at'])+timedelta(seconds=self.c['execution_window_seconds']),
                    'Native continuation changed original bindings/window')
            records.append(r);prior=r
        return records

    @measured('accounting_seconds')
    def accounting(self, *, full=False):
        counts=dict(attempts=0,metadata_attempts=0,bytes=0,sealed=0,covered_empty=0)
        spent=[];assets=[];first=None;last=None;stream_holds=[];global_holds=[];reservations=[]
        roots=sorted((self.root/'history').iterdir()) if self.has('history') else []
        require(all(p.name.isdecimal() and 0<=int(p.name)<len(self.tasks) for p in roots),'Foreign native child')
        # Source bytes are hashed once per complete accounting pass, not once
        # per child. Every pass still rebuilds counters from original events.
        current_sources = source_binding()
        for root in roots:
            saved=legacy.read(root/'prepared.json');b=saved['binding'];tasks=saved['tasks']
            require((b,tasks)==task_binding(self.config_path,self.c,self.inventory,self.tasks[int(root.name)],
                                            collector_sources=current_sources),
                    'Native child differs from bound plan')
            with self.child(root,b['campaign_id']) as j:
                j.verify_records();state=j.snapshot();require(not state['damage'],'Damaged native accounting')
                counts['attempts']+=state['counters']['attempts'];counts['bytes']+=state['counters']['response_bytes']
                for a in state['attempts'].values():
                    t=parse_utc(a['reserved_at']);reservations.append(t);first=min(first,t) if first else t
                    t=parse_utc(a['at']);last=max(last,t) if last else t
                for key,v in state['intervals'].items():
                    attempts=[a for a in state['attempts'].values() if a['interval_key']==key]
                    if v['complete']:
                        complete=v['complete']
                        token=(str(root), j.header_sha, digest(j.events))
                        # Journal open rehashes ALL original objects, including
                        # the sealed envelope. verify_records checks physical
                        # receipts/anchors and closure before any cache hit.
                        # A changed chain forces full semantic reconstruction.
                        if full or token not in self.verified_seals:
                            env=j.completed(key)
                            require(env==raw_envelope(j,key,complete['run'],complete['attempt_keys']) and
                                    complete['state']==('complete_nonempty' if env['rows'] else 'complete_empty') and
                                    complete['acquisition_status']==env['acquisition_status'] and
                                    complete['product_eligible'] is False,
                                    'Native sealed envelope differs from original receipts')
                            if token not in self.verified_seals and len(self.verified_seals) >= MAX_TASKS:
                                self.verified_seals.clear()
                            self.verified_seals.add(token)
                            self.metrics.add('seal_semantic_validations', 1)
                        else:
                            self.metrics.add('seal_semantic_cache_hits', 1)
                        counts['sealed']+=1;counts['covered_empty']+=v['state']=='complete_empty'
                        assets.append(dict(root=str(root),campaign_id=b['campaign_id'],task_id=key,seal=v['complete'],
                                           series_metadata_reference=dict(path='catalog.json',stream_id=tasks[key]['identity']['stream_id'])))
                    elif attempts:
                        spent.append(key)
                        # An explicit terminal access response isolates only that stream.
                        if all(a['state']=='failure' and a.get('status') in (401,403,404,410) and
                               a.get('details',{}).get('error_code')=='http' for a in attempts):
                            stream_holds.append(tasks[key]['identity']['stream_id'])
                        else:global_holds.append(key)
        if first:
            w=dict(first_attempt_at=format_utc(first),deadline=format_utc(first+timedelta(seconds=self.c['execution_window_seconds'])))
            if self.has('window.json'):require(self.get('window.json')==w,'Original native execution window changed')
            else:self.put('window.json',w)
        else:require(not self.has('window.json'),'Missing original reserved attempts')
        self.last_dispatch=last
        if self.clock.fake and last and parse_utc(self.clock.now())<last:self.clock.value=last
        result=dict(**counts,spent_unsealed=spent,assets=assets,held_streams=sorted(set(stream_holds)),
                    global_holds=global_holds,window=self.execution_window())
        if self.has('status.json'):
            old=self.get('status.json')['accounting']
            require(all(counts[k]>=old[k] for k in counts),'Previously recorded native charges disappeared')
            require({a['task_id'] for a in old['assets']}<={a['task_id'] for a in assets},'Native seals disappeared')
        for w in self.window_records():
            require(all(counts[k]>=w['accounting_before'][k] for k in counts),'Continuation refunded accounting')
            times=[t for t in reservations if parse_utc(w['opened_at'])<=t<parse_utc(w['deadline'])]
            marker=f"continuation-first/{w['ordinal']:04d}.json"
            expected=dict(window_sha256=digest(w),first_attempt_at=format_utc(min(times)),deadline=w['deadline']) if times else None
            if self.has(marker):require(self.get(marker)==expected,'Continuation reservation history changed')
            elif expected:self.put(marker,expected)
        windows=([self.get('window.json')]+self.window_records()) if first else []
        require(all(any(parse_utc(w.get('opened_at',w.get('first_attempt_at')))<=t<parse_utc(w['deadline'])
                        for w in windows) for t in reservations),'Attempt outside original/continued windows')
        return result

    def capacity(self, attempts, body_bytes, metadata=0):
        a=self.accounting();require(not a['global_holds'],'Spent/ambiguous native task requires review')
        require(a['attempts']+attempts<=self.c['limits']['attempts'] and
                a['bytes']+body_bytes<=self.c['limits']['bytes'],'Cumulative native budget exhausted')
        w=self.execution_window()
        require(w is None or parse_utc(self.clock.now())<parse_utc(w['deadline']),'Native execution window exhausted')
        self.storage(body_bytes);return a

    def dispatch(self, request, timeout):
        self.verify_scope()
        q=dict(parse_qsl(urlsplit(request.full_url).query));sid=q.get('datastream_id')
        require(urlsplit(request.full_url).path=='/v2/datapoints' and sid in self.c['streams'] and
                q.get('$limit')=='2016' and any(t['stream_id']==sid and t['end']==q.get('time[$lt]') and
                parse_utc(t['start'])<=parse_utc(q['time[$gte]'])<parse_utc(t['end']) for t in self.tasks),
                'Native dispatch outside exact planned observation task')
        # Base method owns reservation-derived first window and cross-child pacing.
        # Its legacy continuation markers are deliberately absent from this mode.
        records=self.window_records()
        if records:
            return self._continued_dispatch(request,timeout)
        return super().dispatch(request,timeout)

    def _continued_dispatch(self, request, timeout):
        require(self.fixture is not None or self.c['enabled'],'Native job disabled')
        w=self.execution_window();remaining=(parse_utc(w['deadline'])-parse_utc(self.clock.now())).total_seconds()
        timeout=min(timeout,remaining);require(timeout>0,'Native continuation expired')
        if self.last_dispatch:
            delay=max(0,1-(parse_utc(self.clock.now())-self.last_dispatch).total_seconds())
            require(delay<timeout,'Native pacing exceeds window');self.clock.wait(delay);timeout-=delay
        self.last_dispatch=parse_utc(self.clock.now())
        if self.fixture is not None:
            q=dict(parse_qsl(urlsplit(request.full_url).query));v=self.fixture['history'][q['time[$gte]']]
            return legacy.Reply(v.get('body',v),v.get('status',200))
        return legacy.anonymous_executor(format_utc(parse_utc(self.clock.now())+timedelta(seconds=timeout)))(request,timeout=timeout)

    def continue_window(self):
        self.verify_scope();a=self.accounting();prior=self.execution_window();at=self.clock.now()
        require(prior is not None and parse_utc(at)>=parse_utc(prior['deadline']) and
                not a['global_holds'] and self.unattempted(a),'Expired clean native window with executable tasks required')
        records=self.window_records();require(len(records)<16,'Native continuation count exhausted')
        require(a['attempts']+3<=self.c['limits']['attempts'] and a['bytes']+3*legacy.BODY<=self.c['limits']['bytes'],
                'No cumulative continuation capacity')
        w=dict(ordinal=len(records)+1,pins=self.pins(),previous_sha256=digest(prior),opened_at=at,
               deadline=format_utc(parse_utc(at)+timedelta(seconds=self.c['execution_window_seconds'])),
               accounting_before={k:a[k] for k in ('attempts','metadata_attempts','bytes','sealed','covered_empty')})
        w['record_sha256']=digest(w)
        self.put(f"continuation-windows/{w['ordinal']:04d}.json",w)
        return dict(outcome='CONTINUATION_WINDOW_OPENED_NO_DISPATCH',window=w)

    def unattempted(self, accounting):
        done={a['root'] for a in accounting['assets']}
        return [n for n,t in enumerate(self.tasks) if str(self.root/'history'/str(n)) not in done and
                t['stream_id'] not in accounting['held_streams']]

    def acquire(self):
        self.acquiring = True
        self.verify_scope();a=self.accounting()
        require(not a['global_holds'],'Spent/ambiguous native task requires review')
        for n in self.unattempted(a):
            t=self.tasks[n]
            root=self.root/'history'/str(n)
            if t['stream_id'] in a['held_streams']:continue
            if self.stop_requested():break
            window=self.execution_window()
            # Leave enough room for the complete bounded child before reserving
            # any of its attempts. Continuation never refunds a spent child.
            if window and (parse_utc(window['deadline'])-parse_utc(self.clock.now())).total_seconds()<301:break
            a=self.capacity(3,3*legacy.BODY)
            binding,tasks=task_binding(self.config_path,self.c,self.inventory,t)
            create=not root.exists()
            if create:
                root.mkdir(mode=0o700,parents=True)
                with Root(root) as fs:fs.write_new('prepared.json',encode(dict(binding=binding,tasks=tasks)),8*legacy.BODY)
            else:
                with self.child(root,binding['campaign_id']) as prior:
                    require(not prior.snapshot()['attempts'],'Spent native task cannot replay')
            window=self.execution_window();end=parse_utc(self.clock.now())+timedelta(seconds=300)
            if window:end=min(end,parse_utc(window['deadline']))
            try:
                with Journal(root,binding,tasks,create=create,inventory=self.inventory,
                             now=self.clock.now,monotonic=self.clock.monotonic) as j:
                    NativeAdapter(j,self.metrics).run(executor=self.dispatch,wait=self.wait,window_end=format_utc(end))
            except (Hold, FetchError, urllib.error.HTTPError):
                a=self.accounting()
                if a['global_holds'] or t['stream_id'] not in a['held_streams']:
                    raise
            finally:
                a=self.accounting();self.status(accounting=a)
            require(not a['global_holds'],'Native acquisition held; preserve charged evidence')
        return self.status()

    def wait(self, seconds):
        self.metrics.add('configured_sleep_seconds', seconds)
        with self.metrics.measure('pacing_seconds'):
            self._clock_wait(seconds)

    def status(self, *, accounting=None):
        a=self.accounting() if accounting is None else accounting
        out=dict(outcome='COMPLETE_FOR_DECLARED_SCOPE' if a['sealed']==len(self.tasks)
            else 'HOLD' if a['spent_unsealed'] else 'PREPARED_DISABLED' if not self.c['enabled'] else 'READY_OR_PARTIAL',
            job_id=digest(self.c),planned_tasks=len(self.tasks),remaining_tasks=len(self.tasks)-a['sealed'],
            accounting=a,product_eligible=False)
        prior = self.get('status.json') if self.has('status.json') else {}
        if self.acquiring:
            out['throughput'] = self.metrics.report()
        elif 'throughput' in prior:
            out['throughput'] = prior['throughput']
        self.summary(out);return out


def main(args):
    require(args.mode in ('inspect','prepare','validate-scope','acquire','resume','status','stop','continue-window') and
            not any(getattr(args,k,None) for k in ('review','output_root','donor_config','request','final_review',
                                                 'recovery','output_config','job_root','enable')),
            'Native version 5 has no automatic metadata/science review or promotion command')
    path=Path(args.config).absolute();c,snapshot,tasks=config(path,verify_reuse=args.mode in
        ('inspect','prepare','acquire','resume','continue-window'))
    fixture=ns.reference(c['fixture']) if c['fixture'] else None
    if fixture is not None:
        def deny(event,unused):
            if event.startswith('socket.'):raise Hold('Offline native fixture denies network')
        sys.addaudithook(deny)
    clock=legacy.Clock(fixture,args.offline_now)
    if args.mode in ('acquire','resume','continue-window'):
        require(fixture is not None or c['enabled'],'Native job disabled')
        require(args.mode=='continue-window' or fixture is not None or args.allow_provider,'Explicit provider permission')
        if fixture is None:
            env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
            state=subprocess.check_output(['git','status','--short'],cwd=legacy.REPO,env=env,text=True).strip()
            require(state in ('','?? .l01-soil-integration/'),'Commit reviewed source before live launch; no dirty checkout')
            subprocess.check_call(['git','ls-files','--error-unmatch',
                'scripts/dendra/history_acquisition/native_program.py','scripts/dendra/history_acquisition/native_snapshot.py',
                'scripts/dendra/history_acquisition/native_reuse.py'],
                cwd=legacy.REPO,env=env,stdout=subprocess.DEVNULL)
    else:require(not args.allow_provider,'Offline native command cannot permit provider')
    job=ProgramJob(c,snapshot,tasks,clock=clock,fixture=fixture,config_path=path)
    if args.mode=='inspect':
        return dict(outcome='VALIDATED_NATIVE_CONFIG_NO_DISPATCH',tasks=len(tasks),streams=len(c['streams']),
                    snapshot=c['snapshot'],source=c['sources'],enabled=c['enabled'],execution_window=None)
    if args.mode=='stop':
        # Stop signals must be writable while the acquisition owns writer.lock.
        with Root(job.root) as fs:
            require(decode(fs.read('job.json',ns.BOUND))==
                    dict(version=VERSION,configuration=c,job_id=digest(c)), 'Exact stop job required')
            value=dict(at=clock.now(),job_id=digest(c))
            fs.write_new('stops/'+digest(value)+'.json',encode(value),ns.BOUND)
        return dict(outcome='STOP_REQUESTED_NO_DISPATCH',job_id=digest(c))
    with job.open(create=args.mode=='prepare'):
        if args.mode in ('prepare','status'):return job.status()
        if args.mode=='validate-scope':return job.verify_scope()
        if args.mode=='continue-window':return job.continue_window()
        if args.mode=='resume':job.acknowledge_stop()
        return job.acquire()
