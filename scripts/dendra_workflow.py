#!/usr/bin/env python3
"""Inactive workflow helper: safe state restore and explicit live selection."""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tarfile,tempfile,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'dendra'))
from bridge import dump,load,validate_catalog,classify,vocabulary_terms

def restore(archive,expected,destination):
    archive=Path(archive);destination=Path(destination)
    if destination.exists():raise ValueError('Restore destination already exists')
    if not re.fullmatch('[0-9a-f]{64}',expected) or hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('State archive hash mismatch')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='dendra-restore-',dir=destination.parent));total=0
    try:
        with tarfile.open(archive,'r:gz') as t:
            members=t.getmembers();seen=set()
            for m in members:
                p=Path(m.name)
                if p.is_absolute() or '..' in p.parts or '\\' in m.name or not p.parts or p.parts[0]!='state' or not (m.isfile() or m.isdir()) or m.name in seen:raise ValueError('Unsafe state archive member')
                seen.add(m.name);total+=m.size
                if total>512*1024*1024:raise ValueError('State restore size limit')
            for m in members:
                p=stage/m.name
                if m.isdir():p.mkdir(parents=True,exist_ok=True)
                else:
                    p.parent.mkdir(parents=True,exist_ok=True)
                    with t.extractfile(m) as f,p.open('xb') as out:shutil.copyfileobj(f,out)
        if not (stage/'state/current.json').is_file() or (stage/'state/writer.lock').exists():raise ValueError('Incomplete/locked state archive')
        os.replace(stage/'state',destination)
    finally:shutil.rmtree(stage)

def select(catalog,discovery,selection,output):
    base=load(catalog);d=load(Path(discovery)/'companion_catalog.json');config=load(selection);wanted=config['stream_ids']
    if d['status']!='verified':raise ValueError('Live discovery unavailable')
    records={r['datastream_id']:r for r in d['records']};old={s['datastream_id']:s for s in base['streams']};streams=[]
    for sid in wanted:
        r=records[sid]
        if not r['processing_eligible']:raise ValueError('Unresolved selected stream')
        # A catalog metadata change is a migration issue, not a silent historical relabel.
        if sid in old:
            prior=old[sid]
            if any(prior.get(k)!=r.get(k) for k in ('station_id','depth_cm','native_unit_name')):raise ValueError('Source identity/depth/unit changed; review migration')
        streams.append({**old.get(sid,{}),**r})
    station_ids={s['station_id'] for s in streams};stations=[s for s in base['stations'] if s['station_id'] in station_ids]
    for s in stations:
        current=next(x for x in d['stations'] if x['station_id']==s['station_id'])
        s.update({k:current[k] for k in ('public_level','source_is_hidden','source_is_geo_protected')})
        if s.get('source_is_geo_protected'):s['geometry']=None
        s['selected_stream_ids']=[x['datastream_id'] for x in streams if x['station_id']==s['station_id']];s['selected_stream_count']=len(s['selected_stream_ids'])
    base.update(streams=streams,stations=stations,stream_count=len(streams),station_count=len(stations),live_identity_status='verified',verified_at_utc=d['checked_at_utc'])
    if 'integration' in config:base['integration']=config['integration']
    for key in ('observation_windows','selection_change'):
        if key in config:base[key]=config[key]
    validate_catalog(base);dump(output,base)

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    q=sub.add_parser('restore');q.add_argument('--archive',required=True);q.add_argument('--sha256',required=True);q.add_argument('--state',required=True)
    q=sub.add_parser('select');q.add_argument('--catalog',required=True);q.add_argument('--discovery',required=True);q.add_argument('--selection',required=True);q.add_argument('--output',required=True)
    a=p.parse_args()
    if a.mode=='restore':restore(a.archive,a.sha256,a.state)
    else:select(a.catalog,a.discovery,a.selection,a.output)
if __name__=='__main__':main()
