import unittest,json,sys,copy,hashlib,csv
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/soil_moisture'))
from prepare_snapshots import validate_response
from scan_transport import decode_scan
ROOT=Path(sys.argv.pop(1)) if len(sys.argv)>1 and not sys.argv[1].startswith('-') else Path('../sm1')
def load(p):return json.loads(p.read_text())
def expanded(k):
 x=load(ROOT/'data'/f'{k}.json')
 for s in x['stations']:s['sensors']=[{**x['sensor_defaults'],**v} for v in s['sensors']]
 return x
class Snapshots(unittest.TestCase):
 def test_scan_exact_latest(self):
  geo=load(ROOT/'preview/data/legacy/scan_soil_moisture_latest.geojson');idx=expanded('scan');n=0
  for f,s in zip(geo['features'],idx['stations']):
   p=f['properties'];self.assertEqual(s['id'],p['station_triplet']);self.assertEqual(s['coordinates'],f['geometry']['coordinates'])
   for v,z in zip(json.loads(p['depth_values_json']),s['sensors']):
    n+=1;self.assertEqual(z['id'],v['sensor_id']);self.assertEqual(z['observation']['value'],v['sms_pct']);self.assertEqual(z['observation']['date'],v['obs_date']);self.assertEqual(z['depth_mm'],v['depth_in']*25.4);self.assertEqual(z['sensor_count'],v['sensor_count'])
  self.assertEqual(n,131)
 def test_scan_all_published_context_tables_exact(self):
  tables={}
  for p in sorted((ROOT/'preview/data/legacy').glob('scan_*.csv')):
   with p.open(newline='') as f:tables[p.name]=list(csv.DictReader(f))
  self.assertTrue(tables)
  for st in expanded('scan')['stations']:
   desc=st['bundle'];path=ROOT/'data'/desc['path'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),desc['sha256']);self.assertEqual(path.stat().st_size,desc['bytes']);b=decode_scan(load(path));site=str(b['feature']['properties']['site_code'])
   self.assertEqual(b['tables'],{k:[r for r in rows if 'site_code' not in r or str(r['site_code'])==site] for k,rows in tables.items()})
 def test_dendra_units_companion_and_values(self):
  i=expanded('dendra');sensors=[s for st in i['stations'] for s in st['sensors']];unknown=[s for s in sensors if s['unit_evidence']=='native_only_scale_unresolved'];self.assertEqual(len(unknown),97);self.assertTrue(all(s['unit'] is None and s['observation'] is None for s in unknown))
  imported=[s for s in sensors if s['history']];self.assertEqual(len(imported),2);self.assertEqual(sum(s['capabilities']['companion'] for s in imported),1)
  original=load(ROOT/'data/dendra/index.json')
  for s in imported:
   native=next(x for x in original['streams'] if x['datastream_id']==s['id']);last=[r for r in native['recent_rows'] if r['ok']][-1];self.assertEqual(s['observation']['value'],last['v']);self.assertEqual(s['observation']['build_time'],original['generated_at_utc']);self.assertEqual(s['change'],native['change'])
 def test_nrcs_scope_raw_and_flags(self):
  manifests=load(ROOT/'data/nrcs-response-manifest.json');counts=[]
  for m in manifests:
   raw=ROOT/'nrcs'/m['file'];self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),m['sha256']);r=load(raw);validate_response(m['spec'],r)
   if m['spec']['kind']=='data':
    counts.extend((m['spec']['params']['duration'],len(b['values'])) for st in r for b in st['data'])
    for mutation in ['unit','depth','date','duplicate']:
     bad=copy.deepcopy(r);b=bad[0]['data'][0]
     if mutation=='unit':b['stationElement']['storedUnitCode']='fraction'
     if mutation=='depth':b['stationElement']['heightDepth']=-99
     if mutation=='date':b['values'][0]['date']='2000-01-01'
     if mutation=='duplicate':b['values'].append(b['values'][0])
     with self.assertRaises(AssertionError):validate_response(m['spec'],bad)
  self.assertEqual(sum(n for k,n in counts if k=='DAILY'),540);self.assertEqual(sum(n for k,n in counts if k=='HOURLY'),168)
  idx=expanded('snotel');self.assertEqual(idx['counts']['stations'],3);self.assertTrue(all(not z['capabilities']['recent_change'] and z['observation']['sample_count'] is None for st in idx['stations'] for z in st['sensors']))
 def test_display_does_not_modify_archive(self):
  for p in (ROOT/'data/dendra').rglob('*'):
   if p.is_file():self.assertEqual(p.read_bytes(),(ROOT/'preview/data/dendra'/p.relative_to(ROOT/'data/dendra')).read_bytes())
if __name__=='__main__':unittest.main()
