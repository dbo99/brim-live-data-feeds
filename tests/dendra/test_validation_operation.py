"""Operation reuse invariants; mocked unit checks are separate from real-R replay."""
import copy,json,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import dendra_candidate as dc
import dendra_validation as dv

class OperationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'candidate'
        dc.write(self.root/dc.FIXED[0],{'generated_at_utc':'2024-01-01T00:00:00Z','mode':'replay','generation':'20240101T000000-1'})
        self.report={'status':'passed','semantic':{'status':'passed','rows_checked':1},'freshness':'frozen-replay'}
    def tearDown(self):self.tmp.cleanup()
    def test_cold_and_exact_in_operation_reuse(self):
        with patch.object(dc,'validate',side_effect=lambda *a,**k:copy.deepcopy(self.report)) as actual:
            op=dv.ValidationOperation();a=op.validate(self.root);a['semantic']['status']='forged'
            self.assertTrue(op.validate(self.root)['operation_reuse']);self.assertEqual(actual.call_count,1)
            dv.ValidationOperation().validate(self.root);self.assertEqual(actual.call_count,2)
    def test_changed_same_path_is_cold_not_a_filename_hit(self):
        with patch.object(dc,'validate',side_effect=lambda *a,**k:copy.deepcopy(self.report)) as actual:
            op=dv.ValidationOperation();op.validate(self.root);i=dc.load(self.root/dc.FIXED[0]);i['parent_generation']='20230101T000000-2';dc.write(self.root/dc.FIXED[0],i)
            self.assertFalse(op.validate(self.root)['operation_reuse']);self.assertEqual(actual.call_count,2)
    def test_source_change_holds(self):
        with patch.object(dc,'validate',side_effect=lambda *a,**k:copy.deepcopy(self.report)):
            op=dv.ValidationOperation();op.validate(self.root)
            with patch.object(dv,'source_binding',return_value=('different',)):
                with self.assertRaisesRegex(ValueError,'source changed'):op.validate(self.root)
    def test_mutated_during_actual_check_holds(self):
        def changed(*a,**kw):
            dc.write(self.root/dc.FIXED[0],{'changed':True});return self.report
        with patch.object(dc,'validate',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed during'):dv.ValidationOperation().validate(self.root)
    def test_forged_disk_or_report_cannot_be_operation(self):
        for value in ({'status':'passed'},str(self.root),object()):
            with self.assertRaisesRegex(ValueError,'trusted'):dv.operation(value)
    def test_no_semantic_false_result_enters_reuse(self):
        with patch.object(dc,'validate',return_value={'semantic':{'status':'not_run'}}):
            with self.assertRaisesRegex(ValueError,'Full R'):dv.ValidationOperation().validate(self.root)
    def test_expiry_forward_backward_and_suspend(self):
        for wall,mono in [(1901,101),(98,101),(101,1901)]:
            with patch.object(dv.time,'time',return_value=100),patch.object(dv.time,'monotonic',return_value=100):op=dv.ValidationOperation()
            with patch.object(dv.time,'time',return_value=wall),patch.object(dv.time,'monotonic',return_value=mono):
                with self.assertRaisesRegex(ValueError,'expired|backwards'):op.validate(self.root)
    def test_symlink_and_outside_ownership_reject(self):
        (self.root/'link').symlink_to(self.root/dc.FIXED[0])
        with self.assertRaises(Exception):dv.ValidationOperation().validate(self.root)
        (self.root/'link').unlink();dc.write(self.root/'outside.json',{})
        with self.assertRaises(Exception):dv.ValidationOperation().validate(self.root)
if __name__=='__main__':unittest.main()
