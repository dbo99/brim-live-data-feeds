"""No implicit mode, network fallback, guessed fixtures or repository output."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy
from contextlib import closing

from scripts.dendra import live_cli
from scripts.dendra.history_acquisition.safety import encode, sha, Hold, digest, decode
from scripts.dendra.live_state import State, SEASON, season_totals
from scripts.dendra.live_range import series_binding
from scripts.dendra.live_export import verify_range
from scripts.dendra.live_provider import request_count
from tests.dendra.test_live_range import plan as range_plan, Injected


def synthetic_live_plan(root, **kw):
    """Test-only plan, never an external grant or genuine provider evidence."""
    p=range_plan(root,**kw)
    p.update(status='APPROVED',representation='LIVE_PROVIDER',persistence_version=2,
             ledger_attempt_limit=SEASON['attempts'])
    return p


def synthetic_run(p, injected, *, authorize=None, **selection):
    pin=digest(p)
    # Network denial belongs to the enclosing qualification harness as well.
    verifier=authorize if authorize is not None else lambda h,at:h==pin and at<p['expires_at']
    return live_cli.supervised_range(p,p['output_root'],authorize=verifier,opener=injected.open,
        now=injected.clock.now,monotonic=injected.clock.mono,wait=injected.clock.wait,**selection)


class SupervisedRangeTests(unittest.TestCase):
    """Live control flow only: synthetic verifier, clock and responses; zero GETs."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dendra-c1-synthetic-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'state'
        net=patch('socket.socket.connect',side_effect=AssertionError('Network denied'))
        net.start();self.addCleanup(net.stop)

    def state(self,p):
        return State(self.root,series_binding(p['roster'],2027,p['ledger_attempt_limit'],version=2))

    def test_entry_append_counts_clocks_science_and_spent_grant(self):
        p=synthetic_live_plan(self.root,days_back=1);inj=Injected(p);calls=[];pin=digest(p)
        out=synthetic_run(p,inj,authorize=lambda h,at:calls.append((h,at)) is None and h==pin,days_back=1)
        self.assertEqual((out['cells'],out['provider_requests'],len(inj.calls)),(2,7,7))
        self.assertEqual(out['run_outcome']['provider_requests'],7)
        self.assertGreater(len(calls),7);self.assertTrue(all(h==pin for h,at in calls))
        s=self.state(p)
        try:
            for cell in s.data['cells'].values():
                self.assertEqual(cell['latest_attempt']['clock_kind'],'LIVE_PROVIDER')
                self.assertEqual(cell['current']['evidence']['representation'],'LIVE_PROVIDER')
                self.assertNotEqual(cell['current']['source_timestamp_utc'],cell['latest_attempt']['clock'])
            before=deepcopy(s.data['cells'])
            self.assertEqual(verify_range(self.root,out['manifest_path'],out['manifest_sha256'],s.binding)['cells'],2)
        finally:s.close()
        retry=Injected(p)
        with self.assertRaisesRegex(Hold,'Spent'):synthetic_run(p,retry,days_back=1)
        self.assertEqual(retry.calls,[])
        n=synthetic_live_plan(self.root,run='append',clock='2026-11-15T10:00:00Z',days_back=1)
        out=synthetic_run(n,Injected(n),days_back=1)
        self.assertEqual((out['cells'],out['provider_requests']),(4,14))
        s=self.state(n)
        try:self.assertTrue(all(s.data['cells'][k]==v for k,v in before.items()))
        finally:s.close()

    def test_no_independent_authority_no_output_or_dispatch(self):
        p=synthetic_live_plan(self.root,days_back=1);inj=Injected(p)
        for auth in (None,False,lambda h,at:False,lambda h,at:h=='0'*64):
            with self.subTest(auth=auth),self.assertRaises(Hold):
                live_cli.supervised_range(p,self.root,authorize=auth,opener=inj.open,now=inj.clock.now,days_back=1)
            self.assertFalse(self.root.exists());self.assertEqual(inj.calls,[])

    def test_plan_source_identity_root_expiry_and_mode_mismatch(self):
        p=synthetic_live_plan(self.root,days_back=1)
        for field,value in [('source_sha256','0'*64),('output_root',str(self.root)+'-other'),
                            ('expires_at',p['not_before']),('status','OFFLINE_ONLY'),
                            ('representation','SYNTHETIC_INJECTED'),('persistence_version',1)]:
            bad=deepcopy(p);bad[field]=value;inj=Injected(bad)
            with self.subTest(field=field),self.assertRaises(Hold):
                live_cli.supervised_range(bad,self.root,authorize=lambda h,at:True,opener=inj.open,now=inj.clock.now,days_back=1)
            self.assertFalse(self.root.exists());self.assertEqual(inj.calls,[])
        bad=deepcopy(p);sid=next(iter(p['roster']));bad['roster'][sid]['identity']['station_id']='wrong'
        inj=Injected(p)
        with self.assertRaises(Hold):synthetic_run(bad,inj,days_back=1)
        self.assertFalse(self.root.exists());self.assertEqual(inj.calls,[])

    def test_target_selection_mismatch_never_dispatches(self):
        p=synthetic_live_plan(self.root,days_back=2);inj=Injected(p)
        with self.assertRaisesRegex(Hold,'selection'):synthetic_run(p,inj,days_back=1)
        self.assertEqual(inj.calls,[])

    def test_revocation_stops_between_requests(self):
        p=synthetic_live_plan(self.root,days_back=1);inj=Injected(p)
        with self.assertRaises(Hold):synthetic_run(p,inj,authorize=lambda h,at:not inj.calls,days_back=1)
        self.assertEqual(len(inj.calls),1)
        s=self.state(p)
        try:
            self.assertEqual(s.data['attempts'][0]['status'],'AMBIGUOUS')
            self.assertIsNone(request_count(s.data['attempts']))
        finally:s.close()

    def test_ambiguous_charge_blocks_new_grant_and_preserves_last_good(self):
        p=synthetic_live_plan(self.root,days_back=1);old=synthetic_run(p,Injected(p),days_back=1)
        s=self.state(p)
        try:prior=deepcopy(s.data['cells']);binding=s.binding
        finally:s.close()
        n=synthetic_live_plan(self.root,run='interrupted',clock='2026-11-14T11:00:00Z',days_back=1)
        key='/'.join(n['range_targets'][0]);inj=Injected(n,fail=[key])
        result=synthetic_run(n,inj,days_back=1)
        self.assertIsNone(result['provider_requests']);self.assertIsNotNone(result['run_outcome']['stop'])
        s=self.state(n)
        try:
            a=s.data['attempts'][-1];self.assertEqual((a['status'],a['bytes']),('AMBIGUOUS',0))
            self.assertEqual(season_totals(s.data)['charged_bytes'],
                             sum(x['bytes'] for x in s.data['attempts'][:-1])+a['cap'])
            self.assertEqual(s.data['cells'][key]['last_candidate'],prior[key]['current'])
            self.assertIsNone(s.data['cells'][key]['current'])
            self.assertEqual(verify_range(self.root,old['manifest_path'],old['manifest_sha256'],binding)['cells'],2)
        finally:s.close()
        n=synthetic_live_plan(self.root,run='no-retry',clock='2026-11-14T12:00:00Z',days_back=1);inj=Injected(n)
        with self.assertRaisesRegex(Hold,'Unresolved live charge'):synthetic_run(n,inj,days_back=1)
        self.assertEqual(inj.calls,[])

    def test_live_revision_zero_empty_and_manual_idempotence(self):
        p=synthetic_live_plan(self.root,days_back=1);synthetic_run(p,Injected(p),days_back=1)
        key='/'.join(p['range_targets'][0])
        n=synthetic_live_plan(self.root,run='zero',clock='2026-11-14T11:00:00Z',days_back=1)
        synthetic_run(n,Injected(n,change={key:0}),days_back=1)
        s=self.state(n)
        try:
            self.assertEqual(s.data['cells'][key]['current']['candidate_value_vwc_percent'],0)
            revisions=len(s.data['revisions'])
        finally:s.close()
        n=synthetic_live_plan(self.root,run='same',clock='2026-11-14T12:00:00Z',days_back=None,start='2026-11-14',through='2026-11-14')
        synthetic_run(n,Injected(n,change={key:0}),start='2026-11-14',through='2026-11-14')
        s=self.state(n)
        try:self.assertEqual(len(s.data['revisions']),revisions)
        finally:s.close()
        n=synthetic_live_plan(self.root,run='empty',clock='2026-11-14T13:00:00Z',days_back=1)
        synthetic_run(n,Injected(n,empty=[key]),days_back=1)
        s=self.state(n)
        try:
            self.assertIsNone(s.data['cells'][key]['current'])
            self.assertEqual(s.data['cells'][key]['latest_attempt']['outcome'],'COMPLETE_EMPTY')
            self.assertEqual(s.data['cells'][key]['last_candidate']['candidate_value_vwc_percent'],0)
        finally:s.close()

    def test_routine_seven_plus_one_uses_same_entry(self):
        p=synthetic_live_plan(self.root,days_back=None);inj=Injected(p);out=synthetic_run(p,inj)
        self.assertEqual((out['cells'],len(inj.calls)),(16,21))
        self.assertEqual(out['run_outcome']['pending'],[])

    def test_fixture_lineage_cannot_promote_to_live(self):
        from scripts.dendra.live_provider import sync
        p=synthetic_live_plan(self.root,days_back=1);p.update(status='OFFLINE_ONLY',representation='SYNTHETIC_INJECTED')
        s=State(self.root,series_binding(p['roster'],2027,p['ledger_attempt_limit'],version=2),create=True);inj=Injected(p)
        try:sync(s,p,opener=inj.open,now=inj.clock.now,monotonic=inj.clock.mono,wait=inj.clock.wait)
        finally:s.close()
        n=synthetic_live_plan(self.root,run='not-promotion',days_back=1);inj=Injected(n)
        with self.assertRaisesRegex(Hold,'fixture/live'):synthetic_run(n,inj,days_back=1)
        self.assertEqual(inj.calls,[])


class CLITests(unittest.TestCase):
    def test_v2_opt_in_dispatch_only_and_live_combination_refused(self):
        with patch.object(live_cli,'science_v2',return_value={}) as candidate, patch.object(live_cli,'run') as old:
            live_cli.main(['--offline-science-v2','--manifest','x','--manifest-sha256','p','--output','o'])
            candidate.assert_called_once_with('x','p','o');old.assert_not_called()
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            live_cli.main(['--live','--offline-science-v2','--manifest','x','--manifest-sha256','p','--output','o'])

    def test_live_entry_stays_sealed_without_external_authority(self):
        with patch('socket.socket.connect',side_effect=AssertionError('No network')):
            with self.assertRaises(Hold):
                live_cli.main(['--live','--manifest','unapproved','--manifest-sha256','0'*64,'--output','unapproved'])

    def test_explicit_offline_mode_and_no_live_flag(self):
        for args in ([], ['--live'], ['--manifest','x','--manifest-sha256','x','--output','x']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                live_cli.main(args)

    def test_bad_manifest_pin_refused_before_output(self):
        with tempfile.TemporaryDirectory(prefix='dendra-live-synthetic-') as td:
            root=Path(td).resolve(); manifest=root/'input.json'; manifest.write_bytes(encode(dict(pages=[])))
            with patch('socket.socket.connect',side_effect=AssertionError('No network')):
                with self.assertRaises(Hold): live_cli.run(manifest,'0'*64,root/'output')
            self.assertFalse((root/'output').exists())

    def test_guessed_four_pages_cannot_replace_original_oracle(self):
        with tempfile.TemporaryDirectory(prefix='dendra-live-synthetic-') as td:
            root=Path(td).resolve(); oracle=root/'oracle'; oracle.write_bytes(b'{}')
            manifest=root/'input.json'; body=encode(dict(oracle_path=str(oracle),pages=[{}]*4)); manifest.write_bytes(body)
            with self.assertRaises(Hold): live_cli.run(manifest,sha(body),root/'output')
            self.assertFalse((root/'output').exists())


def family_fixture(root, pairs=2, *, live=False, target_dates=None, clock='2026-11-14T10:00:00Z'):
    """Fabricated identities and bodies; no private roster or grant material."""
    from scripts.dendra.live_metadata import FAMILY_PAIR
    from scripts.dendra.live_provider import source_fingerprint
    days=['2026-11-13','2026-11-14'] if target_dates is None else target_dates
    template=range_plan(root,days_back=len(days),clock=clock);members=[]
    positions=2*len(days);attempts=5+positions*template['limits']['pages_per_stream']
    rows=positions*template['limits']['pages_per_stream']*2016
    for pair in range(pairs):
        p=deepcopy(template);roster={}
        for i,r in enumerate(template['roster'].values()):
            r=deepcopy(r);sid=f'{pair*2+i+1:024x}'
            r['identity'].update(stream_id=sid,station_id=f'{pair*2+i+100:024x}')
            r['family']=dict(name='Synthetic family',slug='synthetic',organization_id='f'*24)
            r['selection']=dict(policy=FAMILY_PAIR,station_sha256='a'*64,stream_sha256='b'*64,
                crosswalk_sha256='c'*64,configuration_sha256=digest([dict(begins_at='2025-01-01T00:00:00.000Z')]))
            roster[sid]=r
        p.update(roster=roster,output_root=str(root/f'pair-{pair:02}'),grant_id=f'SYNTHETIC-FAMILY-{pair}',
            persistence_version=2,ledger_attempt_limit=SEASON['attempts'],stream_day_budget=positions,target_day=max(days),
            range_targets=[[s,d] for d in days for s in sorted(roster)])
        p['limits'].update(attempts=attempts,observation_rows=rows,response_bytes=1024**2,aggregate_bytes=2*1024**2)
        if live:p.update(status='APPROVED',representation='LIVE_PROVIDER')
        members.append(p)
    child=dict(version='dendra-family-child-1',output_root=str(root),source_sha256=source_fingerprint(),
        not_before=template['not_before'],expires_at=template['expires_at'],members=members,
        reservation=dict(gets=pairs*attempts,body_bytes=pairs*2*1024**2,observation_rows=pairs*rows))
    return dict(version='dendra-family-campaign-1',output_root=str(root),source_sha256=source_fingerprint(),
        target_dates=days,children=[child],
        reservation=dict(gets=pairs*attempts,body_bytes=pairs*2*1024**2,streams=pairs*2,cells=pairs*positions))


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dendra-family-synthetic-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'family'
        net=patch('socket.socket.connect',side_effect=AssertionError('Network denied'));net.start();self.addCleanup(net.stop)

    def injected(self,c):
        fixtures=[Injected(p,change={s+'/'+d:(.1 if p['roster'][s]['identity']['native_unit']==
                    'VolumetricWaterContent' else 10) for s,d in p['range_targets']})
                  for p in c['children'][0]['members']];clock=fixtures[0].clock
        for fixture in fixtures:fixture.clock=clock
        def opener(req,timeout):
            for f in fixtures:
                if req.full_url in f.metadata:return f.open(req,timeout)
            from urllib.parse import parse_qs,urlsplit
            sid=parse_qs(urlsplit(req.full_url).query)['datastream_id'][0]
            return next(f for f in fixtures if sid in f.p['roster']).open(req,timeout)
        return clock,opener,fixtures

    def execute(self,c,**kw):
        clock,opener,fixtures=self.injected(c)
        result=live_cli.family_child(c,0,opener=kw.pop('opener',opener),now=clock.now,
                                    monotonic=clock.mono,wait=clock.wait,**kw)
        return result,fixtures

    def test_one_and_two_day_reservations_and_child_readback(self):
        from scripts.dendra.live_range import validate_family_campaign
        self.root.mkdir(mode=0o700)
        for days in (['2026-11-14'],['2026-11-13','2026-11-14']):
            for pages in (1,3):
                with self.subTest(days=days,pages=pages):
                    c=family_fixture(self.root/f'{len(days)}-{pages}',target_dates=days)
                    child=c['children'][0];positions=2*len(days);gets=5+positions*pages;rows=positions*pages*2016
                    for p in child['members']:p['limits'].update(pages_per_stream=pages,attempts=gets,observation_rows=rows)
                    child['reservation'].update(gets=2*gets,observation_rows=2*rows);c['reservation']['gets']=2*gets
                    self.assertEqual(validate_family_campaign(c)['cells'],2*positions)
                    result,fixtures=self.execute(c)
                    self.assertEqual(result['unstarted_pair_count'],0)
                    self.assertEqual(sum(len(f.calls) for f in fixtures),2*(5+positions))
                    for p,r in zip(child['members'],result['results']):
                        with closing(State(Path(p['output_root']),series_binding(p['roster'],2027,20000,version=2))) as state:
                            self.assertEqual(len(state.data['attempts']),5+positions)
                            self.assertEqual(len(state.data['cells']),positions)
                            self.assertEqual(verify_range(state.root,r['verification']['manifest_path'],
                                r['verification']['manifest_sha256'],state.binding)['cells'],positions)

    def test_one_day_append_preserves_old_cells_charges_and_fresh_readback(self):
        import subprocess
        import sys
        from urllib.parse import parse_qs,urlsplit
        c=family_fixture(self.root,pairs=1,live=True);pin=digest(c['children'][0])
        self.execute(c,live=True,authorize=lambda h,at:h==pin)
        p=c['children'][0]['members'][0];binding=series_binding(p['roster'],2027,20000,version=2)
        with closing(State(Path(p['output_root']),binding)) as state:
            old_cells=deepcopy(state.data['cells']);old_attempts=deepcopy(state.data['attempts']);old_totals=season_totals(state.data)
        self.assertEqual((len(old_cells),len(old_attempts)),(4,9))
        n=family_fixture(self.root,pairs=1,live=True,target_dates=['2026-11-15'],clock='2026-11-16T10:00:00Z')
        p=n['children'][0]['members'][0];p['grant_id']+='-APPEND';pin=digest(n['children'][0])
        clock,opener,fixtures=self.injected(n);key='/'.join(p['range_targets'][0]);fixtures[0].change[key]=0
        result=live_cli.family_child(n,0,live=True,authorize=lambda h,at:h==pin,opener=opener,
            now=clock.now,monotonic=clock.mono,wait=clock.wait)
        calls=fixtures[0].calls;self.assertEqual(len(calls),7)
        queried=[parse_qs(urlsplit(url).query)['time[$gte]'][0][:10] for url in calls if '/datapoints?' in url]
        self.assertEqual(queried,['2026-11-15']*2)
        with closing(State(Path(p['output_root']),binding)) as state:
            self.assertEqual(len(state.data['cells']),6)
            self.assertEqual({k:state.data['cells'][k] for k in old_cells},old_cells)
            self.assertEqual(state.data['attempts'][:9],old_attempts)
            added=state.data['attempts'][9:];self.assertEqual(len(added),7)
            self.assertEqual(season_totals(state.data)['charged_bytes'],old_totals['charged_bytes']+sum(a['bytes'] for a in added))
            self.assertEqual(season_totals(state.data)['charged_rows'],old_totals['charged_rows']+sum(a['rows'] for a in added))
            self.assertEqual(state.data['cells'][key]['current']['candidate_value_vwc_percent'],0)
            self.assertEqual(state.data['cells'][key]['current']['source_timestamp_utc'],'2026-11-15T08:00:00.000Z')
            expected=dict(cells=state.data['cells'],attempts=state.data['attempts'],totals=season_totals(state.data))
            verification=result['results'][0]['verification']
            self.assertEqual(verify_range(state.root,verification['manifest_path'],verification['manifest_sha256'],binding)['cells'],6)
        code='''from contextlib import closing
from pathlib import Path
import sys
from scripts.dendra.history_acquisition.safety import decode,encode
from scripts.dendra.live_state import State,season_totals
with closing(State(Path(sys.argv[1]),decode(sys.argv[2].encode()))) as state:
    sys.stdout.buffer.write(encode(dict(cells=state.data['cells'],attempts=state.data['attempts'],totals=season_totals(state.data))))
'''
        fresh=subprocess.run([sys.executable,'-B','-c',code,p['output_root'],encode(binding).decode()],
            cwd=Path(__file__).resolve().parents[2],capture_output=True,check=True,timeout=30)
        self.assertEqual(decode(fresh.stdout),expected)

    def test_one_day_missing_empty_and_failed_are_not_values(self):
        self.root.mkdir(mode=0o700)
        for outcome in ('MISSING_VALUE','COMPLETE_EMPTY','FAILED'):
            with self.subTest(outcome=outcome):
                c=family_fixture(self.root/outcome,pairs=1,target_dates=['2026-11-14'])
                p=c['children'][0]['members'][0];key='/'.join(p['range_targets'][0])
                clock,opener,fixtures=self.injected(c);f=fixtures[0]
                if outcome=='MISSING_VALUE':f.change[key]=None
                elif outcome=='COMPLETE_EMPTY':f.empty.add(key)
                else:f.fail.add(key)
                live_cli.family_child(c,0,opener=opener,now=clock.now,monotonic=clock.mono,wait=clock.wait)
                with closing(State(Path(p['output_root']),series_binding(p['roster'],2027,20000,version=2))) as state:
                    cell=state.data['cells'][key];self.assertIsNone(cell['current'])
                    self.assertEqual(cell['latest_attempt']['outcome'],outcome)
                    if outcome=='FAILED':
                        a=state.data['attempts'][-1];self.assertEqual(a['status'],'AMBIGUOUS')
                        self.assertEqual(season_totals(state.data)['charged_bytes'],sum(x['bytes'] for x in state.data['attempts'][:-1])+a['cap'])

    def test_malformed_dates_and_reservations_refuse_before_http(self):
        for n in (1,2):
            original=family_fixture(self.root,target_dates=['2026-11-13','2026-11-14'][-n:])
            cases=[]
            for days in ([],['2026-11-14']*2,['2026-11-14','2026-11-13'],
                         ['2026-11-12','2026-11-13','2026-11-14'],['bad-date'],[None]):
                bad=deepcopy(original);bad['target_dates']=days;cases.append(bad)
            for field in ('stream_day_budget','metadata','attempts','observation_rows','child_gets',
                          'child_body_bytes','child_observation_rows','campaign_gets','campaign_body_bytes','campaign_streams','campaign_cells'):
                for delta in (-1,1):
                    bad=deepcopy(original);child=bad['children'][0];p=child['members'][0]
                    if field.startswith('child_'):child['reservation'][field[6:]]+=delta
                    elif field.startswith('campaign_'):bad['reservation'][field[9:]]+=delta
                    elif field=='stream_day_budget':p[field]+=delta
                    else:p['limits'][field]+=delta
                    cases.append(bad)
            for change in ('root','identity','grant','targets'):
                bad=deepcopy(original);a,b=bad['children'][0]['members']
                if change=='root':b['output_root']=a['output_root']
                elif change=='identity':b['roster']=deepcopy(a['roster']);b['range_targets']=deepcopy(a['range_targets'])
                elif change=='grant':b['grant_id']=a['grant_id']
                else:b['range_targets'][0]=b['range_targets'][1]
                cases.append(bad)
            clock,opener,fixtures=self.injected(original)
            for i,bad in enumerate(cases):
                with self.subTest(days=n,case=i),self.assertRaises(Hold):
                    live_cli.family_child(bad,0,opener=opener,now=clock.now,monotonic=clock.mono,wait=clock.wait)
                self.assertFalse(self.root.exists());self.assertTrue(all(not f.calls for f in fixtures))

    def test_one_day_authority_expiry_and_old_runtime_refuse_before_http(self):
        from scripts.dendra.live_provider import source_fingerprint
        old='ac46d3eb7fb5ada8b2f4ed411759f030489d67f60c78f1f9f4cbdbe827050fc7'
        self.assertNotEqual(source_fingerprint(),old)
        for reason in ('unauthorized','expired','old-runtime'):
            c=family_fixture(self.root,live=True,target_dates=['2026-11-14']);clock,opener,fixtures=self.injected(c)
            if reason=='expired':clock.wait(300)
            elif reason=='old-runtime':
                c['source_sha256']=old;c['children'][0]['source_sha256']=old
                for p in c['children'][0]['members']:p['source_sha256']=old
            with self.subTest(reason=reason),self.assertRaises(Hold):
                live_cli.family_child(c,0,live=True,authorize=lambda h,at:reason!='unauthorized',opener=opener,
                    now=clock.now,monotonic=clock.mono,wait=clock.wait)
            self.assertFalse(self.root.exists());self.assertTrue(all(not f.calls for f in fixtures))


    def test_pairs_keep_independent_p1_roots_readback_and_spent_runs(self):
        c=family_fixture(self.root);result,fixtures=self.execute(c)
        self.assertEqual(len(result['results']),2);self.assertEqual(result['unstarted_pair_count'],0)
        starts=[]
        for p,r in zip(c['children'][0]['members'],result['results']):
            with closing(State(Path(p['output_root']),series_binding(p['roster'],2027,20000,version=2))) as state:
                self.assertEqual(state.binding['season_limits'],SEASON);self.assertEqual(len(state.data['cells']),4)
                self.assertEqual(len(state.data['attempts']),9)
                self.assertTrue(all(c['latest_attempt']['outcome']=='AVAILABLE' and
                    c['current']['candidate_value_vwc_percent']==10 for c in state.data['cells'].values()))
                starts.extend(a['attempted_at'] for a in state.data['attempts'])
                self.assertEqual(verify_range(state.root,r['verification']['manifest_path'],
                    r['verification']['manifest_sha256'],state.binding)['cells'],4)
        from scripts.dendra.transport import parse_utc
        self.assertTrue(all((parse_utc(b)-parse_utc(a)).total_seconds()>=1 for a,b in zip(starts,starts[1:])))
        with self.assertRaisesRegex(Hold,'Spent range run'):self.execute(c)

    def test_live_child_requires_independent_child_digest_and_revocation(self):
        c=family_fixture(self.root,live=True)
        with self.assertRaisesRegex(Hold,'Independent exact child grant'):self.execute(c,live=True)
        self.assertFalse(self.root.exists())
        pin=digest(c['children'][0]);clock,opener,fixtures=self.injected(c);calls=[]
        def revoke(req,timeout):calls.append(req.full_url);return opener(req,timeout)
        with self.assertRaises(Hold):live_cli.family_child(c,0,live=True,opener=revoke,
            authorize=lambda h,at:h==pin and len(calls)<1,now=clock.now,monotonic=clock.mono,wait=clock.wait)
        self.assertEqual(len(calls),1)

    def test_plan_bounds_duplicate_roots_members_and_no_writes(self):
        from scripts.dendra.live_range import validate_family_campaign
        original=family_fixture(self.root);validate_family_campaign(original)
        for change in ('budget','root','identity','date','clock','source'):
            c=deepcopy(original);child=c['children'][0];p=child['members'][1]
            if change=='budget':child['reservation']['body_bytes']-=1
            elif change=='root':p['output_root']=child['members'][0]['output_root']
            elif change=='identity':p['roster']=deepcopy(child['members'][0]['roster'])
            elif change=='date':c['target_dates'][1]='2026-11-15'
            elif change=='clock':child['expires_at']='2026-11-14T11:00:00Z'
            else:c['source_sha256']='0'*64
            with self.subTest(change=change),self.assertRaises(Hold):validate_family_campaign(c)
        self.assertFalse(self.root.exists())

    def test_mid_pair_interruption_retains_unknown_full_charge(self):
        c=family_fixture(self.root);clock,opener,fixtures=self.injected(c);calls=[]
        def interrupted(req,timeout):
            calls.append(req.full_url)
            if len(calls)==6:raise KeyboardInterrupt('Synthetic interruption')
            return opener(req,timeout)
        with self.assertRaises(KeyboardInterrupt):live_cli.family_child(c,0,opener=interrupted,
            now=clock.now,monotonic=clock.mono,wait=clock.wait)
        p=c['children'][0]['members'][0]
        with closing(State(Path(p['output_root']),series_binding(p['roster'],2027,20000,version=2))) as state:
            a=state.data['attempts'][-1];self.assertEqual(a['status'],'AMBIGUOUS')
            self.assertEqual(a['cap'],1024**2)
            self.assertGreaterEqual(season_totals(state.data)['charged_bytes'],1024**2)
        self.assertFalse(Path(c['children'][0]['members'][1]['output_root']).exists())

    def test_incomplete_pair_stops_child_and_retains_remaining_work(self):
        c=family_fixture(self.root);clock,opener,fixtures=self.injected(c)
        def failed(req,timeout):
            if '/datapoints?' in req.full_url:raise OSError('Synthetic outage')
            return opener(req,timeout)
        result=live_cli.family_child(c,0,opener=failed,now=clock.now,monotonic=clock.mono,wait=clock.wait)
        self.assertEqual(result['unstarted_pair_count'],1)
        self.assertEqual(len(result['results'][0]['run_outcome']['pending']),4)
        self.assertFalse(Path(c['children'][0]['members'][1]['output_root']).exists())

    def test_live_control_positive_and_absolute_child_expiry(self):
        c=family_fixture(self.root,live=True);pin=digest(c['children'][0])
        result,_=self.execute(c,live=True,authorize=lambda h,at:h==pin)
        self.assertEqual(len(result['results']),2)
        self.assertFalse(result['publication_eligible'])
        other=family_fixture(self.root/'expired');clock,opener,_=self.injected(other);clock.wait(300)
        with self.assertRaisesRegex(Hold,'outside window'):live_cli.family_child(other,0,opener=opener,
            now=clock.now,monotonic=clock.mono,wait=clock.wait)
        self.assertFalse((self.root/'expired').exists())

    def test_fresh_privacy_method_unit_identity_and_configuration_refuse_observations(self):
        for field in ('is_hidden','method','unit','identity','configuration'):
            with self.subTest(field=field):
                c=family_fixture(self.root/field,pairs=1);clock,opener,fixtures=self.injected(c)
                p=c['children'][0]['members'][0];sid=next(iter(p['roster']))
                from scripts.dendra.live_provider import spec
                url=spec(p,'datastream-list',sid)['url'];value=decode(fixtures[0].metadata[url]);row=value['data'][0]
                if field=='is_hidden':row['is_hidden']=True
                elif field=='method':row['terms']['ds']['Aggregate']='Maximum'
                elif field=='unit':row['terms']['dt']['Unit']='Dimensionless'
                elif field=='identity':row['station_id']='e'*24
                else:row['datapoints_config'][0]['begins_at']='2025-02-01T00:00:00Z'
                fixtures[0].metadata[url]=encode(value);self.root.mkdir(mode=0o700,exist_ok=True)
                result=live_cli.family_child(c,0,opener=opener,now=clock.now,monotonic=clock.mono,wait=clock.wait)
                self.assertEqual(result['results'][0]['run_outcome']['completed'],[])
                self.assertFalse(any('/datapoints?' in url for url in fixtures[0].calls))


if __name__ == '__main__':
    unittest.main()
