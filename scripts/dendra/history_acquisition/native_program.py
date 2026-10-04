"""Versioned exact-family native archives; no scientific/product promotion API.

Uses the existing serial provider boundary, paginator and immutable Journal.
Version 1--4 jobs and their scientific admission rules are not reinterpreted.
"""
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps, lru_cache
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
from ..transport import DendraFetcher, FetchError, PaginationError, parse_utc, format_utc

VERSION = 'dendra-local-native-job-5'
MODE = 'program_native_archive_adapter'
ENVELOPE = 'dendra-native-only-envelope-1'
MAX_TASKS = 400
FIELDS = {'version', 'family', 'snapshot', 'streams', 'station_ids', 'stream_scopes',
          'science_states', 'reuse', 'sources', 'limits', 'execution_window_seconds',
          'reserve_bytes', 'root', 'enabled', 'fixture'}


def legacy_chunk_seconds(row):
    """Read old immutable plans; nominal cadence is not new planning authority."""
    cadence = row.get('cadence_seconds')
    if cadence is None:
        require(row['science_status'] != 'SCIENCE_READY', 'Cadence-free quarantine only')
        return 30 * 86400
    require(type(cadence) in (int, float) and 0 < cadence < float('inf'),
            'Invalid source cadence')
    return min(365 * 86400, 4030 * cadence)


def chunk_seconds(row, density=None):
    """Unknown historical density stays bounded, including science-ready rows.

    A density is an independently verified historical planning observation, not
    a scientific cadence claim or a promise that unseen history is no denser.
    The page ceiling and explicit spent-prefix recovery remain mandatory.
    """
    nominal = row.get('cadence_seconds')
    require(nominal is None or type(nominal) in (int,float) and 0<nominal<float('inf'),
            'Invalid nominal source cadence')
    if density is None:
        return 30 * 86400
    seconds = density['seconds']
    require(type(seconds) in (int, float) and 0 < seconds < float('inf'),
            'Invalid historical planning density')
    return min(365 * 86400, 4030 * seconds)


def plan_intervals(row, intervals, density=None):
    """Split already reviewed/subtracted intervals; never discover or fill gaps."""
    result = []
    prior = None
    for scope in intervals:
        start, end = ns.interval(scope)
        require(parse_utc(row['query_start']) <= start and (prior is None or prior <= start),
                'Invalid/overlapping planner input')
        while start < end:
            stop = min(end, start + timedelta(seconds=chunk_seconds(row, density)))
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


def config(path, *, verify_reuse=False, expected_sources=None):
    path = Path(path).absolute()
    c = legacy.read(path)
    require(set(c) in (FIELDS, FIELDS | {'planning'}) and c['version'] == VERSION and path.read_bytes() == encode(c),
            'Canonical version-5 exact configuration required')
    require(c['sources'] == (legacy.sources() if expected_sources is None else expected_sources),
            'Native job source/checkpoint changed; rebind before initialization')
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
    planning = ns.reference(c['planning']) if 'planning' in c else None
    if planning is not None:
        require(planning.get('version') == 'dendra-historical-density-1' and
                set(planning) == {'version','streams','decisions','conditional_recoveries'} and
                set(planning['streams']) == set(planning['decisions']) == set(c['streams']),
                'Exact historical density roster')
        require(isinstance(planning['conditional_recoveries'],list) and
                len(planning['conditional_recoveries'])<=28, 'Bounded conditional recovery dependencies')
        if verify_reuse:
            for dependency in planning['conditional_recoveries']:
                require(set(dependency)=={'config','request'},'Exact conditional recovery references')
                ns.reference(dependency['config']);ns.reference(dependency['request'])
                recovered=PaginationRecovery(dependency['config']['path'],dependency['request']['path']).status()
                require(recovered['recovered_task'] is not None,
                        'Conditional recovery has not sealed; replacement production cannot initialize')
    for sid in c['streams']:
        r = snapshot.validate(sid, c['family'])
        require(c['science_states'][sid] == r['science_status'], 'Bound science state changed')
        scopes = c['stream_scopes'][sid]
        if planning is not None:
            from .native_reuse import historical_density
            density = planning['decisions'][sid]
            require(density is None or set(density)=={'seconds','basis','evidence_sha256','maximum_unseen_density_proven'} and
                    density['evidence_sha256']==digest(planning['streams'][sid]) and
                    density['maximum_unseen_density_proven'] is False and density['basis'] in
                    ('HISTORICAL_CONFIGURATION_EPOCHS','VERIFIED_EXACT_STREAM_OBSERVATIONS','EXACT_HISTORICAL_RECORD'),
                    'Historical planning decision/evidence binding changed')
            # Full historical receipts are checked at every prepare/acquire
            # invocation, like retained reuse, not reparsed for every HTTP page.
            if verify_reuse:
                require(density==historical_density(r,planning['streams'][sid],snapshot.document['fixed_cutoff']),
                        'Historical density decision differs from retained evidence')
            bound = chunk_seconds(r, density)
        else:
            bound = legacy_chunk_seconds(r)
        require(isinstance(scopes, list) and scopes, 'Explicit nonempty per-stream interval list')
        prior = None
        for scope in scopes:
            a, b = ns.interval(scope)
            require(parse_utc(r['query_start']) <= a and (prior is None or prior <= a) and
                    b <= parse_utc(snapshot.document['fixed_cutoff']) and
                    (b-a).total_seconds() <= bound,
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
    if 'pagination_recovery' in binding:
        validate_recovery_binding(binding, tasks)
        return
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
    recovery = journal.binding.get('pagination_recovery')
    ceiling = journal.binding['budgets']['attempts'] if recovery else 3
    require(run == state['intervals'][key]['runs'] and 0 < len(attempts) <= ceiling and
            len(set(attempts)) == len(attempts), 'Native seal run/receipt closure')
    all_run = [a['attempt_key'] for a in state['attempts'].values()
               if a['interval_key'] == key and a['run'] == run]
    require(all_run == attempts, 'Native seal cannot omit a charged page')
    prefix = recovery_prefix(recovery) if recovery else None
    cursor, end = parse_utc(prefix['cursor'] if prefix else task['start']), parse_utc(task['end'])
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
    result = dict(schema_version=ENVELOPE, product_eligible=False,
                acquisition_status='ACQUIRED_SCIENCE_READY' if identity['science_status']=='SCIENCE_READY'
                else 'NATIVE_ONLY_UNRESOLVED_METADATA', identity=identity,
                query_complete=True, requested_interval=dict(start=task['start'], end=task['end']),
                rows=rows, row_semantics='Original source rows including pagination duplicates; no conversion or grouping',
                pages=pages, config_ref=journal.binding['config_ref'],
                configuration_sha256=journal.binding['configuration_sha256'])
    if prefix:
        return recovered_envelope(result, prefix, recovery)
    return result


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

    def _validate_dispatch(self, request, spec, interval_key):
        recovery = self.journal.binding.get('pagination_recovery')
        if recovery is None:
            return super()._validate_dispatch(request, spec, interval_key)
        from .provider_adapter import validate_request
        require(type(spec) is CampaignRequestSpec and interval_key in self.journal.tasks,
                'Exact recovery observation specification required')
        task = self.journal.tasks[interval_key]
        require(spec.selected_stream == task['identity']['stream_id'] and
                (spec.start,spec.end) == (task['start'],task['end']), 'Recovery request stream/interval changed')
        validate_request(request,spec)
        attempts = list(self.journal.snapshot()['attempts'].values())
        require(len(attempts) < self.journal.binding['budgets']['attempts'] and
                all(a['cursor'] != spec.cursor for a in attempts), 'Recovery page ceiling/replay refused')
        expected = recovery['prefix']['cursor']
        if attempts:
            previous = attempts[-1]
            require(previous['state']=='received' and previous['status']==200 and len(previous['objects'])==1,
                    'Recovery previous page is not admissible')
            raw = decode(self.journal.read_object(previous['objects'][0]))
            require(len(raw['data'])==raw['limit'] and raw['data'], 'Recovery previous page was complete')
            expected = format_utc(raw['data'][-1]['t'])
            require(parse_utc(expected)>parse_utc(previous['cursor']), 'Recovery cursor cannot advance')
        require(spec.cursor==expected, 'Recovery cursor differs from verified source boundary')
        self._traffic_guard()
        self._spacing()

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
        recovery = self.journal.binding.get('pagination_recovery')
        cursor = recovery['prefix']['cursor'] if recovery else task['start']
        ceiling = self.journal.binding['budgets']['attempts'] if recovery else 3
        fetcher = DendraFetcher(opener=open_page, timeout=25, max_attempts=1, max_pages=ceiling,
            page_size=2016, max_retry_delay=0, now_fn=lambda:parse_utc(self.journal.now()), sleep_fn=self.pause)
        try:
            fetcher.fetch_interval(sid, cursor, task['end'])
            envelope = raw_envelope(self.journal, key, run, successful)
            self._persist(self.journal.seal, key, run, envelope, successful)
            return dict(cache_hit=False, envelope=envelope)
        except Exception as exc:
            if not self.journal.damage and not self.halted:
                self._persist(self.journal.hold, key, 'transport_or_parse')
            if isinstance(exc, PaginationError) and str(exc)=='Page ceiling reached before interval completion.':
                pages=exc.details['completed_pages']
                self.failure=dict(code=RECOVERY_REASON,task_id=key,stream_id=sid,
                    requested_interval=dict(start=task['start'],end=task['end']),pages_received=len(pages),
                    rows_received=sum(p['row_count'] for p in pages),last_cursor=pages[-1]['last_source_t'],
                    recovery_eligible=recovery is None,provider_retry=False)
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
        require(not self.has('pagination-recovery'), 'Old unattempted scope superseded; recovery only')
        require(self.fixture is not None or 'planning' in self.c,
                'Historical density review required before new native acquisition')
        if 'planning' in self.c:config(self.config_path,verify_reuse=True)
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
            if a['global_holds']:
                with self.child(root,binding['campaign_id']) as held:
                    failure = pagination_failure(held, next(iter(tasks)))
                if failure:
                    name='pagination-failures/'+next(iter(tasks))+'.json'
                    if not self.has(name):self.put(name, failure)
                    raise Hold(encode(failure).decode().strip())
                raise Hold('Native acquisition held; preserve charged evidence')
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


RECOVERY_VERSION = 'dendra-native-pagination-recovery-1'
RECOVERED_ENVELOPE = 'dendra-native-recovered-envelope-1'
RECOVERY_REASON = 'PAGINATION_PAGE_CEILING_INCOMPLETE'
RECOVERY_DIRECTORY = 'pagination-recovery'


def prefix_evidence(journal, key):
    """Recognize only three successful, full, advancing original native pages.

    This is reconstruction from immutable receipts, not trust in the generic
    transport_or_parse label. Re-run the unchanged paginator against saved bytes.
    """
    journal.verify_records()
    state = journal.snapshot()
    require(not state['damage'] and journal.binding['mode'] == MODE and
            'pagination_recovery' not in journal.binding and len(journal.tasks) == 1 and
            key in journal.tasks and state['intervals'][key]['complete'] is None and
            state['intervals'][key]['state'] == 'held' and
            state['intervals'][key]['runs'] == 1 and
            len(state['attempts']) == 3 and state['counters']['attempts'] == 3,
            'Not an original three-page native HOLD')
    task = journal.tasks[key]
    cursor = parse_utc(task['start']); end = parse_utc(task['end'])
    pages, bodies, rows = [], [], []
    from .provider_adapter import observation_shape
    for aid, a in state['attempts'].items():
        require(a['state'] == 'received' and a['status'] == 200 and
                a['interval_key'] == key and a['run'] == 1 and a['ordinal'] == 1 and
                len(a['objects']) == 1 and a['details']['error_code'] is None and
                parse_utc(a['cursor']) == cursor, 'Invalid retained prefix receipt/cursor')
        body = journal.read_object(a['objects'][0])
        require(sha(body) == a['response_sha256'] and len(body) == a['response_bytes'],
                'Retained prefix body/byte mismatch')
        raw = observation_shape(body, task['identity']['stream_id'],
                                quality_policy=journal.binding['quality_policy'])
        require(len(raw['data']) == raw['limit'] == 2016 and a['source_rows'] == 2016,
                'Retained prefix must consist of full 2016-row pages')
        previous = cursor
        for row in raw['data']:
            at = parse_utc(row['t'])
            require(previous <= at < end, 'Retained prefix time ordering/bounds')
            previous = at
        require(previous > cursor, 'Retained prefix cannot advance')
        cursor = previous
        pages.append(dict(attempt_key=aid, object=a['objects'][0], cursor=a['cursor'],
                          requested_at=a['reserved_at'], retrieved_at=a['at']))
        bodies.append(body); rows.extend(raw['data'])
    require([e['kind'] for e in journal.events] ==
            ['session','run','reserved','started','received','reserved','started','received',
             'reserved','started','received','held'], 'Unrecognized retained prefix event history')
    saved = iter(bodies)
    def replay(request, timeout):
        return MemoryResponse(next(saved))
    try:
        DendraFetcher(opener=replay, max_attempts=1, max_pages=3, page_size=2016).fetch_interval(
            task['identity']['stream_id'], task['start'], task['end'])
    except PaginationError as exc:
        require(str(exc) == 'Page ceiling reached before interval completion.' and
                len(exc.details['completed_pages']) == 3, 'Different pagination failure')
    else:
        raise Hold('Retained prefix already proves completion')
    return dict(root=None,
                campaign_id=journal.binding['campaign_id'], task_id=key, task=task,
                header_sha256=journal.header_sha, source_fingerprint=digest(journal.binding['collector_sources']),
                last_anchor_sha256=journal.events[-1]['record_sha256'], pages=pages,
                cursor=format_utc(cursor), attempts=3, bytes=sum(len(b) for b in bodies), rows=rows)


def pagination_failure(journal, key):
    try:
        prefix = prefix_evidence(journal, key)
    except (Hold, ValueError, KeyError, TypeError):
        return None
    return dict(code=RECOVERY_REASON, task_id=key, stream_id=prefix['task']['identity']['stream_id'],
                requested_interval={k:prefix['task'][k] for k in ('start','end')},
                pages_received=3, rows_received=len(prefix['rows']), last_cursor=prefix['cursor'],
                recovery_eligible=True, provider_retry=False)


def prefix_descriptor(root, key):
    saved = legacy.read(Path(root)/'prepared.json')
    with Journal(root, saved['binding'], saved['tasks'], inspect_only=True) as j:
        result = prefix_evidence(j, key)
    result['root'] = str(root)
    result.pop('rows')
    return result


def recovery_prefix(recovery):
    """Read-only lineage check, also usable by future historical archive readers."""
    descriptor = recovery['prefix']
    root = Path(descriptor['root'])
    saved = legacy.read(root/'prepared.json')
    request=ns.reference(recovery['request']);transition=ns.reference(recovery['transition'])
    require(request['version']==RECOVERY_VERSION and request['prefix']==descriptor and
            transition['request']==recovery['request'] and transition['prefix']==descriptor and
            transition['original_sources']==request['original_sources'] and
            transition['execution_sources']==request['execution_sources'] and
            transition['record_sha256']==digest({k:v for k,v in transition.items() if k!='record_sha256'}) and
            saved['binding']['config_ref']==request['config'] and
            digest(ns.reference(request['config']))==saved['binding']['configuration_sha256'],
            'Recovery lineage/config/transition changed')
    with Journal(root, saved['binding'], saved['tasks'], inspect_only=True) as j:
        prefix = prefix_evidence(j, descriptor['task_id'])
    prefix['root'] = str(root)
    require({k:v for k,v in prefix.items() if k != 'rows'} == descriptor,
            'Retained recovery prefix changed')
    return prefix


def recovered_envelope(tail, prefix, recovery):
    """Keep original bodies; deduplicate exact source copies with copy lineage.

    Conflicting values/flags remain separate rows. Nothing here promotes native
    quarantine to an interpreted product or silently chooses a conflict winner.
    """
    require(tail['identity']==prefix['task']['identity'] and
            tail['requested_interval']=={k:prefix['task'][k] for k in ('start','end')},
            'Recovered tail belongs to another exact stream/interval')
    require(parse_utc(tail['pages'][0]['requested_at'])>=parse_utc(prefix['pages'][-1]['retrieved_at']),
            'Recovered continuation precedes its retained prefix')
    rows, seen, copies = [], {}, []
    for n, row in enumerate(prefix['rows'] + tail['rows']):
        identity = dict(row, t=format_utc(row['t']))
        key = digest(identity)
        if key in seen:
            copies.append(dict(source_row_index=n, retained_row_index=seen[key]))
        else:
            seen[key] = len(rows); rows.append(row)
    return dict(tail, schema_version=RECOVERED_ENVELOPE, rows=rows,
                row_semantics='Exact source copies deduplicated; conflicting values/flags retained; original bodies unchanged',
                prefix=recovery['prefix'], transition=recovery['transition'],
                duplicate_copies=copies, raw_source_row_count=len(prefix['rows'])+len(tail['rows']))


@lru_cache(maxsize=8)
def committed_source_files(head):
    """Local Git objects only; never refresh refs or contact a remote."""
    require(isinstance(head, str) and len(head) == 40 and all(c in '0123456789abcdef' for c in head),
            'Exact original source commit required')
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1')
    def git(*args):
        try:
            return subprocess.check_output(['git', *args], cwd=legacy.REPO, env=env,stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as exc:
            raise Hold('Original source commit unavailable in local Git objects') from exc
    files = {name:sha(git('show', head+':scripts/dendra/'+name)) for name in source_binding()}
    return encode(dict(files=files, sources=dict(checkpoint=dict(head=head,
        tree=git('rev-parse', head+'^{tree}').decode().strip()), collector=digest(files),
        r_entrypoint_sha256=sha(git('show', head+':scripts/dendra/acquire_native.R')))))


def original_state(config_path, *, require_prefix=True):
    """Inspect the frozen original job without invoking writable accounting."""
    c = legacy.read(config_path)
    if c['sources'] == legacy.sources():
        captured = source_binding()
    else:
        historical = decode(committed_source_files(c['sources']['checkpoint']['head']))
        require(c['sources'] == historical['sources'], 'Original source does not match local Git objects')
        captured = historical['files']
    c, snapshot, tasks = config(config_path, expected_sources=c['sources'])
    job = ProgramJob(c, snapshot, tasks, clock=legacy.Clock(), config_path=config_path)
    root = Path(c['root'])
    pins = job.pins()
    require(legacy.read(root/'job.json') == dict(version=VERSION, configuration=c, job_id=digest(c)) and
            legacy.read(root/'plan.json') == dict(tasks=tasks,pins=pins) and
            legacy.read(root/'scope-binding.json') == pins and
            legacy.read(root/'catalog.json') == job.series_catalog() and
            legacy.read(root/'asset-map.json') == job.asset_map(), 'Original job binding changed')
    counts = dict(attempts=0, bytes=0, sealed=0, metadata_attempts=0, covered_empty=0)
    seals, spent, prefixes, attempted, reservation_times, event_times = [], [], [], [], [], []
    for child in sorted((root/'history').iterdir()):
        require(child.name.isdecimal() and int(child.name) < len(tasks), 'Foreign original child')
        saved = legacy.read(child/'prepared.json')
        require((saved['binding'],saved['tasks']) == task_binding(config_path,c,snapshot,
                tasks[int(child.name)],collector_sources=captured), 'Original child binding changed')
        with Journal(child,saved['binding'],saved['tasks'],inspect_only=True) as j:
            j.verify_records(); state = j.snapshot(); require(not state['damage'], 'Original damaged Journal')
            event_times.extend(parse_utc(e['at']) for e in j.events)
            counts['attempts'] += state['counters']['attempts']; counts['bytes'] += state['counters']['response_bytes']
            reservation_times.extend(parse_utc(a['reserved_at']) for a in state['attempts'].values())
            key = next(iter(j.tasks)); complete = state['intervals'][key]['complete']
            if state['attempts']:attempted.append(int(child.name))
            if complete:
                require(j.completed(key) == raw_envelope(j,key,complete['run'],complete['attempt_keys']),
                        'Original seal differs from receipts')
                counts['sealed'] += 1; counts['covered_empty'] += complete['state']=='complete_empty'
                seals.append(dict(root=str(child), task_id=key, seal=complete,
                                  header_sha256=j.header_sha, anchor_sha256=j.events[-1]['record_sha256']))
            elif state['attempts']:
                prefix = prefix_evidence(j,key); prefix['root']=str(child); prefix.pop('rows')
                prefixes.append(prefix); spent.append(key)
    require(len(prefixes)==1 if require_prefix else len(prefixes)<=1,
            'Recovery requires exactly one recognized spent original task')
    persisted = legacy.read(root/'status.json')['accounting']
    require(all(persisted[k]==v for k,v in counts.items()) and persisted['spent_unsealed']==spent and
            {a['task_id'] for a in persisted['assets']}=={a['task_id'] for a in seals},
            'Original persisted accounting differs from Journals')
    original_window = legacy.read(root/'window.json')
    first = min(reservation_times)
    require(original_window == dict(first_attempt_at=format_utc(first),
        deadline=format_utc(first+timedelta(seconds=c['execution_window_seconds']))), 'Original window changed')
    with Root(root) as fs:
        job.fs=fs
        records=job.window_records()
        job.fs=None
    windows=[original_window]+records
    require(all(any(parse_utc(w.get('opened_at',w.get('first_attempt_at'))) <= at < parse_utc(w['deadline'])
                    for w in windows) for at in reservation_times), 'Original reservation outside allowed window')
    return dict(config=c, sources=captured, counts=counts, seals=seals, prefix=prefixes[0] if prefixes else None,
                window=windows[-1], original_window=original_window,
                last_original_event_at=format_utc(max(event_times)),
                unattempted=[n for n in range(len(tasks)) if n not in attempted], tasks=tasks)


def original_files(root):
    result = {}
    with Root(root) as fs:
        for p in sorted(root.rglob('*')):
            relative = p.relative_to(root)
            if relative.parts[0] == RECOVERY_DIRECTORY:continue
            require(not p.is_symlink(), 'Symlink in original job evidence')
            if p.is_file():result[str(relative)] = sha(fs.read(str(relative),8*legacy.BODY))
    return result


def make_recovery_request(config_path, *, max_attempts=16):
    """Offline proposal only. No new job, transition, window, or provider IO."""
    path = Path(config_path).absolute(); old=original_state(path); c=old['config']
    require(type(max_attempts) is int and 1<=max_attempts<=32 and
            old['counts']['attempts']+max_attempts<=c['limits']['attempts'] and
            old['counts']['bytes']+max_attempts*legacy.BODY<=c['limits']['bytes'],
            'Insufficient original whole-job capacity for bounded recovery')
    return dict(version=RECOVERY_VERSION, config=dict(path=str(path),sha256=sha(path.read_bytes())),
                original_sources=c['sources'], execution_sources=legacy.sources(), reason=RECOVERY_REASON,
                prefix=old['prefix'], accounting_before=old['counts'], existing_seals=old['seals'],
                original_files=original_files(Path(c['root'])),
                unattempted_disposition=dict(status='SUPERSEDED_UNEXECUTED',task_ordinals=old['unattempted']),
                limits=dict(attempts=max_attempts,bytes=max_attempts*legacy.BODY),
                window_seconds=c['execution_window_seconds'])


def checked_recovery_request(path, config_path, *, current=True):
    request = legacy.read(path)
    require(set(request)=={'version','config','original_sources','execution_sources','reason','prefix',
            'accounting_before','existing_seals','original_files','unattempted_disposition','limits','window_seconds'} and
            request['version']==RECOVERY_VERSION and request['reason']==RECOVERY_REASON and
            Path(request['config']['path'])==Path(config_path) and
            sha(Path(config_path).read_bytes())==request['config']['sha256'], 'Exact recovery request/config required')
    if current:require(request['execution_sources']==legacy.sources(), 'Recovery execution source changed')
    old=original_state(config_path);c=old['config']
    require(request['original_sources']==c['sources'] and request['prefix']==old['prefix'] and
            request['accounting_before']==old['counts'] and request['existing_seals']==old['seals'] and
            request['original_files']==original_files(Path(c['root'])) and
            request['unattempted_disposition']==dict(status='SUPERSEDED_UNEXECUTED',task_ordinals=old['unattempted']) and
            request['window_seconds']==c['execution_window_seconds'], 'Recovery original evidence/accounting changed')
    limits=request['limits']; require(set(limits)=={'attempts','bytes'} and type(limits['attempts']) is int and
        1<=limits['attempts']<=32 and limits['bytes']==limits['attempts']*legacy.BODY and
        old['counts']['attempts']+limits['attempts']<=c['limits']['attempts'] and
        old['counts']['bytes']+limits['bytes']<=c['limits']['bytes'], 'Recovery cumulative limits')
    return request,old


def recovery_binding(request_ref, transition_ref, request):
    prefix=request['prefix'];task=prefix['task'];sid=task['identity']['stream_id'];key=prefix['task_id']
    count=request['limits']['attempts']
    b=dict(version=VERSION,mode=MODE,campaign_id='native-recovery-'+key,
           collector_sources=source_binding(),config_ref=request['config'],
           configuration_sha256=request['config']['sha256'],
           task=dict(stream_id=sid,start=task['start'],end=task['end']),roster={sid:task['identity']},selected_ids=[sid],
           quality_policy=quality_binding(),request_policy=dict(logical_requests=count,http_attempts=count,
               total_bytes=request['limits']['bytes'],wall_seconds=300),
           budgets=dict(logical_requests=count,attempts=count,response_bytes=request['limits']['bytes'],
                        source_rows=count*2016,intervals=1,sessions=16,elapsed_ms=300000),
           pagination_recovery=dict(version=RECOVERY_VERSION,request=request_ref,transition=transition_ref,prefix=prefix))
    require(len(encode(b))+4096<=PAGE_BYTES,'Recovery header bound')
    return b,{key:task}


def validate_recovery_binding(binding,tasks):
    r=binding['pagination_recovery'];request=ns.reference(r['request']);transition=ns.reference(r['transition'])
    require(request['execution_sources']==legacy.sources() and
            transition['request']==r['request'] and transition['execution_sources']==request['execution_sources'] and
            transition['original_sources']==request['original_sources'] and transition['prefix']==request['prefix'] and
            transition['accounting_before']==request['accounting_before'], 'Recovery transition/source changed')
    require((binding,tasks)==recovery_binding(r['request'],r['transition'],request), 'Recovery binding changed')
    recovery_prefix(r)


class PaginationRecovery:
    """A single bounded append-only continuation inside the original job root."""
    def __init__(self,config_path,request_path,*,clock=None):
        self.config_path=Path(config_path).absolute(); self.request_path=Path(request_path).absolute()
        self.request,self.old=checked_recovery_request(self.request_path,self.config_path)
        self.root=Path(self.old['config']['root']);self.output=self.root/RECOVERY_DIRECTORY
        self.fixture=ns.reference(self.old['config']['fixture']) if self.old['config']['fixture'] else None
        self.clock=clock or legacy.Clock(self.fixture)
        if self.clock.fake:
            self.clock.value=max(self.clock.value,parse_utc(self.old['last_original_event_at']))
        self.request_ref=dict(path=str(self.request_path),sha256=sha(self.request_path.read_bytes()))
        self.fs=None;self.journal=None;self.last_dispatch=None

    @contextmanager
    def locked(self):
        with Root(self.root) as fs:
            fd=fs.lock_fd('writer.lock')
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);self.fs=fs
                checked_recovery_request(self.request_path,self.config_path)
                yield self
            finally:self.fs=None;os.close(fd)

    def prepare(self):
        require(not self.output.exists(),'Never recreate a recovery transition/window')
        at=self.clock.now();prior=self.old['window']
        require(parse_utc(at)>=parse_utc(self.old['last_original_event_at']),
                'Recovery clock precedes retained original evidence')
        if parse_utc(at)<parse_utc(prior['deadline']):
            window=dict(kind='ORIGINAL_REMAINING_WINDOW',opened_at=at,deadline=prior['deadline'],previous_sha256=digest(prior))
        else:
            window=dict(kind='BOUNDED_RECOVERY_WINDOW',opened_at=at,
                        deadline=format_utc(parse_utc(at)+timedelta(seconds=self.request['window_seconds'])),previous_sha256=digest(prior))
        transition=dict(version=RECOVERY_VERSION,request=self.request_ref,reason=RECOVERY_REASON,
            original_job_id=digest(self.old['config']),original_config=self.request['config'],
            original_sources=self.request['original_sources'],execution_sources=self.request['execution_sources'],
            accounting_before=self.request['accounting_before'],existing_seals=self.request['existing_seals'],
            prefix=self.request['prefix'],window=window,unattempted_disposition=self.request['unattempted_disposition'])
        transition['record_sha256']=digest(transition)
        self.fs.write_new(RECOVERY_DIRECTORY+'/transition.json',encode(transition),8*legacy.BODY)
        return self.status()

    def transition(self):
        t=legacy.read(self.output/'transition.json');request=self.request
        require(t['record_sha256']==digest({k:v for k,v in t.items() if k!='record_sha256'}) and
            t['request']==self.request_ref and t['original_job_id']==digest(self.old['config']) and
            t['original_config']==request['config'] and t['reason']==RECOVERY_REASON and
            all(t[k]==request[k] for k in ('original_sources','execution_sources','accounting_before',
                                         'existing_seals','prefix','unattempted_disposition')),
            'Append-only recovery transition changed')
        w=t['window'];prior=self.old['window'];opened=parse_utc(w['opened_at'])
        require(opened>=parse_utc(self.old['last_original_event_at']) and w['previous_sha256']==digest(prior) and
            ((w['kind']=='ORIGINAL_REMAINING_WINDOW' and opened<parse_utc(prior['deadline']) and w['deadline']==prior['deadline']) or
             (w['kind']=='BOUNDED_RECOVERY_WINDOW' and opened>=parse_utc(prior['deadline']) and
              parse_utc(w['deadline'])==opened+timedelta(seconds=request['window_seconds']))), 'Recovery window changed')
        return t

    def binding(self):
        self.transition()
        ref=dict(path=str(self.output/'transition.json'),sha256=sha((self.output/'transition.json').read_bytes()))
        return recovery_binding(self.request_ref,ref,self.request)

    def status(self):
        checked_recovery_request(self.request_path,self.config_path)
        transition=self.transition();binding,tasks=self.binding();root=self.output/'history';key=self.request['prefix']['task_id']
        counts=dict(self.request['accounting_before']);complete=None;new_attempts=0;new_bytes=0
        if root.exists():
            with Journal(root,binding,tasks,inspect_only=True) as j:
                j.verify_records();s=j.snapshot();require(not s['damage'],'Damaged recovery Journal')
                new_attempts=s['counters']['attempts'];new_bytes=s['counters']['response_bytes']
                complete=s['intervals'][key]['complete']
                require(all(parse_utc(transition['window']['opened_at'])<=parse_utc(a['reserved_at'])<
                            parse_utc(transition['window']['deadline']) for a in s['attempts'].values()),
                        'Recovery reservation outside window')
                require(all(parse_utc(a['reserved_at'])>=parse_utc(self.old['last_original_event_at'])
                            for a in s['attempts'].values()), 'Recovery precedes retained original evidence')
                if complete:require(j.completed(key)==raw_envelope(j,key,complete['run'],complete['attempt_keys']),
                                    'Recovered seal differs from retained pages')
        counts['attempts']+=new_attempts;counts['bytes']+=new_bytes;counts['sealed']+=complete is not None
        require(counts['attempts']<=self.old['config']['limits']['attempts'] and
                counts['bytes']<=self.old['config']['limits']['bytes'],'Recovery exceeded original whole-job ceilings')
        result = dict(outcome='RECOVERED_TASK_SEALED_OLD_SCOPE_SUPERSEDED' if complete else
                    'RECOVERY_HOLD_SPENT' if new_attempts else 'RECOVERY_PREPARED_NO_DISPATCH',
                    job_id=digest(self.old['config']),accounting=counts,recovery_attempts=new_attempts,
                    recovery_bytes=new_bytes,recovered_task=key if complete else None,
                    spent_unsealed=[] if complete else [key],window=transition['window'],
                    original_window=self.old['original_window'],unattempted_disposition=transition['unattempted_disposition'],
                    original_scope_complete=False,product_eligible=False)
        if (self.output/'failure.json').exists():result['failure']=legacy.read(self.output/'failure.json')
        return result

    def stop(self):
        self.transition();value=dict(at=self.clock.now(),request=self.request_ref)
        self.fs.write_new(RECOVERY_DIRECTORY+'/stops/'+digest(value)+'.json',encode(value),PAGE_BYTES)
        return dict(outcome='RECOVERY_STOP_REQUESTED_NO_DISPATCH')

    def dispatch(self,request,timeout):
        require(not (self.output/'stops').exists(),'Recovery stopped; explicit review required')
        w=self.transition()['window'];remaining=(parse_utc(w['deadline'])-parse_utc(self.clock.now())).total_seconds()
        require(remaining>0,'Recovery window exhausted; no implicit renewal')
        s=self.journal.snapshot();base=self.request['accounting_before'];limits=self.old['config']['limits']
        require(base['attempts']+s['counters']['attempts']<=limits['attempts'] and
                base['bytes']+s['counters']['response_bytes']+legacy.BODY<=limits['bytes'],
                'Original whole-job cumulative recovery capacity exhausted')
        if self.last_dispatch:
            delay=max(0,1-(parse_utc(self.clock.now())-self.last_dispatch).total_seconds())
            require(delay<remaining,'Recovery pacing exceeds window');self.clock.wait(delay);remaining-=delay
        self.last_dispatch=parse_utc(self.clock.now())
        timeout=min(timeout,remaining)
        if self.fixture is not None:
            q=dict(parse_qsl(urlsplit(request.full_url).query));v=self.fixture['history'][q['time[$gte]']]
            return legacy.Reply(v.get('body',v),v.get('status',200))
        return legacy.anonymous_executor(format_utc(parse_utc(self.clock.now())+timedelta(seconds=timeout)))(request,timeout=timeout)

    def acquire(self):
        state=self.status()
        if state['recovered_task']:return state
        require(state['recovery_attempts']==0,'Spent recovery cannot replay or renew itself')
        require(not (self.output/'stops').exists(),'Recovery stopped; explicit review required')
        remaining=(parse_utc(state['window']['deadline'])-parse_utc(self.clock.now())).total_seconds()
        require(remaining>=301,'Insufficient recovery window; do not reserve attempts')
        legacy.Job(self.old['config'],None,clock=self.clock).storage(self.request['limits']['bytes'])
        binding,tasks=self.binding();root=self.output/'history';create=not root.exists()
        if create:
            root.mkdir(mode=0o700)
            with Root(root) as fs:fs.write_new('prepared.json',encode(dict(binding=binding,tasks=tasks)),8*legacy.BODY)
        try:
            with Journal(root,binding,tasks,create=create,now=self.clock.now,monotonic=self.clock.monotonic) as j:
                self.journal=j
                adapter=NativeAdapter(j)
                adapter.run(executor=self.dispatch,wait=self.clock.wait,
                    window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=300)))
                if getattr(adapter,'failure',None):
                    self.fs.write_new(RECOVERY_DIRECTORY+'/failure.json',encode(adapter.failure),PAGE_BYTES)
        finally:self.journal=None
        result=self.status()
        name=RECOVERY_DIRECTORY+'/readbacks/'+str(time.time_ns())+'.json'
        self.fs.write_new(name,encode(result),8*legacy.BODY)
        require(result['recovered_task'] is not None,encode(result.get('failure',dict(
            reason='Recovery incomplete; preserve charged continuation; no automatic retry'))).decode().strip())
        return result


def recovery_main(args):
    require(args.mode in ('inspect','prepare','validate-scope','acquire','status','stop') and
            not any(getattr(args,k,None) for k in ('review','output_root','donor_config','request','final_review',
                                                 'output_config','job_root','enable')),
            'Explicit bounded recovery modes only; no ordinary resume')
    c=legacy.read(Path(args.config).absolute());fixture=ns.reference(c['fixture']) if c['fixture'] else None
    require(args.mode=='acquire' or not args.allow_provider,'Offline recovery command cannot permit provider')
    if fixture is not None:
        def deny(event,unused):
            if event.startswith('socket.'):raise Hold('Offline recovery fixture denies network')
        sys.addaudithook(deny)
    if args.mode=='acquire':
        require(fixture is not None or c['enabled'] and args.allow_provider,'Explicit recovery provider permission')
        if fixture is None:
            env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
            state=subprocess.check_output(['git','status','--short'],cwd=legacy.REPO,env=env,text=True).strip()
            require(state in ('','?? .l01-soil-integration/'),'Commit reviewed recovery source before acquisition')
    runner=PaginationRecovery(args.config,args.recovery,clock=legacy.Clock(fixture,args.offline_now))
    if args.mode=='inspect':
        return dict(outcome='VALIDATED_RECOVERY_REQUEST_NO_DISPATCH',prefix=runner.request['prefix'],
                    accounting_before=runner.request['accounting_before'],limits=runner.request['limits'],
                    new_window_opened=False,unattempted_disposition=runner.request['unattempted_disposition'])
    if args.mode=='stop':
        # Cooperative stop must not wait for the provider process's job lock.
        with Root(runner.root) as fs:
            runner.fs=fs
            try:return runner.stop()
            finally:runner.fs=None
    with runner.locked():
        if args.mode=='prepare':return runner.prepare()
        if args.mode in ('status','validate-scope'):return runner.status()
        return runner.acquire()


def main(args):
    if getattr(args, 'recovery', None):
        return recovery_main(args)
    require(args.mode in ('inspect','prepare','validate-scope','acquire','resume','status','stop','continue-window') and
            not any(getattr(args,k,None) for k in ('review','output_root','donor_config','request','final_review',
                                                 'recovery','output_config','job_root','enable')),
            'Native version 5 has no automatic metadata/science review or promotion command')
    path=Path(args.config).absolute()
    if args.mode in ('inspect','validate-scope','status') and legacy.read(path)['sources']!=legacy.sources():
        require(not args.allow_provider and args.offline_now is None,'Historical native inspection is read-only')
        old=original_state(path,require_prefix=False)
        persisted=legacy.read(Path(old['config']['root'])/'status.json')
        return dict(persisted,read_only=True,original_source=old['config']['sources'],
                    execution_source=legacy.sources(),historical_receipts_verified=True)
    c,snapshot,tasks=config(path,verify_reuse=args.mode in
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
