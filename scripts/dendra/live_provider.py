"""Finite explicitly injected provider seam. No import-time/default network path.

Request forms derive from the authenticated R1 metadata manifest and unchanged
d3_plan/transport. This module deliberately imports neither old controller.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import io
from pathlib import Path
import signal
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .transport import DendraFetcher, FetchError, parse_utc, format_utc
from .live_capture import shape
from .live_sampling import window
from .history_acquisition.safety import require, Hold, digest
from .live_state import read
from types import SimpleNamespace
from .live_product import candidate_sample_v2

VERSION = 'dendra-finite-pilot-plan-1'
BASE = 'https://api.dendra.science/v2/'
LIMITS = dict(attempts=11, metadata=5, pages_per_stream=3, response_bytes=8*1024**2,
              aggregate_bytes=16*1024**2, observation_rows=12096, header_bytes=16384,
              error_bytes=65536, request_seconds=25, acquisition_seconds=300, spacing_seconds=1)


def source_fingerprint():
    from .history_acquisition.safety import sha
    root=Path(__file__).resolve().parent
    names=['live_'+n+'.py' for n in ('capture','cli','state','product','sampling','provider','metadata')]
    names += ['transport.py','history_acquisition/safety.py','history_acquisition/observation_quality.py',
              'history_acquisition/__init__.py']
    names += [n for n in ('live_export.py','live_range.py') if (root/n).exists()]
    return digest({n:sha(read(root/n)) for n in names})


def validate_plan(plan, now, *, live=False):
    from .live_metadata import validate_roster
    require(plan['version'] == VERSION and plan['status'] == ('APPROVED' if live else 'OFFLINE_ONLY'),
            'Separate finite live approval required')
    validate_roster(plan['roster'])
    limits = plan['limits']
    ceilings = dict(LIMITS)
    if 'range_targets' in plan:
        from .live_range import validate_range_plan
        validate_range_plan(plan,now,live=live)
        ceilings.update(attempts=256,observation_rows=256*2016)
    require(set(limits) == set(LIMITS) and all(type(limits[k]) in (int,float) and
            0 < limits[k] <= v for k,v in ceilings.items() if k != 'spacing_seconds') and
            type(limits['spacing_seconds']) in (int,float) and 1 <= limits['spacing_seconds'] <= 300, 'Finite policy bound')
    require(all(type(limits[k]) is int for k in LIMITS if k.endswith('bytes') or
                k in ('attempts','metadata','pages_per_stream','observation_rows')), 'Integer limits')
    require(plan['retries'] == 0 and plan['redirects'] == 0 and plan['publication'] is False, 'No retry/redirect/publication')
    require(plan['source_sha256'] == source_fingerprint(), 'Approved source changed')
    lo, hi, at = map(parse_utc, (plan['not_before'],plan['expires_at'],now))
    require(lo <= at < hi and (hi-lo).total_seconds() <= 300, 'Grant expired/not yet valid')
    return window(plan['target_day'], now)


def spec(plan, kind, sid=None, cursor=None, day=None):
    require(kind in ('unit-vocabulary','station','datastream-list','observations'), 'Endpoint kind')
    if kind == 'unit-vocabulary':
        require(sid is None and cursor is None, 'Vocabulary scope')
        url = BASE+'vocabularies/dt-unit'
    else:
        require(sid in plan['roster'], 'Stream outside exact roster')
        station = plan['roster'][sid]['identity']['station_id']
        if kind == 'station': url = BASE+'stations/'+station
        elif kind == 'datastream-list':
            url = BASE+'datastreams?'+urlencode({'station_id':station,'$limit':500,'$sort[_id]':1})
        else:
            if 'range_targets' in plan:
                require([sid,day] in plan['range_targets'], 'Target outside explicit range plan')
            else:
                require(day is None, 'Legacy one-day request')
            win = window(day or plan['target_day'], plan['not_before'])
            require(cursor is not None and parse_utc(win['start']) <= parse_utc(cursor) < parse_utc(win['end']), 'Cursor scope')
            url = BASE+'datapoints?'+urlencode({'datastream_id':sid,'time[$gte]':format_utc(cursor),
                'time[$lt]':win['end'],'$sort[time]':1,'$limit':2016})
        require(kind == 'observations' or cursor is None, 'Metadata pagination not granted')
    result = dict(kind=kind, stream_id=sid, cursor=cursor, url=url)
    if 'range_targets' in plan:
        result['target_day'] = day
    return result


@contextmanager
def deadline(seconds):
    require(threading.current_thread() is threading.main_thread(), 'Serial main-thread requests only')
    require(signal.getitimer(signal.ITIMER_REAL) == (0,0), 'Conflicting process timer')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_): raise TimeoutError('Physical request deadline')
    signal.signal(signal.SIGALRM,expired); signal.setitimer(signal.ITIMER_REAL,seconds)
    try: yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0); signal.signal(signal.SIGALRM,previous)


def explicit_https_opener():
    """Only an explicitly approved caller may inject this; never selected by default.

    urllib/http.client also impose parser line/count limits. The smaller 16 KiB
    accepted-header bound is checked before reading bodies. Wire bytes differ
    from body bytes consumed here; no compression decoding, proxy or cookies.
    """
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open


class Provider:
    def __init__(self, state, plan, *, opener, live=False, authorize=None,
                 now=lambda: datetime.now(timezone.utc), monotonic=time.monotonic, wait=time.sleep):
        require(callable(opener), 'Explicit opener required; no network fallback')
        self.state, self.plan = state, deepcopy(plan)
        self.opener, self.live, self.authorize = opener, live, authorize
        self.now, self.monotonic, self.wait = now, monotonic, wait
        self.win = validate_plan(plan, self.clock(), live=live)
        self.pin = digest(plan)
        self.ranged = 'range_targets' in plan
        if self.ranged:
            from .live_range import series_binding
            require(state.binding == series_binding(plan['roster'],plan['water_year'],plan['ledger_attempt_limit'],
                                                    version=plan.get('persistence_version',1)),
                    'Range state/roster/year binding')
        else:
            require(state.binding.get('plan_sha256') == self.pin, 'State/grant binding')
        repo=Path(__file__).resolve().parents[2]
        require(str(state.root)==plan['output_root'] and repo not in (state.root,*state.root.parents) and
                state.root.stat().st_mode & 0o077 == 0, 'Owner-private granted output root required')
        self.check_authority()
        if self.ranged:
            runs = state.data.setdefault('range_runs',[])
            require(all(r['plan']['grant_id'] != plan['grant_id'] for r in runs), 'Spent range run; explicit new run required')
            require(all((r['plan']['status']=='APPROVED')==live for r in runs),
                    'No fixture/live lineage promotion')
            if live:
                require(state.p1 and not any(a['status'] in ('RESERVED','AMBIGUOUS') for a in state.data['attempts']),
                        'Unresolved live charge; no restart dispatch')
            # An interrupted older run never refunds an unknown reservation.
            for a in state.data['attempts']:
                if a['status'] == 'RESERVED': a['status'] = 'AMBIGUOUS'
            self.start = len(state.data['attempts'])
            runs.append(dict(plan={k:deepcopy(v) for k,v in plan.items() if state.p1 or k!='output_root'},
                             plan_sha256=self.pin,start_ordinal=self.start))
            state.data['range_pending']=[sid+'/'+day for sid,day in plan['range_targets']]
        else:
            require(not state.data['attempts'] and 'provider' not in state.data,
                    'No restart dispatch: retain charged prior session for review')
            self.start = 0
        self.end = monotonic()+min(plan['limits']['acquisition_seconds'],
                                  (parse_utc(plan['expires_at'])-parse_utc(self.clock())).total_seconds())
        state.data['provider'] = dict(plan_sha256=self.pin, started_at=self.clock(),
            deadline_utc=format_utc(parse_utc(self.clock())+timedelta(seconds=self.end-monotonic())),
            live=live, closed=False)
        if self.ranged: state.data['provider']['start_ordinal'] = self.start
        state.commit()

    def clock(self): return format_utc(self.now())

    def check_authority(self):
        validate_plan(self.plan,self.clock(),live=self.live)
        if self.live:
            require(callable(self.authorize) and self.authorize(self.pin,self.clock()) is True,
                    'Exact external approval absent/revoked or source changed')

    def exchange(self, request_spec):
        day = request_spec.get('target_day')
        require(request_spec == spec(self.plan,request_spec['kind'],request_spec['stream_id'],request_spec['cursor'],day),
                'Request substitution')
        require(not self.state.data['provider']['closed'], 'Closed acquisition')
        self.check_authority()
        limits, attempts = self.plan['limits'], self.state.data['attempts'][self.start:]
        kind, sid = request_spec['kind'], request_spec['stream_id']
        if kind == 'observations':
            from .live_metadata import admit_all
            if self.ranged:
                admissions(self.state,self.plan,day,self.clock(),self.start)
            else:
                admit_all(self.state,self.plan,self.clock())  # Fresh source gates before every physical page.
            require(sum(a['rows'] or 0 for a in attempts)+2016 <= limits['observation_rows'],
                    'Full requested page row reservation unavailable')
        require(len(attempts) < limits['attempts'], 'Physical attempt ceiling')
        prior = [a for a in attempts if a['context']['kind'] == kind and a['context']['stream_id'] == sid and
                 (not self.ranged or a['context'].get('target_day') == day)]
        require(len(prior) < (limits['pages_per_stream'] if kind == 'observations' else 1), 'Page/repeat ceiling')
        require(kind == 'observations' or sum(a['context']['kind'] != 'observations' for a in attempts) < limits['metadata'],
                'Metadata ceiling')
        if attempts:
            delay = limits['spacing_seconds']-(parse_utc(self.clock())-parse_utc(attempts[-1]['attempted_at'])).total_seconds()
            if delay > 0:
                require(delay < self.end-self.monotonic() and delay <
                        (parse_utc(self.plan['expires_at'])-parse_utc(self.clock())).total_seconds(), 'Spacing exceeds deadline')
                self.wait(delay)
        self.check_authority()
        seconds = min(limits['request_seconds'],self.end-self.monotonic(),
                      (parse_utc(self.plan['expires_at'])-parse_utc(self.clock())).total_seconds())
        require(seconds > 0, 'Acquisition deadline')
        cap = min(limits['response_bytes'],limits['aggregate_bytes']-sum(a['bytes'] for a in attempts))
        require(cap > 0, 'Aggregate body ceiling')
        context = dict(kind=kind,stream_id=sid,plan_sha256=self.pin,source_sha256=self.plan['source_sha256'],
                       deadline_utc=format_utc(parse_utc(self.clock())+timedelta(seconds=seconds)),
                       budget=deepcopy(limits),live=self.live)
        context['row_cap'] = 2016 if kind == 'observations' else 0
        if self.ranged: context['target_day'] = day
        ordinal = self.state.reserve(kind+'/'+str(sid),request_spec['url'],self.clock(),cap,context=context)
        body, complete, status, header_info = bytearray(), False, None, None
        response = None
        try:
            self.check_authority()  # Reservation persistence never extends authority or its deadline.
            seconds = min(seconds,self.end-self.monotonic(),
                          (parse_utc(context['deadline_utc'])-parse_utc(self.clock())).total_seconds())
            require(seconds > 0, 'Reservation consumed request deadline; no dispatch')
            with deadline(seconds):
                request = urllib.request.Request(request_spec['url'],method='GET',headers={
                    'Accept':'application/json','User-Agent':'BRIM-Dendra-D3/1.0'})
                try: response = self.opener(request,timeout=seconds)
                except urllib.error.HTTPError as exc: response = exc
                status = response.status
                require(response.geturl() == request_spec['url'], 'Redirect or orphaned response')
                headers = list(response.headers.items())
                require(len(headers) <= 32 and all(type(k) is str and type(v) is str and
                        len(k)+len(v) <= 4096 for k,v in headers), 'Header count/line bound')
                require(sum(len(k.encode())+len(v.encode())+4 for k,v in headers) <= limits['header_bytes'], 'Header byte bound')
                lowered = [k.lower() for k,v in headers]
                require(len(lowered) == len(set(lowered)), 'Duplicate response header')
                h = {k.lower():v for k,v in headers}
                require(h.get('content-encoding','identity').lower() == 'identity', 'Compressed response held')
                require('content-length' not in h or 'transfer-encoding' not in h, 'Ambiguous body framing')
                require(h.get('transfer-encoding','chunked').lower() == 'chunked', 'Unsupported transfer framing')
                length = h.get('content-length')
                require(length is None or length.isascii() and length.isdigit(), 'Body length shape')
                length = None if length is None else int(length)
                read_cap = cap if status == 200 else min(cap,limits['error_bytes'])
                require(length is None or length <= read_cap, 'Declared body exceeds cap')
                header_info = dict(bytes=sum(len(k.encode())+len(v.encode())+4 for k,v in headers),sha256=digest(headers))
                while True:
                    self.check_authority()
                    require(self.monotonic() < self.end, 'Acquisition deadline')
                    if length is not None and len(body) == length:
                        complete=True; break
                    allowance = min(65536,read_cap-len(body),read_cap if length is None else length-len(body))
                    require(allowance > 0, 'Unframed body reached cap; EOF unknown')
                    try: chunk = response.read(allowance)
                    except http.client.IncompleteRead as exc:
                        body.extend(exc.partial[:allowance]); raise Hold('Partial body retained') from exc
                    require(type(chunk) is bytes and len(chunk) <= allowance, 'Reader exceeded bound')
                    body.extend(chunk)
                    if not chunk:
                        require(length is None, 'Premature body EOF')
                        complete=True; break
                require(status == 200, 'HTTP failure/redirect; no retry')
                require(h.get('content-type','').split(';')[0].strip().lower() == 'application/json', 'JSON representation required')
                rows = len(shape(bytes(body),sid)['data']) if kind == 'observations' else 0
                require(sum(a['rows'] or 0 for a in attempts)+rows <= limits['observation_rows'], 'Observation row ceiling')
            self.check_authority()
            self.state.receive(ordinal,bytes(body),status='RECEIVED',rows=rows,
                details=dict(status=status,headers=header_info,complete=complete,retrieved_at=self.clock(),clock_kind='LIVE_PROVIDER' if self.live else 'OFFLINE_DRY_RUN'))
            if self.ranged:
                self.state.data['attempts'][ordinal]['details']['saved_provenance']=(dict(representation='LIVE_PROVIDER')
                    if self.live else deepcopy(getattr(response,'saved_provenance',{'representation':'SYNTHETIC_INJECTED'})))
                self.state.commit()
            return bytes(body), self.state.data['attempts'][ordinal]
        except BaseException:
            # Preserve charge/known prefix even on timeout/cancellation. A failed
            # local receipt leaves RESERVED on disk, never a new permission.
            if self.state.data['attempts'][ordinal]['status'] == 'RESERVED':
                self.state.receive(ordinal,bytes(body),status='AMBIGUOUS',details=dict(
                    status=status,headers=header_info,complete=complete,retrieved_at=self.clock(),clock_kind='LIVE_PROVIDER' if self.live else 'OFFLINE_DRY_RUN'))
            self.state.data['provider']['closed']=True; self.state.commit()
            raise
        finally:
            if response is not None: response.close()

    def observations(self,sid,day=None):
        win = window(day,self.plan['not_before']) if self.ranged else self.win
        raw, receipts = [], []
        class Response(io.BytesIO):
            status, headers = 200, {}
        def opener(request,timeout):
            from urllib.parse import parse_qs,urlsplit
            cursor = parse_qs(urlsplit(request.full_url).query)['time[$gte]'][0]
            expected = spec(self.plan,'observations',sid,cursor,day)
            require(request.full_url == expected['url'], 'Paginator request drift')
            body, receipt = self.exchange(expected)
            raw.extend(shape(body,sid)['data']); receipts.append(deepcopy(receipt))
            return Response(body)
        try:
            envelope = DendraFetcher(opener=opener,max_attempts=1,max_pages=self.plan['limits']['pages_per_stream'],
                page_size=2016,now_fn=self.now,sleep_fn=lambda _:None).fetch_interval(sid,win['start'],win['end'])
            return raw, dict(kind='LIVE_PROVIDER' if self.live else 'OFFLINE_DRY_RUN',pages=receipts,
                             envelope_sha256=envelope['content_sha256'],query_completed_at=self.clock())
        except BaseException:
            if not self.ranged:
                self.state.outcome(sid+'/'+self.plan['target_day'],self.clock(),status='FAILED')
            self.state.data['provider']['closed']=True;self.state.commit()
            raise

def admissions(state, plan, day, at, start):
    """Re-evaluate unchanged metadata predicates for a subset day, not a new grant.

    Original receipt contexts remain immutable. A transient evaluation plan differs
    only in target_day; each source receipt must first match the actual run plan.
    The actual request authority always remains Provider.plan and its exact target set.
    """
    from .live_metadata import admit_v2
    require(day in {x[1] for x in plan['range_targets']}, 'Unrequested metadata evaluation day')
    evaluation = dict(plan,target_day=day); records = []
    end = next((r['start_ordinal'] for r in state.data['range_runs'] if r['start_ordinal']>start),len(state.data['attempts']))
    for original in state.data['attempts'][start:end]:
        if original['context']['kind']=='observations': continue
        require(original['context']['plan_sha256']==digest(plan), 'Metadata run provenance')
        record=deepcopy(original);record['context']['plan_sha256']=digest(evaluation);records.append(record)
    view = SimpleNamespace(root=state.root,binding=dict(plan_sha256=digest(evaluation)),data=dict(attempts=records))
    return admit_v2(view,evaluation,at)


def sync(state, plan, *, opener, now, monotonic, wait, live=False, authorize=None):
    """Single shared provider/session for either manual or routine target selection."""
    provider=Provider(state,plan,opener=opener,now=now,monotonic=monotonic,wait=wait,live=live,authorize=authorize)
    pending=[sid+'/'+day for sid,day in plan['range_targets']]; completed=[]; stop=None
    state.data['range_pending']=pending[:];state.commit()
    try:
        for kind,sid in [('unit-vocabulary',None)]+[(k,s) for s in plan['roster'] for k in ('station','datastream-list')]:
            provider.exchange(spec(plan,kind,sid))
        for sid,day in plan['range_targets']:
            key=sid+'/'+day;attempts=state.data['attempts'][provider.start:];limits=plan['limits']
            if (len(completed)>=plan['stream_day_budget'] or len(attempts)>=limits['attempts'] or
                sum(a['bytes'] for a in attempts)>=limits['aggregate_bytes'] or
                sum(a['rows'] or 0 for a in attempts)+2016>limits['observation_rows'] or
                monotonic()>=provider.end):
                stop='WORK_BUDGET_EXHAUSTED';break
            ordinal=len(state.data['attempts'])
            try:
                raw,evidence=provider.observations(sid,day)
                admitted=admissions(state,plan,day,evidence['query_completed_at'],provider.start)[sid]
                evidence.update(query_complete=True,range_plan_sha256=digest(plan),representation=plan['representation'])
                result=candidate_sample_v2(raw,admitted,day,plan['not_before'],evidence)
                state.range_outcome(key,provider.clock(),result)
            except (Hold,FetchError,TimeoutError,OSError) as exc:
                spent=state.data['attempts'][ordinal:]
                status='INCOMPLETE' if spent and any(a.get('details',{}).get('status')==200 for a in spent) else ('FAILED' if spent else 'HELD')
                state.range_outcome(key,provider.clock(),status=status)
                stop=type(exc).__name__+': '+str(exc);break
            completed.append(key);pending.remove(key)
            state.data['range_pending']=pending[:];state.commit()
    finally:
        state.data['provider']['closed']=True
        state.data['range_pending']=pending[:]
        state.data['range_stop']=stop
        state.commit()
    return dict(completed=completed,pending=pending,stop=stop,
                provider_requests=request_count(state.data['attempts'][provider.start:]))


def request_count(attempts):
    """Completed live receipts count requests; interrupted dispatch is unknown.

    Reservation alone cannot prove a physical GET. Never report zero for an
    ambiguous live session or treat an injected offline exchange as real I/O.
    """
    live=[a for a in attempts if a.get('context',{}).get('live') is True]
    return None if any(a['status']!='RECEIVED' for a in live) else len(live)
