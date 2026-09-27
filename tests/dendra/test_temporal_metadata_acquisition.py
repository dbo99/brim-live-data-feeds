"""Synthetic temporal metadata through the shared Journal/transport path.

Explicit task-owned scratch; no live request or eligibility approval. The test
runner installs socket/DNS/real-sleep guards before importing repository code.
"""
import copy
from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs,urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from dendra.history_acquisition import metadata_acquisition as m
from dendra.history_acquisition import authority_witness as w, eligibility, presentation
from dendra.history_acquisition.d3_plan import RequestSpec, IDENTITIES
from dendra.history_acquisition.journal import Journal,UnknownSourceRowCount
from dendra.history_acquisition.model import Inventory,INVENTORY_SHA256,source_binding
from dendra.history_acquisition.provider_metadata import load_authority,parse_datastreams,parse_station,parse_vocabulary
from dendra.history_acquisition.provider_adapter import WitnessAdapter,MetadataAdmissionHold,Deadline
from dendra.history_acquisition.safety import Hold,encode,decode,digest,sha
from dendra.transport import parse_utc,format_utc
from test_authority_witness import Clock,Reply,NOW,SID,OTHER,FIRST


def setUpModule():
    global INV,AUTHORITY
    INV=Inventory.load(os.environ["DENDRA_INVENTORY"],INVENTORY_SHA256)
    AUTHORITY=load_authority(INV,Path(__file__).resolve().parents[2]/"data/input/dendra/pilot_catalog.json")


def station(sid):
    return dict(_id=IDENTITIES[sid]["station_id"],public_level=3,is_hidden=False,is_geo_protected=False)


def page(sid):
    ident=IDENTITIES[sid];attrs={}
    if ident["depth_cm"] is not None:attrs["depth"]=dict(value=ident["depth_cm"],unit="Centimeter")
    if ident["orientation"] is not None:attrs["orientation"]=ident["orientation"]
    row=dict(_id=sid,station_id=ident["station_id"],public_level=3,is_hidden=False,is_geo_protected=False,
        attributes=attrs,terms=dict(dt=dict(Unit=ident["native_unit"]),ds=dict(Medium="Soil",Variable="VolumetricWaterContent")),
        datapoints_config=[dict(begins_at="2020-01-01T00:00:00.000Z",ends_before="2024-01-01T00:00:00.000Z"),
                           dict(begins_at="2024-01-01T00:00:00.000Z")])
    return dict(data=[row],limit=500,total=1,skip=0)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.calls=[]
        self.binding,self.tasks=m.prepare(INV,AUTHORITY,selected_ids=[SID,OTHER],campaign_id="synthetic-metadata")
        self.root=Path(tempfile.mkdtemp(prefix="metadata-",dir=os.environ["DENDRA_TEST_ROOT"]))
        self.j=Journal(self.root,self.binding,self.tasks,create=True,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic)
        self.addCleanup(self.j.close)
        self.a=m.MetadataAdapter(self.j)

    def auth(self):
        return dict(binding_sha256=digest(self.binding),approval_reference="synthetic-only",
            window_start=NOW,window_end=format_utc(parse_utc(NOW)+timedelta(seconds=150)))

    def execute(self,request,timeout,mutate=None):
        a=list(self.j.snapshot()["attempts"].values())[-1]
        self.assertEqual(a["state"],"started")
        for e in self.j.events[-2:]:
            self.assertEqual(self.j.fs.read(self.j._event_path(e["sequence"]),65536),encode(e))
        self.assertEqual([e["kind"] for e in self.j.events[-2:]],["reserved","started"])
        self.assertLessEqual(timeout,25)
        spec=self.a.current_spec;self.calls.append((m.slot(spec),self.clock.seconds))
        if spec.kind=="unit-vocabulary":value=dict(_id="dt-unit",terms=list(AUTHORITY["dictionary_terms"].values()))
        elif spec.kind=="station":value=station(spec.selected_stream)
        else:value=page(spec.selected_stream)
        if mutate is not None:
            value=mutate(spec,value)
        return value if isinstance(value,Reply) else Reply(encode(value))

    def run_metadata(self,mutate=None):
        return self.a.run(executor=lambda req,timeout:self.execute(req,timeout,mutate),
                          wait=self.clock.advance,authorization=self.auth())

    def test_five_exact_slots_no_history_pagination_deterministic(self):
        self.assertEqual(self.tasks,{})
        self.assertEqual(len(self.binding["requests"]),5)
        self.assertEqual((self.binding,self.tasks),m.prepare(INV,AUTHORITY,selected_ids=[OTHER,SID],campaign_id="synthetic-metadata"))
        for s in self.binding["requests"].values():
            self.assertNotIn("datapoints",s["url"])
            self.assertIsNone(s["cursor"])

    def test_exact_source_temporal_provenance_and_deterministic_packet(self):
        result=self.run_metadata()
        for sid,e in result.items():
            p=e["packet"]
            self.assertEqual(p["frozen_identity"],INV.identity(sid))
            self.assertEqual(p["metadata_binding"]["collector_fingerprint"],digest(source_binding()))
            self.assertEqual(len(p["configuration_evidence"]["configurations"]),2)
            self.assertEqual(p["historical_applicability"],dict(kind="unknown_history"))
            self.assertEqual(m.packet_evidence(self.j,sid,now=self.clock.now()),e)
            self.assertEqual(e["packet_sha256"],digest(p))
            self.assertEqual(p["original_response_sha256"],sha(encode(page(sid))))
            from dendra.history_acquisition.dimensionless_probe import review_packet,CAMPAIGN_TEMPORAL_PROFILE
            station_obj=decode(self.j.read_object(e['provenance']['station']['sanitized_object']))
            replay=review_packet(encode(page(sid)),station_obj,INV,stream_id=sid,
                metadata_profile=CAMPAIGN_TEMPORAL_PROFILE,checked_at=p['checked_at'],now=p['checked_at'])
            self.assertEqual(replay,p)
            self.assertEqual(set(e["provenance"]),{"vocabulary","station","datastream_list"})
            for v in e["provenance"].values():self.assertEqual(set(v["records"]),{"reserved","started","received"})
        self.assertEqual(self.j.snapshot()["intervals"],{})

    def test_packets_prepare_real_witness_path_without_rebinding(self):
        result=self.run_metadata()
        # The separately budgeted phase must also honor the preceding phase's
        # last durable start. This synthetic wait models future caller pacing.
        self.clock.advance(1)
        packets={sid:e["packet"] for sid,e in result.items()}
        binding,tasks=w.prepare(INV,campaign_id="synthetic-first",packets=packets,as_of=self.clock.now())
        with Journal(self.root,binding,tasks,create=True,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            def fake(request,timeout):
                self.assertEqual(list(j.snapshot()["attempts"].values())[-1]["state"],"started")
                sid=parse_qs(urlsplit(request.full_url).query)["datastream_id"][0]
                self.calls.append(("witness-"+sid,self.clock.seconds))
                return Reply(encode(dict(data=[dict(t=FIRST,v=0,datastream_id=sid)],limit=1,total=4)))
            auth=dict(binding_sha256=digest(binding),approval_reference="synthetic-only",
                window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60)))
            witnesses=WitnessAdapter(j).run(executor=fake,wait=self.clock.advance,authorization=auth)
            for sid,e in witnesses.items():
                review=dict(rule=w.REVIEW,disposition="ACCEPT_SOURCE_START",evidence_sha256=e["evidence_sha256"],
                    reviewer_ref="synthetic-only",reviewed_at=self.clock.now())
                proof=presentation.review_journal_first(j,sid,review=review,as_of=self.clock.now())
                self.assertEqual(proof["state"],presentation.REVIEWED)
                self.assertFalse(proof["dispatch_ready"])
        self.assertEqual(len(self.calls),7)
        self.assertTrue(all(b[1]-a[1]>=1 for a,b in zip(self.calls,self.calls[1:])))

    def bundle(self,packet,accept=False):
        fp=digest(source_binding());body=encode(packet)
        review=eligibility.propose(INV,body,packet_sha256=sha(body),packet_source_fingerprint=fp,
            executor_fingerprint=fp,start=FIRST,end="2026-09-26T08:00:00.000Z")
        if accept:
            review.update(disposition="ACCEPT_NATIVE",reviewer_ref="synthetic-explicit-review",
                reviewed_at=self.clock.now(),expires_at=format_utc(parse_utc(self.clock.now())+timedelta(hours=1)),
                acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
        return dict(packet=packet,review=review,decision=eligibility.decide(INV,body,review,executor_fingerprint=fp,now=self.clock.now()))

    def test_eligibility_pending_then_explicit_review_and_separate_source_start(self):
        p=self.run_metadata()[SID]["packet"]
        pending=self.bundle(p)
        def readiness(bundle,authority):return eligibility.dispatch_readiness(INV,SID,bundle=bundle,
            source_start_authority=authority,executor_fingerprint=digest(source_binding()),now=self.clock.now())
        self.assertIn("review_required",readiness(pending,presentation.REVIEWED)["dispatch_blockers"])
        accepted=self.bundle(p,True)
        self.assertEqual(readiness(accepted,presentation.REVIEWED)["dispatch_readiness"],"DISPATCH_READY")
        self.assertIn("source_start_non_executable",readiness(accepted,presentation.AUDIT)["dispatch_blockers"])
        self.assertFalse(p["observation_acquisition_authorized"])
        self.assertEqual(readiness(None,presentation.REVIEWED)["dispatch_readiness"],"NOT_READY")

    def test_stale_metadata_remains_not_ready(self):
        p=self.run_metadata()[SID]["packet"];bundle=self.bundle(p,True);self.clock.advance(86401)
        with self.assertRaises(Hold):m.packet_evidence(self.j,SID,now=self.clock.now())
        r=eligibility.dispatch_readiness(INV,SID,bundle=bundle,source_start_authority=presentation.REVIEWED,
            executor_fingerprint=digest(source_binding()),now=self.clock.now())
        self.assertEqual(r["dispatch_readiness"],"NOT_READY")
        self.assertIn("access_metadata_stale_or_future",r["dispatch_blockers"])

    def test_wrong_selected_target_unit_depth_orientation_and_config_hold(self):
        for name in ('unit','depth','orientation','config'):
            p=page(SID)
            if name=='unit':p['data'][0]['terms']['dt']['Unit']='Dimensionless'
            if name=='depth':p['data'][0]['attributes']['depth']['value']=99
            if name=='orientation':p['data'][0]['attributes']['orientation']='Vertical'
            if name=='config':p['data'][0]['datapoints_config']=[]
            from dendra.history_acquisition.dimensionless_probe import review_packet,CAMPAIGN_TEMPORAL_PROFILE
            with self.subTest(name=name),self.assertRaises(Hold):
                review_packet(encode(p),parse_station(encode(station(SID)),IDENTITIES[SID]['station_id'],checked_at=NOW,now=NOW),
                              INV,stream_id=SID,metadata_profile=CAMPAIGN_TEMPORAL_PROFILE,checked_at=NOW,now=NOW)

    def test_one_accounted_stream_hold_preserves_other(self):
        def bad(spec,value):
            if spec.kind=='datastream-list' and spec.selected_stream==OTHER:
                value['data'][0]['datapoints_config'][1]['begins_at']='2023-01-01T00:00:00.000Z'
            return value
        result=self.run_metadata(bad)
        self.assertEqual(result[OTHER]['outcome'],'HOLD')
        self.assertIn('packet',result[SID])
        self.assertEqual(len(self.calls),5)
        with self.assertRaises(Hold):m.packet_evidence(self.j,OTHER,now=self.clock.now())

    def test_incomplete_list_no_extra_page(self):
        def bad(spec,value):
            if spec.kind=='datastream-list':value['total']=2
            return value
        results=self.run_metadata(bad)
        self.assertTrue(all(v['outcome']=='HOLD' for v in results.values()))
        self.assertEqual(len(self.calls),5)
        self.assertTrue(all(a['ordinal']==1 for a in self.j.snapshot()['attempts'].values()))

    def test_unknown_rows_preserves_global_hold(self):
        def bad(spec,value):
            if spec.kind=='station':value['is_hidden']=True
            return value
        with self.assertRaises(UnknownSourceRowCount):self.run_metadata(bad)
        self.assertEqual(len(self.calls),2)

    def test_protected_station_geometry_is_not_retained(self):
        def protected(spec,value):
            if spec.kind=='station':value.update(is_geo_protected=True,geo=dict(type='Point',coordinates=[1,2,3]))
            return value
        result=self.run_metadata(protected)
        for e in result.values():
            station_obj=decode(self.j.read_object(e['provenance']['station']['sanitized_object']))
            self.assertIsNone(station_obj['geometry']);self.assertNotIn('geo_z_native',station_obj)
            self.assertTrue(e['packet']['access_evidence']['geo_protected'])

    def test_reserved_and_started_ambiguity_cannot_retry(self):
        key=self.j.reserve('unit-vocabulary','unit-vocabulary');self.j.started(key);self.j.close()
        with Journal(self.root,self.binding,self.tasks,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold):j.reserve('unit-vocabulary','unit-vocabulary')
            with self.assertRaises(Hold):m.MetadataAdapter(j).run(executor=lambda *a,**k:self.fail('retry'),wait=self.clock.advance,authorization=self.auth())
            self.assertEqual(j.snapshot()['counters']['attempts'],1)

    def test_restart_review_no_append_or_dispatch(self):
        result=self.run_metadata();count=len(self.j.events);self.j.close()
        with Journal(self.root,self.binding,self.tasks,inspect_only=True,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            self.assertEqual(m.packet_evidence(j,SID,now=self.clock.now()),result[SID])
            self.assertEqual(len(j.events),count)
            with self.assertRaises(Hold):m.MetadataAdapter(j)

    def test_object_corruption_fails_closed(self):
        result=self.run_metadata();descriptor=result[SID]['provenance']['datastream_list']['sanitized_object']
        (self.root/self.j.prefix/descriptor['path']).write_bytes(b'corrupt synthetic object')
        with self.assertRaises(Hold):m.packet_evidence(self.j,SID,now=self.clock.now())

    def test_receipt_or_anchor_corruption_fails_closed(self):
        self.run_metadata();self.j.events[-1]['data']['response_sha256']='0'*64
        with self.assertRaises(Hold):m.packet_evidence(self.j,SID,now=self.clock.now())

    def test_source_change_refuses_promotion_but_historical_object_readable(self):
        result=self.run_metadata();descriptor=result[SID]['provenance']['datastream_list']['sanitized_object']
        with patch.object(m,'source_binding',return_value={}):
            with self.assertRaises(Hold):m.packet_evidence(self.j,SID,now=self.clock.now())
            self.assertEqual(decode(self.j.read_object(descriptor)),result[SID]['packet'])

    def test_old_packet_does_not_become_current_authority(self):
        p=self.run_metadata()[SID]['packet'];p['metadata_binding']['collector_fingerprint']='0'*64
        p['metadata_binding_sha256']=digest(p['metadata_binding'])
        with self.assertRaisesRegex(Hold,'Packet binding mismatch'):w.check_metadata(INV,SID,p,now=self.clock.now())

    def test_history_and_wrong_cursor_reservations_forbidden(self):
        for task,cursor,kw in [('history','observations',{'interval_key':'history','run':1}),
                               ('metadata-'+SID,'station-page-2',{}),('metadata-'+'0'*24,'station',{})]:
            with self.subTest(task=task,cursor=cursor),self.assertRaises(Hold):self.j.reserve(task,cursor,**kw)
        self.assertEqual(self.j.snapshot()['counters']['attempts'],0)

    def test_dependencies_cannot_be_skipped(self):
        with self.assertRaises(Hold):self.j.reserve('metadata-'+SID,'station')
        with self.assertRaises(Hold):self.j.reserve('metadata-'+SID,'datastream-list')
        self.assertEqual(self.j.snapshot()['counters']['attempts'],0)

    def test_transport_failure_spent_zero_retry(self):
        def fail(req,timeout):raise urllib.error.URLError('synthetic failure')
        with self.assertRaises(urllib.error.URLError):self.a.run(executor=fail,wait=self.clock.advance,authorization=self.auth())
        a=next(iter(self.j.snapshot()['attempts'].values()))
        self.assertFalse(a['details']['retryable']);self.assertEqual(a['state'],'failure')
        with self.assertRaises(Hold):self.run_metadata()

    def test_throttle_no_retry(self):
        def throttle(spec,value):r=Reply(b'');r.status=429;return r
        with self.assertRaises(urllib.error.HTTPError):self.run_metadata(throttle)
        self.assertEqual(len(self.calls),1)
        self.assertFalse(next(iter(self.j.snapshot()['attempts'].values()))['details']['retryable'])

    def test_redirect_no_follow(self):
        def redirect(spec,value):r=Reply(b'');r.status=302;return r
        with self.assertRaises(Hold):self.run_metadata(redirect)
        self.assertEqual(len(self.calls),1)

    def test_spacing_cannot_be_faked(self):
        with self.assertRaises(Hold):self.a.run(executor=self.execute,wait=lambda seconds:None,authorization=self.auth())
        self.assertEqual(len(self.calls),1)

    def test_approval_and_duplicate_run_refused(self):
        auth=self.auth();auth['binding_sha256']='0'*64
        with self.assertRaises(Hold):self.a.run(executor=self.execute,wait=self.clock.advance,authorization=auth)
        self.assertEqual(self.j.events,[])
        self.run_metadata()
        with self.assertRaises(Hold):self.run_metadata()
        self.assertEqual(len(self.calls),5)

    def test_durable_receipt_failure_is_spent(self):
        with patch.object(self.j,'received',side_effect=OSError('synthetic')):
            with self.assertRaises(Hold):self.run_metadata()
        self.assertEqual(next(iter(self.j.snapshot()['attempts'].values()))['state'],'started')
        with self.assertRaises(Hold):self.run_metadata()
        self.assertEqual(len(self.calls),1)

    def test_reentrant_run_refused(self):
        def nested(spec,value):
            with self.assertRaises(Hold):self.run_metadata()
            return value
        self.run_metadata(nested);self.assertEqual(len(self.calls),5)

    def test_historical_d3_rules_are_not_relaxed(self):
        raw=page(SID);admitted=parse_station(encode(station(SID)),IDENTITIES[SID]['station_id'],checked_at=NOW,now=NOW)
        vocab=parse_vocabulary(encode(dict(_id='dt-unit',terms=list(AUTHORITY['dictionary_terms'].values()))),AUTHORITY)
        with self.assertRaisesRegex(Hold,'Missing or ambiguous configured cadence'):
            parse_datastreams(encode(raw),IDENTITIES[SID],admitted,vocab,AUTHORITY,checked_at=NOW,now=NOW)

    def test_wrong_station_and_stream_returned_fail_closed(self):
        def wrong(spec,value):
            if spec.kind=='datastream-list':value['data'][0]['station_id']='0'*24
            return value
        results=self.run_metadata(wrong)
        self.assertTrue(all(v['outcome']=='HOLD' for v in results.values()))
        for sid in results:
            with self.assertRaises(Hold):m.packet_evidence(self.j,sid,now=self.clock.now())

    def test_nonretryable_station_refusal_skips_list_only_for_that_stream(self):
        def refused(spec,value):
            if spec.kind=='station' and spec.selected_stream==OTHER:
                r=Reply(b'');r.status=403;return r
            return value
        results=self.run_metadata(refused)
        self.assertEqual(results[OTHER]['outcome'],'HOLD');self.assertIn('packet',results[SID])
        self.assertEqual(len(self.calls),4)
        self.assertNotIn('datastream-list-'+OTHER,[s for s,t in self.calls])

    def test_raw_metadata_storage_without_details_is_forbidden(self):
        key=self.j.reserve('unit-vocabulary','unit-vocabulary');self.j.started(key)
        with self.assertRaises(Hold):self.j.received(key,b'private raw metadata',source_rows=0)

    def test_unanchored_event_cannot_be_ignored(self):
        self.run_metadata()
        path=self.root/self.j._event_path(len(self.j.events));path.write_bytes(b'synthetic orphan')
        with self.assertRaises(Hold):m.packet_evidence(self.j,SID,now=self.clock.now())

    def test_body_ceiling_no_raw_object_no_retry(self):
        def oversized(spec,value):return Reply(b' '*(8*1024**2+1))
        with self.assertRaises(Hold):self.run_metadata(oversized)
        a=next(iter(self.j.snapshot()['attempts'].values()))
        self.assertEqual(a['response_bytes'],8*1024**2+1);self.assertFalse(a['body_retained'])
        with self.assertRaises(Hold):self.run_metadata()
        self.assertEqual(len(self.calls),1)

    def test_deadline_is_spent_without_retry(self):
        def expired(request,timeout):raise Deadline('synthetic deadline')
        with self.assertRaises(Deadline):self.a.run(executor=expired,wait=self.clock.advance,authorization=self.auth())
        a=next(iter(self.j.snapshot()['attempts'].values()))
        self.assertEqual(a['details']['error_code'],'deadline');self.assertFalse(a['details']['retryable'])

    def test_wrong_query_refused_before_reservation(self):
        spec=RequestSpec('datastream-list',SID);self.a.current_spec=spec
        req=self.a._request(spec);req.full_url += '&$skip=500'
        with self.assertRaises(Hold):self.a._validate_dispatch(req,spec,None)
        self.assertEqual(self.j.snapshot()['counters']['attempts'],0)


if __name__=='__main__':unittest.main()
