"""Synthetic outage and saved-source range tests. No provider requests."""
from copy import deepcopy
from datetime import timedelta
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

from scripts.dendra import live_range as rg
from scripts.dendra.live_provider import Provider,spec,source_fingerprint,sync
from scripts.dendra.live_state import State,read
from scripts.dendra.live_export import range_preview,verify_range
from scripts.dendra.live_capture import capture,Page
from scripts.dendra.live_product import candidate_sample_v2
from scripts.dendra.history_acquisition.safety import Hold,encode,decode,digest,sha
from tests.dendra import test_live_provider as fixture


def plan(root,cells=None,*,clock='2026-11-14T10:00:00Z',run='first',budget=64,days_back=21,start=None,through=None):
    p=fixture.plan(root);p.update(not_before=clock,expires_at=(fixture.parse_utc(clock)+timedelta(seconds=300)).isoformat().replace('+00:00','Z'),
        grant_id='SYNTHETIC-'+run,water_year=2027,ledger_attempt_limit=4096,stream_day_budget=budget,
        representation='SYNTHETIC_INJECTED')
    p['range_targets']=rg.targets(p['roster'],2027,clock,cells or {},days_back=days_back,start=start,through=through)
    p['target_day']=max(x[1] for x in p['range_targets'])
    p['limits'].update(attempts=128,response_bytes=256*1024,aggregate_bytes=8*1024**2,observation_rows=128*2016)
    return p


class Injected:
    def __init__(self,p,*,change=None,empty=(),fail=(),pages=None):
        self.p=p;self.clock=fixture.Clock(p);self.calls=[];self.change=change or {};self.empty=set(empty);self.fail=set(fail)
        bodies=fixture.metadata_bodies(p);self.metadata={};i=0
        for kind,sid in [('unit-vocabulary',None)]+[(k,s) for s in p['roster'] for k in ('station','datastream-list')]:
            value=decode(bodies[i]);i+=1
            for x in (value['data'] if kind=='datastream-list' else [value]) if kind!='unit-vocabulary' else []:
                x.update(is_enabled=True,state='ready')
            self.metadata[spec(p,kind,sid)['url']]=encode(value)
        self.pages=pages or {}
    def open(self,request,timeout):
        url=request.full_url;self.calls.append(url);self.clock.wait(.01)
        if url in self.metadata:body=self.metadata[url]
        else:
            query=parse_qs(urlsplit(url).query);sid=query['datastream_id'][0];day=query['time[$gte]'][0][:10];key=sid+'/'+day
            if key in self.fail:raise OSError('synthetic provider outage')
            if key in self.pages:body=self.pages[key]
            else:
                unit = self.p['roster'][sid]['identity']['native_unit']
                value=self.change.get(key,0.067 if unit == 'VolumetricWaterContent' else 48.77)
                body=encode(dict(data=[] if key in self.empty else [dict(t=day+'T08:00:00Z',v=value)],limit=2016))
        return fixture.Response(body,url)
    def sync(self,state):
        return sync(state,self.p,opener=self.open,now=self.clock.now,monotonic=self.clock.mono,wait=self.clock.wait)


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dendra-range-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'state';self.state=None
        self.addCleanup(lambda:self.state.close() if self.state else None)
    def start(self,p):
        self.state=State(self.root,rg.series_binding(p['roster'],2027,p['ledger_attempt_limit']),create=True)
    def reopen(self):
        binding=self.state.binding;self.state.close();self.state=State(self.root,binding)

    def test_date_boundary_october_leap_and_manual_21(self):
        self.assertEqual(rg.dates(2027,'2026-11-14T08:29:59Z',days_back=1),['2026-11-13'])
        self.assertEqual(rg.dates(2027,'2026-11-14T08:30:00Z',days_back=1),['2026-11-14'])
        self.assertEqual(len(rg.dates(2027,'2026-11-14T09:00:00Z',days_back=21)),21)
        self.assertEqual(rg.dates(2027,'2026-10-01T08:30:00Z',days_back=7),['2026-10-01'])
        self.assertIn('2028-02-29',rg.dates(2028,'2028-03-01T09:00:00Z',days_back=3))
        for kw in [dict(start='2026-09-30',through='2026-10-02'),dict(start='2026-11-14',through='2026-11-13'),
                   dict(start='2026-11-14',through='2026-11-15'),dict(days_back=0),dict(days_back=21,start='2026-11-01')]:
            with self.assertRaises((Hold,ValueError)):rg.dates(2027,'2026-11-14T09:00:00Z',**kw)
        with self.assertRaises(Hold):rg.dates(2027,'2026-10-01T08:29:59Z')

    def test_nightly_7_plus_oldest_gap_and_empty_cooldown(self):
        p=plan(self.root,days_back=None);work=p['range_targets'];self.assertEqual(len(work),16)
        self.assertEqual([x[1] for x in work[:2]],['2026-10-01']*2)
        sid=work[0][0];cells={sid+'/2026-10-01':{'latest_attempt':{'outcome':'COMPLETE_EMPTY','clock':'2026-11-13T10:00:00Z'}}}
        work=rg.targets(p['roster'],2027,p['not_before'],cells)
        self.assertEqual(next(d for s,d in work if s==sid),'2026-10-02')

    def test_multiday_outage_budget_restart_manual_repair_and_idempotence(self):
        p=plan(self.root,budget=5);self.start(p);out=Injected(p).sync(self.state)
        self.assertEqual((len(out['completed']),len(out['pending'])),(5,37))
        first=range_preview(self.state);self.reopen()
        n=plan(self.root,self.state.data['cells'],clock='2026-11-15T10:00:00Z',run='nightly',days_back=None,budget=16)
        out=Injected(n).sync(self.state);self.assertEqual(len(out['completed']),16)
        self.assertTrue(any(k.endswith('/2026-10-01') for k in out['completed']))
        remaining=[s+'/'+d for s,d in p['range_targets'] if not self.state.data['cells'].get(s+'/'+d,{}).get('current')]
        self.assertTrue(remaining)  # Recent overlap is not a repaired 21-day outage.
        n=plan(self.root,clock='2026-11-16T10:00:00Z',run='manual',days_back=None,start='2026-10-25',through='2026-11-14')
        out=Injected(n).sync(self.state);self.assertEqual(out['pending'],[])
        self.assertTrue(all(self.state.data['cells'][s+'/'+d]['current'] for s,d in p['range_targets']))
        revisions=len(self.state.data['revisions']);cells=len(self.state.data['cells'])
        n=plan(self.root,clock='2026-11-17T10:00:00Z',run='same',days_back=None,start='2026-10-25',through='2026-11-14')
        Injected(n).sync(self.state)
        self.assertEqual((len(self.state.data['revisions']),len(self.state.data['cells'])),(revisions,cells))
        self.assertGreater(len(self.state.data['attempts']),64)
        self.assertEqual(verify_range(self.root,first['manifest_path'],first['manifest_sha256'],self.state.binding)['cells'],5)

    def test_revision_empty_failure_inactive_held_and_no_forward_fill(self):
        p=plan(self.root,days_back=1);self.start(p);Injected(p).sync(self.state);key=p['range_targets'][0][0]+'/2026-11-14'
        original=deepcopy(self.state.data['cells'][key]['current']);count=len(self.state.data['revisions'])
        p=plan(self.root,run='revision',clock='2026-11-14T11:00:00Z',days_back=1)
        Injected(p,change={key:0}).sync(self.state)
        self.assertEqual(len(self.state.data['revisions']),count+1);self.assertEqual(self.state.data['cells'][key]['current']['candidate_value_vwc_percent'],0)
        p=plan(self.root,run='empty',clock='2026-11-14T12:00:00Z',days_back=1)
        Injected(p,empty=[key]).sync(self.state);self.assertIsNone(self.state.data['cells'][key]['current'])
        self.assertEqual(self.state.data['cells'][key]['latest_attempt']['outcome'],'COMPLETE_EMPTY')
        for i,status in enumerate(['HELD','INACTIVE','UNQUERIED','INCOMPLETE'],13):
            self.state.range_outcome(key,f'2026-11-14T{i}:00:00Z',status=status)
            self.assertIsNone(self.state.data['cells'][key]['current'])
            self.assertEqual(self.state.data['cells'][key]['last_candidate']['source_timestamp_utc'],original['source_timestamp_utc'])

    def test_interrupted_charges_new_run_only_and_atomic_preview(self):
        p=plan(self.root,days_back=1);self.start(p);Injected(p).sync(self.state);old=range_preview(self.state);pointer=read(self.root/'accepted.json')
        p=plan(self.root,run='crash',clock='2026-11-14T11:00:00Z',days_back=1);inj=Injected(p)
        def crash(*a,**kw):raise KeyboardInterrupt('synthetic cancellation')
        with self.assertRaises(KeyboardInterrupt):sync(self.state,p,opener=crash,now=inj.clock.now,monotonic=inj.clock.mono,wait=inj.clock.wait)
        self.assertEqual(self.state.data['attempts'][-1]['status'],'AMBIGUOUS');ordinal=len(self.state.data['attempts'])-1
        self.reopen()
        with self.assertRaises(Hold):Injected(p).sync(self.state)
        p=plan(self.root,run='explicit-resume',clock='2026-11-14T12:00:00Z',days_back=1);Injected(p).sync(self.state)
        self.assertEqual(self.state.data['attempts'][ordinal]['status'],'AMBIGUOUS')
        def fail():raise OSError('synthetic pointer interruption')
        with self.assertRaises(OSError):range_preview(self.state,before_pointer=fail)
        self.assertEqual(read(self.root/'accepted.json'),pointer)
        self.assertEqual(verify_range(self.root,old['manifest_path'],old['manifest_sha256'],self.state.binding)['cells'],2)

    def test_offline_range_cannot_activate_live_or_escape_targets(self):
        p=plan(self.root,days_back=1);self.start(p);inj=Injected(p)
        with self.assertRaises(Hold):Provider(self.state,p,opener=inj.open,live=True,now=inj.clock.now)
        sid=next(iter(p['roster']))
        with self.assertRaises(Hold):spec(p,'observations',sid,'2026-11-13T07:30:00Z','2026-11-13')

    def test_missing_withheld_failed_and_incomplete_preserve_last_candidate(self):
        p=plan(self.root,days_back=1);self.start(p);Injected(p).sync(self.state)
        sid,day=p['range_targets'][0];key=sid+'/'+day
        original=deepcopy(self.state.data['cells'][key]['current'])
        for hour,(value,q,status) in enumerate([(None,None,'MISSING_VALUE'),(.067,0,'WITHHELD')],11):
            p=plan(self.root,run=status,clock=f'2026-11-14T{hour}:00:00Z',days_back=1)
            row=dict(t=day+'T08:00:00Z',v=value)
            if q is not None:row['q']=q
            Injected(p,pages={key:encode(dict(data=[row],limit=2016))}).sync(self.state)
            c=self.state.data['cells'][key]
            self.assertEqual(c['latest_attempt']['outcome'],status);self.assertIsNone(c['current'])
            self.assertEqual(c['last_candidate'],original)
        p=plan(self.root,run='failed',clock='2026-11-14T13:00:00Z',days_back=1)
        out=Injected(p,fail=[key]).sync(self.state)
        self.assertEqual(self.state.data['cells'][key]['latest_attempt']['outcome'],'FAILED')
        self.assertIn(key,out['pending']);self.reopen()
        p=plan(self.root,run='incomplete',clock='2026-11-14T14:00:00Z',days_back=1)
        # A full page has no advancing cursor: complete HTTP is not complete query.
        out=Injected(p,pages={key:encode(dict(data=[dict(t=day+'T08:00:00Z',v=.067)]*2016,limit=2016))}).sync(self.state)
        self.assertEqual(self.state.data['cells'][key]['latest_attempt']['outcome'],'INCOMPLETE')
        self.assertIn(key,out['pending']);self.assertIsNone(self.state.data['cells'][key]['current'])
        self.assertEqual(self.state.data['cells'][key]['last_candidate'],original)
        verified=range_preview(self.state);self.assertEqual(verified['cells'],2)

    def test_injected_multiday_pagination_uses_existing_capture(self):
        p=plan(self.root,days_back=1);self.start(p);sid=next(iter(p['roster']))
        rows=[dict(t=t,v=.1) for t in ['2026-10-14T08:00:00Z','2026-10-15T08:00:00Z','2026-10-15T08:10:00Z']]
        pages=[Page(encode(dict(data=a,limit=2))) for a in [rows[:2],rows[1:],[rows[2]]]]
        win=dict(start='2026-10-14T07:30:00Z',end='2026-10-15T08:30:00Z')
        raw,ev=capture(self.state,'synthetic-multiday',sid,win,pages,'2026-11-14T10:00:00Z',body_cap=4096)
        self.assertEqual(len(ev['pages']),3);self.assertEqual({r['t'][:10] for r in raw},{'2026-10-14','2026-10-15'})


if __name__=='__main__':unittest.main()
