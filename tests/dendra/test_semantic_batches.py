"""Batch orchestration fails closed on timeout, missing coverage and worker errors."""
import json,sys,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import dendra_candidate as dc
class BatchBounds(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);dc.write(self.root/dc.FIXED[0],{'streams':[{}]*33})
 def tearDown(self):self.tmp.cleanup()
 def test_timeout_holds(self):
  with patch.object(dc.subprocess,'run',side_effect=subprocess.TimeoutExpired('R',120)):
   with self.assertRaisesRegex(ValueError,'timed out'):dc.semantic_validate(self.root)
 def test_bad_worker_holds(self):
  with patch.object(dc.subprocess,'run',return_value=subprocess.CompletedProcess([],2,'','SYNTHETIC rejection')):
   with self.assertRaisesRegex(ValueError,'SYNTHETIC rejection'):dc.semantic_validate(self.root)
 def test_missing_stream_coverage_holds(self):
  with patch.object(dc.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'status':'passed','streams_checked':0}),'')):
   with self.assertRaisesRegex(ValueError,'coverage'):dc.semantic_validate(self.root)
 def test_global_deadline_holds_before_worker(self):
  with patch.object(dc,'SEMANTIC_TOTAL_SECONDS',0),patch.object(dc.subprocess,'run') as worker:
   with self.assertRaisesRegex(ValueError,'deadline'):dc.semantic_validate(self.root)
   worker.assert_not_called()
if __name__=='__main__':unittest.main()
