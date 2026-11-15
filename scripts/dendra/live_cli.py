"""Explicit offline fixture/science/range commands; command-line live stays sealed.

The programmatic supervised_range seam requires a separately authenticated
private caller and explicit transport. This module never issues live authority.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from .live_capture import Page, capture, shape
from .live_sampling import window, sample
from .live_state import State, read
from .live_product import publish_private, SCIENCE
from .history_acquisition.safety import sha, decode, encode, require

ORACLE_SHA256 = 'cc83f07c241128a568d9af0463b294383243d203ee842109bbfa1ade1be741da'
BODY_SHA256 = frozenset((
    'a5548cc1c89a726d85cb5a04cf80033fd3fa2a1a6a2d355fb8595c385de31f36',
    '58cf94991039a4b463857379e63141c1097e98c5bd38e7e663f9e182a97e76f4',
    '872ead126eaa2d31b06ba11f7fb0d995a8fcca91d60d5d5deac5b81505253c5f',
    'e71b72671bd40b1f7b1a974dec9c0454d022d542f4cd75d09ab6bf4e5d2d2581'))


def inputs(path, expected_sha256):
    body = read(Path(path), 32*1024)
    require(sha(body) == expected_sha256, 'Explicit fixture manifest pin mismatch')
    m = decode(body)
    require(set(m) == {'oracle_path','pages'} and type(m['pages']) is list and len(m['pages']) == 4,
            'Exactly four fixture tasks required')
    original = read(Path(m['oracle_path']), 128*1024)
    require(sha(original) == ORACLE_SHA256, 'Independent R1 result oracle pin mismatch')
    oracle = decode(original)
    require(oracle['synthetic'] is False, 'Authentic oracle required')
    bodies, references = {}, {}
    for item in m['pages']:
        require(set(item) == {'path','sha256','stream_id','target_date'}, 'Explicit fixture fields')
        key = item['stream_id']+'/'+item['target_date']
        require(key not in bodies and item['sha256'] in BODY_SHA256, 'Duplicate/unapproved fixture')
        raw = read(Path(item['path']), 467)
        require(len(raw) == 467 and sha(raw) == item['sha256'], 'Authentic native page pin mismatch')
        reference = oracle['view']['state']['series'][item['stream_id']][item['target_date']]['current']
        payload = shape(raw, item['stream_id'])
        require(len(payload['data']) == 6 and payload['limit'] == 2016 and
                [r for r in payload['data'] if r['t'] == reference['source_timestamp_utc']] == reference['source_records'],
                'Original page/oracle binding')
        bodies[key], references[key] = raw, reference
    require({sha(b) for b in bodies.values()} == BODY_SHA256 and
            len({k.split('/')[0] for k in bodies}) == 2 and
            {k.split('/')[1] for k in bodies} == {'2026-10-14','2026-10-15'}, 'Bounded representative roster')
    binding = dict(lineage='dendra-independent-offline-stage-a-1', parent_generation=None,
                   fixture_source='AUTHENTIC_R1_SAVED_NATIVE_RESPONSES', manifest_sha256=expected_sha256,
                   oracle_sha256=ORACLE_SHA256, bodies={k:sha(b) for k,b in bodies.items()},
                   as_of_source_cutoff=oracle['captured_as_of_utc'])
    return binding, bodies, references


def run(manifest, pin, output):
    binding, bodies, references = inputs(manifest, pin)  # Authenticate before any output.
    root = Path(output).absolute()
    repo = Path(__file__).resolve().parents[2]
    require(root == root.resolve() and repo not in (root, *root.parents), 'Private output outside repository required')
    state = State(root, binding, create=True)  # Existing lineage always refused.
    try:
        for key, body in bodies.items():
            sid, day = key.split('/')
            ref = references[key]
            clock = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            raw, evidence = capture(state, key, sid, window(day, binding['as_of_source_cutoff']), [Page(body)], clock,
                                    body_cap=467)
            evidence.update(original=ref['evidence'], fixture_source=binding['fixture_source'])
            result = sample(raw, ref['metadata'], day, binding['as_of_source_cutoff'], evidence)
            require(all(result[k] == ref[k] for k in SCIENCE), 'Four-page scientific parity failed')
            state.outcome(key, clock, result)
        return publish_private(state, references)
    finally:
        state.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline-fixtures', action='store_true')
    mode.add_argument('--offline-dry-run', action='store_true')
    mode.add_argument('--offline-science-v2', action='store_true')
    mode.add_argument('--offline-export-v2', action='store_true')
    mode.add_argument('--offline-range', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--days-back',type=int)
    parser.add_argument('--from',dest='start')
    parser.add_argument('--through')
    a = parser.parse_args(argv)
    require(not a.live, 'Live CLI is sealed: separate finite approval and trusted revocation/source verifier required')
    if a.offline_range:
        runner=lambda m,p,o: range_offline(m,p,o,days_back=a.days_back,start=a.start,through=a.through)
    elif a.offline_export_v2:
        from .live_export import export
        runner = export
    else:
        runner = science_v2 if a.offline_science_v2 else (dry_run if a.offline_dry_run else run)
    result = runner(a.manifest, a.manifest_sha256, a.output)
    result['dendra_imports'] = sorted(n for n in sys.modules if n.startswith('scripts.dendra'))
    print(encode(result).decode(), end='')


def range_offline(manifest,pin,output,**selection):
    import io
    from datetime import timedelta
    from .live_range import targets,series_binding
    from .live_provider import source_fingerprint,sync
    from .live_export import range_preview
    body=read(Path(manifest),1024*1024);require(sha(body)==pin,'Range input manifest pin')
    m=decode(body);require(set(m)=={'plan','responses'},'Exact offline range inputs')
    plan=m['plan'];root=Path(output).absolute();repo=Path(__file__).resolve().parents[2]
    require(root==root.resolve() and repo not in (root,*root.parents) and 'private_custody' not in root.parts and
            plan['output_root']==str(root) and plan['source_sha256']==source_fingerprint(),'Private source-bound range root')
    binding=series_binding(plan['roster'],plan['water_year'],plan['ledger_attempt_limit'],version=plan.get('persistence_version',1))
    responses={r['url']:r for r in m['responses']};require(len(responses)==len(m['responses'])<=768,'Finite unique fixture queries')
    elapsed=[0.0]
    def now():return parse_utc(plan['not_before'])+timedelta(seconds=elapsed[0])
    def wait(seconds):elapsed[0]+=seconds
    from .transport import parse_utc
    class Response(io.BytesIO):
        def geturl(self):return self.url
    def opener(request,timeout):
        require(request.full_url in responses,'No saved response for exact query')
        r=responses[request.full_url];b=read(Path(r['path']),plan['limits']['response_bytes'])
        require(sha(b)==r['sha256'] and r['provenance']['representation'] in
                ('AUTHENTIC_SAVED_NATIVE','SYNTHETIC_INJECTED'),'Pinned labeled response required')
        response=Response(b);response.url=request.full_url;response.status=r.get('status',200)
        response.headers={'Content-Length':str(len(b)+(0 if r.get('complete',True) else 1)),'Content-Type':'application/json'}
        response.saved_provenance=r['provenance'];elapsed[0]+=.01;return response
    state=State(root,binding,create=not root.exists())
    try:
        selected=targets(plan['roster'],plan['water_year'],plan['not_before'],state.data['cells'],**selection)
        require(plan['range_targets']==selected,'Operator selection differs from pinned work plan')
        outcome=sync(state,plan,opener=opener,now=now,monotonic=lambda:elapsed[0],wait=wait)
        return dict(range_preview(state),run_outcome=outcome)
    finally:state.close()


def supervised_range(plan, output, *, authorize, opener, now=None, monotonic=None, wait=None, **selection):
    """Explicit private-supervisor entry; the command-line live mode stays sealed.

    The future attended caller must authenticate Dave's independent exact grant,
    source/plan/root/target/budget binding, actual UTC, revocation and spent state.
    A local APPROVED field, plan hash or fixture is not that external authority.
    No default verifier, network opener, grant issuance or implicit retry exists.
    Tests may inject a synthetic verifier/clock/transport with sockets denied.
    """
    from copy import deepcopy
    import time
    from .live_provider import validate_plan,sync
    from .live_range import targets,series_binding
    from .live_export import range_preview
    from .history_acquisition.safety import digest
    from .transport import format_utc
    require(callable(authorize) and callable(opener), 'Independent verifier and explicit transport required')
    plan=deepcopy(plan)
    now=now or (lambda:datetime.now(timezone.utc))
    monotonic=monotonic or time.monotonic;wait=wait or time.sleep
    clock=format_utc(now());validate_plan(plan,clock,live=True)
    root=Path(output).absolute();repo=Path(__file__).resolve().parents[2]
    require(root==root.resolve() and str(root)==plan['output_root'] and
            repo not in (root,*root.parents) and 'private_custody' not in root.parts,
            'Private exact granted range root')
    require(authorize(digest(plan),clock) is True, 'Independent exact grant not verified')
    binding=series_binding(plan['roster'],plan['water_year'],plan['ledger_attempt_limit'],version=2)
    state=State(root,binding,create=not root.exists())
    try:
        selected=targets(plan['roster'],plan['water_year'],plan['not_before'],state.data['cells'],**selection)
        require(plan['range_targets']==selected, 'Operator selection differs from authorized range')
        outcome=sync(state,plan,opener=opener,now=now,monotonic=monotonic,wait=wait,live=True,authorize=authorize)
        return dict(range_preview(state),run_outcome=outcome)
    finally:state.close()


def family_child(campaign, child_index, *, opener, live=False, authorize=None,
                 now=None, monotonic=None, wait=None):
    """Serial existing-engine pairs; no default transport, grant or retry.

    The attended supervisor verifies the independent child grant, committed-clean
    source and spent/revoked state. Summed pair reservations bound the child even
    after interruption; an incomplete pair stops this invocation with its charge
    retained. Re-entry requires an explicit new exact grant/run, never a retry.
    """
    from copy import deepcopy
    import os
    import time
    from .live_range import validate_family_campaign,series_binding
    from .live_provider import sync
    from .live_export import range_preview
    from .history_acquisition.safety import digest
    from .transport import parse_utc,format_utc
    campaign=deepcopy(campaign);validate_family_campaign(campaign,live=live)
    require(type(child_index) is int and 0<=child_index<len(campaign['children']) and callable(opener),
            'Exact child and explicit transport required')
    child=campaign['children'][child_index];pin=digest(child)
    now=now or (lambda:datetime.now(timezone.utc))
    monotonic=monotonic or time.monotonic;wait=wait or time.sleep
    def approved(member_pin, clock):
        return (member_pin in member_pins and parse_utc(child['not_before'])<=parse_utc(clock)<
                parse_utc(child['expires_at']) and callable(authorize) and authorize(pin,clock) is True)
    member_pins={digest(p) for p in child['members']}
    clock=format_utc(now())
    require(parse_utc(child['not_before'])<=parse_utc(clock)<parse_utc(child['expires_at']), 'Child outside window')
    require(not live or approved(next(iter(member_pins)),clock), 'Independent exact child grant required')
    root=Path(child['output_root']);repo=Path(__file__).resolve().parents[2]
    require(repo not in (root,*root.parents) and 'private_custody' not in root.parts,
            'Private family root outside source/custody')
    if not root.exists():root.mkdir(mode=0o700)
    require(root.is_dir() and root.stat().st_uid==os.getuid() and root.stat().st_mode & 0o077==0,
            'Owner-private family root')
    results=[]
    for p in child['members']:
        # A new Provider's first request has no prior spacing record. Waiting
        # after the previous pair finishes also bounds spacing across pairs.
        if results:wait(1)
        clock=format_utc(now())
        require(parse_utc(clock)<parse_utc(child['expires_at']) and
                (not live or approved(digest(p),clock)), 'Child expired/revoked before pair')
        binding=series_binding(p['roster'],p['water_year'],p['ledger_attempt_limit'],version=2)
        pair=Path(p['output_root']);state=State(pair,binding,create=not pair.exists())
        try:
            outcome=sync(state,p,opener=opener,now=now,monotonic=monotonic,wait=wait,
                         live=live,authorize=approved if live else None)
            results.append(dict(root=str(pair),plan_sha256=digest(p),
                                verification=range_preview(state),run_outcome=outcome))
            if outcome['pending'] or outcome['stop']:break
        finally:state.close()
    return dict(child_sha256=pin,results=results,unstarted_pair_count=len(child['members'])-len(results),
                publication_eligible=False)


def dry_inputs(manifest, pin):
    from .live_provider import validate_plan
    body=read(Path(manifest),128*1024)
    require(sha(body)==pin,'Dry-run input pin')
    m=decode(body)
    require(set(m)=={'plan','fixtures','oracle_path'} and len(m['fixtures'])==7,'Finite explicit dry-run inputs')
    plan=m['plan'];validate_plan(plan,plan['not_before'])
    oracle_body=read(Path(m['oracle_path']),128*1024)
    require(sha(oracle_body)==ORACLE_SHA256,'Independent original oracle')
    oracle=decode(oracle_body)['view']['state']['series']
    refs={sid+'/'+plan['target_day']:oracle[sid][plan['target_day']]['current'] for sid in plan['roster']}
    bodies=[]
    for f in m['fixtures']:
        b=read(Path(f['path']))
        require(sha(b)==f['sha256'],'Saved response body pin')
        if f['kind']=='observations':
            require(sha(b) in BODY_SHA256 and f['representation']=='AUTHENTIC_R1_SAVED_NATIVE_RESPONSES','Native fixture binding')
        else:
            require(f['representation']=='SYNTHETIC_FROM_RETAINED_PROJECTIONS','Metadata is synthetic, not a native original')
        bodies.append(b)
    return m,bodies,refs


def dry_run(manifest, pin, output):
    """Seven injected exchanges; clock simulation is explicit, never freshness."""
    import io
    from datetime import timedelta
    from .live_provider import Provider,spec
    from .live_metadata import acquire
    from .live_product import publish_readiness
    from .history_acquisition.safety import digest
    from .transport import parse_utc
    m,bodies,refs=dry_inputs(manifest,pin);plan=m['plan'];root=Path(output).absolute()
    repo=Path(__file__).resolve().parents[2]
    require(root==root.resolve() and repo not in (root,*root.parents) and str(root)==plan['output_root'], 'Private explicit output binding')
    state=State(root,dict(plan_sha256=digest(plan),input_sha256=pin,lineage='dendra-independent-stage-b-dry-run-1'),create=True)
    elapsed=[0.0];used=[]
    def now():return parse_utc(plan['not_before'])+timedelta(seconds=elapsed[0])
    def wait(seconds):elapsed[0]+=seconds
    class SavedResponse(io.BytesIO):
        status=200
        def geturl(self):return self.url
    def opener(request,timeout):
        require(len(used)<len(bodies),'Saved exchange exhaustion')
        f=m['fixtures'][len(used)];b=bodies[len(used)]
        expected=spec(plan,f['kind'],f['stream_id'],f.get('cursor'))
        require(request.full_url==expected['url'],'Exact saved request order/scope')
        response=SavedResponse(b);response.url=request.full_url
        response.headers={'Content-Length':str(len(b)),'Content-Type':'application/json'}
        used.append(expected);elapsed[0]+=.01
        return response
    try:
        provider=Provider(state,plan,opener=opener,now=now,monotonic=lambda:elapsed[0],wait=wait)
        admitted=acquire(provider)
        for sid in plan['roster']:
            raw,evidence=provider.observations(sid);key=sid+'/'+plan['target_day']
            evidence['original']=refs[key]['evidence']
            result=sample(raw,admitted[sid]['metadata'],plan['target_day'],provider.clock(),evidence)
            # Precision admission is separate from unchanged numerical science.
            result['admission']=admitted[sid]
            if admitted[sid]['precision_holds'] and result['status']=='AVAILABLE':
                result.update(status='HELD',value_vwc_percent=None,normalized_eligible=False)
            state.outcome(key,provider.clock(),result)
        require(len(used)==7,'All seven declared exchanges required')
        return publish_readiness(state,plan,refs)
    finally:state.close()


def science_v2(manifest, pin, output):
    """Explicit offline opt-in; no State writer, provider or V1 pointer updates."""
    from .live_product import derive_science_v2, verify_science_v2, V2_VERSION
    from .live_state import durable
    body = read(Path(manifest), 128*1024)
    require(sha(body) == pin, 'V2 explicit input pin')
    inputs = decode(body)
    require(set(inputs) == {'capture_root','plan'}, 'V2 exact saved input fields')
    source = Path(inputs['capture_root']).absolute();root = Path(output).absolute()
    repo = Path(__file__).resolve().parents[2]
    require(root == root.resolve() and repo not in (root,*root.parents) and
            'private_custody' not in root.parts and source not in (root,*root.parents) and
            root not in source.parents and not root.exists(), 'Fresh isolated V2 output required')
    built_at = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    product = derive_science_v2(source, inputs['plan'], built_at=built_at)
    root.mkdir(mode=0o700);(root/'products').mkdir(mode=0o700)
    b = encode(product);name = 'products/candidate-'+sha(b)+'.json'
    durable(root/name,b)
    m = dict(version=V2_VERSION, publication_eligible=False, input_sha256=pin,
             product=dict(path=name,sha256=sha(b),bytes=len(b)))
    b = encode(m);durable(root/'MANIFEST.json',b)
    return verify_science_v2(root,sha(b),source,inputs['plan'])


if __name__ == '__main__':
    main()
