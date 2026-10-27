"""Portable selection of accepted NEW snapshot routes; no annual stress replay."""
from pathlib import Path
import json, os, socket, sys, unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_snotel_snapshot import SnapshotTests
from test_snotel_workflow import WorkflowTests
from test_snotel_sms_publisher import PublisherTests
from test_snotel_year_end import YearEndTests

class NativeIsolationTests(unittest.TestCase):
    def test_external_denied(self):
        # Reserved documentation address, never a provider. In the Linux namespace
        # there is no external interface/route; Mac preliminary uses Seatbelt.
        with socket.socket() as s:
            s.settimeout(.3)
            with self.assertRaises(OSError):s.connect(('192.0.2.1',9))


def run(output):
    loader=unittest.TestLoader();suite=unittest.TestSuite()
    for cls in [SnapshotTests,PublisherTests,YearEndTests,NativeIsolationTests]:suite.addTests(loader.loadTestsFromTestCase(cls))
    omitted='test_os_network_denial_fresh_process'
    suite.addTests(WorkflowTests(name) for name in loader.getTestCaseNames(WorkflowTests) if name!=omitted)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report=dict(status='PASS' if result.wasSuccessful() else 'FAIL',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),new_snapshot_route_exercised=True,required_new_route='test_complete_32_98_and_closed_candidate: CANDIDATE_READY; 32 saved attempts/98 synthetic positions',reconstruction='fresh-process supported snotel_rebuild and exact two-file equality',legacy_network_test_not_selected=omitted,reason='Old case forbids loopback; hosted OS boundary intentionally permits loopback. Portable external denial and actual native loopback success are separately required.',annual_stress='Not rerun; accepted unchanged source capacity evidence reused.',synthetic_git_authority='Existing StrictGit fixture seam only; actual checkout SHA is independently recorded.',provider_requests=0)
    Path(output).write_text(json.dumps(report,indent=2)+'\n')
    return result.wasSuccessful()
if __name__=='__main__':sys.exit(0 if run(sys.argv[1]) else 1)
