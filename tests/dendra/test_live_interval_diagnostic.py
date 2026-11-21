"""Synthetic, socket-denied interval checks. No private identities or authority."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.dendra.history_acquisition.safety import Hold,decode,digest,encode
from scripts.dendra.live_metadata import acquire,admit_v2
from scripts.dendra.live_provider import Provider,spec,validate_plan,source_fingerprint
from scripts.dendra.live_range import diagnostic_window,diagnostic_projection,diagnostic_native_rows
from scripts.dendra.live_state import State
from scripts.dendra.transport import FetchError,parse_utc,format_utc
from tests.dendra import test_live_provider as fixture


def plan(root):
    p = fixture.plan(root)
    p.update(not_before='2026-11-18T10:00:00.000Z',expires_at='2026-11-18T10:05:00.000Z',
             target_day='2026-11-09',water_year=2027,admission=False,representation='SYNTHETIC_INJECTED',
             diagnostic_interval=dict(version='dendra-single-stream-interval-diagnostic-1',
                 start_day='2026-10-13',through_day='2026-11-09',comparison_days=['2026-11-08','2026-11-09']))
    # Deliberately different dates and synthetic identities: no run becomes a default.
    sid = list(p['roster'])[1]
    p['roster'] = {sid:p['roster'][sid]}
    p['limits'].update(attempts=6,metadata=3,response_bytes=1024**2,
                       aggregate_bytes=2*1024**2,observation_rows=6048)
    return p


def metadata(p):
    bodies = fixture.metadata_bodies(p)
    for i in (1,2):
        obj = decode(bodies[i])
        row = obj if i == 1 else obj['data'][0]
        row.update(is_enabled=True,state='ready')
        bodies[i] = encode(obj)
    return bodies


class IntervalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dendra-diagnostic-synthetic-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()/'receipts'
        self.p = plan(self.root);self.sid = next(iter(self.p['roster']))
        self.clock = fixture.Clock(self.p);self.calls = [];self.state = None
        self.bodies = metadata(self.p)
        self.addCleanup(lambda:self.state.close() if self.state else None)
        net = patch('socket.socket.connect',side_effect=AssertionError('Network denied'))
        net.start();self.addCleanup(net.stop)

    def start(self, **kwargs):
        self.state = State(self.root,dict(plan_sha256=digest(self.p),
                           lineage='dendra-isolated-interval-diagnostic-1'),create=True)
        def opener(req,timeout):
            self.calls.append(req.full_url);self.clock.wait(.01)
            return fixture.Response(self.bodies[len(self.calls)-1],req.full_url)
        return Provider(self.state,self.p,opener=opener,now=self.clock.now,
                        monotonic=self.clock.mono,wait=self.clock.wait,**kwargs)

    def rows(self, count=3894, step=600):
        start = parse_utc(diagnostic_window(self.p,self.p['not_before'])['start'])
        return [dict(t=format_utc(start+timedelta(seconds=i*step)),v=10,lt='native-clock') for i in range(count)]

    def run_pages(self, pages):
        self.bodies += [encode(dict(data=rows,limit=limit)) for rows,limit in pages]
        provider = self.start();acquire(provider)
        raw,evidence = provider.observations(self.sid)
        admitted = admit_v2(self.state,self.p,provider.clock())[self.sid]
        projection = diagnostic_projection(raw,admitted,self.p,evidence)
        self.assertEqual(self.state.data['cells'],{})
        self.assertEqual(self.state.data['revisions'],[])
        self.assertFalse((self.root/'accepted.json').exists())
        self.assertFalse(projection['operational_coverage_changed'])
        return raw,evidence,projection

    def test_28_days_two_pages_preserve_windows_and_charge_overlap(self):
        rows = self.rows()
        raw,ev,result = self.run_pages([(rows[:2016],2016),(rows[2015:],2016)])
        self.assertEqual(raw,rows)
        self.assertEqual((len(self.calls),ev['consumed_rows'],ev['interval_native_rows']),(5,3895,3894))
        self.assertEqual(len(result['daily_diagnostics']),28)
        self.assertEqual(sum(len(d['native_records']) for d in result['daily_diagnostics'].values()),168)
        self.assertTrue(all(d['candidate']['candidate_value_vwc_percent']==10 and
                            d['candidate']['support_interval']=='UNKNOWN' and
                            d['candidate']['timestamp_position']=='UNKNOWN'
                            for d in result['daily_diagnostics'].values()))
        times = [parse_utc(a['attempted_at']) for a in self.state.data['attempts']]
        self.assertTrue(all((b-a).total_seconds()>=1 for a,b in zip(times,times[1:])))
        self.assertLessEqual(sum(a['bytes'] for a in self.state.data['attempts']),2*1024**2)
        self.assertEqual(len(ev['pages']),2)

    def test_third_short_page_can_complete(self):
        rows = self.rows()
        raw,ev,_ = self.run_pages([(rows[:1500],1500),(rows[1499:2999],1500),(rows[2998:],1500)])
        self.assertEqual(raw,rows);self.assertEqual(len(self.calls),6)
        self.assertEqual(ev['consumed_rows'],3896)

    def test_full_third_page_holds_without_fourth_or_cell_write(self):
        rows = self.rows(6046,step=60)
        self.bodies += [encode(dict(data=rows[i:i+2016],limit=2016)) for i in (0,2015,4030)]
        p = self.start();acquire(p)
        with self.assertRaises(FetchError):p.observations(self.sid)
        self.assertEqual(len(self.calls),6);self.assertTrue(self.state.data['provider']['closed'])
        self.assertEqual(self.state.data['cells'],{})

    def test_duplicate_boundary_preserves_multiplicity_and_rejects_revision(self):
        a,b = self.rows(2)
        variant = dict(b,v=None,q=None)
        self.assertEqual(diagnostic_native_rows([[a,b],[b,b,variant]]),[a,b,b,variant])
        with self.assertRaises(Hold):diagnostic_native_rows([[a,b,b],[b,variant]])
        with self.assertRaises(Hold):diagnostic_native_rows([[a,b],[variant]])
        with self.assertRaises(Hold):diagnostic_native_rows([[a,b],[]])

    def test_nonadvancing_duplicate_group_holds(self):
        row = self.rows(1)[0]
        self.bodies += [encode(dict(data=[row]*2016,limit=2016))]*2
        p = self.start();acquire(p)
        with self.assertRaises(FetchError):p.observations(self.sid)
        self.assertEqual(len(self.calls),5);self.assertEqual(self.state.data['cells'],{})

    def test_configuration_must_cover_off_hour_interval_before_observations(self):
        b = decode(self.bodies[2]);b['data'][0]['datapoints_config']=[dict(begins_at='2026-11-09T00:00:00Z')]
        self.bodies[2] = encode(b)
        p = self.start()
        with self.assertRaises(Hold):acquire(p)
        self.assertEqual(len(self.calls),3);self.assertEqual(self.state.data['cells'],{})

    def test_visibility_or_unit_change_holds_before_observations(self):
        b = decode(self.bodies[2]);b['data'][0]['is_hidden']=True;self.bodies[2]=encode(b)
        p = self.start()
        with self.assertRaises(Hold):acquire(p)
        self.assertEqual(len(self.calls),3)

    def test_no_live_authority_no_dispatch(self):
        self.p.update(status='APPROVED',representation='LIVE_PROVIDER')
        with self.assertRaises(Hold):self.start(live=True)
        self.assertEqual(self.calls,[])

    def test_unchanged_default_and_exact_host_no_arbitrary_interval(self):
        ordinary = fixture.plan(self.root);sid = next(iter(ordinary['roster']))
        with self.assertRaises(Hold):spec(ordinary,'observations',sid,'2026-10-14T08:00:00Z')
        request = spec(self.p,'observations',self.sid,'2026-10-13T07:30:00Z')
        self.assertTrue(request['url'].startswith('https://api.dendra.science/v2/datapoints?'))
        self.assertNotIn('edge',request['url'])
        with self.assertRaises(Hold):spec(self.p,'observations','f'*24,'2026-10-13T07:30:00Z')
        with self.assertRaises(Hold):spec(self.p,'observations',self.sid,'2026-11-09T08:30:00Z')

    def test_invalid_scope_and_larger_caps_refuse(self):
        original = deepcopy(self.p)
        mutations = [lambda p:p.update(admission=True),lambda p:p.update(range_targets=[]),
                     lambda p:p.update(roster=fixture.roster()),lambda p:p['diagnostic_interval'].update(start_day='2026-09-30'),
                     lambda p:p['diagnostic_interval'].update(start_day='2026-10-12'),
                     lambda p:p['diagnostic_interval'].update(through_day='2026-11-19'),
                     lambda p:p['diagnostic_interval'].update(comparison_days=['2026-10-01'])]
        mutations += [lambda p,k=k:p['limits'].__setitem__(k,p['limits'][k]+1) for k in
                      ('attempts','metadata','pages_per_stream','response_bytes','aggregate_bytes','observation_rows')]
        for change in mutations:
            p=deepcopy(original);change(p)
            with self.subTest(plan=p),self.assertRaises(Hold):validate_plan(p,p['not_before'])

    def test_timestamp_first_tie_null_zero_empty_and_conflict(self):
        day=self.p['target_day']
        rows=[dict(t=day+'T07:50:00Z',v=None,q=None),dict(t=day+'T08:10:00Z',v=20)]
        _,_,out=self.run_pages([(rows,2016)])
        c=out['daily_diagnostics'][day]['candidate']
        self.assertEqual((c['sample_status'],c['selection_offset_seconds']),('MISSING_VALUE',-600))
        self.assertIsNone(c['candidate_value_vwc_percent'])
        self.assertEqual(out['daily_diagnostics']['2026-10-13']['candidate']['sample_status'],'COMPLETE_EMPTY')
        from scripts.dendra.live_sampling import sample
        for data,status,value in [([dict(t=day+'T08:00:00Z',v=0)],'AVAILABLE',0),
                                  ([dict(t=day+'T08:00:00Z')],'MISSING_VALUE',None),
                                  ([dict(t=day+'T08:00:00Z',v='bad')],'INVALID',None),
                                  ([dict(t=day+'T08:00:00Z',v=10),dict(t=day+'T08:00:00Z',v=20)],'CONFLICT',None)]:
            got=sample(data,c['admission']['metadata'],day,self.p['not_before'],{})
            self.assertEqual((got['status'],got['value_vwc_percent']),(status,value))

    def test_wrong_identity_holds(self):
        row=self.rows(1)[0];row['datastream_id']='e'*24
        self.bodies += [encode(dict(data=[row],limit=2016))]
        p=self.start();acquire(p)
        with self.assertRaises(Hold):p.observations(self.sid)
        self.assertEqual(len(self.calls),4);self.assertEqual(self.state.data['cells'],{})

    def test_unsupported_quality_holds_and_retains_body(self):
        row=self.rows(1)[0];row['q']={'unknown-slot':['unreviewed']}
        self.bodies += [encode(dict(data=[row],limit=2016))]
        p=self.start();acquire(p)
        with self.assertRaises(Hold):p.observations(self.sid)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.state.data['attempts'][-1]['bytes'],len(self.bodies[-1]))
        self.assertEqual(self.state.data['cells'],{})

    def test_native_annotations_are_retained_and_withheld(self):
        row=dict(t=self.p['target_day']+'T08:00:00Z',v=10,q={'annotation_ids':['synthetic-review']})
        raw,_,out=self.run_pages([([row],2016)])
        self.assertEqual(raw,[row])
        self.assertEqual(out['daily_diagnostics'][self.p['target_day']]['candidate']['sample_status'],'WITHHELD')

    def test_cumulative_body_cap_stops_with_charge(self):
        self.bodies=[b+b' '*600000 for b in self.bodies]
        self.bodies += [encode(dict(data=[],limit=2016))+b' '*400000]
        p=self.start();acquire(p)
        with self.assertRaises(Hold):p.observations(self.sid)
        self.assertEqual(len(self.calls),4)
        self.assertLessEqual(sum(a['bytes'] for a in self.state.data['attempts']),2*1024**2)
        self.assertTrue(self.state.data['provider']['closed'])
        self.assertEqual(self.state.data['cells'],{})

    def test_interval_end_is_exclusive(self):
        row=dict(t=diagnostic_window(self.p,self.p['not_before'])['end'],v=10)
        self.bodies += [encode(dict(data=[row],limit=2016))]
        p=self.start();acquire(p)
        with self.assertRaises(FetchError):p.observations(self.sid)
        self.assertEqual(len(self.calls),4);self.assertEqual(self.state.data['cells'],{})

    def test_response_cap_keeps_charge_and_stops(self):
        self.bodies += [b'x'*(1024**2+1)]
        p=self.start();acquire(p)
        with self.assertRaises(Hold):p.observations(self.sid)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.state.data['attempts'][-1]['status'],'AMBIGUOUS')
        self.assertTrue(self.state.data['provider']['closed'])
        self.assertEqual(self.state.data['cells'],{})


if __name__ == '__main__':unittest.main()
