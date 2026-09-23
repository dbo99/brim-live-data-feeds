#!/usr/bin/env python3
"""Prepare normalized CSV inputs for frozen replay of saved fresh intervals.
No HTTP or daily calculation. Verified intervals replace their complete bounds.
"""
import argparse,csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/dendra'))
from bridge import load,dump,sha,write_csv,validate_catalog
from transport import _content_hash

def main():
 p=argparse.ArgumentParser();p.add_argument('--catalog',required=True);p.add_argument('--interval-store',required=True);p.add_argument('--output',required=True);p.add_argument('--historical-manifest');a=p.parse_args()
 catalog=validate_catalog(load(a.catalog));out=Path(a.output);out.mkdir(parents=True,exist_ok=True);historical={x['stream']['datastream_id']:x for x in load(a.historical_manifest)['streams']} if a.historical_manifest else {};spec=[]
 for s in catalog['streams']:
  sid=s['datastream_id'];prior=historical.get(sid);rows=[];chunks=[]
  if prior:
   if sha(prior['native_csv'])!=prior['native_sha256']:raise ValueError('Historical native CSV integrity failure')
   with open(prior['native_csv'],newline='') as f:rows=list(csv.DictReader(f))
   chunks=prior['chunks']
  files=sorted((Path(a.interval_store)/'chunks'/sid).glob('*.json'))
  if len(files)!=1:raise ValueError('Choose exactly one saved completed replacement interval per stream')
  e=load(files[0])
  if e.get('query_complete') is not True or e.get('datastream_id')!=sid or e['content_sha256']!=_content_hash(e):raise ValueError('Interval integrity failure')
  b=e['requested_interval'];rows=[r for r in rows if not b['start_inclusive']<=r['t']<b['end_exclusive']]
  tmp=out/(sid+'-interval.csv');write_csv(tmp,e['rows'],s)
  with tmp.open(newline='') as f:reader=csv.DictReader(f);fields=reader.fieldnames;rows.extend(reader)
  rows.sort(key=lambda r:r['t']);csvpath=out/(sid+'.csv')
  with csvpath.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
  chunks=chunks+[{k:e[k] for k in ('content_sha256','requested_interval','retrieval_last_utc','latest_observation_utc')}]
  spec.append({'stream':s,'native_csv':str(csvpath.resolve()),'native_sha256':sha(csvpath),'native_row_count':len(rows),'chunks':chunks})
 dump(out/'native_manifest.json',{'catalog':catalog,'streams':spec,'complete':True,'failures':[],'provenance':'offline full-interval source replacement from hashed review evidence'})
if __name__=='__main__':main()
