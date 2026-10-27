#!/usr/bin/env python3
"""Thin source-specific preparation/result adapter; publisher ownership is unchanged.

Live acquisition requires an explicit future enable flag. SM3A tests replace only
source I/O/clock. The separate SNOTEL route defaults off and requires committed,
release-approved inputs; the legacy preparation modes retain their boundaries.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,tempfile,signal
import base64,hashlib,re,time
from pathlib import Path
from datetime import datetime,timezone
import soil_moisture_operations as ops
import dendra_candidate as dc
import scan_soil_moisture_publisher as scan
from soil_moisture import snotel_snapshot as ss, snotel_transport as st
import snotel_sms_publisher as sp
import main_publisher as mp

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

# SNOTEL operating adapter v1. These are ceilings, never provider permission.
SNOTEL_LIMITS = dict(attempts=32, station_bytes=1048576, station_seconds=60,
                     total_bytes=33554432, total_seconds=1920, finalization_seconds=120)
SNOTEL_SOURCES = (
    'scripts/soil_network_workflow.py', 'scripts/soil_moisture_operations.py',
    'scripts/soil_moisture/snotel_snapshot.py', 'scripts/soil_moisture/snotel_history.py',
    'scripts/soil_moisture/snotel_transport.py', 'scripts/snotel_sms_publisher.py',
    'scripts/main_publisher.py',
)
SNOTEL_CATALOG = 'data/input/snotel_sms_catalog.json'
SNOTEL_RUN_VERSION = 'snotel-operating-run-1'


def snotel_plain(path):
    p = Path(path).absolute()
    ops.require('..' not in p.parts and all(not x.is_symlink() for x in (p, *p.parents)),
                'Symlink or traversal in SNOTEL path')
    return p


def snotel_write(path, data):
    """Exclusive durable evidence write; never reset or replace a prior run."""
    path = snotel_plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class SnotelGitReader:
    """Explicit immutable Git-object reads. No fetch, index refresh or Git writes.

    The injected command function is a test seam, not a hosted CLI option.
    All returned blobs are checked against Git object IDs as well as byte bounds.
    """
    def __init__(self, repo, git=None):
        self.repo = snotel_plain(repo).resolve()
        self.test_stub = git is not None
        self.git = git or self._git
        origin = self.git(['remote', 'get-url', 'origin']).decode().strip()
        ops.require(origin in ('https://github.com/dbo99/brim-live-data-feeds.git',
                               'git@github.com:dbo99/brim-live-data-feeds.git'),
                    'Unexpected SNOTEL repository origin')

    def _git(self, args):
        p = subprocess.run(['git', '--no-optional-locks', *args], cwd=self.repo,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1'),
                           capture_output=True, timeout=30)
        ops.require(p.returncode == 0, 'Read-only Git object lookup failed')
        return p.stdout

    def commit(self, commit):
        ops.require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit),
                    'Explicit full source commit required')
        ops.require(self.git(['cat-file', '-t', commit]).strip() == b'commit', 'Not a commit object')
        ops.require(self.git(['rev-parse', '--verify', commit+'^{commit}']).decode().strip() == commit,
                    'Commit resolution mismatch')
        return commit

    def tree(self, commit, path):
        self.commit(commit)
        raw = self.git(['ls-tree', '-rz', '--full-tree', commit, '--', path])
        ops.require(len(raw) <= 1024**2, 'Committed inventory bound')
        entries = {}
        for row in raw.split(b'\0'):
            if not row: continue
            meta, name = row.split(b'\t', 1); mode, kind, oid = meta.decode().split()
            name = name.decode()
            ops.require(name not in entries and mode in ('100644', '100755') and kind == 'blob'
                        and re.fullmatch('[0-9a-f]{40}', oid), 'Nonregular/duplicate committed input')
            entries[name] = (mode, oid)
        return entries

    def blob(self, oid, cap):
        size = int(self.git(['cat-file', '-s', oid]))
        ops.require(0 <= size <= cap, 'Committed blob size limit')
        raw = self.git(['cat-file', 'blob', oid])
        ops.require(len(raw) == size and hashlib.sha1(
            b'blob '+str(size).encode()+b'\0'+raw).hexdigest() == oid, 'Committed blob identity mismatch')
        return raw

    def file(self, commit, path, cap):
        entries = self.tree(commit, path)
        ops.require(set(entries) == {path}, 'Exact committed file required')
        return self.blob(entries[path][1], cap)

    def source(self, commit):
        ops.require(self.test_stub or self.repo == HERE.parent.resolve(), 'Executing source checkout mismatch')
        self.commit(commit)
        ops.require(self.git(['rev-parse', 'HEAD']).decode().strip() == commit,
                    'Source checkout does not match selected commit')
        pins = {}
        entries = self.tree(commit, 'scripts')
        names = sorted(n for n in entries if n.endswith('.py'))
        ops.require(set(SNOTEL_SOURCES) <= set(names), 'Required source dependency absent')
        for name in names:
            raw = self.file(commit, name, 2*1024**2)
            p = snotel_plain(self.repo/name)
            ops.require(p.is_file() and p.read_bytes() == raw, 'Effective source differs from selected commit')
            pins[name] = ss.h.sha256(raw)
        return pins

    def product(self, commit, *, expected=None, bootstrap=False):
        entries = self.tree(commit, 'docs/data/snotel-sms')
        if not entries:
            ops.require(bootstrap and expected is None, 'Absent committed product requires explicit bootstrap')
            return None, None
        ops.require(set(entries) == set(ss.PATHS) and
                    all(v[0] == '100644' for v in entries.values()), 'Partial/nonregular/extra product')
        files = {}; remaining = ss.MAX_PRODUCT
        for name in ss.PATHS:
            files[name] = self.blob(entries[name][1], remaining); remaining -= len(files[name])
        binding = dict(commit=commit, **ss._binding(files[ss.PATHS[0]], files[ss.PATHS[1]]))
        if expected is not None: ops.require(binding == expected, 'Selected committed product pin mismatch')
        ss.import_product(files, trusted_binding=binding)
        return files, binding


def snotel_catalog(reader, source_commit, catalog_sha256, roster_sha256, *, synthetic):
    raw = reader.file(source_commit, SNOTEL_CATALOG, 4*1024**2)
    ops.require(ss.h.sha256(raw) == ss._hash(catalog_sha256), 'Committed catalog pin mismatch')
    c = ss.h._json(raw)
    ops.require(set(c) == {'version', 'disclosure', 'roster_sha256', 'groups'}
                and c['version'] == 'snotel-sms-catalog-1', 'Catalog schema')
    ops.require(c['disclosure'] == ('SYNTHETIC_ONLY' if synthetic else 'RELEASE_APPROVED_PUBLIC'),
                'Release-approved public catalog/native fields required')
    ops.require(c['roster_sha256'] == ss._hash(roster_sha256), 'Catalog roster pin mismatch')
    groups = []
    for g in c['groups']:
        ops.require(set(g) == {'metadata_base64', 'metadata_sha256', 'sensor_identities'}, 'Catalog group schema')
        groups.append(dict(metadata_body=base64.b64decode(g['metadata_base64'], validate=True),
                           metadata_sha256=g['metadata_sha256'], sensor_identities=g['sensor_identities']))
    binding = ss.roster_binding(groups)
    ops.require(binding == sorted(binding, key=lambda x: x['station_triplet']) and
                ss.h.sha256(ss.h.canonical(binding)) == roster_sha256, 'Exact canonical 32/98 roster')
    if synthetic:
        ops.require(all(':ZZ:SNTL' in b['station_triplet'] for b in binding),
                    'Synthetic seam requires fictional station namespace')
    else:
        ops.require(all(':ZZ:SNTL' not in b['station_triplet'] for b in binding),
                    'Synthetic identities cannot be released or acquired')
    return groups


def _snotel_lane(root, group, request, **kw):
    return st.StationTransport(root, [dict(request=request, metadata_body=group['metadata_body'],
        metadata_sha256=group['metadata_sha256'])], max_attempts=1, retries=0, retry_delay=0,
        max_response_bytes=SNOTEL_LIMITS['station_bytes'], max_total_bytes=SNOTEL_LIMITS['station_bytes'],
        max_elapsed_seconds=SNOTEL_LIMITS['station_seconds'], **kw)


def _snotel_capture(group, request, client, captures):
    ops.require(len(captures) == 1 and len(client.ledger['attempts']) == 1,
                'Exactly one physical attempt and retained capture required')
    ops.require(not any(a.get('retry_after') is not None for a in client.ledger['attempts']),
                'Provider Retry-After obligation; stop before next station')
    c = captures[0]; r = c['receipt']; body = c['body']
    ops.require(r['request'] == request and r.get('http_status') == 200 and r.get('complete') is True
                and r.get('error') is None and r.get('physical_attempt') == 1
                and r.get('body_accounting_state') == 'COMPLETE'
                and r.get('total_consumed_body_bytes') == r.get('captured_prefix_bytes') == len(body)
                and 0 < len(body) <= SNOTEL_LIMITS['station_bytes'], 'Failed/incomplete/body-accounting hold')
    history = ss.h.validate_history(ss.h.build_history(group['metadata_body'], group['metadata_sha256'], captures))
    ops.require(all(q['successful_coverage'] for q in history['query_ledger']), 'Required station source validation hold')
    # Reuse snapshot field/flag disclosure checks before permitting the next station.
    for row in history['observations']:
        record = ss._native(row, 0, 0)
        ops.require(all(v is None or len(ss.h.canonical(v).strip()) <= ss.MAX_NUMBER_BYTES
                        for v in record[:2]), 'Native numeric representation bound')
    return c


def _snotel_finish(root, destination, groups, plan, captures, prior, trust, source_sha, built_at):
    ss.check_run_year(plan, built_at)
    files = ss.build_saved_snapshot(groups, iter(captures), year=plan['water_year'],
        start=plan['start'], end=plan['end'], as_of=plan['as_of'], built_at=built_at,
        expected_roster_sha256=plan['roster_sha256'],
        expected_capture_sha256s=[c['receipt_sha256'] for c in captures], prior_files=prior, trusted_prior=trust)
    d = ss.h._json(files[ss.PATHS[1]]); ss.validate_routine_candidate(d)
    transfer = snotel_plain(destination)
    ops.require(not transfer.exists(), 'Fresh candidate transfer required')
    transfer.mkdir(mode=0o700)
    ss.write_new_product(transfer/'candidate', files)
    sp.validate_product(transfer/'candidate', candidate=True)
    mp.prepare_metadata(argparse.Namespace(candidate_root=transfer/'candidate',
        output=transfer/'candidate-metadata.json', product_id=ss.PRODUCT, allowlist=list(ss.PATHS),
        owned_root=[], semantic_key_type='generation', semantic_key=d['generation'], source_event_sha=source_sha))
    return d


def snotel_prepare(reader, root, *, source_sha, prior_commit, catalog_sha256, roster_sha256,
                   acquire=False, publish=False, bootstrap=False, release_approved=False,
                   budget=None, window_end=None, clock=now, monotonic=time.monotonic,
                   opener=None, transport_wall=time.time):
    """Only the injected opener seam permits synthetic execution; CLI has no such switch.

    One fresh root is one run. Existing/uncertain roots never resume acquisition.
    Consumed bytes are measured at the native body-read interface, not on the wire.
    """
    ops.require(not publish or acquire, 'Publication requires explicit acquisition')
    root = snotel_plain(root)
    ops.require(root != reader.repo and reader.repo not in root.parents, 'Run root must be outside source')
    ops.require(not root.exists(), 'Run already exists; never reset or resume acquisition')
    root.mkdir(mode=0o700, parents=False)
    started = clock(); synthetic = opener is not None
    report = dict(version=SNOTEL_RUN_VERSION, status='DISABLED', candidate_ready=False,
                  synthetic=synthetic, attempts=0, consumed_body_bytes=0, completed_stations=0,
                  station_call_seconds=0.0, source_commit=source_sha, source_check=None,
                  latest_measurement=None, candidate_generation=None, publication_acknowledgment=None)
    error = None; plan = None; captures = []; prior = trust = None
    try:
        if not acquire: return report
        ops.require(os.environ.get('GITHUB_RUN_ATTEMPT', '1') == '1',
                    'Hosted rerun cannot replace spent or missing attempt state; separate authority required')
        ops.require(budget == SNOTEL_LIMITS, 'Exact explicit SNOTEL run budget required')
        ops.require(synthetic or release_approved, 'Public catalog/native-field release clearance required')
        ops.require(synthetic or not reader.test_stub, 'Test Git cannot authorize provider I/O')
        ops.require(not synthetic or not publish, 'Synthetic candidates cannot authorize publication')
        source_pins = reader.source(source_sha)
        prior, trust = reader.product(prior_commit, bootstrap=bootstrap)
        groups = snotel_catalog(reader, source_sha, catalog_sha256, roster_sha256, synthetic=synthetic)
        plan = ss.plan_current_wy(started, groups, expected_roster_sha256=roster_sha256)
        bound = dict(version=SNOTEL_RUN_VERSION, source_sha=source_sha, source_pins=source_pins,
            prior_commit=prior_commit, prior_binding=trust, bootstrap=bootstrap, synthetic=synthetic,
            catalog_sha256=catalog_sha256, roster_sha256=roster_sha256, limits=budget, plan=plan)
        run_bytes = ss.h.canonical(bound); snotel_write(root/'run.json', run_bytes)
        report['run_sha256'] = ss.h.sha256(run_bytes)
        if not plan['requests']:
            report['status'] = 'NO_ELIGIBLE_INTERVAL'; return report
        if prior is not None:
            old = ss.h._json(prior[ss.PATHS[1]])
            ops.require(old['water_year'] in (plan['water_year'], plan['water_year']-1), 'Prior WY incompatible')
            if old['water_year'] == plan['water_year']:
                ops.require(old['end'] <= plan['end'] and old['roster_sha256'] == roster_sha256,
                            'Same-WY shrink/metadata drift')
        ops.require(window_end is not None, 'Explicit acquisition window end required')
        def guarded_wall():
            epoch = transport_wall()
            current = datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            ss.check_run_year(plan, current)
            ops.require(ops.time(current) < ops.time(window_end), 'Acquisition window closed')
            return epoch
        for i, (g, request) in enumerate(zip(groups, plan['requests'])):
            current = clock(); ss.check_run_year(plan, current)
            remaining = (ops.time(window_end)-ops.time(current)).total_seconds()
            ops.require(remaining >= (32-i)*60+120, 'Acquisition/finalization headroom exhausted')
            ops.require(report['attempts'] == i and report['consumed_body_bytes'] is not None and
                report['consumed_body_bytes']+(32-i)*1048576 <= budget['total_bytes'] and
                report['station_call_seconds']+(32-i)*60 <= budget['total_seconds'],
                'Aggregate acquisition budget exhausted')
            # Durable native reservation happens in collect(), before the injected/real opener.
            client = _snotel_lane(root/f'station-{i+1:02d}', g, request,
                live=not synthetic, opener=opener, wall=guarded_wall)
            t0 = monotonic()
            try:
                saved = client.collect()
            finally:
                report['station_call_seconds'] += max(0, monotonic()-t0)
                if client.ledger is not None:
                    report['attempts'] += len(client.ledger['attempts'])
                    amounts = [a.get('total_consumed_bytes') for a in client.ledger['attempts']]
                    if report['consumed_body_bytes'] is None or any(type(n) is not int for n in amounts):
                        report['consumed_body_bytes'] = None
                    else: report['consumed_body_bytes'] += sum(amounts)
            capture = _snotel_capture(g, request, client, saved)
            ops.require(report['station_call_seconds'] <= budget['total_seconds']
                        and report['consumed_body_bytes'] is not None
                        and report['consumed_body_bytes'] <= budget['total_bytes'], 'Cumulative acquisition limit')
            captures.append(capture); report['completed_stations'] += 1
            snotel_write(root/f'completed-{i+1:02d}.json', ss.h.canonical(dict(
                receipt_sha256=capture['receipt_sha256'], request_sha256=ss.h.sha256(ss.h.canonical(request)),
                cumulative_attempts=report['attempts'], cumulative_bytes=report['consumed_body_bytes'],
                cumulative_station_call_seconds=report['station_call_seconds'])))
        build_clock = clock()
        capture_manifest = ss.h.canonical(dict(run_sha256=report['run_sha256'],
            receipt_sha256s=[c['receipt_sha256'] for c in captures],
            completion_sha256s=[ss.h.sha256((root/f'completed-{i+1:02d}.json').read_bytes()) for i in range(32)]))
        snotel_write(root/'captures.json', capture_manifest)
        report['capture_manifest_sha256'] = ss.h.sha256(capture_manifest)
        ops.require((ops.time(window_end)-ops.time(build_clock)).total_seconds() >= 120,
                    'Finalization window margin exhausted')
        d = _snotel_finish(root, root/'transfer', groups, plan, captures, prior, trust, source_sha, build_clock)
        ss.check_run_year(plan, clock())
        report.update(status='CANDIDATE_READY', candidate_ready=True,
            source_check=max(c['receipt']['retrieved_at_utc'] for c in captures),
            latest_measurement=max((row[1] for sensor in d['sensors'] for row in sensor['days']
                                    if row[3] is not None), default=None), candidate_generation=d['generation'],
            coverage=d['coverage'], candidate_bytes=sum((root/'transfer/candidate'/p).stat().st_size for p in ss.PATHS),
            science_changed=any(d['coverage']['admission_counts'][x] for x in ('OBSERVED_ACCEPTED','ACCEPTED_REVISION')))
    except Exception as exc:
        report.update(status='HELD', candidate_ready=False)
        error = type(exc).__name__+': '+str(exc)
        snotel_write(root/'failure.txt', error.encode())
    finally:
        report['finished_at_utc'] = clock()
        report['elapsed_total_seconds'] = (ops.time(report['finished_at_utc'])-ops.time(started)).total_seconds()
        report['timing_basis'] = 'Native 60s acquisition excludes close/persistence; station call timing conservatively includes both. No whole-process deadline claim.'
        report['body_basis'] = 'Consumed body-read-interface bytes; unknown remains null, not prefix size or physical wire bytes.'
        report['attempt_basis'] = 'Durable reserved/consumed lanes; a failed reservation does not prove opener entry. Complete station receipts establish physical attempts.'
        report['proven_completed_physical_attempts'] = report['completed_stations']
        snotel_write(root/'result.json', ss.h.canonical(report))
    return report


def snotel_rebuild(reader, root, destination, *, run_sha256, capture_manifest_sha256, clock=now):
    """Fresh-process saved-only rebuild. Never calls collect or creates missing lanes."""
    destination = snotel_plain(destination)
    ops.require(destination != reader.repo and reader.repo not in destination.parents,
                'Rebuild output must be outside source')
    root = snotel_plain(root); run_path = snotel_plain(root/'run.json')
    ops.require(run_path.stat().st_size < 4*1024**2, 'Run binding size limit')
    raw = run_path.read_bytes()
    ops.require(len(raw) < 4*1024**2 and ss.h.sha256(raw) == ss._hash(run_sha256), 'Run binding mismatch')
    manifest_path = snotel_plain(root/'captures.json')
    ops.require(manifest_path.is_file(), 'Missing/ambiguous completed capture manifest')
    ops.require(manifest_path.stat().st_size <= 16384, 'Capture manifest size limit')
    manifest_bytes = manifest_path.read_bytes()
    ops.require(len(manifest_bytes) <= 16384 and ss.h.sha256(manifest_bytes) == ss._hash(capture_manifest_sha256),
                'Independent saved capture manifest binding mismatch')
    manifest = ss.h._json(manifest_bytes)
    ops.require(set(manifest) == {'run_sha256', 'receipt_sha256s', 'completion_sha256s'}
                and manifest['run_sha256'] == run_sha256
                and len(manifest['receipt_sha256s']) == len(manifest['completion_sha256s']) == 32,
                'Capture manifest closure')
    b = ss.h._json(raw)
    ops.require(b['version'] == SNOTEL_RUN_VERSION and b['limits'] == SNOTEL_LIMITS, 'Run contract mismatch')
    ops.require(reader.source(b['source_sha']) == b['source_pins'], 'Rebuild source drift')
    prior, trust = reader.product(b['prior_commit'], expected=b['prior_binding'], bootstrap=b['bootstrap'])
    groups = snotel_catalog(reader, b['source_sha'], b['catalog_sha256'], b['roster_sha256'], synthetic=b['synthetic'])
    plan = ss.plan_current_wy(b['plan']['as_of'], groups, expected_roster_sha256=b['roster_sha256'])
    ops.require(plan == b['plan'] and len(plan['requests']) == 32, 'Saved plan mismatch/no work')
    captures = []; total = 0
    for i, (g, req) in enumerate(zip(groups, plan['requests'])):
        lane = snotel_plain(root/f'station-{i+1:02d}')
        done = snotel_plain(root/f'completed-{i+1:02d}.json')
        ops.require((lane/'ledger.json').is_file() and done.is_file(), 'Missing/ambiguous completed lane; no new attempt')
        client = _snotel_lane(lane, g, req)
        cap = _snotel_capture(g, req, client, client.saved_captures())
        ops.require(done.stat().st_size <= 4096, 'Completion receipt size limit')
        done_bytes = done.read_bytes()
        ops.require(ss.h.sha256(done_bytes) == manifest['completion_sha256s'][i]
                    and cap['receipt_sha256'] == manifest['receipt_sha256s'][i], 'Saved capture authentication mismatch')
        receipt = ss.h._json(done_bytes); total += len(cap['body'])
        ops.require(receipt['receipt_sha256'] == cap['receipt_sha256']
            and receipt['request_sha256'] == ss.h.sha256(ss.h.canonical(req))
            and receipt['cumulative_attempts'] == i+1 and receipt['cumulative_bytes'] == total
            and 0 <= receipt['cumulative_station_call_seconds'] <= SNOTEL_LIMITS['total_seconds'],
            'Completed lane binding/budget mismatch')
        captures.append(cap)
    return _snotel_finish(root, destination, groups, plan, captures, prior, trust, b['source_sha'], clock())


def snotel_acknowledge(reader, commit, candidate, *, clock=now):
    expected = sp.validate_product(candidate, candidate=True)
    files, binding = reader.product(commit)
    if files != expected:
        decision = sp.classify(expected, files)
        ops.require(decision['decision'] == 'no-op' and decision['candidate_state'] == 'stale',
                    'Ambiguous acknowledgment: committed product differs')
        return dict(status='STALE_NO_OP_NEWER_PRODUCT_PRESERVED', acknowledged=False,
                    committed=binding, observed_at_utc=clock())
    receipt = sp.verify_acknowledgment(expected, files, commit_binding=binding)
    receipt['observed_at_utc'] = clock()
    return receipt


def snotel_publish_args(repo, transfer, *, catalog_sha256, roster_sha256, clock=now):
    """Future publisher command only; publication itself remains the shared owner."""
    repo, transfer = Path(repo).resolve(), snotel_plain(transfer)
    reader = SnotelGitReader(repo)
    metadata = json.loads((transfer/'candidate-metadata.json').read_bytes())
    source_sha = metadata['source_event_sha']; reader.source(source_sha)
    mp.validate_candidate_metadata(root=transfer/'candidate', metadata_path=transfer/'candidate-metadata.json',
        product_id=ss.PRODUCT, allowlist=ss.PATHS, expected_source_sha=source_sha)
    files = sp.validate_product(transfer/'candidate', candidate=True)
    d = ss.h._json(files[ss.PATHS[1]])
    snotel_catalog(reader, source_sha, catalog_sha256, roster_sha256, synthetic=False)
    ops.require(d['roster_sha256'] == roster_sha256, 'Publication catalog/roster mismatch')
    ss.check_run_year(dict(as_of=d['as_of'], water_year=d['water_year']), clock())
    callback = repo/'scripts/soil_network_workflow.py'
    args = ['python3', repo/'scripts/main_publisher.py', 'publish', '--repo', repo,
        '--candidate-root', transfer/'candidate', '--metadata', transfer/'candidate-metadata.json',
        '--product-id', ss.PRODUCT, '--target-ref', 'refs/heads/main',
        '--commit-subject', 'Update SNOTEL current-WY SMS product']
    for p in ss.PATHS: args += ['--allowlist', p]
    for flag in ('candidate-validator', 'reconcile-callback', 'staged-validator'): args += ['--'+flag, callback]
    return list(map(str, args))

def snotel_callback(environment=None, *, clock=now):
    env = os.environ if environment is None else environment
    files = sp.validate_product(env['BRIM_PUBLISH_CANDIDATE_ROOT'], candidate=True)
    d = ss.h._json(files[ss.PATHS[1]])
    ss.check_run_year(dict(as_of=d['as_of'], water_year=d['water_year']), clock())
    return sp.callback_main(env)

def main():
    if len(sys.argv)==1 and os.environ.get('BRIM_PUBLISH_PRODUCT_ID')==ss.PRODUCT:
        return snotel_callback()
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='mode',required=True)
    a=s.add_parser('snotel-prepare', help='Disabled unless acquisition and release inputs are explicit')
    for name in ('repo','root','source-sha','prior-commit','catalog-sha256','roster-sha256'):
        a.add_argument('--'+name,required=True)
    for name in ('acquire','publish','bootstrap','release-approved'):
        a.add_argument('--'+name,action='store_true')
    a.add_argument('--budget-json');a.add_argument('--window-end')
    a=s.add_parser('snotel-rebuild', help='Saved complete lanes only; never resumes acquisition')
    for name in ('repo','root','destination','run-sha256','capture-manifest-sha256'):a.add_argument('--'+name,required=True)
    a=s.add_parser('snotel-acknowledge')
    for name in ('repo','commit','candidate'):a.add_argument('--'+name,required=True)
    a=s.add_parser('snotel-publish')
    for name in ('repo','transfer','catalog-sha256','roster-sha256'):a.add_argument('--'+name,required=True)
    a=s.add_parser('offline-wy2027',help='Pinned local fixture review only; no acquisition or publication')
    a.add_argument('--request',required=True);a.add_argument('--request-sha256',required=True);a.add_argument('--output',required=True)
    a.add_argument('--offline-only',action='store_true',required=True)
    a.add_argument('--resume');a.add_argument('--resume-sha256');a.add_argument('--max-events',type=int)
    a=s.add_parser('prepare');a.add_argument('--network',choices=list(ops.SOURCES),required=True)
    for name in ('root','health','run-id','cycle','source-sha'):a.add_argument('--'+name,required=True)
    for name in ('state','catalog','as-of','ledger','collection-limits'):a.add_argument('--'+name)
    a.add_argument('--attempt',type=int,default=1);a.add_argument('--budget',type=int,default=0);a.add_argument('--enable-acquisition',action='store_true')
    a=s.add_parser('publish');a.add_argument('--network',choices=['scan','dendra'],required=True);a.add_argument('--repo',required=True);a.add_argument('--transfer',required=True)
    a=s.add_parser('fallback-result');a.add_argument('--network',choices=list(ops.SOURCES),required=True);a.add_argument('--root',required=True);a.add_argument('--health',required=True);a.add_argument('--run-id',required=True);a.add_argument('--cycle',required=True);a.add_argument('--attempt',type=int,default=1)
    a=s.add_parser('publication-result');a.add_argument('--health',required=True);a.add_argument('--prepared',required=True);a.add_argument('--repo',required=True);a.add_argument('--success',action='store_true')
    a=p.parse_args()
    if a.mode.startswith('snotel-'):
        if a.mode=='snotel-publish':return subprocess.call(snotel_publish_args(a.repo,a.transfer,
            catalog_sha256=a.catalog_sha256,roster_sha256=a.roster_sha256))
        reader=SnotelGitReader(a.repo)
        if a.mode=='snotel-acknowledge':
            print(json.dumps(snotel_acknowledge(reader,a.commit,a.candidate),sort_keys=True));return 0
        if a.mode=='snotel-rebuild':
            d=snotel_rebuild(reader,a.root,a.destination,run_sha256=a.run_sha256,
                capture_manifest_sha256=a.capture_manifest_sha256)
            print(json.dumps(dict(status='REBUILT_SAVED_ONLY',generation=d['generation'])));return 0
        r=snotel_prepare(reader,a.root,source_sha=a.source_sha,prior_commit=a.prior_commit,
            catalog_sha256=a.catalog_sha256,roster_sha256=a.roster_sha256,acquire=a.acquire,
            publish=a.publish,bootstrap=a.bootstrap,release_approved=a.release_approved,
            budget=json.loads(a.budget_json) if a.budget_json else None,window_end=a.window_end)
        # A public log contains only this positive summary, never requests/metadata/errors.
        print(json.dumps({k:r[k] for k in ('status','candidate_ready','synthetic','attempts')}))
        return 2 if r['status']=='HELD' else 0
    if a.mode=='offline-wy2027':
        ops.require(bool(a.resume)==bool(a.resume_sha256),'Both checkpoint path and pin are required')
        request=ops.offline_read(dict(path=a.request,sha256=a.request_sha256))
        ops.require(request.get('offline_only') is True,'Request must explicitly select offline mode')
        resume=dict(path=a.resume,sha256=a.resume_sha256) if a.resume else None
        print(json.dumps(ops.offline_run(request,a.output,resume=resume,max_events=a.max_events),sort_keys=True));return 0
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
