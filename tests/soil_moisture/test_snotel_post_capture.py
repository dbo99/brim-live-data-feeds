"""Post-capture integration: real public catalog, explicitly synthetic observations."""
import base64
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import soil_network_workflow as w
from test_snotel_workflow import WorkflowTests
from test_snotel_first_run import FirstPrefixTests
s=w.ss
ROOT=Path(__file__).resolve().parents[2]
ROSTER='c85422467f7b49d603cbce3d7ce92e8cd55f01fcdcbb42053373a11f01ab66da'


def real_catalog_fixture(mode='mixed'):
    raw=(ROOT/'data/input/snotel_sms_catalog.json').read_bytes()
    assert s.h.sha256(raw)=='d82973ad46b00df2737d6b902a2dbea3a450210d859bf10c82d80384e405beb8'
    c=json.loads(raw)
    groups=[dict(metadata_body=base64.b64decode(g['metadata_base64'],validate=True),
        metadata_sha256=g['metadata_sha256'],sensor_identities=g['sensor_identities']) for g in c['groups']]
    plan=s.plan_current_wy('2026-11-12T20:00:00Z',groups,expected_roster_sha256=ROSTER)
    captures=[]
    for g,request in zip(groups,plan['requests']):
        sensors=s.h._sensors(g['metadata_body'],g['metadata_sha256']);blocks=[]
        for sid in g['sensor_identities']:
            values=[]
            for k,day in enumerate(s.interval(2027,plan['start'],plan['end'])):
                row=dict(date=day,value=0 if k%4==0 else 14.5,origValue=0,qcFlag='V',qaFlag='V',origQcFlag='V')
                if mode=='mixed' and k%4==1:row['value']=None;row['qcFlag']='M'
                if mode=='withheld' or (mode=='mixed' and k%4==2):row.pop('value');row['qcFlag']='S'
                if mode!='mixed' or k%4!=3:values.append(row)
            blocks.append(dict(stationElement=sensors[sid]['station_element'],values=values))
        body=s.h.canonical([] if mode=='empty' else [dict(stationTriplet=request['parameters']['stationTriplets'],data=blocks)])
        receipt=dict(request=request,retrieved_at_utc='2026-11-12T20:00:00.750000Z',response_sha256=s.h.sha256(body),
            response_bytes=len(body),http_status=200,complete=True,error=None,physical_attempt=1,
            body_accounting_state='COMPLETE',captured_prefix_bytes=len(body),total_consumed_body_bytes=len(body))
        captures.append(dict(body=body,receipt=receipt,receipt_sha256=s.h.sha256(s.h.canonical(receipt))))
    return groups,plan,captures


class PostCaptureTests(unittest.TestCase):
    def test_actual_clock_precision_and_unchanged_other_clock(self):
        instant=datetime(2026,11,12,20,0,0,900000,tzinfo=timezone.utc)
        with patch.object(w,'datetime') as clock:
            clock.now.return_value=instant
            self.assertEqual(w.now(),'2026-11-12T20:00:00Z')
            self.assertEqual(w.snotel_now(),'2026-11-12T20:00:00.900000Z')
        self.assertIs(__import__('inspect').signature(w.snotel_prepare).parameters['clock'].default,w.snotel_now)
        self.assertIs(__import__('inspect').signature(w.snotel_rebuild).parameters['clock'].default,w.snotel_now)

    def test_real_catalog_fractional_chronology_and_native_statuses(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root=Path(directory).resolve()
            for mode in ('mixed','empty','withheld'):
                groups,plan,captures=real_catalog_fixture(mode)
                before=s.h.sha256(s.h.canonical([c['receipt'] for c in captures]))
                self.assertEqual(len({c['receipt_sha256'] for c in captures}),32)
                with self.assertRaisesRegex(ValueError,'Capture clock outside completed interval/build'):
                    w._snotel_finish(root,root/('old-'+mode),groups,plan,captures,None,None,'a'*40,'2026-11-12T20:00:00Z')
                phases=[]
                d=w._snotel_finish(root,root/mode,groups,plan,captures,None,None,'a'*40,'2026-11-12T20:00:00.900000Z',phase=phases.append)
                self.assertEqual(phases,['snapshot_construction','candidate_validation','candidate_persistence','publisher_metadata'])
                self.assertEqual(d['roster_sha256'],ROSTER);self.assertEqual(len(d['sensors']),98)
                self.assertEqual({x['sensor_identity'] for x in d['sensors']},{x for g in groups for x in g['sensor_identities']})
                self.assertEqual(before,s.h.sha256(s.h.canonical([c['receipt'] for c in captures])))
                files=s.read_product(root/mode/'candidate');s.import_product(files,trusted_binding=dict(commit='a'*40,**s._binding(files[s.PATHS[0]],files[s.PATHS[1]])))
                self.assertEqual(d['coverage']['positions'],4116)
                if mode=='mixed':self.assertGreater(d['coverage']['genuine_zeros'],0);self.assertGreater(d['coverage']['admission_counts']['WITHHELD'],0)
                if mode=='empty':self.assertEqual(d['coverage']['admission_counts']['COMPLETE_VALID_EMPTY'],4116)
                if mode=='withheld':self.assertEqual(d['coverage']['admission_counts']['WITHHELD'],4116)


class FailurePhaseTests(WorkflowTests):
    def test_post_capture_phase_and_private_failure_retention(self):
        self.git.trees[__import__('test_snotel_workflow').PRIOR]={}
        canary='PRIVATE_EXCEPTION_CANARY'
        with patch.object(w.mp,'prepare_metadata',side_effect=ValueError(canary)):
            result=self.run_candidate(bootstrap=True)
        self.assertEqual((result['status'],result['failure_phase'],result['error_type']),('HELD','publisher_metadata','ValueError'))
        self.assertEqual((result['attempts'],result['completed_stations']),(32,32))
        self.assertTrue((self.runroot/'captures.json').is_file())
        self.assertEqual(len(list(self.runroot.glob('completed-*.json'))),32)
        self.assertIn(canary,(self.runroot/'failure.txt').read_text())
        self.assertNotIn(canary,json.dumps(result))
        self.assertFalse(result['candidate_ready'])
        files=[p for p in self.runroot.rglob('*') if p.is_file()]
        before={p.relative_to(self.runroot).as_posix():s.h.sha256(p.read_bytes()) for p in files}
        self.assertEqual(self.runroot.stat().st_mode & 0o777,0o700)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in files))
        with self.assertRaisesRegex(ValueError,'Run already exists'):
            self.run_candidate(bootstrap=True)
        self.assertEqual(len(self.calls),32)
        self.assertEqual(before,{p.relative_to(self.runroot).as_posix():s.h.sha256(p.read_bytes()) for p in self.runroot.rglob('*') if p.is_file()})


if __name__=='__main__':
    os.umask(0o077)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PostCaptureTests)
    suite.addTest(FailurePhaseTests('test_post_capture_phase_and_private_failure_retention'))
    suite.addTest(FirstPrefixTests('test_finalization_alarm_holds_without_candidate'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
