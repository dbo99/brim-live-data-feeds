#!/usr/bin/env python3
"""Thin source-specific preparation/result adapter; publisher ownership is unchanged.

Live acquisition requires an explicit future enable flag. SM3A tests replace only
source I/O/clock. SNOTEL is unconditionally gated pending its own source contract.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,tempfile,signal
from pathlib import Path
from datetime import datetime,timezone
import soil_moisture_operations as ops
import dendra_candidate as dc
import scan_soil_moisture_publisher as scan

HERE=Path(__file__).resolve().parent
SCAN_ENV={'SCAN_STATION_INDEX_CSV':'scan_station_index.csv','SCAN_SMS_PERCENTILES_CSV':'scan_sms_waterday_percentiles.csv','SCAN_DEPTH_STYLE_CSV':'scan_depth_style.csv'}
SCAN_BOUNDS={'SCAN_NWCC_PREFLIGHT_RETRIES':'3','SCAN_NWCC_PREFLIGHT_PAUSE_SEC':'10','SCAN_NWCC_PREFLIGHT_TIMEOUT_SEC':'20','SCAN_FETCH_RETRIES':'3','SCAN_FETCH_RETRY_PAUSE_SEC':'6','SCAN_FETCH_TIMEOUT_SEC':'60','SCAN_SMS_REQUEST_PAUSE_SEC':'0.35'}
def now():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def command(args,*,cwd=None,env=None):
    p=subprocess.run(list(map(str,args)),cwd=cwd,env=env,capture_output=True,text=True,timeout=1500)
    if p.returncode:raise RuntimeError((p.stdout+p.stderr)[-6000:])
    return p.stdout

def publisher_args(network,repo,transfer):
    ops.require(network in ('scan','dendra'),'SNOTEL has no approved publisher product')
    root=Path(repo).resolve();transfer=Path(transfer).resolve();sub=transfer/'publication' if network=='dendra' else transfer
    callback=root/'scripts'/('dendra_publisher.py' if network=='dendra' else 'scan_soil_moisture_publisher.py')
    args=['python3',root/'scripts/main_publisher.py','publish','--repo',root,'--candidate-root',sub/'candidate','--metadata',sub/'candidate-metadata.json','--product-id',dc.PRODUCT if network=='dendra' else scan.PRODUCT_ID,'--target-ref','refs/heads/main','--commit-subject',f'Update {network} prepared soil feed']
    for path in dc.FIXED if network=='dendra' else scan.ALLOWLIST:args+=['--allowlist',str(path)]
    if network=='dendra':
        for path in dc.OWNED:args+=['--owned-root',path]
    for flag in ('candidate-validator','reconcile-callback','staged-validator'):args+=['--'+flag,callback]
    return list(map(str,args))

def source_details(network,candidate):
    if network=='dendra':
        import dendra_archive as da
        idx=da.open_index(candidate);dates=[s['latest_accepted']['date'] for s in idx['streams'] if s.get('latest_accepted')]
        # Exact accepted calendar labels, not manufactured UTC observation instants.
        return idx['generation'],max(dates,default=None)
    summary=json.loads((candidate/scan.SUMMARY_PATH).read_text());geo=json.loads((candidate/scan.GEOJSON_PATH).read_text())
    dates=[f['properties']['display_obs_datetime_utc'] for f in geo['features'] if f['properties'].get('display_obs_datetime_utc') and f['properties'].get('display_sms_pct') is not None]
    return summary['feed_build_time_utc'],max(dates,default=None)

def scientific_fingerprint(candidate):
    import dendra_archive as da
    idx=da.open_index(candidate)
    return ops.digest([{ 'stream':s['datastream_id'], 'rows':da.stream_product(candidate,s)[1]['rows']} for s in idx['streams']])

def prepare(network,root,health,run_id,cycle,source_sha,*,state=None,catalog=None,as_of=None,budget=0,
            enabled=False,runner=command,clock=now,ledger=None,attempt=1,collection_limits=None):
    root=Path(root).resolve();ops.require(not root.exists(),'Fresh preparation directory required');root.mkdir(parents=True)
    existing_runs=set((Path(state)/'runs').glob('*/plan.json')) if state else set()
    previous_science=None
    ledger=Path(ledger) if ledger else root/'http.json'
    started=clock();outcome='not_enabled';error=None;generation=observation=check=None
    coverage=None;requests=dict(attempts=0,budget=budget,basis='no acquisition performed');candidate=root/'transfer/candidate'
    try:
        ops.require(network!='snotel' and enabled,'Source not enabled; SNOTEL source contract remains gated')
        if network=='dendra':
            ops.require(state is not None and catalog is not None and 0<budget<=80,'Explicit Dendra state/catalog/positive budget required')
            import dendra_state as ds
            pp=Path(state)/('published.json' if (Path(state)/'published.json').exists() else 'seed.json')
            previous_science=scientific_fingerprint(ds.candidate_at(Path(state),dc.load(pp)))
            runner(['Rscript','--vanilla',HERE/'build_dendra_daily.R','--mode','update','--catalog',catalog,'--state',state,'--output',root/'output','--as-of',as_of,'--ledger',ledger,'--budget',str(budget)]+(['--collection-limits',collection_limits] if collection_limits else []))
            runner(['python3',HERE/'dendra_state.py','transfer','--state',state,'--destination',root/'transfer','--source-sha',source_sha])
            candidate=root/'transfer/publication/candidate'
        elif network=='scan':
            requests=dict(attempts=None,budget=None,basis='Existing SCAN bounded per-call retry/backoff; cumulative attempts unknown even if builder fails.')
            candidate.mkdir(parents=True);env=dict(os.environ,**SCAN_BOUNDS,**{k:str(HERE.parent/'data/input'/v) for k,v in SCAN_ENV.items()})
            runner(['Rscript','--vanilla','-e','source('+json.dumps(str(HERE/'build_scan_soil_moisture_latest.R'))+')'],cwd=candidate,env=env)
            semantic,_=scan.validate_product(candidate)
            args=['python3',HERE/'main_publisher.py','prepare','--candidate-root',candidate,'--output',root/'transfer/candidate-metadata.json','--product-id',scan.PRODUCT_ID,'--semantic-key-type','feed_build_time_utc','--semantic-key',semantic,'--source-event-sha',source_sha]
            for path in scan.ALLOWLIST:args+=['--allowlist',path]
            runner(args)
            requests=dict(attempts=None,budget=None,basis='Existing SCAN bounded per-call retry/backoff; cumulative instrumentation unavailable. No inference from R-call count.')
        else:raise ValueError('Unknown network')
        generation,observation=source_details(network,candidate);outcome='success_no_data_change' if network=='dendra' and scientific_fingerprint(candidate)==previous_science else 'success_changes'
    except Exception as exc:
        error=str(exc)
        if network=='snotel' or not enabled:outcome='not_enabled'
        elif 'loss held' in error.lower():outcome='data_loss_hold'
        elif any(x in error.lower() for x in ('source failure','timeout','http','connection')):outcome='provider_failure'
        else:outcome='semantic_hold'
    finally:
        if network=='dendra' and state is not None:
            # Only the run started by this preparation. Never reuse a previous attempt's success.
            runs=[]
            for p in (Path(state)/'runs').glob('*/plan.json'):
                if p not in existing_runs:runs.append(p.parent)
            if runs:
                run=max(runs,key=lambda p:p.stat().st_mtime);c=run/'native/collection-result.json'
                if c.exists():
                    x=json.loads(c.read_text());coverage={k:x[k] for k in ('required_intervals','completed_intervals','backlog_intervals')};coverage['requested_intervals']=coverage.pop('required_intervals');coverage['basis']='validated collection result; candidate/acknowledgement remain separate'
                    if x['complete']:
                        check=max((chunk['retrieval_last_utc'] for i in x['completed'] for chunk in i['chunks']),key=ops.time,default=None)
                    elif any(f['error_class']=='checkpoint_or_identity_hold' for f in x['failures']):outcome='semantic_hold'
                    elif outcome!='data_loss_hold':outcome='incomplete_catch_up' if x['deferred'] or any(f['error_class']=='budget_limited' for f in x['failures']) else 'provider_failure'
            if ledger.exists():requests=dict(attempts=len(json.loads(ledger.read_text())),budget=budget,basis='actual BudgetClient cumulative attempt ledger, including retries')
        result=ops.result(network,run_id,cycle,started,clock(),outcome,attempt=attempt,cutoff=as_of,error_class=outcome if error else None,requests=requests,coverage=coverage,last_successful_source_check=check,latest_eligible_observation=observation,candidate_generation=generation)
        ops.append(health,result);(root/'run-result.json').write_text(json.dumps(result,indent=2)+'\n')
        if error:(root/'failure.txt').write_text(error+'\n')
    return result

def record_publication(health,prepared,repo,success,clock=now):
    x=ops.validate(json.loads(Path(prepared).read_text()));n=x['network'];receipt=None;error=None
    try:
        ops.require(success and x['outcome'] in ('success_changes','success_no_data_change'),'Publication did not succeed')
        repo=Path(repo).resolve();commit=command(['git','rev-parse','HEAD'],cwd=repo).strip()
        if n=='dendra':
            import dendra_state as ds
            with tempfile.TemporaryDirectory() as t:
                dest=Path(t)/'state';ds.restore_public(repo,dest);ptr=dc.load(dest/'published.json')
                receipt=dict(generation=ptr['generation'],commit=ptr['publication_commit'],index_sha256=ptr['index_sha256'])
        else:
            scan.validate_product(repo)
            for path in scan.ALLOWLIST:
                raw=subprocess.check_output(['git','show',commit+':'+str(path)],cwd=repo)
                ops.require(raw==(repo/path).read_bytes(),'SCAN working bytes differ from committed receipt')
            receipt=dict(generation=source_details(n,repo)[0],commit=commit,index_sha256=dc.sha(repo/scan.SUMMARY_PATH))
        ops.require(receipt['commit']==commit and commit==command(['git','rev-parse','HEAD'],cwd=repo).strip(),'Receipt HEAD moved')
        receipt.update(observed_at_utc=clock(),evidence='verified_committed_product',commit_ancestry=command(['git','rev-list','--max-count=256',commit],cwd=repo).splitlines())
    except (Exception,KeyboardInterrupt) as exc:error=str(exc);receipt=None
    r=ops.result(n,x['run_id'],x['cycle'],x['started_at_utc'],clock(),x['outcome'] if receipt else 'publication_failure',attempt=x['attempt'],phase='publication',cutoff=x['cutoff'],error_class=None if receipt else 'publication_or_receipt_failure',requests=x['requests'],coverage=x['coverage'],last_successful_source_check=x['last_successful_source_check'],latest_eligible_observation=x['latest_eligible_observation'],candidate_generation=x['candidate_generation'],acknowledgement=receipt)
    ops.append(health,r);return r

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='mode',required=True)
    a=s.add_parser('prepare');a.add_argument('--network',choices=list(ops.SOURCES),required=True)
    for name in ('root','health','run-id','cycle','source-sha'):a.add_argument('--'+name,required=True)
    for name in ('state','catalog','as-of','ledger','collection-limits'):a.add_argument('--'+name)
    a.add_argument('--attempt',type=int,default=1);a.add_argument('--budget',type=int,default=0);a.add_argument('--enable-acquisition',action='store_true')
    a=s.add_parser('publish');a.add_argument('--network',choices=['scan','dendra'],required=True);a.add_argument('--repo',required=True);a.add_argument('--transfer',required=True)
    a=s.add_parser('fallback-result');a.add_argument('--network',choices=list(ops.SOURCES),required=True);a.add_argument('--root',required=True);a.add_argument('--health',required=True);a.add_argument('--run-id',required=True);a.add_argument('--cycle',required=True);a.add_argument('--attempt',type=int,default=1)
    a=s.add_parser('publication-result');a.add_argument('--health',required=True);a.add_argument('--prepared',required=True);a.add_argument('--repo',required=True);a.add_argument('--success',action='store_true')
    a=p.parse_args()
    if a.mode=='prepare':
        r=prepare(a.network,a.root,a.health,a.run_id,a.cycle,a.source_sha,state=a.state,catalog=a.catalog,as_of=a.as_of,budget=a.budget,enabled=a.enable_acquisition,ledger=a.ledger,attempt=a.attempt,collection_limits=a.collection_limits);return 0 if r['outcome'] in ('success_changes','success_no_data_change') else 2
    if a.mode=='fallback-result':
        root=Path(a.root);path=root/'run-result.json'
        if not path.exists():
            root.mkdir(parents=True,exist_ok=True);t=now();x=ops.result(a.network,a.run_id,a.cycle,t,t,'not_enabled' if a.network=='snotel' else 'semantic_hold',attempt=a.attempt,error_class='preparation_setup_failed_no_source_check_established');ops.append(a.health,x);path.write_text(json.dumps(x)+'\n')
        return 0
    if a.mode=='publish':return subprocess.call(publisher_args(a.network,a.repo,a.transfer))
    def interrupted(signum,frame):raise RuntimeError('Receipt finalization interrupted')
    previous=signal.signal(signal.SIGTERM,interrupted)
    try:
        r=record_publication(a.health,a.prepared,a.repo,a.success)
        print(json.dumps(r,sort_keys=True));return 0 if r['acknowledgement'] is not None else 2
    finally:signal.signal(signal.SIGTERM,previous)
if __name__=='__main__':raise SystemExit(main())
