"""Independent native job supervision; existing Journals are the request ledger.

No daily science, publisher, alternate HTTP stack or automatic review decision.
The offline fixture route is permanently bound to its job and denies sockets.
"""
import argparse
from contextlib import contextmanager
from datetime import timedelta
from functools import lru_cache
import fcntl
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

from . import authority_package as ap, authority_witness as aw, campaign, campaign_execution as ce
from . import eligibility, metadata_acquisition as ma, provider_metadata, recovery, sealed_history
from .journal import Journal, utc_now
from .model import Inventory, INVENTORY_SHA256, source_binding
from .provider_adapter import CampaignAdapter, anonymous_executor
from .safety import Root, Hold, decode, digest, encode, require, sha
from ..transport import parse_utc, format_utc

VERSION = "dendra-local-native-job-1"
IMPORT_VERSION = "dendra-local-native-job-2"
SCOPED_VERSION = "dendra-local-native-job-3"
STREAM_SCOPED_VERSION = "dendra-local-native-job-4"
REVIEWED_VERSIONS = (SCOPED_VERSION, STREAM_SCOPED_VERSION)
WINDOW_VERSION = "dendra-local-continuation-window-1"
MAX_CONTINUATION_WINDOWS = 16
REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "scripts/dendra/acquire_native.R"
LIMITS = dict(attempts=1500, metadata_attempts=64, bytes=1073741824, seconds=7200)
MAX_EXECUTION_WINDOW_SECONDS = 12600
CDFW = {
    "635319fcb055ac5348842453": "63531a67a9b61453fa1ca4ed 63531a68a9b6141b4b1ca4ef 63531a684b24f740d53623e6 63531a688f3bc3f3df655be6 63531a684bc26eea5ce90e24 63531a695d431185dee54556".split(),
    "60f8c62d40a87301e3f50614": "6106a59fa8f01166960a5b6c 6106a5a011a7e0c02186a5a0 6106a5a039e80fde713aec8e 6106a5a0afcb8b440547c450 6106a5a02a1c986d61641ddd 6106a5a1fec94c52a5c59270".split(),
    "60f8c97440a87302d1f5061c": "6106a6e2d00b7982989f0baa 6106a6e2a8f01165a60a5b89 6106a6e211a7e07c8286a5c3 6106a6e739e80f1ba73aeca9 6106a6e7afcb8ba94647c46f 6106a6e72a1c98f46f641df8".split(),
    "6535588602096992cfa0cf26": "65355b8c5c0d5f806969a8ea 65355b8c552645d72d3f0162 65355b8dd07087215fd59748 65355b8d8081876e27c95d31 65355b8dd070878668d5974a 65355b8e2148dbec414875a4".split(),
    "618081c89418f2f86833e143": "618082719418f251bf33e149 618082705f31bfd049b592bb".split(),
    "618081d6aaddf804d88f7eab": "6180829cbf59db55af47b3dc 6180829c4c304fa176369ccc".split(),
}
SCOPE = dict(start="2025-10-01T08:00:00.000Z", end="2026-10-01T08:00:00.000Z")
BODY = 8388608


def read(path, bound=8*BODY):
    path = Path(path)
    with Root(path.parent) as fs:
        return decode(fs.read(path.name, bound))


def sources():
    return dict(checkpoint=ap.current_checkpoint(), collector=digest(source_binding()),
                r_entrypoint_sha256=sha(ENTRY.read_bytes()))


def config(path, *, allow_continuation=False):
    c = read(path)
    if c.get("version") in (IMPORT_VERSION, *REVIEWED_VERSIONS):
        with Root(Path(path).parent) as fs:
            require(fs.read(Path(path).name,8*BODY) == encode(c), "Canonical serialized execution config required")
    current=sources()
    if c.get('sources') != current:
        c,inv=_config(c,c['sources'])
        require(c['version'] in REVIEWED_VERSIONS and
                (allow_continuation or (Path(c['root'])/'continuation-windows').exists()),
                'Job source/checkpoint changed; explicit continuation required')
        continuation_sources(c)
        return c,inv
    return _config(c, current)


@lru_cache(maxsize=32)
def _verified_capture(configuration_sources, captured_sources):
    # Git objects are immutable. Cache only their verification, never mutable
    # execution files, authority, job state, or continuation authorization.
    from . import preparation_recovery as pr
    pr._sources(dict(sources=decode(configuration_sources)),decode(captured_sources))


def continuation_sources(c):
    """Supervisor-only compatibility, without replacing original job authority."""
    require(c['version'] in REVIEWED_VERSIONS, 'Continuation requires exact reviewed scope')
    root=Path(c['root'])/'authority';package=read(root/'package.json')
    station=next(iter(ap.station_groups(package)))
    old=read(root/'campaigns'/ap.metadata_campaign_id(package,station)/'manifest.json')['binding']['collector_sources']
    _verified_capture(encode(c['sources']),encode(old))
    current=source_binding()
    require(set(old)==set(current) and
            {k for k in old if old[k]!=current[k]} <= {'history_acquisition/local_job.py'} and
            c['sources']['r_entrypoint_sha256']==sha(ENTRY.read_bytes()),
            'Continuation requires identical parser/Journal/transport/science/R sources')
    return dict(capture_sources=old,execution_sources=current,execution_checkpoint=sources())


def _config(c, expected_sources):
    """Same validation for live config and separately verified historical input."""
    fields = {"version", "inventory", "catalog", "catalog_sha256", "streams", "scope", "limits",
                      "reserve_bytes", "root", "sources", "enabled", "fixture", "reuse",
                      "attribution", "organization_labels"}
    if c.get("version") == IMPORT_VERSION: fields.add("authority_import")
    if c.get("version") == STREAM_SCOPED_VERSION: fields.add("stream_scopes")
    if c.get("version") in REVIEWED_VERSIONS and 'execution_window_seconds' in c:
        fields.add('execution_window_seconds')
    require(set(c) == fields, "Exact job configuration fields")
    require(c["version"] in (VERSION,IMPORT_VERSION,*REVIEWED_VERSIONS) and c["sources"] == expected_sources, "Job source/checkpoint changed")
    require(type(c["enabled"]) is bool, "Explicit job permission required")
    if c["version"] in REVIEWED_VERSIONS:
        scope=c["scope"]
        require(set(scope)=={'start','end'} and all(format_utc(parse_utc(scope[k]))==scope[k] for k in scope) and
                parse_utc(scope['start'])<parse_utc(scope['end']), "Exact canonical half-open scope required")
    else:
        require(c["scope"] == SCOPE, "Exact WY2026 scope/permission required")
    require(set(c["limits"]) == set(LIMITS) and all(type(c["limits"][k]) is int and
            0 < c["limits"][k] <= v for k,v in LIMITS.items()), "Whole-job ceilings")
    if 'execution_window_seconds' in c:
        require(type(c['execution_window_seconds']) is int and
                LIMITS['seconds'] <= c['execution_window_seconds'] <= MAX_EXECUTION_WINDOW_SECONDS and
                c['limits']['seconds'] == LIMITS['seconds'],
                'Explicit execution window must be 7200..12600 seconds with the default legacy time limit')
    require(type(c["reserve_bytes"]) is int and c["reserve_bytes"] >= BODY, "Explicit storage safety reserve")
    inv = Inventory.load(c["inventory"], INVENTORY_SHA256)
    require(sha(Path(c['catalog']).read_bytes()) == c['catalog_sha256'], 'Original metadata catalog changed')
    require(isinstance(c["streams"], list) and 0 < len(c["streams"]) <= 28 and
            len(set(c["streams"])) == len(c["streams"]), "Exact unique bounded selection")
    if c['version'] == STREAM_SCOPED_VERSION:
        scopes=c['stream_scopes']
        require(isinstance(scopes,dict) and set(scopes)==set(c['streams']), 'Exact per-stream scope roster required')
        for scope in scopes.values():
            require(isinstance(scope,dict) and set(scope)=={'start','end'} and
                    all(format_utc(parse_utc(scope[k]))==scope[k] for k in scope) and
                    parse_utc(scope['start'])<parse_utc(scope['end']), 'Exact canonical per-stream interval required')
        require(c['scope']==dict(start=min(s['start'] for s in scopes.values()),
                                end=max(s['end'] for s in scopes.values())), 'Job envelope differs from per-stream scopes')
    for sid in c["streams"]:
        ident = inv.identity(sid)
        require(sid in CDFW.get(ident["station_id"], []) and ident["native_unit"] == "Percent", "Outside CDFW selection")
    root = Path(c["root"])
    require(root.is_absolute() and root.parent.is_dir() and root == root.resolve() and
            (REPO/".l01-soil-integration") in root.parents, "Fresh private task-owned destination required")
    require(isinstance(c["reuse"], list) and len(c["reuse"]) <= 128, "Bounded original reuse entries")
    require(isinstance(c['attribution'],dict) and set(c['attribution'])==set(c['streams']) and
            isinstance(c['organization_labels'],dict), 'Exact series attribution references')
    for e in c["reuse"]:
        require(set(e) == set(sealed_history.ENTRY_FIELDS) and
                e["identity"] == inv.identity(e["identity"]["stream_id"]) and
                e["identity"]["stream_id"] in c["streams"], "Exact selected reuse identity")
    if c["fixture"] is not None:
        require(set(c["fixture"]) == {"path", "sha256"} and
                sha(Path(c["fixture"]["path"]).read_bytes()) == c["fixture"]["sha256"], "Offline fixture binding")
    return c, inv


def execution_window_seconds(c):
    """Opt-in duration; omitted fields preserve every legacy time limit."""
    return c['execution_window_seconds'] if 'execution_window_seconds' in c else c['limits']['seconds']


def stream_scope(c, sid):
    """The reporting envelope grants no per-stream permission in version 4."""
    require(sid in c['streams'], 'Stream outside configured roster')
    return c['stream_scopes'][sid] if c['version']==STREAM_SCOPED_VERSION else c['scope']


def pointer(document, value):
    require(isinstance(value,str) and (not value or value.startswith('/')), 'JSON pointer required')
    for part in value.split('/')[1:]:
        key=part.replace('~1','/').replace('~0','~')
        document=document[int(key)] if isinstance(document,list) else document[key]
    return document


def attribution(c, identity):
    """Source organization is separate from platform and frozen series identity."""
    refs=c['attribution'][identity['stream_id']]
    require(isinstance(refs,list) and len(refs)<=8,'Bounded original organization evidence')
    claims=[]
    for ref in refs:
        require(set(ref)=={'path','sha256','record_pointer'},'Attribution reference fields')
        path=Path(ref['path'])
        with Root(path.parent) as fs:body=fs.read(path.name,8*BODY)
        require(sha(body)==ref['sha256'],'Organization evidence changed')
        row=pointer(decode(body),ref['record_pointer'])
        require(row.get('station_id',row.get('_id',row.get('id')))==identity['station_id'] and
                row.get('datastream_id',row.get('stream_id',identity['stream_id']))==identity['stream_id'],
                'Organization evidence belongs to another series/station')
        key=row.get('organization_id'); name=row.get('organization_name',row.get('organization'))
        require(key is None or isinstance(key,str),'Organization ID type')
        require(name is None or isinstance(name,str),'Organization name type')
        claims.append(dict(subprovider_key=key,subprovider_name=name,evidence=ref,
            association_scope='stream_record' if ('datastream_id' in row or 'stream_id' in row) else 'station_record',
            historical_applicability='not_inferred'))
    pairs={(v['subprovider_key'],v['subprovider_name']) for v in claims}
    known=len(pairs)==1 and all(next(iter(pairs)))
    key,name=next(iter(pairs)) if len(pairs)==1 else (None,None)
    label=c['organization_labels'].get(key) if known else None
    require(label is None or isinstance(label,str) and 0<len(label)<=80,'Explicit display mapping')
    return dict(status='KNOWN' if known else 'CONFLICT' if len(pairs)>1 else 'MISSING',
        subprovider_key=key,subprovider_name=name,subprovider_label='Dendra-'+label if label else None,
        claims=claims,platform='dendra',identity_is_not_display_label=True)


class Clock:
    def __init__(self, fixture=None, override=None):
        require(override is None or fixture is not None, "Real dispatch clock cannot be overridden")
        self.fake = fixture is not None
        self.value = parse_utc(override or fixture["now"]) if self.fake else None
        self.tick = 0.0

    def now(self):
        return format_utc(self.value) if self.fake else utc_now()

    def monotonic(self):
        return self.tick if self.fake else time.monotonic()

    def wait(self, seconds):
        if self.fake:
            self.value += timedelta(seconds=seconds); self.tick += seconds
        else:
            time.sleep(seconds)


class Reply(io.BytesIO):
    headers = {}
    def __init__(self, value, status=200):
        super().__init__(encode(value)); self.status = status


class Job:
    def __init__(self, c, inventory, *, clock, fixture=None):
        self.c, self.inventory, self.clock, self.fixture = c, inventory, clock, fixture
        self.root = Path(c["root"])
        self.fs = None
        self.lock = None
        self.last_dispatch = None
        self.transfer_cache = None
        self.config_path = None
        self.continuing = False

    def put(self, name, value):
        self.fs.write_new(name, encode(value), 8*BODY)

    def replace_summary(self, target, value):
        """Generated summaries only; immutable Journals remain authoritative."""
        name='summary-'+str(os.getpid())+'-'+str(time.time_ns())+'.tmp'
        self.put(name,value)
        os.replace(name,target,src_dir_fd=self.fs.fd,dst_dir_fd=self.fs.fd)
        os.fsync(self.fs.fd)

    def summary(self, value):
        self.replace_summary('status.json', value)

    def series_catalog(self, reviews=None):
        rows={}
        for sid in self.c['streams']:
            identity=self.inventory.identity(sid)
            review=(reviews or {}).get(sid,{})
            scope=review.get('scope',stream_scope(self.c,sid))
            configuration=review.get('native_review',{}).get('configuration_evidence_sha256')
            series_key=digest(dict(identity=identity,scope=scope,configuration=configuration))
            rows[sid]=dict(series_key=series_key,identity=identity,scope=scope,
                configuration_evidence_sha256=configuration,source_organization=attribution(self.c,identity),
                admission='PENDING' if not review else 'EXCLUDED' if review=={'disposition':'EXCLUDE'} else 'REVIEWED',
                review=review)
        return dict(platform='dendra',streams=rows)

    def get(self, name):
        return decode(self.fs.read(name, 8*BODY))

    def has(self, name):
        return (self.root/name).exists()

    def storage(self, incoming=0):
        # Conservative native + normalized archive + temporary staging allowance.
        require(shutil.disk_usage(self.root).free >= self.c["reserve_bytes"] + 4*incoming,
                "Insufficient storage including safety reserve")

    @contextmanager
    def open(self, create=False):
        if create:
            require(self.c["version"] != IMPORT_VERSION or self.c["enabled"],
                    "Finalize imported config before initialization")
            self.root.mkdir(mode=0o700, exist_ok=False)
            with Root(self.root) as fs:
                fs.write_new("writer.lock", b"", 0)
        with Root(self.root) as fs:
            self.fs = fs
            fd = fs.lock_fd("writer.lock")
            try:
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc: raise Hold("Job writer already active") from exc
                self.lock = fd
                if create:
                    self.storage(self.c["limits"]["bytes"])
                    self.put("job.json", dict(version=VERSION, configuration=self.c,
                        job_id=digest(self.c), created_at=self.clock.now()))
                    self.put('catalog.json',self.series_catalog())
                require(self.get("job.json")["configuration"] == self.c and
                        self.get("job.json")["job_id"] == digest(self.c), "Same-job configuration/state changed")
                reviews=self.get('review.json')['streams'] if self.has('review.json') else None
                require(self.get('catalog.json') == self.series_catalog(reviews), 'Series catalog/evidence changed')
                yield self
            finally:
                self.lock = None
                os.close(fd)
                self.fs = None

    def child(self, root, cid):
        return recovery.open_evidence(str(root), cid, self.inventory)

    def window_records(self):
        """Verify the append-only window chain and unchanged original pins."""
        if not self.has('continuation-windows'): return []
        names=self.fs.list('continuation-windows')
        require(0<len(names)<=MAX_CONTINUATION_WINDOWS and
                sorted(names)==[f'{n:04d}' for n in range(1,len(names)+1)], 'Continuation window sequence changed')
        prior=self.get('window.json');previous=digest(prior);records=[]
        pins={n:sha(self.fs.read(n,8*BODY)) for n in
              ('job.json','review.json','catalog.json','plan.json','plan-binding.json','asset-map.json','scope-binding.json','window.json')}
        for name in sorted(names):
            prefix='continuation-windows/'+name
            require(set(self.fs.list(prefix)) <= {'opened.json','first-attempt.json'} and
                    'opened.json' in self.fs.list(prefix), 'Partial/foreign continuation state')
            r=self.get(prefix+'/opened.json')
            require(set(r)=={'version','ordinal','job_id','configuration_sha256','pins','previous_sha256','record_sha256',
                    'opened_at','first_attempt_at','deadline','sources','accounting_before','remaining_tasks'} and
                    r['version']==WINDOW_VERSION and r['ordinal']==int(name) and r['job_id']==digest(self.c) and
                    r['configuration_sha256']==digest(self.c) and r['pins']==pins and
                    r['previous_sha256']==previous and r['first_attempt_at'] is None and
                    r['record_sha256']==digest({k:v for k,v in r.items() if k!='record_sha256'}),
                    'Continuation identity/pins/window history changed')
            opened,deadline=map(parse_utc,(r['opened_at'],r['deadline']))
            require(parse_utc(prior['deadline'])<=opened and
                    deadline==opened+timedelta(seconds=execution_window_seconds(self.c)), 'Continuation window timestamps changed')
            compat=r['sources'];original=compat['capture_sources'];execution=compat['execution_sources']
            require(set(compat)=={'capture_sources','execution_sources','execution_checkpoint'} and
                    digest(original)==self.c['sources']['collector'] and set(original)==set(execution) and
                    {k for k in original if original[k]!=execution[k]} <= {'history_acquisition/local_job.py'} and
                    digest(execution)==compat['execution_checkpoint']['collector'], 'Continuation source compatibility changed')
            _verified_capture(encode(compat['execution_checkpoint']),encode(execution))
            if self.has(prefix+'/first-attempt.json'):
                start=self.get(prefix+'/first-attempt.json')
                require(set(start)=={'window_sha256','first_attempt_at','deadline'} and
                        start['window_sha256']==digest(r) and start['deadline']==r['deadline'] and
                        opened<=parse_utc(start['first_attempt_at'])<deadline, 'Continuation first reservation changed')
            records.append(r);previous=digest(r);prior=r
        require(self.continuing or records[-1]['sources']['execution_checkpoint']==sources(),
                'Continuation execution source changed; explicit new window required')
        return records

    def execution_window(self):
        records=self.window_records()
        return records[-1] if records else self.get('window.json') if self.has('window.json') else None

    def remaining_tasks(self, accounting):
        plan=self.get('plan.json')['tasks'];remaining=[]
        assets={a['root']:a for a in accounting['assets']}
        require(len(assets)==len(accounting['assets']), 'Duplicate task assets')
        if self.has('history'):
            require({p.name for p in (self.root/'history').iterdir()} <= {str(n) for n in range(len(plan))},
                    'Foreign history child')
        for n,t in enumerate(plan):
            root=self.root/'history'/str(n)
            if str(root) in assets:
                saved=read(root/'prepared.json');tasks=saved['tasks'];asset=assets[str(root)]
                require(len(tasks)==1 and asset['task_id'] in tasks and
                        asset['campaign_id']==saved['binding']['campaign_id'], 'Sealed child identity changed')
                task=tasks[asset['task_id']]
                require(task['identity']==self.inventory.identity(t['stream_id']) and
                        all(task[k]==t[k] for k in ('start','end')), 'Sealed task differs from bound plan')
            else:
                require(not root.exists(), 'Unsealed child requires separate recovery; continuation cannot retry')
                remaining.append(n)
        require(len(plan)==len(assets)+len(remaining), 'Task/asset closure changed')
        return remaining

    def continue_window(self):
        require(self.lock is not None and self.c['version'] in REVIEWED_VERSIONS, 'Locked reviewed job required')
        require(self.has('window.json'), 'Original window record required; preserve state')
        a=self.accounting();prior=self.execution_window();at=self.clock.now()
        require(prior is not None and parse_utc(at)>=parse_utc(prior['deadline']), 'Prior execution window is still active')
        require(not a['spent_unsealed'], 'Spent unsealed work requires separate recovery')
        remaining=self.remaining_tasks(a);require(remaining, 'No eligible unsealed tasks remain')
        require(a['attempts']+3*len(remaining)<=self.c['limits']['attempts'] and
                a['bytes']+3*BODY<=self.c['limits']['bytes'], 'Cumulative continuation attempt/byte budget exhausted')
        self.storage(3*BODY)
        records=self.window_records();require(len(records)<MAX_CONTINUATION_WINDOWS, 'Continuation window count exhausted')
        # Recheck authority after potentially lengthy old-archive validation,
        # immediately before granting the additional bounded wall-clock window.
        self.verify_scope();at=self.clock.now()
        r=dict(version=WINDOW_VERSION,ordinal=len(records)+1,job_id=digest(self.c),configuration_sha256=digest(self.c),
            pins={n:sha(self.fs.read(n,8*BODY)) for n in
                  ('job.json','review.json','catalog.json','plan.json','plan-binding.json','asset-map.json','scope-binding.json','window.json')},
            previous_sha256=digest(prior),opened_at=at,first_attempt_at=None,
            deadline=format_utc(parse_utc(at)+timedelta(seconds=execution_window_seconds(self.c))),
            sources=continuation_sources(self.c),accounting_before={k:v for k,v in a.items() if k not in ('continuation_windows','execution_window')},
            remaining_tasks=remaining)
        r['record_sha256']=digest(r)
        self.put(f"continuation-windows/{r['ordinal']:04d}/opened.json",r)
        return dict(outcome='CONTINUATION_WINDOW_OPENED_NO_DISPATCH',window=r,remaining_tasks=len(remaining))

    def accounting(self, *, evidence_sources=None):
        if evidence_sources is not None:
            require(self.lock is None, "Historical accounting is inspection only")
        records=self.window_records()
        historical=self.c['sources']!=sources()
        capture=continuation_sources(self.c)['capture_sources'] if historical and evidence_sources is None else source_binding()
        allowed_sources=[capture]+[r['sources']['execution_sources'] for r in records]
        if evidence_sources is not None: allowed_sources=[evidence_sources]
        counts = dict(attempts=0, metadata_attempts=0, bytes=0, sealed=0, covered_empty=0)
        first = None; last = None; unsealed = []; asset = []; reservations=[]
        roots = [self.root/"authority"]
        if self.has("history"):
            roots += sorted((self.root/"history").iterdir())
        for root in roots:
            if not root.exists(): continue
            campaigns = root/"campaigns"
            if not campaigns.exists():
                require(not (root/"registry").exists() and not (root/'prepared.json').exists(), "Registered child missing campaign state")
                continue
            require({p.stem for p in (root/'registry').iterdir()} == {p.name for p in campaigns.iterdir()},
                    'Journal registration/campaign closure changed')
            for directory in sorted(campaigns.iterdir()):
                with self.child(root, directory.name) as j:
                    j.verify_records(); state = j.snapshot()
                    require(j.binding["collector_sources"] in allowed_sources and not state["damage"], "Child source/integrity changed")
                    counts["attempts"] += state["counters"]["attempts"]
                    counts["bytes"] += state["counters"]["response_bytes"]
                    meta = j.binding["mode"] != ce.MODE
                    if meta: counts["metadata_attempts"] += state["counters"]["attempts"]
                    for a in state["attempts"].values():
                        at = parse_utc(a["reserved_at"]); first = min(first,at) if first else at
                        reservations.append(at)
                        stamp = parse_utc(a["at"]); last = max(last,stamp) if last else stamp
                        if a["state"] != "received" or not a.get("response_bytes") or a.get('status') != 200:
                            unsealed.append(a["attempt_key"])
                    for key,s in state["intervals"].items():
                        if s["complete"]:
                            j.completed(key)
                            counts["sealed"] += 1
                            counts["covered_empty"] += s["state"] == "complete_empty"
                            asset.append(dict(root=str(root), campaign_id=directory.name, task_id=key,
                                seal=s["complete"], original_checked_at=s["complete"]["checked_at"],
                                series_metadata_reference=dict(path='catalog.json',stream_id=j.tasks[key]['identity']['stream_id'])))
                        elif any(a["interval_key"] == key for a in state["attempts"].values()): unsealed.append(key)
        if first is not None:
            window = dict(first_attempt_at=format_utc(first), deadline=format_utc(first+timedelta(seconds=execution_window_seconds(self.c))))
            if self.has("window.json"):
                require(self.get("window.json") == window, "Original job deadline changed")
            else:
                require(evidence_sources is None, "Historical job deadline missing; preserve state")
                self.put("window.json", window)
        else:
            require(not self.has("window.json"), "Attempt state missing behind job deadline")
            window = None
        if self.clock.fake and last and parse_utc(self.clock.now()) < last:
            self.clock.value = last
        self.last_dispatch = last
        result=dict(**counts, window=window, spent_unsealed=sorted(set(unsealed)), assets=asset)
        if records:
            windows=[]
            for r in records:
                baseline=r['accounting_before']
                require(all(counts[k]>=baseline[k] for k in counts) and baseline['window']==window and
                        all(v in asset for v in baseline['assets']), 'Continuation cumulative accounting/seals changed')
                times=[t for t in reservations if parse_utc(r['opened_at'])<=t<parse_utc(r['deadline'])]
                marker=f"continuation-windows/{r['ordinal']:04d}/first-attempt.json"
                start=dict(window_sha256=digest(r),first_attempt_at=format_utc(min(times)),deadline=r['deadline']) if times else None
                if self.has(marker):require(start==self.get(marker), 'Continuation reservation history changed')
                elif start:
                    require(evidence_sources is None and self.lock is not None, 'Continuation reservation marker missing; preserve state')
                    self.put(marker,start)
                windows.append(dict(ordinal=r['ordinal'],opened_at=r['opened_at'],
                    first_attempt_at=start['first_attempt_at'] if start else None,deadline=r['deadline']))
            result.update(continuation_windows=windows,execution_window=windows[-1])
        if self.has('status.json'):
            old=self.get('status.json').get('accounting',{})
            require(all(counts[k]>=old.get(k,0) for k in counts), 'Previously recorded job accounting disappeared')
            require({(a['campaign_id'],a['task_id']) for a in old.get('assets',[])} <=
                    {(a['campaign_id'],a['task_id']) for a in asset}, 'Completed work disappeared')
        return result

    def capacity(self, attempts, body_bytes, metadata=0):
        a = self.accounting(); limits = self.c["limits"]
        require(not a["spent_unsealed"], "Spent unsealed child requires review; no automatic retry")
        require(a["attempts"]+attempts <= limits["attempts"] and
                a["metadata_attempts"]+metadata <= limits["metadata_attempts"] and
                a["bytes"]+body_bytes <= limits["bytes"], "Whole-job attempt/byte capacity exhausted")
        window=self.execution_window()
        if window: require(parse_utc(self.clock.now()) < parse_utc(window['deadline']), 'Execution window deadline exhausted' if self.has('continuation-windows') else 'Original job deadline exhausted')
        self.storage(body_bytes)
        return a

    def stop_requested(self):
        stops = self.fs.list("stops") if self.has("stops") else []
        acknowledgements = self.fs.list("resumes") if self.has("resumes") else []
        return bool(set(stops)-set(acknowledgements))

    def acknowledge_stop(self):
        for name in self.fs.list("stops") if self.has("stops") else []:
            if not self.has("resumes/"+name): self.put("resumes/"+name, dict(at=self.clock.now()))

    def dispatch(self, request, timeout):
        # Journal reservation/start are already durable. This marker is NOT a
        # second request ledger: reconstruct the first reservation after close.
        require(self.c["fixture"] is not None or self.c["enabled"], "Live job disabled")
        if self.c['version'] in REVIEWED_VERSIONS:
            require(self.config_path is not None and config(self.config_path)[0] == self.c,
                    'Runtime execution configuration changed')
            url=urlsplit(request.full_url); q=parse_qs(url.query)
            if url.path == '/v2/datapoints' and q.get('$limit') != ['1']:
                self.verify_scope()
                require(q.get('datastream_id') in [[s] for s in self.c['streams']] and
                        len(q.get('time[$gte]',[]))==len(q.get('time[$lt]',[]))==1,
                        'Reviewed history request identity/interval required')
                sid=q['datastream_id'][0];lo=parse_utc(q['time[$gte]'][0]);hi=parse_utc(q['time[$lt]'][0])
                if self.c['version']==STREAM_SCOPED_VERSION:
                    scope=stream_scope(self.c,sid)
                    require(parse_utc(scope['start'])<=lo<hi<=parse_utc(scope['end']),
                            'Dispatch outside exact per-stream scope')
                require(any(t['stream_id']==sid and parse_utc(t['start'])<=lo<hi==parse_utc(t['end'])
                            for t in self.get('plan.json')['tasks']), 'Dispatch outside bound plan')
            else:
                require(not self.has('scope-binding.json'), 'Reviewed job forbids additional metadata/witness requests')
        if self.c["version"] == IMPORT_VERSION:
            from . import evidence_import as imported
            require(self.c["enabled"] and self.config_path is not None and config(self.config_path)[0] == self.c,
                    "Imported execution configuration changed/disabled")
            imported.pins(self.c)
            require(self.transfer_cache is not None and self.get('import.json') == self.transfer_cache[0] and
                    self.get('review.json') == self.transfer_cache[0]['review'], "Runtime imported review changed")
            q = parse_qs(urlsplit(request.full_url).query)
            require(urlsplit(request.full_url).path == '/v2/datapoints' and '$limit' in q and
                    q['$limit'] != ['1'] and q.get('datastream_id',[None])[0] in self.c['streams'],
                    "Imported jobs allow history requests only")
        if not self.has('window.json'):
            # The collector has already persisted reserved/started records. A
            # crash here cannot hide that attempt: closeout/restart reconstructs
            # this exact marker from those same records.
            records=[]
            for root in [self.root/'authority',self.root/'history']:
                if root.exists():
                    for p in root.glob('**/events/*/*.json'):
                        e=read(p)
                        if e['kind']=='reserved':records.append(parse_utc(e['at']))
            require(records,'Durable reservation must precede job dispatch')
            first=min(records)
            self.put('window.json',dict(first_attempt_at=format_utc(first),deadline=format_utc(first+timedelta(seconds=execution_window_seconds(self.c)))))
        window=self.execution_window()
        if self.has('continuation-windows') and not self.has(f"continuation-windows/{window['ordinal']:04d}/first-attempt.json"):
            # The current child holds its writer lock. Read its durable
            # reservations directly, as for the original window above; do not
            # attempt to reopen that Journal. Closeout verifies this marker
            # against the complete original accounting chain.
            reservations=[]
            for n in window['remaining_tasks']:
                for path in (self.root/'history'/str(n)).glob('campaigns/*/events/*/*.json'):
                    event=read(path)
                    if event['kind']=='reserved':
                        require(event['record_sha256']==digest({k:v for k,v in event.items() if k!='record_sha256'}),
                                'Continuation reservation record changed')
                        at=parse_utc(event['at'])
                        if parse_utc(window['opened_at'])<=at<parse_utc(window['deadline']):reservations.append(at)
            require(reservations, 'Durable continuation reservation required before dispatch')
            self.put(f"continuation-windows/{window['ordinal']:04d}/first-attempt.json",
                dict(window_sha256=digest(window),first_attempt_at=format_utc(min(reservations)),deadline=window['deadline']))
        deadline = parse_utc(window['deadline'])
        if deadline: timeout = min(timeout,(deadline-parse_utc(self.clock.now())).total_seconds())
        require(timeout > 0, "Original job deadline exhausted")
        if self.last_dispatch:
            delay = max(0,1-(parse_utc(self.clock.now())-self.last_dispatch).total_seconds())
            require(delay < timeout, "Job pacing exceeds request deadline")
            self.clock.wait(delay); timeout -= delay
        self.last_dispatch = parse_utc(self.clock.now())
        if self.fixture is not None:
            q = parse_qs(urlsplit(request.full_url).query); path = urlsplit(request.full_url).path
            if path.endswith('/dt-unit'): value = self.fixture['vocabulary']
            elif '/stations/' in path: value = self.fixture['stations'][path.rsplit('/',1)[1]]
            elif path.endswith('/datastreams'): value = self.fixture['datastreams'][q['station_id'][0]]
            elif q.get('$limit') == ['1']: value = self.fixture['witnesses'][q['datastream_id'][0]]
            else:
                if self.fixture.get('crash_history'): os._exit(77)
                value = self.fixture['history'][q['time[$gte]'][0]]
            return Reply(value.get('body',value), value.get('status',200))
        end = parse_utc(self.clock.now())+timedelta(seconds=timeout)
        return anonymous_executor(end)(request,timeout=timeout)

    def metadata(self):
        require(self.c['version'] != IMPORT_VERSION, "Imported job forbids repeat metadata/witness requests")
        if self.has("metadata.json"): return self.get("metadata.json")
        package = ap.make(self.inventory, selected_ids=self.c['streams'], checkpoint=self.c['sources']['checkpoint'])
        n = package['ceilings']['http_attempts']
        # Reserve worst-case capacity for the entire bounded metadata package;
        # its own Journals enforce the per-child caps, including crash spending.
        self.capacity(n,n*BODY,n)
        root = self.root/'authority'
        if not root.exists(): root.mkdir(mode=0o700)
        if (root/'authorization.json').exists(): authorization=read(root/'authorization.json')
        else:
            start=self.clock.now(); end=parse_utc(start)+timedelta(seconds=min(package['ceilings']['wall_seconds'],execution_window_seconds(self.c)))
            if self.has('window.json'): end=min(end,parse_utc(self.get('window.json')['deadline']))
            authorization=dict(package_id=package['package_id'],approval_reference='Explicit local operator metadata command',window_start=start,window_end=format_utc(end))
        try:
            result=ap.run(root,package,self.inventory,provider_metadata.load_authority(self.inventory,self.c['catalog']),
                authorization=authorization,executor=self.dispatch,wait=self.clock.wait,now=self.clock.now,monotonic=self.clock.monotonic)
        finally: self.accounting()
        self.put('metadata.json',result)
        proposed={}
        for sid,row in result.items():
            proposed[sid]=dict(status='PENDING' if 'witness' in row else 'HOLD',metadata=row,
                source_review=None,native_review=None,placement_review=None,scope=None)
        self.put('review-request.json',dict(job_id=digest(self.c),streams=proposed,
            catalog_reference='catalog.json',
            note='One explicit review input; no automatic acceptance. Depth/deployment, geographic CRS, units/time meaning and source-start review required.'))
        return result

    def review(self, path):
        require(self.c['version'] != IMPORT_VERSION, "Use import-authority for the pinned imported review")
        r=read(path); require(set(r)=={'job_id','streams'} and r['job_id']==digest(self.c) and
            set(r['streams'])==set(self.c['streams']), 'Exact review/selection binding')
        require(not self.has('review.json'), 'Review is immutable; no replacement')
        # Validate everything before storing any accepted caller input.
        bundles={}; scopes={}
        for sid,item in r['streams'].items():
            if item == {'disposition':'EXCLUDE'}:
                require(self.c['version'] not in REVIEWED_VERSIONS, 'Exact admitted roster cannot contain excluded/held streams')
                continue
            bundle,scope=self.review_stream(sid,item); bundles[sid]=bundle;scopes[sid]=scope
        require(bundles, 'No reviewed eligible stream')
        self.put('review.json',r)
        self.replace_summary('catalog.json',self.series_catalog(r['streams']))
        result=self.make_plan(bundles,scopes)
        if self.c['version'] in REVIEWED_VERSIONS:
            with Root(path.parent) as fs: body=fs.read(path.name,8*BODY)
            require(decode(body)==r, 'Reviewed input changed during binding')
            self.put('scope-binding.json',self.scope_binding(dict(path=str(path),sha256=sha(body))))
        return result

    def scope_binding(self, review_ref):
        """Immutable link to the original accepted input, not a new request ledger."""
        binding=dict(version=self.c['version'],job_id=digest(self.c),configuration_sha256=digest(self.c),
            sources=self.c['sources'],scope=self.c['scope'],
            identities=[self.inventory.identity(s) for s in self.c['streams']],
            review_input=review_ref,review_sha256=digest(self.get('review.json')),
            catalog_sha256=digest(self.get('catalog.json')),plan_sha256=digest(self.get('plan.json')),
            asset_map_sha256=digest(self.get('asset-map.json')))
        if self.c['version']==STREAM_SCOPED_VERSION: binding['stream_scopes']=self.c['stream_scopes']
        return binding

    def verify_scope(self):
        """Recheck scope, original acceptance and currentness before any history dispatch."""
        require(self.config_path is not None and config(self.config_path,allow_continuation=self.continuing)[0] == self.c,
                'Bound execution configuration changed')
        require(self.get('job.json')['configuration']==self.c and self.get('job.json')['job_id']==digest(self.c),
                'Bound job identity changed')
        require(self.has('scope-binding.json'), 'Exact reviewed scope binding required')
        binding=self.get('scope-binding.json');ref=binding['review_input'];path=Path(ref['path'])
        self.window_records()
        require(set(ref)=={'path','sha256'} and path.is_absolute(), 'Original reviewed input reference required')
        with Root(path.parent) as fs:body=fs.read(path.name,8*BODY)
        require(sha(body)==ref['sha256'] and decode(body)==self.get('review.json'), 'Original reviewed input/hash changed')
        require(binding==self.scope_binding(ref), 'Reviewed scope/config/roster/plan binding changed')
        review=self.get('review.json');plan=self.get('plan.json')
        require(set(review)=={'job_id','streams'} and review['job_id']==digest(self.c) and
                set(review['streams'])==set(self.c['streams']), 'Exact admitted roster/job required')
        require(plan['job_id']==digest(self.c) and plan['review_sha256']==digest(review) and
                self.get('plan-binding.json')==dict(sha256=digest(plan)), 'Bound plan/review changed')
        for sid in self.c['streams']:
            self.review_stream(sid,review['streams'][sid])
        if self.c['version']==STREAM_SCOPED_VERSION:
            for task in plan['tasks']:
                scope=stream_scope(self.c,task['stream_id'])
                require(parse_utc(scope['start'])<=parse_utc(task['start'])<parse_utc(task['end'])<=parse_utc(scope['end']),
                        'Bound task outside exact per-stream scope')
                require(task['series_metadata_reference']==dict(path='catalog.json',stream_id=task['stream_id']),
                        'Bound task metadata identity changed')
        require(self.get('catalog.json')==self.series_catalog(review['streams']), 'Bound metadata/placement/attribution changed')
        result=dict(outcome='VALIDATED_REVIEWED_SCOPE_NO_DISPATCH',job_id=digest(self.c),
                    scope=self.c['scope'],streams=self.c['streams'],planned_tasks=len(plan['tasks']))
        if self.c['version']==STREAM_SCOPED_VERSION: result['stream_scopes']=self.c['stream_scopes']
        return result

    def review_stream(self,sid,item):
        if self.c['version'] == IMPORT_VERSION:
            from . import evidence_import as imported
            inputs = imported.pins(self.c)
            bundle,scope,accepted = imported.stream(self.c,self.inventory,inputs['review_request'],inputs['final_review'],
                                                    sid,now=self.clock.now())
            require(item == accepted, 'Imported runtime review changed')
            return bundle,scope
        require(set(item)=={'source_review','native_review','placement_review','scope'}, 'Explicit source/native/placement review required')
        scope=item['scope']; lo,hi=map(parse_utc,(scope['start'],scope['end']))
        if self.c['version'] in REVIEWED_VERSIONS:
            require(scope==stream_scope(self.c,sid), 'Review differs from exact configured scope')
        else:
            require(parse_utc(SCOPE['start']) <= lo < hi <= parse_utc(SCOPE['end']), 'Review outside approved WY2026')
        root=self.root/'authority';package=read(root/'package.json');station=self.inventory.identity(sid)['station_id']
        with self.child(root,ap.metadata_campaign_id(package,station)) as mj, self.child(root,ap.witness_campaign_id(package,sid)) as wj:
            nr=item['native_review'];require(nr['disposition']=='ACCEPT_NATIVE' and nr['scope']==scope, 'Caller native acceptance/scope required')
            if self.c['sources']!=sources():
                from . import preparation_recovery as pr, presentation
                require(self.continuing or self.has('continuation-windows'), 'Explicit continuation required for prior supervisor')
                old=continuation_sources(self.c)['capture_sources']
                expected=ap.make(self.inventory,selected_ids=self.c['streams'],checkpoint=ap.current_checkpoint())
                expected.update(checkpoint=self.c['sources']['checkpoint'],collector_fingerprint=digest(old))
                expected['package_id']=digest({k:v for k,v in expected.items() if k!='package_id'})
                require(package==expected, 'Original authority package changed')
                meta=pr._metadata(mj,self.inventory,provider_metadata.load_authority(self.inventory,self.c['catalog']),
                    package,station,ap.station_groups(package)[station],old,self.clock.now())[sid]
                packet=meta['packet'];aw.evidence(wj,sid)
                require(wj.binding['collector_sources']==old and wj.binding['metadata_packets']=={sid:packet},
                        'Original witness/metadata source changed')
                source=presentation.review_journal_first(wj,sid,review=item['source_review'],as_of=self.clock.now())
                original=eligibility.decide(self.inventory,encode(packet),nr,
                    executor_fingerprint=self.c['sources']['collector'],now=self.clock.now())
                require(original['native_acquisition_eligible'] and source['state']==presentation.REVIEWED and
                        parse_utc(source['start'])<=lo, 'Original authority expired or held')
                # Only new child execution provenance changes. Original config,
                # review, packet, reviewed_at and expiry bytes remain untouched.
                nr=dict(nr,executor_fingerprint=digest(source_binding()))
                decision=eligibility.decide(self.inventory,encode(packet),nr,
                    executor_fingerprint=digest(source_binding()),now=self.clock.now())
                require(decision['native_acquisition_eligible'], 'Continuation native authority HOLD')
                bundle=dict(packet=packet,review=nr,decision=decision)
            else:
                meta=ma.packet_evidence(mj,sid,now=self.clock.now());packet=meta['packet']
                decision=eligibility.decide(self.inventory,encode(packet),nr,executor_fingerprint=digest(source_binding()),now=self.clock.now())
                bundle=dict(packet=packet,review=nr,decision=decision)
                status=ap.reviewed_status(self.inventory,sid,metadata_journal=mj,witness_journal=wj,
                    source_review=item['source_review'],native_bundle=bundle,now=self.clock.now())
                require(status['status']=='AUTHORITY_READY' and parse_utc(status['source_start']['start'])<=lo, 'Source-start/native authority HOLD')
            place=item['placement_review']
            require(set(place)=={'disposition','reviewer_ref','station_metadata_sha256','configuration_evidence_sha256',
                                'depth_cm','crs','timestamp_meaning','evidence','scope'} and
                    place['disposition']=='ACCEPT_PLACEMENT' and isinstance(place['reviewer_ref'],str) and bool(place['reviewer_ref']) and
                    place['scope']==scope and place['crs']=='EPSG:4326' and place['timestamp_meaning']=='UTC t; preserve native timestamps' and
                    type(place['depth_cm']) in (int,float) and place['depth_cm']==self.inventory.identity(sid)['depth_cm'] and
                    place['station_metadata_sha256']==packet['access_evidence']['station_metadata_sha256'] and
                    place['configuration_evidence_sha256']==digest(packet['configuration_evidence']), 'Depth/location/deployment review required')
            station_data,_=ma._receipt(mj,ma._spec(mj.binding,'station',sid))
            require(station_data.get('geometry') is not None and station_data['geo_protected'] is False, 'Public station-specific geometry required')
            require(isinstance(place['evidence'],list) and 0<len(place['evidence'])<=8, 'Original placement evidence required')
            for ref in place['evidence']:
                require(set(ref)=={'path','sha256'} and sha(Path(ref['path']).read_bytes())==ref['sha256'], 'Placement evidence binding')
        return bundle,scope

    def reused(self,bundles):
        intervals={sid:[] for sid in bundles}; assets=[]
        from . import daily_handoff as handoff
        for e in self.c['reuse']:
            sid=e['identity']['stream_id']
            if sid not in bundles: continue
            with self.child(Path(e['root']),e['campaign_id']) as j:
                require(j.header_sha==e['header_sha256'] and digest(j.binding['collector_sources'])==e['source_fingerprint'], 'Reuse source/header changed')
                task=j.tasks[e['task_id']];require(all(task[k]==e[k] for k in ('identity','start','end')), 'Reuse exact identity/scope')
                handoff._binding(j.binding,j.tasks,self.inventory,e['source_fingerprint'])
                if self.c['version'] == IMPORT_VERSION:
                    env,oldwin=sealed_history.referenced_archive(j,e,self.inventory)
                else:
                    env,seal,_=handoff._seal(j,e['task_id'],j.snapshot(),self.inventory,e['source_fingerprint'])
                    ev=[v for v in j.events if v['kind']=='sealed' and v['data']==seal]
                    require(len(ev)==1 and ev[0]['record_sha256']==e['seal_sha256'] and seal['objects'][0]['sha256']==e['archive_sha256'], 'Reuse seal/archive binding')
                    oldwin=sealed_history.review_interval(task,j.binding['reviewed_bundles'][sid]['decision'])
                new=bundles[sid]['decision']
                require(any(w['object_sha256']==oldwin['object_sha256'] and w['start']==oldwin['start'] and w['end']==oldwin['end'] for w in new['configuration_windows']), 'Reuse configuration contradiction')
                intervals[sid].append((parse_utc(e['start']),parse_utc(e['end'])))
                assets.append(dict(**e,original_retrieval_times=[p['retrieved_at_utc'] for p in env['pages']],
                    series_metadata_reference=dict(path='catalog.json',stream_id=sid),
                    association_only=True,historical_attribution_overwritten=False))
        return intervals,assets

    def make_plan(self,bundles,scopes):
        if self.c['version']==STREAM_SCOPED_VERSION:
            require(set(bundles)==set(self.c['streams']) and scopes==self.c['stream_scopes'],
                    'Planner requires exact admitted per-stream scopes')
        reused,assets=self.reused(bundles);planned=[]
        for sid in self.c['streams']:
            if sid not in bundles:continue
            lo,hi=map(parse_utc,(scopes[sid]['start'],scopes[sid]['end'])); gaps=[(lo,hi)]
            for a,b in sorted(reused[sid]):
                next_gaps=[]
                for x,y in gaps:
                    if b<=x or a>=y: next_gaps.append((x,y));continue
                    if x<a:next_gaps.append((x,a))
                    if b<y:next_gaps.append((b,y))
                gaps=next_gaps
            for x,y in gaps:
                while x<y:
                    end=min(y,x+timedelta(days=30))
                    cuts=[parse_utc(w[k]) for w in bundles[sid]['decision']['configuration_windows'] for k in ('start','end') if w[k] and x<parse_utc(w[k])<end]
                    if cuts:end=min(cuts)
                    require(sum(parse_utc(w['start'])<=x and (w['end'] is None or end<=parse_utc(w['end']))
                        for w in bundles[sid]['decision']['configuration_windows'])==1, 'Unreviewed configuration gap')
                    planned.append(dict(stream_id=sid,start=format_utc(x),end=format_utc(end),
                        series_metadata_reference=dict(path='catalog.json',stream_id=sid)));x=end
        self.put('asset-map.json',dict(reused=assets,new_native_root=str(self.root/'history'),logs_are_not_archives=True))
        plan=dict(job_id=digest(self.c),review_sha256=digest(self.get('review.json')),tasks=planned)
        self.put('plan.json',plan)
        self.put('plan-binding.json',dict(sha256=digest(plan)))
        return dict(planned_tasks=len(planned),reused_seals=len(assets))

    def acquire(self,resume=False):
        if self.c['version'] == IMPORT_VERSION: self.verify_import()
        if self.c['version'] in REVIEWED_VERSIONS: self.verify_scope()
        require(self.has('review.json') and self.has('plan.json'), 'Explicit valid reviewed input/plan required')
        if resume:self.acknowledge_stop()
        plan=self.get('plan.json');review=self.get('review.json')
        require(plan['job_id']==digest(self.c) and plan['review_sha256']==digest(review) and
                self.get('plan-binding.json')['sha256']==digest(plan),'Plan/review binding')
        if self.has('history'):
            require({p.name for p in (self.root/'history').iterdir()} <= {str(n) for n in range(len(plan['tasks']))},'Foreign history child')
        for n,t in enumerate(plan['tasks']):
            if self.stop_requested():return dict(outcome='STOPPED_AT_TASK_BOUNDARY')
            root=self.root/'history'/str(n)
            saved=root/'prepared.json'
            if saved.exists():
                p=read(saved);binding,tasks=p['binding'],p['tasks']
                require(len(tasks)==1 and all(v['identity']['stream_id']==t['stream_id'] and
                        v['start']==t['start'] and v['end']==t['end'] for v in tasks.values()), 'Preserved task differs from plan')
                with self.child(root,binding['campaign_id']) as j:
                    if all(j.completed(k) is not None for k in tasks):continue
                raise Hold('Existing unsealed child requires review; never reopen spent history')
            require(not root.exists(),'Incomplete child initialization; preserve and review')
            a=self.capacity(3,3*BODY)
            bundle,scope=self.review_stream(t['stream_id'],review['streams'][t['stream_id']])
            require(parse_utc(scope['start'])<=parse_utc(t['start'])<parse_utc(t['end'])<=parse_utc(scope['end']), 'Task exceeds review')
            cid='local-'+digest(dict(job=digest(self.c),ordinal=n,task=t))[:50]
            manifest=campaign.make_campaign(self.inventory,campaign_id=cid,executor_fingerprint=digest(source_binding()),
                horizons={t['stream_id']:dict(start=t['start'],end=t['end'])},decisions={t['stream_id']:bundle['decision']},
                budgets=campaign.policy(logical_requests=3,attempts=3,total_bytes=3*BODY,wall_seconds=600))
            binding,tasks=ce.prepare(manifest,self.inventory,{t['stream_id']:bundle},now=self.clock.now())
            require(len(tasks)==1,'One original bounded task per child')
            root.mkdir(parents=True,mode=0o700)
            with Root(root) as fs:fs.write_new('prepared.json',encode(dict(binding=binding,tasks=tasks)),BODY)
            end=parse_utc(self.clock.now())+timedelta(seconds=600)
            window=self.execution_window()
            if window:end=min(end,parse_utc(window['deadline']))
            try:
                with Journal(root,binding,tasks,create=True,inventory=self.inventory,now=self.clock.now,monotonic=self.clock.monotonic) as j:
                    result=CampaignAdapter(j).run(executor=self.dispatch,wait=self.clock.wait,window_end=format_utc(end),task_keys=list(tasks))
                    require(all(j.completed(k) is not None for k in tasks),'History HOLD; preserve partial receipts')
            finally:self.summary(dict(outcome='TASK_CLOSEOUT',accounting=self.accounting()))
            if self.fixture and self.fixture.get('stop_after_task')==n+1:
                self.put('stops/offline-boundary.json',dict(at=self.clock.now()))
        return dict(outcome='COMPLETE_FOR_DECLARED_SCOPE')

    def verify_import(self):
        from . import evidence_import as imported
        require(self.c['enabled'], 'Imported execution config disabled')
        validated = self.transfer_cache or imported.validate(self.c,self.inventory,now=self.clock.now())
        self.transfer_cache = validated
        require(self.has('import.json') and self.get('import.json') == validated[0] and
                self.has('review.json') and self.get('review.json') == validated[0]['review'],
                'Missing/changed imported job/review binding')
        summary,buffers = imported.plan(self.c,self.inventory,validated)
        require(all(self.has(n) and self.get(n) == buffers[n] for n in ('plan.json','plan-binding.json','asset-map.json')),
                'Imported deterministic plan changed')
        return summary

    def import_authority(self):
        from . import evidence_import as imported
        require(self.c['version'] == IMPORT_VERSION, 'Explicit imported job required')
        validated = self.transfer_cache or imported.validate(self.c,self.inventory,now=self.clock.now())
        self.transfer_cache = validated
        if self.has('import.json'): return self.verify_import()
        require(not any(self.has(n) for n in ('review.json','plan.json','plan-binding.json','asset-map.json','history')),
                'Partial import; preserve and review')
        summary,buffers = imported.plan(self.c,self.inventory,validated)
        self.put('import.json',validated[0]);self.put('review.json',validated[0]['review'])
        self.replace_summary('catalog.json',self.series_catalog(validated[0]['review']['streams']))
        for n in ('asset-map.json','plan.json','plan-binding.json'): self.put(n,buffers[n])
        return dict(outcome='IMPORTED_REVIEWED_NO_REQUESTS',**summary)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['inspect','prepare','metadata','review','validate-scope','continue-window','acquire','resume','status','stop','recover-review',
                                'bind-import','validate-import','finalize-import','import-authority'])
    p.add_argument('config');p.add_argument('--review');p.add_argument('--allow-provider',action='store_true')
    p.add_argument('--offline-now')
    p.add_argument('--output-root')
    p.add_argument('--donor-config');p.add_argument('--request');p.add_argument('--final-review');p.add_argument('--recovery')
    p.add_argument('--output-config');p.add_argument('--job-root');p.add_argument('--enable',action='store_true')
    args=p.parse_args(argv)
    job=None
    try:
        if read(Path(args.config).absolute()).get('version') == 'dendra-local-native-job-5':
            from . import native_program
            out = native_program.main(args)
            print(encode(out).decode(), end='')
            return 0
        if args.mode in ('bind-import','finalize-import'):
            from . import evidence_import as imported
            require(not args.allow_provider and args.output_root is None and args.review is None,
                    'Offline configuration operation only')
            raw=read(Path(args.config).absolute());fixture=read(raw['fixture']['path']) if raw['fixture'] else None
            clock=Clock(fixture,args.offline_now)
            if args.mode=='bind-import':
                require(all((args.donor_config,args.request,args.final_review,args.recovery,args.output_config,args.job_root)) and
                        not args.enable, 'Complete explicit donor/review/output references required')
                out=imported.bind(Path(args.config).absolute(),donor_config=args.donor_config,request=args.request,
                    review=args.final_review,recovery_ref=args.recovery,output=Path(args.output_config).absolute(),
                    job_root=args.job_root,now=clock.now())
            else:
                require(args.enable and args.output_config is not None, 'Explicit --enable and new output config required')
                out=imported.finalize(Path(args.config).absolute(),Path(args.output_config).absolute(),now=clock.now())
            print(encode(out).decode(),end='');return 0
        require(not any((args.donor_config,args.request,args.final_review,args.recovery,args.output_config,args.job_root,args.enable)),
                'Configuration binding arguments require bind-import/finalize-import')
        if args.mode == 'recover-review':
            require(args.output_root is not None and not args.allow_provider and args.offline_now is None and
                    args.review is None, 'Offline evidence output only; no provider/review/clock override')
            from .preparation_recovery import recover_review
            out = recover_review(Path(args.config).absolute(), Path(args.output_root).absolute())
            print(encode(out).decode(), end=''); return 0
        require(args.output_root is None or args.mode=='validate-import', 'Output root is only for offline evidence export')
        c,inv=config(Path(args.config).absolute(),allow_continuation=args.mode=='continue-window')
        fixture=read(c['fixture']['path']) if c['fixture'] else None
        if fixture is not None:
            def no_socket(event,unused):
                if event.startswith('socket.'):raise Hold('Offline network attempt refused')
            sys.addaudithook(no_socket)
        if args.mode=='continue-window':
            require(not args.allow_provider and args.review is None and (fixture is not None or c['enabled']),
                    'Explicit offline continuation for an enabled job required')
        if args.mode in ('metadata','acquire','resume','continue-window'):
            require(args.mode=='continue-window' or ((c['enabled'] and (fixture is not None or args.allow_provider)) if c['version']==IMPORT_VERSION else
                    fixture is not None or (c['enabled'] and args.allow_provider)), 'Explicit enabled job and provider permission required')
            require(c['version']!=IMPORT_VERSION or args.mode!='metadata','Imported job forbids repeat metadata/witness requests')
            if fixture is None:
                env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
                status=subprocess.check_output(['git','status','--short'],cwd=REPO,env=env,text=True).strip()
                require(status in ('','?? .l01-soil-integration/'),'Commit reviewed source before live launch; no dirty checkout')
                subprocess.check_call(['git','ls-files','--error-unmatch','scripts/dendra/acquire_native.R',
                    'scripts/dendra/history_acquisition/local_job.py'],cwd=REPO,env=env,stdout=subprocess.DEVNULL)
        job=Job(c,inv,clock=Clock(fixture,args.offline_now),fixture=fixture)
        job.config_path=Path(args.config).absolute()
        job.continuing=args.mode=='continue-window'
        if c['version']==IMPORT_VERSION and args.mode not in ('inspect','stop'):
            from . import evidence_import as imported
            job.transfer_cache=imported.validate(c,inv,now=job.clock.now())
        if args.mode=='validate-import':
            require(job.transfer_cache is not None and not args.allow_provider and args.review is None,
                    'Offline imported configuration inspection required')
            summary,buffers=imported.plan(c,inv,job.transfer_cache)
            out=dict(outcome='VALIDATED_IMPORT_NO_INITIALIZATION',binding=job.transfer_cache[0],planning=summary,
                     plan=buffers['plan.json'],asset_map=buffers['asset-map.json'],next_expiry=job.transfer_cache[0]['next_expiry'])
            if args.output_root:
                imported.write_new(Path(args.output_root).absolute()/'IMPORT_REVIEW_BINDING.json',out)
            print(encode(out).decode(),end='');return 0
        if args.mode=='inspect':out=dict(outcome='INSPECTED_NO_DISPATCH',configuration=c)
        elif args.mode=='stop':
            with Root(job.root) as fs:
                require(decode(fs.read('job.json',8*BODY))['configuration']==c,'Stop job mismatch')
                name='stops/'+digest(dict(at=utc_now(),pid=os.getpid()))+'.json'
                fs.write_new(name,encode(dict(requested_at=utc_now())),4096)
            out=dict(outcome='STOP_REQUESTED_TASK_BOUNDARY')
        else:
            with job.open(create=args.mode=='prepare'):
                try:
                    if args.mode=='metadata':out=dict(outcome='REVIEW_REQUIRED',results=job.metadata())
                    elif args.mode=='review':
                        require(args.review is not None,'Review file required');out=job.review(Path(args.review).absolute())
                    elif args.mode=='import-authority':out=job.import_authority()
                    elif args.mode=='continue-window':out=job.continue_window()
                    elif args.mode=='validate-scope':
                        require(c['version'] in REVIEWED_VERSIONS and not args.allow_provider,
                                'Offline reviewed-scope validation required')
                        out=job.verify_scope()
                    elif args.mode in ('acquire','resume'):out=job.acquire(args.mode=='resume')
                    else:out=dict(outcome=('PREPARED_AWAITING_IMPORT' if c['version']==IMPORT_VERSION else
                                           'PREPARED_DISABLED') if args.mode=='prepare' else 'STATUS')
                    out['accounting']=job.accounting()
                    job.summary(out)
                except (Hold,ValueError,KeyError,TypeError,OSError):
                    # Command outcome only, never an alternate attempt ledger.
                    # Keep failure evidence even when state reconciliation fails.
                    job.put('logs/hold-'+str(time.time_ns())+'.json',dict(mode=args.mode,outcome='HOLD',
                        at=job.clock.now(),accounting_source='original Journals; no retry/refund'))
                    raise
        print(encode(out).decode(),end='');return 0
    except (Hold,ValueError,KeyError,TypeError,OSError) as exc:
        # Never print provider bodies, URLs, credentials or arbitrary exceptions.
        print(encode(dict(outcome='HOLD',reason=str(exc) if isinstance(exc,Hold) else type(exc).__name__,
                          accounting='consult preserved original Journals',provider_retry=False)).decode(),end='')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
