#!/usr/bin/env python3
"""Local immutable run results and pure morning proposals. No network/dispatch code."""
from __future__ import annotations
import argparse, hashlib, json, os, re
from pathlib import Path
from datetime import datetime, timezone

VERSION='soil-network-result-1'
SOURCES={'dendra':'Dendra public v2; fixed UTC-08 completed days',
         'scan':'NRCS SCAN; original agency DAILY labels',
         'snotel':'NRCS SNOTEL pilot; production source contract unresolved'}
OUTCOMES={'success_changes','success_no_data_change','provider_failure','incomplete_catch_up',
          'semantic_hold','data_loss_hold','publication_failure','missed_run','not_enabled'}
ACTIVE={'queued','in_progress','requested','waiting','pending'}
OPTIONAL=('last_successful_source_check','latest_eligible_observation','candidate_generation','acknowledgement')
FIELDS={'version','network','source_identity','cycle','run_id','attempt','phase','started_at_utc',
        'finished_at_utc','cutoff','outcome','error_class','requests','coverage','null_reasons',*OPTIONAL}

def require(ok,msg):
    if not ok: raise ValueError(msg)
def time(value):
    require(isinstance(value,str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z',value),'UTC timestamp required')
    return datetime.fromisoformat(value.replace('Z','+00:00'))
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def plain(p):
    p=Path(p).absolute()
    require(all(not x.is_symlink() for x in (p,*p.parents)),'Symlink in operations state')
    return p

def validate(x):
    require(set(x)==FIELDS and x['version']==VERSION,'Run-result schema/field closure')
    require(x['network'] in SOURCES and x['source_identity']==SOURCES[x['network']],'Source identity')
    for k in ('cycle','run_id'):require(isinstance(x[k],str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',x[k]),'Run/cycle key')
    require(type(x['attempt']) is int and 1<=x['attempt']<=100,'Attempt key')
    require(x['phase'] in ('prepare','publication') and x['outcome'] in OUTCOMES,'Phase/outcome')
    start,end=time(x['started_at_utc']),time(x['finished_at_utc']);require(start<=end,'Run clock order')
    if x['cutoff'] is not None:time(x['cutoff'])
    require(x['error_class'] is None or (isinstance(x['error_class'],str) and len(x['error_class'])<=100),'Error class')
    require(set(x['requests'])=={'attempts','budget','basis'},'Request fields')
    for k in ('attempts','budget'):require(x['requests'][k] is None or type(x['requests'][k]) is int and x['requests'][k]>=0,'Request counters')
    require(isinstance(x['requests']['basis'],str) and bool(x['requests']['basis']),'Request provenance')
    require(set(x['coverage'])=={'requested_intervals','completed_intervals','backlog_intervals','basis'},'Coverage fields')
    c=x['coverage'];nums=[c[k] for k in ('requested_intervals','completed_intervals','backlog_intervals')]
    require(all(v is None for v in nums) or all(type(v) is int and v>=0 for v in nums) and nums[0]==nums[1]+nums[2],'Coverage accounting')
    require(isinstance(c['basis'],str) and bool(c['basis']),'Coverage provenance')
    require(set(x['null_reasons'])=={k for k in (*OPTIONAL,'cutoff') if x[k] is None},'Explicit null reasons')
    require(all(isinstance(v,str) and v for v in x['null_reasons'].values()),'Null reason text')
    if x['last_successful_source_check'] is not None:require(time(x['last_successful_source_check'])<=end,'Future check time')
    v=x['latest_eligible_observation']
    if v is not None:
        if re.fullmatch(r'\d{4}-\d\d-\d\d',v):require(datetime.fromisoformat(v).date()<=end.date(),'Future observation date')
        else:require(time(v)<=end,'Future observation time')
    if x['candidate_generation'] is not None:require(isinstance(x['candidate_generation'],str) and 0<len(x['candidate_generation'])<=120,'Generation identity')
    if x['acknowledgement'] is not None:
        a=x['acknowledgement']
        require(set(a)=={'generation','commit','observed_at_utc','evidence','index_sha256','commit_ancestry'},'Acknowledgement fields')
        require(isinstance(a['generation'],str) and bool(a['generation']) and re.fullmatch('[0-9a-f]{40}',a['commit']) and re.fullmatch('[0-9a-f]{64}',a['index_sha256']),'Exact acknowledgement identity')
        require(isinstance(a['commit_ancestry'],list) and 1<=len(a['commit_ancestry'])<=256 and a['commit_ancestry'][0]==a['commit'] and len(set(a['commit_ancestry']))==len(a['commit_ancestry']) and all(re.fullmatch('[0-9a-f]{40}',v) for v in a['commit_ancestry']),'Bounded committed ancestry proof')
        require(a['evidence']=='verified_committed_product' and time(a['observed_at_utc'])<=end,'Acknowledgement evidence/time')
    return x

def result(network,run_id,cycle,started,finished,outcome,*,attempt=1,phase='prepare',cutoff=None,error_class=None,
           requests=None,coverage=None,last_successful_source_check=None,latest_eligible_observation=None,
           candidate_generation=None,acknowledgement=None):
    x=dict(version=VERSION,network=network,source_identity=SOURCES[network],run_id=run_id,cycle=cycle,
           attempt=attempt,phase=phase,started_at_utc=started,finished_at_utc=finished,cutoff=cutoff,
           outcome=outcome,error_class=error_class,requests=requests or dict(attempts=None,budget=None,basis='not instrumented; never inferred from one R invocation'),
           coverage=coverage or dict(requested_intervals=None,completed_intervals=None,backlog_intervals=None,basis='source does not expose Dendra interval semantics'),
           last_successful_source_check=last_successful_source_check,latest_eligible_observation=latest_eligible_observation,
           candidate_generation=candidate_generation,acknowledgement=acknowledgement)
    x['null_reasons']={k:'not established by this attempt; retain independently verified prior value' for k in (*OPTIONAL,'cutoff') if x[k] is None}
    if candidate_generation is not None and latest_eligible_observation is None:x['null_reasons']['latest_eligible_observation']='validated candidate has no eligible observation'
    return validate(x)

def key(x):return f"{x['network']}/{x['run_id']}-{x['attempt']}-{x['phase']}.json"
def append(root,x):
    validate(x);p=plain(Path(root)/'runs'/key(x));p.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'
    if p.exists():require(p.read_text()==data,'Immutable run key conflict');return p
    with p.open('x') as f:f.write(data)
    return p

def records(root):
    root=plain(Path(root)/'runs');out=[];size=0
    for p in sorted(root.glob('*/*.json')):
        plain(p);size+=p.stat().st_size;require(len(out)<10000 and size<=64*1024**2,'Operations retention envelope exceeded; explicit archival needed')
        x=validate(json.loads(p.read_text()));require(p.relative_to(root).as_posix()==key(x),'Record namespace binding');out.append(x)
    return out

def aggregate(items,as_of):
    now=time(as_of);groups={k:[] for k in SOURCES};seen={}
    for x in items:
        validate(x);k=key(x);require(k not in seen or seen[k]==digest(x),'Conflicting attempt key');seen[k]=digest(x)
        require(time(x['finished_at_utc'])<=now,'Future result');groups[x['network']].append(x)
    out={}
    for n,rs in groups.items():
        order=lambda x:(time(x['started_at_utc']),x['attempt'],x['phase']=='publication',time(x['finished_at_utc']),x['run_id'])
        rs.sort(key=order);last=rs[-1] if rs else None
        acks=[x['acknowledgement'] for x in rs if x['acknowledgement']]
        # An old committed checkout observed late is still an old publication.
        # Only ancestry from the immutable committed tree can order receipts.
        by_commit={}
        for a in acks:
            if a['commit'] in by_commit:
                b=by_commit[a['commit']]
                require((a['generation'],a['index_sha256'])==(b['generation'],b['index_sha256']),'Conflicting committed receipt identity')
            by_commit[a['commit']]=a
        tips=[a for c,a in by_commit.items() if not any(c in b['commit_ancestry'][1:] for b in by_commit.values())]
        require(len(tips)<=1,'Publication ancestry incomplete/divergent; preserve prior report and review')
        def latest(k):
            values=[x[k] for x in rs if x[k]]
            return max(values,key=(lambda v:datetime.fromisoformat(v).replace(tzinfo=timezone.utc)) if k=='latest_eligible_observation' and values and len(values[0])==10 else time,default=None)
        authoritative=[x for x in rs if x['candidate_generation'] is not None]
        data=authoritative[-1] if authoritative else None
        out[n]=dict(source_identity=SOURCES[n],last_attempt=last,run_results=rs,
                    last_successful_source_check=latest('last_successful_source_check'),
                    latest_eligible_observation=data['latest_eligible_observation'] if data else None,
                    latest_eligible_reason=data['null_reasons'].get('latest_eligible_observation') if data else 'No validated candidate result recorded',
                    acknowledgement=tips[0] if tips else None,
                    state=last['outcome'] if last else 'not_enabled' if n=='snotel' else 'unknown',
                    absence_reason=None if last else 'No durable result; absence alone is not a missed scheduled run')
    return dict(version='soil-network-health-1',as_of_utc=as_of,networks=out,
                interpretation='Report generation time is neither a source check nor an observation or publication time.')

def scheduler_runs(inventory,now):
    """Explicit attempt chronology; scheduler conclusions are not product receipts."""
    require(inventory.get('complete') is True and time(inventory['checked_at_utc'])<=now and
            (now-time(inventory['checked_at_utc'])).total_seconds()<=300,'Fresh complete scheduler inventory required')
    runs=inventory['runs'];require(isinstance(runs,list) and len(runs)<=10000,'Scheduler inventory bound')
    seen=set()
    for r in runs:
        require(r['network'] in SOURCES and r['kind'] in ('primary','retry'),'Scheduler source/kind')
        for k in ('cycle','run_id'):require(isinstance(r[k],str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',r[k]),'Scheduler identity')
        require(type(r['attempt']) is int and 1<=r['attempt']<=100,'Scheduler attempt required')
        k=(r['network'],r['run_id'],r['attempt']);require(k not in seen,'Duplicate/ambiguous scheduler attempt');seen.add(k)
        require(r['status'] in ACTIVE|{'completed','cancelled','reserved'},'Scheduler status')
        require(time(r['started_at_utc'])<=now,'Future scheduler start')
        if r['status'] in ('completed','cancelled'):
            require(time(r['started_at_utc'])<=time(r['finished_at_utc'])<=now,'Scheduler terminal chronology')
            require(r['conclusion'] in ('success','failure','cancelled','timed_out','neutral','skipped','action_required'),'Scheduler conclusion')
        else:require(r.get('finished_at_utc') is None and r.get('conclusion') is None,'Active scheduler terminal fields')
    grouped={}
    for r in runs:grouped.setdefault((r['network'],r['run_id']),[]).append(r)
    for attempts in grouped.values():
        attempts.sort(key=lambda r:r['attempt'])
        for first,second in zip(attempts,attempts[1:]):
            require((first['cycle'],first['kind'])==(second['cycle'],second['kind']),'Scheduler run identity changed')
            require(time(second['started_at_utc'])>=time(first['started_at_utc']),'Attempt chronology reversed')
    return runs

def proposals(health,cycle,now,window_start,window_end,enabled,run_inventory):
    """Pure bounded proposals; no dispatch, reservation or inferred acknowledgement.

    Publication-only recovery requires separate revalidation of the named prepared
    artifact before any future execution. A proposal never certifies its presence.
    """
    t=time(now);require(time(window_start)<time(window_end),'Recovery window order')
    require(time(health['as_of_utc'])<=t,'Future health');runs=scheduler_runs(run_inventory,t)
    decisions=[]
    for n in SOURCES:
        all_runs=[r for r in runs if r['network']==n];rs=[r for r in all_runs if r['cycle']==cycle]
        h=health['networks'][n];last=h['last_attempt'];reason='outside_recovery_window';action='none';target=None
        if not enabled.get(n,False) or n=='snotel':reason='source_not_enabled'
        elif not time(window_start)<=t<time(window_end):pass
        elif any(r['status'] in ACTIVE or r['status']=='reserved' for r in all_runs):reason='primary_or_retry_active'
        elif any(r['kind']=='retry' for r in rs):reason='retry_already_reserved_or_attempted'
        elif last and last['outcome'] in ('semantic_hold','data_loss_hold'):reason='review_required'
        elif not rs:
            if last and last['cycle']==cycle:reason='health_scheduler_inventory_disagreement'
            else:reason='missed_primary';action='propose_retry'
        else:
            # Start time describes attempt chronology, finish time may arrive late.
            newest=max(time(r['started_at_utc']) for r in rs)
            latest=[r for r in rs if time(r['started_at_utc'])==newest]
            if len({r['run_id'] for r in latest})!=1:reason='ambiguous_scheduler_chronology'
            else:
                target=max(latest,key=lambda r:r['attempt'])
                matches=[x for x in h.get('run_results',[last] if last else []) if x['cycle']==cycle and
                         x['run_id']==target['run_id'] and x['attempt']==target['attempt']]
                for x in matches:validate(x);require(time(x['finished_at_utc'])<=t,'Future run result')
                phases={x['phase']:x for x in matches}
                require(len(phases)==len(matches),'Ambiguous phase results')
                result=phases.get('publication') or phases.get('prepare')
                if result is None:reason='terminal_run_result_missing_or_stale'
                elif result['outcome'] in ('semantic_hold','data_loss_hold') or any(x['outcome'] in ('semantic_hold','data_loss_hold') for x in matches):reason='review_required'
                elif result['phase']=='publication' and result['outcome'] in ('success_changes','success_no_data_change') and result['acknowledgement']:
                    reason='verified_delivery' if target['conclusion']=='success' else 'verified_delivery_workflow_degraded'
                elif result['outcome'] in ('success_changes','success_no_data_change','publication_failure'):
                    # Successful source preparation is not delivered success. Never
                    # reacquire solely because publication setup/receipt failed.
                    if result['candidate_generation']:
                        reason='prepared_candidate_delivery_unverified';action='propose_publication_recovery'
                    else:reason='delivery_evidence_missing'
                elif result['outcome'] in ('provider_failure','incomplete_catch_up','missed_run'):
                    if target['conclusion']=='success':reason='health_scheduler_outcome_disagreement'
                    else:reason=result['outcome'];action='propose_retry'
                else:reason='terminal_run_result_missing_or_stale'
        decisions.append(dict(network=n,cycle=cycle,action=action,reason=reason,
                              target_run_id=target['run_id'] if target else None,
                              target_attempt=target['attempt'] if target else None,
                              candidate_generation=(result['candidate_generation'] if action=='propose_publication_recovery' else None),
                              recovery_condition=('revalidate exact prepared transfer and fresh parent; no provider acquisition' if action=='propose_publication_recovery' else None),
                              idempotency_key=f'{n}-{cycle}-morning-1',max_retries=1))
    return dict(version='soil-morning-proposals-1',decisions=decisions,dispatches=0)

def export(root,destination):
    xs=records(root);dest=plain(destination);require(not dest.exists(),'Fresh health export directory required')
    dest.mkdir(parents=True)
    for x in xs:append(dest,x)
    manifest={key(x):digest(x) for x in xs};(dest/'manifest.json').write_text(json.dumps(dict(version='soil-health-snapshot-1',records=manifest),sort_keys=True)+'\n')
    return hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest()

def restore(snapshot,expected,destination):
    src=plain(snapshot);dest=plain(destination);require(not dest.exists(),'Fresh health restore required')
    m=plain(src/'manifest.json');require(hashlib.sha256(m.read_bytes()).hexdigest()==expected,'Health snapshot checksum')
    value=json.loads(m.read_text());require(value['version']=='soil-health-snapshot-1','Health snapshot version')
    xs=records(src);require({key(x):digest(x) for x in xs}==value['records'],'Health snapshot closure')
    require({p.relative_to(src).as_posix() for p in src.rglob('*') if p.is_file()}=={'manifest.json'}|{'runs/'+key(x) for x in xs},'Extra snapshot files')
    for p in src.rglob('*'):plain(p)
    dest.mkdir(parents=True)
    for x in xs:append(dest,x)

OFFLINE_VERSION = 'soil-wy2027-offline-review-1'
OFFLINE_STATES = {'NOT_QUERIED', 'UNKNOWN', 'INACTIVE', 'WITHHELD', 'UNRESOLVED'}
SNOTEL_POOL_VERSION = 'snotel-single-capture-pool-1'
OFFLINE_BUNDLE_LIMIT = 32 * 1024**2


def _snotel_events(history, binding, history_ref=None):
    """Original deterministic single-capture event order, without copying history."""
    from soil_moisture import snotel_history as sn
    receipt = history['captures'][0]
    _, dates = sn._request(receipt['receipt']['request'],
                          {s['sensor_identity']: s for s in history['sensors']})
    for query in history['query_ledger']:
        failed = query['status'] == 'failed'
        for date in ([None] if failed else dates):
            payload = dict(date=date)
            payload['history' if history_ref is None else 'history_ref'] = history if history_ref is None else history_ref
            event = dict(network='snotel', identity=query['sensor_identity'], binding=binding,
                         event_id='snotel-capture:' + digest([receipt['receipt_sha256'], query['sensor_identity'], date]),
                         source_checked_at=receipt['receipt']['retrieved_at_utc'],
                         kind='FAILED' if failed else 'OBSERVATION', payload=payload)
            if failed: event['reason'] = query['failure_reason']
            yield event


def _snotel_pool(bundle):
    """Authenticate complete pool/reference closure, even for a prefix replay.

    Canonical bytes retain the original provider bytes and receipts inside each
    history. Pins are integrity checks within the caller-pinned replay bundle;
    they are not independent provider or acquisition authority.
    """
    import base64
    from soil_moisture import snotel_history as sn
    pool = bundle.get('snotel_history_pool')
    references = [e for e in bundle['events'] if 'history_ref' in e.get('payload', {})]
    if pool is None:
        require(not references, 'Missing SNOTEL history pool')
        return {}
    require(len(sn.canonical(bundle)) <= OFFLINE_BUNDLE_LIMIT, 'Combined compact replay byte bound')
    require(isinstance(pool, dict) and set(pool) == {'version', 'members'} and
            pool['version'] == SNOTEL_POOL_VERSION, 'Compact history pool version/fields')
    require(isinstance(pool['members'], list) and 0 < len(pool['members']) <= 32, 'Compact history pool count')
    histories, receipts, expected = {}, set(), []
    expanded_bytes = len(sn.canonical(pool))
    previous_clock = None
    for member in pool['members']:
        require(isinstance(member, dict) and set(member) == {'sha256', 'bytes', 'body_base64'}, 'Compact history member fields')
        key = member['sha256']
        require(isinstance(key, str) and re.fullmatch('[0-9a-f]{64}', key) and key not in histories,
                'Duplicate/invalid compact history hash')
        require(type(member['bytes']) is int and 0 < member['bytes'] <= sn.MAX_HISTORY and
                isinstance(member['body_base64'], str), 'Compact history member bound')
        raw = base64.b64decode(member['body_base64'], validate=True)
        require(len(raw) == member['bytes'] and sn.sha256(raw) == key, 'Compact history bytes/hash mismatch')
        doc = sn._json(raw)
        require(sn.canonical(doc) == raw, 'Compact history canonical bytes required')
        require(len(doc['captures']) == 1, 'Compact history must retain one physical capture')
        history = sn.validate_history(doc)
        capture = history['captures'][0]; receipt = capture['receipt']
        body = base64.b64decode(capture['body_base64'], validate=True)
        require(type(receipt['physical_attempt']) is int and 1 <= receipt['physical_attempt'] <= 64 and
                isinstance(receipt['plan_sha256'], str) and re.fullmatch('[0-9a-f]{64}', receipt['plan_sha256']),
                'Compact original transport identity')
        require(type(receipt['http_status']) is int and 100 <= receipt['http_status'] <= 599 and
                receipt['body_accounting_state'] == 'COMPLETE' and
                type(receipt['total_consumed_body_bytes']) is int and type(receipt['captured_prefix_bytes']) is int and
                receipt['total_consumed_body_bytes'] == receipt['captured_prefix_bytes'] == len(body) <= sn.MAX_BODY and
                receipt['complete'] is (receipt['http_status'] == 200 and receipt['error'] is None),
                'Compact incomplete or unknown consumed body')
        require(capture['receipt_sha256'] not in receipts, 'Duplicate compact capture receipt')
        receipts.add(capture['receipt_sha256'])
        clock = time(receipt['retrieved_at_utc'])
        require(previous_clock is None or clock >= previous_clock, 'Compact capture chronology')
        previous_clock = clock
        histories[key] = history
        for event in _snotel_events(history, bundle['bindings']['snotel'],
                                    dict(version=SNOTEL_POOL_VERSION, sha256=key)):
            expanded_bytes += len(sn.canonical(event))
            require(len(expected) < 10000 and expanded_bytes <= sn.MAX_HISTORY, 'Compact reference expansion bound')
            expected.append(event)
    require(references == expected, 'Compact reference identity/date/order/closure mismatch')
    first = next((i for i, e in enumerate(bundle['events']) if 'history_ref' in e.get('payload', {})), len(bundle['events']))
    require(bundle['events'][first:] == references, 'Compact references must follow immutable legacy prefix')
    return histories


def offline_append_snotel_compact(bundle, bridge):
    """Append validated compact events; never repack the caller's legacy prefix."""
    import copy
    from soil_moisture import snotel_history as sn
    require(set(bridge) == {'events', 'snotel_history_pool'}, 'Compact bridge fields')
    pool = bridge['snotel_history_pool']
    require(isinstance(pool, dict) and set(pool) == {'version', 'members'} and
            isinstance(pool['members'], list), 'Compact bridge pool fields')
    require(len(sn.canonical(bridge)) <= sn.MAX_HISTORY, 'Offline event expansion bound')
    _snotel_pool(bundle)
    combined = copy.deepcopy(bundle)
    combined['events'].extend(copy.deepcopy(bridge['events']))
    require(len(combined['events']) <= 10000, 'Offline event bound')
    if 'snotel_history_pool' not in combined:
        combined['snotel_history_pool'] = dict(version=SNOTEL_POOL_VERSION, members=[])
    require(bridge['snotel_history_pool']['version'] == SNOTEL_POOL_VERSION, 'Compact history pool version')
    combined['snotel_history_pool']['members'].extend(copy.deepcopy(bridge['snotel_history_pool']['members']))
    require(len(sn.canonical(combined)) <= OFFLINE_BUNDLE_LIMIT, 'Combined compact replay byte bound')
    _snotel_pool(combined)
    return combined


def offline_read(ref):
    """Read a caller-pinned local fixture; a pin is not independent acceptance."""
    from soil_moisture.scan_history import decoded
    require(set(ref) == {'path', 'sha256'}, 'Pinned local input required')
    p = plain(ref['path'])
    require(p.is_file() and p.stat().st_size <= 32 * 1024**2, 'Offline input bound')
    raw = p.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Offline input hash mismatch')
    return decoded(raw)


def offline_identity(row):
    """Exact source identities; neither display depths nor reference eligibility."""
    from soil_moisture import snotel_history as sn, scan_history as sc
    n, source = row['network'], row['source']
    if n == 'snotel':
        identity = sn.sensor_identity(source['station_triplet'], source['signed_depth'], source['ordinal'])
    elif n == 'scan':
        site, depth = sc.pair(source['site_code'], source['depth_in'])
        require(re.fullmatch(str(site) + r':[A-Z]{2}:SCAN', source['station_triplet']), 'SCAN triplet')
        identity = f'{source["station_triplet"]}|depth_in:{depth}'
    else:
        require(n == 'dendra', 'Offline network')
        require(all(re.fullmatch('[0-9a-f]{24}', source[k]) for k in ('station_id', 'stream_id')), 'Dendra exact identity')
        require(source.get('export_local_series_key'), 'Dendra export bridge required')
        require(source['depth_status'] in ('UNKNOWN', 'CONFLICTING', 'ACCEPTED_EXACT_STREAM_REVIEW'), 'Dendra depth status')
        require((source['depth_cm'] is None) == (source['depth_status'] != 'ACCEPTED_EXACT_STREAM_REVIEW'), 'Dendra depth hold')
        require(all(source['daily_identity'][k] == source[k] for k in ('station_id', 'stream_id', 'export_local_series_key', 'depth_cm')), 'Dendra daily identity bridge')
        identity = source['station_id'] + '|' + source['stream_id']
    require(identity == row['identity'], 'Roster identity mismatch')
    return n + ':' + identity


def offline_roster(bundle, request):
    require(bundle['version'] == OFFLINE_VERSION and request['version'] == OFFLINE_VERSION, 'Offline version')
    require(bundle['bindings'] == request['bindings'], 'Source generation/manifest/policy mismatch')
    require(set(bundle['bindings']) == set(SOURCES), 'Three source authorities required')
    for b in bundle['bindings'].values():
        require(set(b) == {'manifest_sha256', 'generation', 'policy'} and
                re.fullmatch('[0-9a-f]{64}', b['manifest_sha256']) and b['generation'] and b['policy'], 'Source binding')
    rows = bundle['roster']; require(0 < len(rows) <= 600, 'Roster bound')
    result = {}
    for row in rows:
        k = offline_identity(row)
        require(k not in result, 'Duplicate candidate identity')
        require(row['initial_outcome'] in OFFLINE_STATES and bool(row['evidence']), 'Explicit initial evidence required')
        require(set(row['eligibility']) == {'source', 'current', 'public', 'reference'}, 'Independent eligibility axes')
        require(all(v in ('VERIFIED', 'HELD', 'UNKNOWN', 'INACTIVE') for v in row['eligibility'].values()), 'Eligibility state')
        require(isinstance(row['holds'], list) and all(isinstance(v, str) and v for v in row['holds']), 'Hold reasons')
        eligible = all(row['eligibility'][a] == 'VERIFIED' for a in ('source', 'current', 'public')) and not row['holds']
        require(eligible or row['holds'], 'Unverified eligibility needs a reason')
        result[k] = dict(row, eligible_current_feed=eligible, selected=False)
    require(request['expected_counts'] == {n: sum(r['network'] == n for r in rows) for n in SOURCES}, 'Exact roster counts')
    selected = request['selection']
    require(set(selected) <= set(SOURCES) and selected, 'Explicit network selection')
    for n, ids in selected.items():
        require(isinstance(ids, list) and len(ids) == len(set(ids)), 'Unique selected identities')
        for identity in ids:
            require(n + ':' + identity in result, 'Selected identity outside roster')
            result[n + ':' + identity]['selected'] = True
    return result


def offline_plan(roster, request):
    """Request ceilings are data for later review, never collection permission."""
    from dendra.history_acquisition import routine_update as ru
    plans = {}
    for n in SOURCES:
        cutoff = request['cutoffs'][n]
        require(set(cutoff) == {'as_of', 'start', 'end'}, 'Pinned source cutoff')
        time(cutoff['as_of'])
        if n == 'dendra':
            require(ru.boundary(cutoff['start']) < ru.boundary(cutoff['end']) <= ru.completed_boundary(cutoff['as_of']), 'Incomplete Dendra day')
            require(time('2026-10-01T08:00:00Z') <= time(cutoff['start']) < time(cutoff['end']) <= time('2027-10-01T08:00:00Z'), 'Dendra WY2027 only')
            require((time(cutoff['end']) - time(cutoff['start'])).days <= 366, 'Dendra interval bound')
        else:
            from soil_moisture.scan_history import day
            require(day(cutoff['start']) <= day(cutoff['end']) <= time(cutoff['as_of']).date(), 'Source date order')
            require('2026-10-01' <= cutoff['start'] <= cutoff['end'] <= '2027-09-30', 'WY2027 only')
            if n == 'snotel':
                from soil_moisture.snotel_history import source_timestamp
                require(time(source_timestamp(cutoff['end'])) <= time(cutoff['as_of']), 'SNOTEL END incomplete')
        selected = [k for k, r in roster.items() if r['network'] == n and r['selected'] and r['eligible_current_feed']]
        budget = request['budgets'][n]
        require(all(type(v) is int and v >= 0 for v in budget.values()), 'Integer dry-run ceilings')
        if n == 'snotel':
            stations = {roster[k]['source']['station_triplet'] for k in selected}
            require(set(budget) == {'attempts', 'response_bytes'} and budget['attempts'] <= 64 and budget['response_bytes'] <= 1048576, 'SNOTEL budget bound')
            require(len(stations) <= 32, 'SNOTEL station scope')
            needed = 2 * len(stations)
            detail = dict(logical_station_requests=len(stations), grouping='REQUIRES_PROVIDER_VERIFICATION', response_bytes_per_attempt=budget['response_bytes'])
        elif n == 'scan':
            stations = {roster[k]['source']['site_code'] for k in selected}
            years = int(cutoff['end'][:4]) - int(cutoff['start'][:4]) + 1
            require(set(budget) == {'attempts', 'canaries', 'preflight'} and budget['attempts'] <= 105 and budget['canaries'] <= 6 and budget['preflight'] <= 3, 'SCAN budget bound')
            needed = (len(stations) * years + (budget['canaries'] if selected else 0)) * 3 + (budget['preflight'] if selected else 0)
            detail = dict(station_year_calls=len(stations) * years, underlying_http_attempts='UNKNOWN', composite_equivalence='PER_IDENTITY_HOLDS_PRESERVED')
        else:
            require(set(budget) == {'attempts', 'seconds', 'response_bytes', 'new_intervals'} and budget['attempts'] <= 80 and budget['seconds'] <= 300 and budget['response_bytes'] <= 64 * 1024**2 and budget['new_intervals'] <= 16, 'Dendra epoch bound')
            needed = None
            from datetime import timedelta
            intervals = []
            for identity in selected:
                cursor, end = time(cutoff['start']), time(cutoff['end'])
                while cursor < end:
                    stop = min(cursor + timedelta(days=30), end)
                    intervals.append(dict(identity=identity, start=cursor.strftime('%Y-%m-%dT08:00:00Z'), end=stop.strftime('%Y-%m-%dT08:00:00Z')))
                    cursor = stop
            detail = dict(intervals_this_epoch=intervals[:budget['new_intervals']], deferred=intervals[budget['new_intervals']:], routine_policy=ru.policy(),
                          scope='Requested intervals only; existing query evidence reconciled separately; no new overlap policy', full_roster_cost='UNKNOWN')
        plans[n] = dict(eligible_selected=len(selected), held_selected=sum(r['network'] == n and r['selected'] and not r['eligible_current_feed'] for r in roster.values()),
                        attempt_estimate=needed, budget=budget, budget_sufficient=None if needed is None else needed <= budget['attempts'], detail=detail, cutoff=cutoff)
    return dict(version=OFFLINE_VERSION, actual_requests=0, acquisition_authorized=False, networks=plans)


def offline_snotel_capture_bridge(bundle, request, groups, captures, *,
                                  expected_groups_sha256, expected_plan_sha256,
                                  expected_capture_set_sha256, compact=False):
    """Pure retained-capture replay; no collection, authority admission or I/O.

    Pins must be retained independently by the offline caller. The groups pin
    hashes the ordered {request, metadata_sha256} list using history.canonical;
    the capture-set pin hashes the ordered original receipt_sha256 list. These
    integrity bindings do not establish real metadata or collection authority.
    Returned events preserve single-capture history and physical attempt order.
    A rejected input returns no events and never changes the supplied evidence.
    """
    from soil_moisture import snotel_history as sn
    import base64
    require(type(compact) is bool, 'Explicit compact mode')
    pool = dict(version=SNOTEL_POOL_VERSION, members=[])
    require(request.get('offline_only') is True, 'Offline-only bridge required')
    for pin in (expected_groups_sha256, expected_plan_sha256, expected_capture_set_sha256):
        require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'Independent fixture pin required')
    require(isinstance(groups, list) and 0 < len(groups) <= 32, 'Station group bound')
    require(isinstance(captures, list) and len(captures) <= 64, 'Physical attempt bound')
    require(sn.sha256(sn.canonical([dict(request=g['request'], metadata_sha256=g['metadata_sha256'])
                                    for g in groups])) == expected_groups_sha256, 'Group pin mismatch')
    require(sn.sha256(sn.canonical([c['receipt_sha256'] for c in captures])) ==
            expected_capture_set_sha256, 'Ordered capture-set pin mismatch')
    roster = offline_roster(bundle, request)
    offline_plan(roster, request)
    budget = request['budgets']['snotel']
    require(len(captures) <= budget['attempts'], 'Pinned attempt budget')
    cutoff = request['cutoffs']['snotel']; specs = {}; selected = set(); stations = set()
    for group in groups:
        require(set(group) == {'request', 'metadata_body', 'metadata_sha256'}, 'Exact retained group fields')
        sensors = sn._sensors(group['metadata_body'], group['metadata_sha256'])
        identities, dates = sn._request(group['request'], sensors)
        station = group['request']['parameters']['stationTriplets']
        require({s['station_triplet'] for s in sensors.values()} == {station}, 'Separate station metadata required')
        require(station not in stations and not selected.intersection(identities), 'Duplicate logical station/sensor')
        require(dates[0] == cutoff['start'] and dates[-1] == cutoff['end'], 'Exact pinned query window required')
        key = sn.sha256(sn.canonical(group['request']))
        require(key not in specs, 'Duplicate logical request')
        specs[key] = (group, identities, dates, station)
        stations.add(station); selected.update(identities)
    require(selected == set(request['selection'].get('snotel', [])), 'Missing or expanded logical sensor scope')
    events = []; seen = set(); counts = {}; sizes = {}; total = 0; event_bytes = 0; previous_clock = None; statuses = {}
    for number, capture in enumerate(captures, 1):
        require(set(capture) == {'body', 'receipt', 'receipt_sha256'}, 'Original retained capture required')
        receipt = capture['receipt']; body = capture['body']
        require(type(body) is bytes and len(body) <= min(sn.MAX_BODY, budget['response_bytes']), 'Retained body bound')
        require(sn.sha256(sn.canonical(receipt)) == capture['receipt_sha256'], 'Original receipt pin mismatch')
        require(capture['receipt_sha256'] not in seen, 'Duplicate physical capture')
        seen.add(capture['receipt_sha256'])
        require(type(receipt['physical_attempt']) is int and receipt['physical_attempt'] == number,
                'Original contiguous physical attempt order required')
        require(receipt['plan_sha256'] == expected_plan_sha256, 'Original transport plan binding')
        require(type(receipt['http_status']) is int and 100 <= receipt['http_status'] <= 599,
                'Complete physical response status required')
        require(receipt['body_accounting_state'] == 'COMPLETE' and
                type(receipt['total_consumed_body_bytes']) is int and
                type(receipt['captured_prefix_bytes']) is int and
                receipt['total_consumed_body_bytes'] == receipt['captured_prefix_bytes'] == len(body),
                'Incomplete or unknown consumed body')
        require(receipt['complete'] is (receipt['http_status'] == 200 and receipt['error'] is None),
                'Physical completion is not query success')
        key = sn.sha256(sn.canonical(receipt['request']))
        require(key in specs, 'Capture request outside pinned station/element/window group')
        group, identities, dates, station = specs[key]
        require(key not in statuses or statuses[key] in (429, 500, 502, 503, 504), 'Impossible transport retry sequence')
        statuses[key] = receipt['http_status']
        counts[station] = counts.get(station, 0) + 1
        sizes[station] = sizes.get(station, 0) + len(body); total += len(body)
        require(counts[station] <= 3 and sizes[station] <= 8 * sn.MAX_BODY and total <= 64 * sn.MAX_BODY,
                'Retained station/aggregate capture bound')
        clock = time(receipt['retrieved_at_utc'])
        require(clock <= time(cutoff['as_of']) and (previous_clock is None or clock >= previous_clock),
                'Original receipt chronology')
        previous_clock = clock
        history = sn.build_history(group['metadata_body'], group['metadata_sha256'], [capture])
        history_ref = None
        if compact:
            raw = sn.canonical(history)
            key = sn.sha256(raw)
            pool['members'].append(dict(sha256=key, bytes=len(raw), body_base64=base64.b64encode(raw).decode('ascii')))
            history_ref = dict(version=SNOTEL_POOL_VERSION, sha256=key)
        for query in history['query_ledger']:
            failed = query['status'] == 'failed'
            # Invalid HTTP-200 and failed retries retain raw evidence as FAILED,
            # with no successful day coverage; absent groups produce no events.
            for date in ([None] if failed else dates):
                event = dict(network='snotel', identity=query['sensor_identity'],
                             binding=bundle['bindings']['snotel'],
                             event_id='snotel-capture:' + digest([capture['receipt_sha256'], query['sensor_identity'], date]),
                             source_checked_at=receipt['retrieved_at_utc'],
                             kind='FAILED' if failed else 'OBSERVATION',
                             payload=dict(history=history, date=date))
                if failed: event['reason'] = query['failure_reason']
                if compact: event['payload'] = dict(history_ref=history_ref, date=date)
                event_bytes += len(sn.canonical(event))
                require(len(events) < 10000 and event_bytes <= sn.MAX_HISTORY, 'Offline event expansion bound')
                events.append(event)
    # Existing replay validates clocks, scope and eligibility without promoting
    # held candidates. Canonical copying detaches all returned mutable values.
    events = sn._json(sn.canonical(events))
    replay = dict(bundle, events=events)
    replay.pop('snotel_history_pool', None)
    if compact:
        require(bool(pool['members']), 'Compact bridge needs retained capture')
        replay['snotel_history_pool'] = pool
        result = dict(events=events, snotel_history_pool=pool)
        require(len(sn.canonical(result)) <= sn.MAX_HISTORY, 'Offline event expansion bound')
    else: result = events
    offline_reconcile(replay, request, roster)
    return result


def offline_observation(event, candidate, bundle, cutoff, *, _validated_history=None):
    """Delegate science validation; never calculate a replacement daily mean."""
    from soil_moisture import snotel_history as sn, scan_history as sc
    n = candidate['network']; payload = event['payload']
    if n == 'snotel':
        # Only the replay-local, fully authenticated pool supplies this cache.
        doc = sn.validate_history(payload['history']) if _validated_history is None else _validated_history
        date = payload['date']; events = sn.coverage_on(doc, candidate['identity'], date)
        require(len(events) == 1, 'One SNOTEL capture per replay event; preserve attempt order')
        source = events[0]
        require(source['state'] != 'UNQUERIED', 'Observation has no SNOTEL query evidence')
        rows = [r for r in doc['observations'] if r['sensor_identity'] == candidate['identity'] and r['provider_date'] == date]
        state = source['state']
        outcome = {'FAILED': 'FAILED', 'QUERIED_EMPTY': 'COMPLETE_VALID_EMPTY', 'OMITTED_SOURCE_GAP': 'MISSING_WITHIN_COMPLETED_QUERY',
                   'EXPLICIT_MISSING': 'MISSING', 'SUSPECT_ORIGINAL_ONLY': 'WITHHELD', 'SUSPECT_NO_NUMERIC_VALUE': 'WITHHELD'}.get(state, 'OBSERVED_ACCEPTED')
        if rows and rows[0].get('qc_flag') == 'S': outcome = 'WITHHELD'
        require(time(source['retrieved_at_utc']) <= time(cutoff['as_of']), 'Future SNOTEL retrieval')
        require(time(source['retrieved_at_utc']) == time(event['source_checked_at']), 'SNOTEL query clock binding')
        require(time(sn.source_timestamp(date)) <= time(event['source_checked_at']), 'SNOTEL source END after retrieval')
        value = dict(representation='NRCS_DAILY_END', source_timestamp=sn.source_timestamp(date), query_evidence=source, rows=rows)
    elif n == 'scan':
        pair, row = sc.normalize(payload['row'], False); date = row[0]
        require(pair == sc.pair(candidate['source']['site_code'], candidate['source']['depth_in']), 'SCAN observation identity')
        members = candidate['source']['dated_membership'].get(date)
        outcome = 'OBSERVED_ACCEPTED' if members == row[5] else 'UNRESOLVED_COMPOSITE'
        value = dict(representation='NRCS_SOURCE_CIVIL_DATE_COMPOSITE', provider_date=date, row=row, expected_membership=members,
                     outside_0_100=not 0 <= row[1] <= 100)
    else:
        from dendra.history_acquisition import candidate3_delivery as c3, routine_update as ru
        row = payload['row']; identity = candidate['source']['daily_identity']
        c3.validate_record(row, bundle['dendra_row_schema'], identity)
        require(row['depth_status'] == candidate['source']['depth_status'], 'Dendra depth bridge')
        date = row['date']
        from datetime import timedelta
        end = (datetime.fromisoformat(date) + timedelta(days=1)).strftime('%Y-%m-%dT08:00:00Z')
        require(ru.boundary(end) <= ru.boundary(cutoff['end']), 'Uncompleted Dendra mean')
        if row['latest_source_timestamp_utc'] is not None:
            require(time(date+'T08:00:00Z') <= time(row['latest_source_timestamp_utc']) < time(end) and
                    time(row['latest_source_timestamp_utc']) <= time(cutoff['as_of']), 'Dendra source timestamp outside completed day')
        outcome = {'ACCEPTED': 'OBSERVED_ACCEPTED', 'WITHHELD_BY_EXISTING_SCREEN': 'WITHHELD', 'UNRESOLVED_SEMANTICS': 'UNRESOLVED', 'MISSING': 'MISSING'}[row['daily_status']]
        value = dict(representation='SOURCE_COMPLETED_DAILY_RECORD', row=row, latest_instantaneous=None)
    require(cutoff['start'][:10] <= date <= cutoff['end'][:10] and '2026-10-01' <= date <= '2027-09-30', 'Observation outside pinned WY2027 scope')
    return date, outcome, value


def offline_query(event, candidate, cutoff):
    """Validate saved response bytes and source completion; never infer a query."""
    import base64
    from datetime import timedelta
    from soil_moisture.scan_history import decoded, normalize, pair
    proof = event['query']
    require(candidate['network'] != 'snotel', 'SNOTEL completion requires validated history ledger')
    require(proof['complete'] is True and proof['response_present'] is True, 'Missing response is not valid empty')
    def body(encoded, expected, count):
        require(isinstance(encoded, str) and len(encoded) <= 2 * 1024**2, 'Saved response bound')
        raw = base64.b64decode(encoded, validate=True)
        require(len(raw) == count and hashlib.sha256(raw).hexdigest() == expected, 'Saved response bytes/hash mismatch')
        return decoded(raw)
    if candidate['network'] == 'scan':
        # SCAN has no existing durable request ledger. Only an explicitly
        # accepted, pinned saved-query receipt can supply this information.
        require(proof['authority'] == 'accepted_saved_query_receipt' and proof['http_status'] == 200, 'SCAN source completion authority')
        rows = body(proof['response_base64'], proof['response_sha256'], proof['response_bytes'])
        require(isinstance(rows, list) and len(rows) <= 366, 'Saved SCAN exact-pair rows')
        dates = []
        for row in rows:
            key, value = normalize(row, False)
            require(key == pair(candidate['source']['site_code'], candidate['source']['depth_in']), 'SCAN response identity')
            dates.append(value[0])
        require(len(dates) == len(set(dates)), 'Duplicate SCAN response date')
        require(sorted(dates) == proof['returned_dates'], 'SCAN returned-date evidence')
    else:
        import sys
        from dendra import transport
        # The protected helper retains script-style imports. Restore import
        # search paths after loading it; use validate_envelope only, never load/save.
        saved = list(sys.path)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent/'dendra'))
            from dendra.coverage_collect import BoundChunks
        finally: sys.path[:] = saved
        from dendra_coverage import completed_intervals
        env = proof['envelope']; interval = env['requested_interval']
        lo, hi = time(interval['start_inclusive']), time(interval['end_exclusive'])
        require(env['query_complete'] is True and env['datastream_id'] == candidate['source']['stream_id'] and
                env['content_sha256'] == transport._content_hash(env), 'Dendra checkpoint content/identity')
        require(candidate['source'].get('collection_binding') is not None, 'Accepted Dendra collection binding required')
        task = dict(start=lo.date().isoformat(), end=hi.date().isoformat(), collection_binding=candidate['source']['collection_binding'])
        BoundChunks('.', task).validate_envelope(env)
        require(time(env['retrieval_last_utc']) == time(event['source_checked_at']), 'Dendra query clock binding')
        require(len(proof['responses_base64']) == len(env['pages']), 'Complete saved page set')
        native = []; previous_last = None
        for i, (encoded, page) in enumerate(zip(proof['responses_base64'], env['pages'])):
            value = body(encoded, page['response_sha256'], page['response_bytes'])
            require(page['status'] == 200 and isinstance(value.get('data'), list) and len(value['data']) == page['row_count'], 'Dendra response page')
            require(type(value.get('limit')) is int and value['limit'] > 0 and page['effective_limit'] == value['limit'] and
                    len(value['data']) <= value['limit'], 'Dendra effective page limit binding')
            stamps = [time(r['t']) for r in value['data']]
            require(stamps == sorted(stamps) and all(lo <= t < hi for t in stamps), 'Dendra source order/interval')
            if stamps:
                require(previous_last is None or stamps[-1] > previous_last, 'Dendra pagination failed to advance')
                previous_last = stamps[-1]
            if i < len(env['pages']) - 1: require(len(stamps) == value['limit'], 'Premature terminal page')
            native.extend(value['data'])
        last = env['pages'][-1]
        require(last['row_count'] == 0 or type(last['effective_limit']) is int and 0 < last['row_count'] < last['effective_limit'], 'Unproven Dendra final page')
        require(env['diagnostics']['completion_reason'] == ('empty_page' if last['row_count'] == 0 else 'short_page_with_effective_limit'), 'Dendra completion reason')
        normalized, _ = transport.normalize_rows(native, interval['start_inclusive'], interval['end_exclusive'])
        require(normalized == env['rows'], 'Dendra raw response/checkpoint mismatch')
        dates = [(lo + timedelta(days=i)).date().isoformat() for i in range((hi-lo).days)]
        require(dates == proof['dates'], 'Dendra complete interval/date correspondence')
        queries = {d: dict(status='queried', content_sha256=env['content_sha256'], retrieved_at_utc=env['retrieval_last_utc']) for d in dates}
        covered, _ = completed_intervals(dict(source_snapshot=dict(chunks=[env], query_by_date=queries)), time(cutoff['as_of']))
        require({d.isoformat() for d in covered} == set(dates), 'Dendra completed coverage')
        returned = sorted({(time(r['t'])-timedelta(hours=8)).date().isoformat() for r in normalized})
        require(returned == proof['returned_dates'], 'Dendra returned-day evidence')
    require(event['kind'] != 'COMPLETE_VALID_EMPTY' or not proof['returned_dates'], 'Empty response contains observations')
    require(event['kind'] != 'COMPLETE_QUERY' or bool(proof['returned_dates']), 'Wholly empty query must be classified complete-valid-empty')
    return proof


def offline_reconcile(bundle, request, roster, limit=None):
    """Immutable fixture replay. Failures/holds retain previously accepted rows."""
    events = bundle['events']; require(isinstance(events, list) and len(events) <= 10000, 'Offline event bound')
    histories = _snotel_pool(bundle)
    require(limit is None or type(limit) is int and 0 <= limit <= len(events), 'Replay prefix bound')
    states = {k: dict(outcome=r['initial_outcome'], observations={}, attempts=[], completed_dates={}) for k, r in roster.items()}
    seen = set(); chronology = {}
    for i, event in enumerate(events):
        history = None
        if 'history_ref' in event.get('payload', {}):
            history = histories[event['payload']['history_ref']['sha256']]
            event = dict(event, payload=dict(date=event['payload']['date'], history=history))
        k = event['network'] + ':' + event['identity']; require(k in roster, 'Fixture identity outside roster')
        require(isinstance(event['event_id'], str) and event['event_id'] and event['event_id'] not in seen, 'Duplicate fixture event')
        seen.add(event['event_id'])
        require(event['binding'] == bundle['bindings'][event['network']], 'Event source generation/policy mismatch')
        cutoff = request['cutoffs'][event['network']]
        if event['source_checked_at'] is None:
            require(event['kind'] == 'OBSERVATION' and event['network'] != 'snotel', 'Missing query check clock')
            clock = time(event['evidence_as_of'])
        else: clock = time(event['source_checked_at'])
        if event.get('product_build_at') is not None: time(event['product_build_at'])
        require(clock <= time(cutoff['as_of']) and (k not in chronology or clock >= chronology[k]), 'Query chronology')
        chronology[k] = clock
        if limit is not None and i >= limit: continue
        if not roster[k]['selected']: continue
        s = states[k]; kind = event['kind']; outcome = kind; dates = []
        if kind == 'OBSERVATION':
            date, outcome, value = offline_observation(event, roster[k], bundle, cutoff, _validated_history=history)
            if outcome == 'OBSERVED_ACCEPTED':
                if not roster[k]['eligible_current_feed']:
                    outcome = 'HELD_ELIGIBILITY'
                else:
                    previous = s['observations'].get(date)
                    # Provenance clock changes alone do not revise a scientific row.
                    def science(v):
                        if v is None: return None
                        if 'row' in v: return v['row']
                        return [{f: r.get(f) for f in ('provider_record', 'response_station_element', 'observation_state',
                                  'source_timestamp_utc', 'sensor_identity', 'unit_native')} for r in v['rows']]
                    prior_science = science(previous)
                    outcome = 'NO_DATA_CHANGE' if science(value) == prior_science else 'ACCEPTED_REVISION' if previous else outcome
                    s['observations'][date] = value
            dates = [date]
        elif kind in ('COMPLETE_VALID_EMPTY', 'COMPLETE_QUERY'):
            proof = offline_query(event, roster[k], cutoff)
            dates = proof['dates']
            require(isinstance(dates, list) and 0 < len(dates) <= 366 and dates == sorted(set(dates)), 'Completed query dates')
            from soil_moisture.scan_history import day
            for date in dates:
                day(date); require(cutoff['start'][:10] <= date <= cutoff['end'][:10], 'Query date outside scope')
                if event['network'] == 'dendra':
                    require(date < cutoff['end'][:10], 'Incomplete Dendra query day')
            require(set(proof['returned_dates']) <= set(dates), 'Returned query dates')
            require(kind != 'COMPLETE_VALID_EMPTY' or not proof['returned_dates'], 'Empty query returned records')
            for date in dates:
                s['completed_dates'][date] = 'COMPLETE_VALID_EMPTY' if kind == 'COMPLETE_VALID_EMPTY' else 'RETURNED' if date in proof['returned_dates'] else 'MISSING_WITHIN_COMPLETED_QUERY'
            # Completion proof does not authorize source deletion or loss replacement.
            if any(d in s['observations'] and d not in proof['returned_dates'] for d in dates): outcome = 'REVISION_LOSS_REVIEW_REQUIRED'
            elif any(v == 'MISSING_WITHIN_COMPLETED_QUERY' for d, v in s['completed_dates'].items() if d in dates): outcome = 'MISSING_WITHIN_COMPLETED_QUERY'
        else:
            require(kind in {'FAILED', 'NOT_QUERIED', 'INACTIVE', 'WITHHELD', 'UNRESOLVED', 'PENDING', 'INCOMPLETE_CONTINUATION'}, 'Offline outcome')
            require(bool(event.get('reason')), 'Explicit outcome evidence required')
        s['outcome'] = outcome
        s['attempts'].append(dict(event_id=event['event_id'], event_sha256=digest(event), outcome=outcome,
                                  source_checked_at=event['source_checked_at'], evidence_as_of=event.get('evidence_as_of'),
                                  product_build_at=event.get('product_build_at'), affected_dates=dates))
    for k, s in states.items():
        s['pending_events'] = sum(e['network'] + ':' + e['identity'] == k for e in events[(len(events) if limit is None else limit):]) if roster[k]['selected'] else 0
        s['continuation_status'] = 'PENDING' if s['pending_events'] else 'COMPLETE_FIXTURE_REPLAY'
        if roster[k]['network'] == 'dendra':
            from dendra.history_acquisition import routine_update as ru
            from datetime import timedelta
            # Union completed overlapping intervals into disjoint day views; saved
            # daily rows alone never establish completed native query coverage.
            views = [dict(start=d+'T08:00:00Z', end=(datetime.fromisoformat(d)+timedelta(days=1)).strftime('%Y-%m-%dT08:00:00Z'),
                          row_count=0 if state == 'COMPLETE_VALID_EMPTY' else 1) for d, state in sorted(s['completed_dates'].items())]
            c = request['cutoffs']['dendra']; s['query_coverage'] = ru.coverage(views, c['start'], c['end'])
    return dict(version=OFFLINE_VERSION, cursor=len(events) if limit is None else limit,
                total_events=len(events), pending_events=len(events) - (len(events) if limit is None else limit), identities=states)


def offline_run(request, output, *, resume=None, max_events=None):
    """Fresh private review directory only. No transport, subprocess or publisher."""
    require(request.get('offline_only') is True, 'Explicit offline-only opt-in required')
    bundle = offline_read(request['bundle'])
    roster = offline_roster(bundle, request); plan = offline_plan(roster, request)
    intent = digest(request)
    if resume is not None:
        prior = offline_read(resume)
        require(prior['version'] == OFFLINE_VERSION and prior['intent_sha256'] == intent, 'Checkpoint intent changed')
        expected = offline_reconcile(bundle, request, roster, prior['reconciliation']['cursor'])
        require(prior['reconciliation'] == expected, 'Checkpoint replay mismatch')
        require(max_events is None or max_events >= expected['cursor'], 'Checkpoint cannot move backwards')
    reconciliation = offline_reconcile(bundle, request, roster, max_events)
    checkpoint = dict(version=OFFLINE_VERSION, intent_sha256=intent, reconciliation=reconciliation)
    out = plain(output)
    require(not out.exists(), 'Fresh offline scratch directory required')
    scratch = plain(request['scratch_root'])
    require(scratch.is_dir() and out.parent == scratch, 'Output must be a fresh direct child of the task-owned scratch root')
    protected = [Path(__file__).resolve().parents[1], plain(request['bundle']['path']).parent]
    if resume is not None: protected.append(plain(resume['path']).parent)
    require(all(out != p and p not in out.parents for p in protected), 'Output inside protected source/checkpoint')
    require(not any((p / '.git').exists() for p in (out, *out.parents)), 'Output must be outside every repository')
    outputs = {'CURRENT_ROSTER_DECISIONS.json': list(roster.values()), 'ACQUISITION_PLAN_DRYRUN.json': plan,
               'CHECKPOINT_RECONCILIATION_DRYRUN.json': checkpoint,
               'SOURCE_TIME_AND_POLICY_VALIDATION.json': dict(version=OFFLINE_VERSION, bindings=bundle['bindings'], cutoffs=request['cutoffs'],
                    provider_requests=0, daily_reduction_performed=False, build_clock_used_as_source=False),
               'RESULT.json': dict(version=OFFLINE_VERSION, status='OFFLINE_REVIEW_ONLY', intent_sha256=intent,
                    reconciliation_sha256=digest(reconciliation), pending_events=reconciliation['pending_events'], provider_requests=0,
                    publication_eligible=False, files={})}
    outputs['RESULT.json']['files'] = {name: digest(value) for name, value in outputs.items() if name != 'RESULT.json'}
    out.mkdir(mode=0o700)
    # Each complete file becomes durable before RESULT is written last. An
    # interrupted directory is preserved; restart in a fresh directory.
    for name, value in outputs.items():
        with (out / name).open('x') as f:
            os.chmod(f.fileno(), 0o600)
            f.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
            f.flush(); os.fsync(f.fileno())
    fd = os.open(out, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return outputs['RESULT.json']


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['record','aggregate','propose','export','restore']);p.add_argument('--root');p.add_argument('--input');p.add_argument('--output');p.add_argument('--as-of');p.add_argument('--sha256');a=p.parse_args()
    if a.mode=='record':append(a.root,json.loads(Path(a.input).read_text()))
    elif a.mode=='export':print(export(a.root,a.output))
    elif a.mode=='restore':restore(a.input,a.sha256,a.output)
    else:
        v=aggregate(records(a.root),a.as_of) if a.mode=='aggregate' else proposals(**json.loads(Path(a.input).read_text()))
        Path(a.output).write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':main()
