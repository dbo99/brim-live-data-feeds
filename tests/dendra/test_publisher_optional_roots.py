"""Focused callback filesystem contract; full scientific Git proof is separate."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import dendra_publisher as dp
class OptionalRoots(unittest.TestCase):
 def test_first_moisture_only_publication_creates_empty_declared_root(self):
  with tempfile.TemporaryDirectory() as td:
   r=Path(td);c=r/'candidate';t=r/'tree';t.mkdir();idx={'parent_generation':None}
   unrelated=['docs/data/scan_soil_moisture_latest.geojson','docs/data/snow_pillow_latest.geojson','docs/data/soil-moisture/scan.json']
   for rel in unrelated:dp.dc.write(t/rel,{'sentinel':'unrelated current generation'})
   preserved={rel:(t/rel).read_bytes() for rel in unrelated}
   dp.dc.write(c/dp.dc.FIXED[0],idx)
   for rel in [dp.dc.OWNED[0],dp.dc.OWNED[1],dp.dc.OWNED[3]]:dp.dc.write(c/rel/'fixture.json',{'synthetic':'filesystem-only'})
   env={'BRIM_PUBLISH_PRODUCT_ID':dp.dc.PRODUCT,'BRIM_PUBLISH_FIXED_PATHS':json.dumps(dp.dc.FIXED),'BRIM_PUBLISH_OWNED_ROOTS':json.dumps(dp.dc.OWNED),'BRIM_PUBLISH_PHASE':'reconcile','BRIM_PUBLISH_CANDIDATE_ROOT':str(c),'BRIM_PUBLISH_WORKTREE':str(t),'BRIM_PUBLISH_RESULT':str(r/'result.json')}
   with patch.dict(os.environ,env),patch.object(dp,'validate_product',return_value=idx),patch.object(dp.dc,'reconcile_decision',return_value={'decision':'publish'}),patch.object(dp.dc,'freshness',return_value='current'):dp.callback()
   self.assertTrue(all((t/p).is_dir() for p in dp.dc.OWNED));self.assertEqual(list((t/dp.dc.OWNED[2]).iterdir()),[])
   self.assertFalse((c/dp.dc.OWNED[2]).exists());self.assertEqual(dp.dc.load(r/'result.json')['decision'],'publish')
   self.assertEqual({rel:(t/rel).read_bytes() for rel in unrelated},preserved)
   # A later complete candidate may remove an optional owned companion, while
   # retaining every other product and creating an empty stageable owned root.
   dp.dc.write(t/dp.dc.OWNED[2]/'old.json',{'synthetic':'obsolete companion'})
   with patch.dict(os.environ,env),patch.object(dp,'validate_product',return_value=idx),patch.object(dp.dc,'reconcile_decision',return_value={'decision':'publish'}),patch.object(dp.dc,'freshness',return_value='current'):dp.callback()
   self.assertEqual(list((t/dp.dc.OWNED[2]).iterdir()),[])
   self.assertEqual({rel:(t/rel).read_bytes() for rel in unrelated},preserved)
if __name__=='__main__':unittest.main()
