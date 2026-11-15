"""Focused SYNTHETIC provider-seam checks; never real sockets or authority."""
from copy import deepcopy
from datetime import timedelta
import http.client
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.dendra.live_provider import Provider,spec,LIMITS,VERSION,source_fingerprint
from scripts.dendra.live_metadata import acquire
from scripts.dendra.live_state import State
from scripts.dendra.transport import parse_utc,format_utc
from scripts.dendra.history_acquisition.safety import encode,decode,digest,Hold

def roster():
    """Self-contained synthetic family selection; no private catalog dependency."""
    from scripts.dendra.live_metadata import FAMILY_PAIR
    result = {}
    for i, unit in enumerate(('VolumetricWaterContent', 'Percent'), 1):
        sid = f'{i:024x}'
        result[sid] = dict(
            identity=dict(station_id=f'{i+100:024x}', stream_id=sid,
                          native_unit=unit, depth_cm=None, orientation='UNKNOWN'),
            family=dict(organization_id='f'*24, name='Synthetic family', slug='synthetic'),
            terms=dict(dq=dict(Purpose='ReadytoUse'),
                       ds=dict(Aggregate='Average', Medium='Soil', Variable='VolumetricWaterContent'),
                       dt=dict(Unit=unit)), attributes={},
            selection=dict(policy=FAMILY_PAIR, station_sha256='a'*64,
                           stream_sha256='b'*64, crosswalk_sha256='c'*64,
                           configuration_sha256=digest([dict(begins_at='2025-01-01T00:00:00.000Z')])))
    return result


def plan(root):
    return dict(version=VERSION,status='OFFLINE_ONLY',grant_id='SYNTHETIC-NOT-AUTHORITY',
        not_before='2026-10-15T23:09:28.508Z',expires_at='2026-10-15T23:14:28.508Z',target_day='2026-10-15',
        roster=roster(),limits=deepcopy(LIMITS),retries=0,redirects=0,publication=False,
        source_sha256=source_fingerprint(),output_root=str(root))


def metadata_bodies(p):
    from scripts.dendra.live_metadata import UNITS
    out=[encode(dict(_id='dt-unit',terms=list(UNITS.values())))]
    for sid,r in p['roster'].items():
        common=dict(organization_id=r['family']['organization_id'],public_level=3,is_hidden=False,
                    is_geo_protected=False,updated_at='2026-01-01T00:00:00.000Z')
        out.append(encode(dict(_id=r['identity']['station_id'],**common)))
        out.append(encode(dict(data=[dict(_id=sid,station_id=r['identity']['station_id'],terms=r['terms'],
            attributes=r['attributes'],datapoints_config=[dict(begins_at='2025-01-01T00:00:00.000Z')],**common)],
            limit=500,skip=0,total=1)))
    return out


def native(t='08:00:00',v=10):return dict(t='2026-10-15T'+t+'Z',v=v)


class Clock:
    def __init__(self,p):self.base=parse_utc(p['not_before']);self.elapsed=0
    def now(self):return self.base+timedelta(seconds=self.elapsed)
    def mono(self):return self.elapsed
    def wait(self,n):self.elapsed+=n


class Response(io.BytesIO):
    def __init__(self,b,url,*,status=200,headers=None):
        super().__init__(b);self.url=url;self.status=status
        self.headers=headers if headers is not None else {'Content-Length':str(len(b)),'Content-Type':'application/json'}
    def geturl(self):return self.url


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dendra-stage-b-synthetic-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'state';self.p=plan(self.root);self.clock=Clock(self.p)
        self.responses=metadata_bodies(self.p);self.calls=[];self.state=None
        self.addCleanup(lambda:self.state.close() if self.state else None)
        self.net=patch('socket.socket.connect',side_effect=AssertionError('No real network'));self.net.start();self.addCleanup(self.net.stop)

    def opener(self,request,timeout):
        self.calls.append(request.full_url);self.clock.wait(.01)
        return Response(self.responses[len(self.calls)-1],request.full_url)

    def start(self,**kw):
        self.state=State(self.root,dict(plan_sha256=digest(self.p)),create=True)
        return Provider(self.state,self.p,opener=kw.pop('opener',self.opener),now=self.clock.now,
                        monotonic=self.clock.mono,wait=self.clock.wait,**kw)

    def test_exact_five_metadata_and_two_observation_requests(self):
        self.responses += [encode(dict(data=[native(v=0)],limit=2016))]*2
        p=self.start();a=acquire(p)
        for sid in self.p['roster']:
            rows,ev=p.observations(sid)
            self.assertEqual(rows[0]['v'],0);self.assertEqual(ev['kind'],'OFFLINE_DRY_RUN')
            self.assertFalse(a[sid]['normalized_eligible'])
        self.assertEqual(len(self.calls),7)
        self.assertIn('vocabularies/dt-unit',self.calls[0])
        self.assertIn('%24limit=500',self.calls[2]);self.assertIn('%24limit=2016',self.calls[5])
        times=[parse_utc(a['attempted_at']) for a in self.state.data['attempts']]
        self.assertTrue(all((b-a).total_seconds()>=1 for a,b in zip(times,times[1:])))
        self.assertTrue(all(a['context']['deadline_utc'] for a in self.state.data['attempts']))

    def test_no_default_opener_or_unapproved_live(self):
        self.p['status']='APPROVED'
        with self.assertRaises(Hold):self.start(live=True)
        self.assertEqual(self.calls,[])

    def test_exact_revocation_checked_before_next_attempt(self):
        self.p['status']='APPROVED';valid=[True];pin=digest(self.p)
        p=self.start(live=True,authorize=lambda h,at:valid[0] and h==pin)
        p.exchange(spec(self.p,'unit-vocabulary'));valid[0]=False
        with self.assertRaises(Hold):p.exchange(spec(self.p,'station',next(iter(self.p['roster']))))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.state.data['attempts']),1)

    def test_observation_cannot_precede_metadata(self):
        p=self.start();sid=next(iter(self.p['roster']))
        with self.assertRaises(Hold):p.observations(sid)
        self.assertEqual(self.calls,[])

    def test_source_change_and_expiration_refuse_before_send(self):
        p=self.start()
        with patch('scripts.dendra.live_provider.source_fingerprint',return_value='changed'):
            with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.clock.wait(301)
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.calls,[])

    def test_redirect_error_body_retained_without_follow_or_retry(self):
        def redirect(req,timeout):
            self.calls.append(req.full_url);return Response(b'redirect',req.full_url,status=302)
        p=self.start(opener=redirect)
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        a=self.state.data['attempts'][0]
        self.assertEqual((len(self.calls),a['bytes'],a['status']),(1,8,'AMBIGUOUS'))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))

    def test_timeout_is_charged_and_restart_cannot_redispatch(self):
        self.p['limits']['request_seconds']=.02
        def slow(req,timeout):self.calls.append(req.full_url);time.sleep(.1)
        p=self.start(opener=slow)
        with self.assertRaises(TimeoutError):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['status'],'AMBIGUOUS')
        self.state.close();self.state=State(self.root,dict(plan_sha256=digest(self.p)))
        with self.assertRaises(Hold):Provider(self.state,self.p,opener=self.opener,now=self.clock.now)
        self.assertEqual(len(self.calls),1)

    def test_incomplete_read_retains_prefix(self):
        class Broken(Response):
            def read(self,n):raise http.client.IncompleteRead(b'abc',10)
        p=self.start(opener=lambda req,timeout:Broken(b'longbody',req.full_url))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['bytes'],3)

    def test_body_cap_unknown_eof_holds_without_overread(self):
        self.p['limits']['response_bytes']=8
        p=self.start(opener=lambda req,timeout:Response(b'123456789',req.full_url,
                     headers={'Content-Type':'application/json'}))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['bytes'],8)

    def test_attempt_cap_refuses_before_second_send(self):
        self.p['limits']['attempts']=1;p=self.start();p.exchange(spec(self.p,'unit-vocabulary'))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'station',next(iter(self.p['roster']))))
        self.assertEqual(len(self.calls),1)

    def test_orphaned_response_refuses(self):
        p=self.start(opener=lambda req,timeout:Response(b'{}','https://other.invalid/'))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['status'],'AMBIGUOUS')

    def test_header_and_compression_bounds(self):
        p=self.start(opener=lambda req,timeout:Response(b'{}',req.full_url,headers={'X-Large':'x'*4097}))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['bytes'],0)

    def test_full_three_pages_never_claim_completion(self):
        self.responses += [encode(dict(data=[native(a),native(b)],limit=2)) for a,b in
                           [('07:30:00','07:40:00'),('07:40:00','07:50:00'),('07:50:00','08:00:00')]]
        p=self.start();acquire(p);sid=next(iter(self.p['roster']))
        with self.assertRaises(Exception):p.observations(sid)
        self.assertEqual(len(self.calls),8)
        self.assertEqual(self.state.cell(sid+'/'+self.p['target_day'])['latest_attempt']['status'],'FAILED')

    def test_aggregate_cap_refuses_new_attempt_after_exact_exhaustion(self):
        self.p['limits']['aggregate_bytes']=len(self.responses[0])
        p=self.start();p.exchange(spec(self.p,'unit-vocabulary'))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'station',next(iter(self.p['roster']))))
        self.assertEqual(len(self.calls),1)

    def test_pre_send_reservation_failure_never_opens_client(self):
        p=self.start()
        with patch.object(self.state,'commit',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.calls,[])

    def test_cancelled_exchange_charge_survives(self):
        def cancelled(req,timeout):raise KeyboardInterrupt('synthetic cancellation')
        p=self.start(opener=cancelled)
        with self.assertRaises(KeyboardInterrupt):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['status'],'AMBIGUOUS')
        self.assertTrue(self.state.data['provider']['closed'])

    def test_compressed_body_refused_before_read(self):
        p=self.start(opener=lambda req,timeout:Response(b'compressed',req.full_url,
                     headers={'Content-Encoding':'gzip'}))
        with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.state.data['attempts'][0]['bytes'],0)

    def test_request_substitution_and_row_budget_refuse(self):
        self.p['limits']['observation_rows']=2015;p=self.start()
        bad=spec(self.p,'unit-vocabulary');bad['url']+='?unapproved=1'
        with self.assertRaises(Hold):p.exchange(bad)
        acquire(p)
        with self.assertRaises(Hold):p.observations(next(iter(self.p['roster'])))
        self.assertEqual(len(self.calls),5)

    def test_slow_reservation_cannot_extend_request_deadline(self):
        p=self.start();original=self.state.reserve
        def slow(*args,**kwargs):
            ordinal=original(*args,**kwargs);self.clock.wait(26);return ordinal
        with patch.object(self.state,'reserve',side_effect=slow):
            with self.assertRaises(Hold):p.exchange(spec(self.p,'unit-vocabulary'))
        self.assertEqual(self.calls,[])
        self.assertEqual(self.state.data['attempts'][0]['status'],'AMBIGUOUS')


if __name__=='__main__':unittest.main()
