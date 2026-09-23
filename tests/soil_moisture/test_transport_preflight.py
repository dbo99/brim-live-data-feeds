"""SM1R1 saved-output preflight failures; no network or original-input mutation."""
import sys,json,hashlib,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/soil_moisture'))
import prepare_snapshots as adapter
from scan_transport import preflight,decode_scan,BUNDLE_BYTES
ROOT=Path(sys.argv.pop(1)).resolve()
CHECK=Path(sys.argv.pop(1)).resolve()

class Preflight(unittest.TestCase):
 def test_all_advertised_and_bounds(self):
  r=preflight(ROOT/'data');self.assertEqual(len(r['files']),51);self.assertTrue(all(x['bytes']<=x['limit'] for x in r['files']))
  ruby=next(x for x in r['files'] if x['path']=='scan-2229.json');self.assertEqual(ruby['bytes'],2809802);self.assertEqual(ruby['expanded_bytes'],7308292)
 def test_invalid_candidates(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as temp:
   target=Path(temp)/'data';shutil.copytree(ROOT/'data',target)
   index_path=target/'scan.json';original=index_path.read_bytes();idx=json.loads(original);desc=next(s['bundle'] for s in idx['stations'] if s['id']=='2229:NV:SCAN');p=target/desc['path'];body=p.read_bytes()
   for case in ['missing','oversize','hash','schema','row','renamed-column','missing-column']:
    index_path.write_bytes(original);p.write_bytes(body)
    if case=='missing':p.unlink()
    elif case=='oversize':p.write_bytes(b' '*(BUNDLE_BYTES+1))
    elif case=='hash':p.write_bytes(body.replace(b'sm1-scan-popup-2',b'sm1-scan-popup-x'))
    else:
     b=json.loads(body)
     if case=='schema':b['schema']='invalid'
     elif case=='renamed-column':b['tables']['scan_soil_moisture_current_wy_trace.csv']['columns'][0]='unexpected_field'
     elif case=='missing-column':b['tables']['scan_soil_moisture_current_wy_trace.csv']['columns'].pop()
     else:b['tables']['scan_depth_style.csv']['rows'][0].pop()
     payload=json.dumps(b,separators=(',',':')).encode();p.write_bytes(payload)
     idx=json.loads(original);d=next(s['bundle'] for s in idx['stations'] if s['id']=='2229:NV:SCAN');d.update(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest());index_path.write_text(json.dumps(idx))
    with self.subTest(case=case),self.assertRaises((AssertionError,FileNotFoundError)):preflight(target)
 def test_failed_preparation_does_not_install_target(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as temp:
   target=Path(temp)/'never-complete'
   with patch.object(adapter,'preflight',side_effect=ValueError('SYNTHETIC preflight failure')):
    with self.assertRaisesRegex(ValueError,'SYNTHETIC'):
     adapter.prepare(ROOT/'preview/data/legacy',ROOT/'data/dendra',ROOT.parents[1]/'reference/dendra_map_lab/data/ui_snapshot.json',ROOT/'nrcs',target,CHECK)
   self.assertFalse(target.exists());self.assertEqual(list(Path(temp).iterdir()),[])
 def test_existing_output_preserved(self):
  with self.assertRaisesRegex(ValueError,'new directory'):
   adapter.prepare(None,None,None,None,ROOT/'data',CHECK)

if __name__=='__main__':unittest.main()
