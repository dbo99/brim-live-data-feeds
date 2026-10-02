"""Independent native job supervision; existing Journals are the request ledger.

No daily science, publisher, alternate HTTP stack or automatic review decision.
The offline fixture route is permanently bound to its job and denies sockets.
"""
import argparse
from contextlib import contextmanager
from datetime import timedelta
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
REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "scripts/dendra/acquire_native.R"
LIMITS = dict(attempts=1500, metadata_attempts=64, bytes=1073741824, seconds=7200)
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


def config(path):
    c = read(path)
    require(set(c) == {"version", "inventory", "catalog", "catalog_sha256", "streams", "scope", "limits",
                      "reserve_bytes", "root", "sources", "enabled", "fixture", "reuse",
                      "attribution", "organization_labels"}, "Exact job configuration fields")
    require(c["version"] == VERSION and c["sources"] == sources(), "Job source/checkpoint changed")
    require(c["scope"] == SCOPE and type(c["enabled"]) is bool, "Exact WY2026 scope/permission required")
    require(set(c["limits"]) == set(LIMITS) and all(type(c["limits"][k]) is int and
            0 < c["limits"][k] <= v for k,v in LIMITS.items()), "Whole-job ceilings")
    require(type(c["reserve_bytes"]) is int and c["reserve_bytes"] >= BODY, "Explicit storage safety reserve")
    inv = Inventory.load(c["inventory"], INVENTORY_SHA256)
    require(sha(Path(c['catalog']).read_bytes()) == c['catalog_sha256'], 'Original metadata catalog changed')
    require(isinstance(c["streams"], list) and 0 < len(c["streams"]) <= 28 and
            len(set(c["streams"])) == len(c["streams"]), "Exact unique bounded selection")
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
            scope=review.get('scope',self.c['scope'])
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

    def accounting(self):
        counts = dict(attempts=0, metadata_attempts=0, bytes=0, sealed=0, covered_empty=0)
        first = None; last = None; unsealed = []; asset = []
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
                    require(j.binding["collector_sources"] == source_binding() and not state["damage"], "Child source/integrity changed")
                    counts["attempts"] += state["counters"]["attempts"]
                    counts["bytes"] += state["counters"]["response_bytes"]
                    meta = j.binding["mode"] != ce.MODE
                    if meta: counts["metadata_attempts"] += state["counters"]["attempts"]
                    for a in state["attempts"].values():
                        at = parse_utc(a["reserved_at"]); first = min(first,at) if first else at
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
            window = dict(first_attempt_at=format_utc(first), deadline=format_utc(first+timedelta(seconds=self.c["limits"]["seconds"])))
            if self.has("window.json"):
                require(self.get("window.json") == window, "Original job deadline changed")
            else: self.put("window.json", window)
        else:
            require(not self.has("window.json"), "Attempt state missing behind job deadline")
            window = None
        if self.clock.fake and last and parse_utc(self.clock.now()) < last:
            self.clock.value = last
        self.last_dispatch = last
        result=dict(**counts, window=window, spent_unsealed=sorted(set(unsealed)), assets=asset)
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
        if a["window"]: require(parse_utc(self.clock.now()) < parse_utc(a["window"]["deadline"]), "Original job deadline exhausted")
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
            self.put('window.json',dict(first_attempt_at=format_utc(first),deadline=format_utc(first+timedelta(seconds=self.c['limits']['seconds']))))
        deadline = parse_utc(self.get("window.json")["deadline"])
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
            start=self.clock.now(); end=parse_utc(start)+timedelta(seconds=min(package['ceilings']['wall_seconds'],self.c['limits']['seconds']))
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
        r=read(path); require(set(r)=={'job_id','streams'} and r['job_id']==digest(self.c) and
            set(r['streams'])==set(self.c['streams']), 'Exact review/selection binding')
        require(not self.has('review.json'), 'Review is immutable; no replacement')
        # Validate everything before storing any accepted caller input.
        bundles={}; scopes={}
        for sid,item in r['streams'].items():
            if item == {'disposition':'EXCLUDE'}: continue
            bundle,scope=self.review_stream(sid,item); bundles[sid]=bundle;scopes[sid]=scope
        require(bundles, 'No reviewed eligible stream')
        self.put('review.json',r)
        self.replace_summary('catalog.json',self.series_catalog(r['streams']))
        return self.make_plan(bundles,scopes)

    def review_stream(self,sid,item):
        require(set(item)=={'source_review','native_review','placement_review','scope'}, 'Explicit source/native/placement review required')
        scope=item['scope']; lo,hi=map(parse_utc,(scope['start'],scope['end']))
        require(parse_utc(SCOPE['start']) <= lo < hi <= parse_utc(SCOPE['end']), 'Review outside approved WY2026')
        root=self.root/'authority';package=read(root/'package.json');station=self.inventory.identity(sid)['station_id']
        with self.child(root,ap.metadata_campaign_id(package,station)) as mj, self.child(root,ap.witness_campaign_id(package,sid)) as wj:
            meta=ma.packet_evidence(mj,sid,now=self.clock.now());packet=meta['packet']
            nr=item['native_review'];require(nr['disposition']=='ACCEPT_NATIVE' and nr['scope']==scope, 'Caller native acceptance/scope required')
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
                env,seal,_=handoff._seal(j,e['task_id'],j.snapshot(),self.inventory,e['source_fingerprint'])
                ev=[v for v in j.events if v['kind']=='sealed' and v['data']==seal]
                require(len(ev)==1 and ev[0]['record_sha256']==e['seal_sha256'] and seal['objects'][0]['sha256']==e['archive_sha256'], 'Reuse seal/archive binding')
                old=j.binding['reviewed_bundles'][sid]['decision'];new=bundles[sid]['decision']
                oldwin=sealed_history.review_interval(task,old)
                require(any(w['object_sha256']==oldwin['object_sha256'] and w['start']==oldwin['start'] and w['end']==oldwin['end'] for w in new['configuration_windows']), 'Reuse configuration contradiction')
                intervals[sid].append((parse_utc(e['start']),parse_utc(e['end'])))
                assets.append(dict(**e,original_retrieval_times=[p['retrieved_at_utc'] for p in env['pages']],
                    series_metadata_reference=dict(path='catalog.json',stream_id=sid),
                    association_only=True,historical_attribution_overwritten=False))
        return intervals,assets

    def make_plan(self,bundles,scopes):
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
            if a['window']:end=min(end,parse_utc(a['window']['deadline']))
            try:
                with Journal(root,binding,tasks,create=True,inventory=self.inventory,now=self.clock.now,monotonic=self.clock.monotonic) as j:
                    result=CampaignAdapter(j).run(executor=self.dispatch,wait=self.clock.wait,window_end=format_utc(end),task_keys=list(tasks))
                    require(all(j.completed(k) is not None for k in tasks),'History HOLD; preserve partial receipts')
            finally:self.summary(dict(outcome='TASK_CLOSEOUT',accounting=self.accounting()))
            if self.fixture and self.fixture.get('stop_after_task')==n+1:
                self.put('stops/offline-boundary.json',dict(at=self.clock.now()))
        return dict(outcome='COMPLETE_FOR_DECLARED_SCOPE')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['inspect','prepare','metadata','review','acquire','resume','status','stop'])
    p.add_argument('config');p.add_argument('--review');p.add_argument('--allow-provider',action='store_true')
    p.add_argument('--offline-now')
    args=p.parse_args(argv)
    job=None
    try:
        c,inv=config(Path(args.config).absolute())
        fixture=read(c['fixture']['path']) if c['fixture'] else None
        if fixture is not None:
            def no_socket(event,unused):
                if event.startswith('socket.'):raise Hold('Offline network attempt refused')
            sys.addaudithook(no_socket)
        if args.mode in ('metadata','acquire','resume'):
            require(fixture is not None or (c['enabled'] and args.allow_provider),'Explicit enabled job and provider permission required')
            if fixture is None:
                env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
                status=subprocess.check_output(['git','status','--short'],cwd=REPO,env=env,text=True).strip()
                require(status in ('','?? .l01-soil-integration/'),'Commit reviewed source before live launch; no dirty checkout')
                subprocess.check_call(['git','ls-files','--error-unmatch','scripts/dendra/acquire_native.R',
                    'scripts/dendra/history_acquisition/local_job.py'],cwd=REPO,env=env,stdout=subprocess.DEVNULL)
        job=Job(c,inv,clock=Clock(fixture,args.offline_now),fixture=fixture)
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
                    elif args.mode in ('acquire','resume'):out=job.acquire(args.mode=='resume')
                    else:out=dict(outcome='PREPARED_DISABLED' if args.mode=='prepare' else 'STATUS')
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
