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

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['record','aggregate','propose','export','restore']);p.add_argument('--root');p.add_argument('--input');p.add_argument('--output');p.add_argument('--as-of');p.add_argument('--sha256');a=p.parse_args()
    if a.mode=='record':append(a.root,json.loads(Path(a.input).read_text()))
    elif a.mode=='export':print(export(a.root,a.output))
    elif a.mode=='restore':restore(a.input,a.sha256,a.output)
    else:
        v=aggregate(records(a.root),a.as_of) if a.mode=='aggregate' else proposals(**json.loads(Path(a.input).read_text()))
        Path(a.output).write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':main()
