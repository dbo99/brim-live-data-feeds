#!/usr/bin/env python3
"""Reserialize the exact D2A one-day/three-stream saved synthetic fixture in v2.
Measures uncompressed file bytes only, not transfer or Git pack savings.
"""
import argparse,copy,json,sys
from pathlib import Path
from archive_fixture import dc
import dendra_archive as da
import dendra_state as ds

def convert(source,root):
    source=Path(source);root=Path(root);assert not root.exists();root.mkdir(parents=True);dc.validate(source);old=dc.load(source/dc.FIXED[0]);index=copy.deepcopy(old)
    for k in ['files','retention']:index.pop(k,None)
    index.update(schema_version=da.SCHEMA,integration_version=da.INTEGRATION,windows=da.windows(),scope='SYNTHETIC D2A one-day append comparison, same source daily rows',lineage={'kind':'imported_seed','state_parent_generation':None,'seed_sha256':dc.sha(source/dc.FIXED[0]),'seed_generation':index['generation'],'selection_change':None},parent_generation=None,mode='replay',publication_time_utc=None,expires_at_utc=None)
    for s in index['streams']:
        product=dc.load(source/s['diagnostics_path']);dc.write(root/'generation/daily'/f"{s['datastream_id']}.json",product)
        for k in ['histories','diagnostics_path','csv_path']:s.pop(k,None)
        s.update(acquired_through_date=product['rows'][-1]['date'],processed_at_utc=product['generated_at_utc'],recent_rows=[dict(dc.projected(r),v=r['mean_value']) for r in product['rows'][-60:]],latest_accepted=next((dict(dc.projected(r),v=r['mean_value']) for r in reversed(product['rows']) if r['plot_eligible']),None),expires_at_utc=None)
    dc.write(root/'generation/index.json',index);candidate=root/'candidate';candidate.mkdir();da.write_layout(root/'generation',candidate);da.validate(candidate);return candidate

def delta(a,b):
    old={x['path']:x for x in ds.inventory(a)};new={x['path']:x for x in ds.inventory(b)};changed=[v for k,v in new.items() if old.get(k)!=v]
    return {'changed_files':len(changed),'new_changed_file_bytes':sum(x['bytes'] for x in changed),'unchanged_files':sum(old.get(k)==v for k,v in new.items()),'removed_names':sorted(set(old)-set(new)),'changed_paths':[x['path'] for x in changed]}
def run(before,after,root):
    root=Path(root);a=convert(before,root/'before');b=convert(after,root/'after');report={'status':'passed','method':'exact saved D2A before/after one-day three-stream fixture reserialized and independently validated as v2; not an additional live update; uncompressed bytes, no wire/pack savings claim','before_index_sha256':dc.sha(Path(before)/dc.FIXED[0]),'after_index_sha256':dc.sha(Path(after)/dc.FIXED[0]),'version1':delta(before,after),'version2':delta(a,b),'provider_requests':0};dc.write(root/'report.json',report);print(json.dumps(report))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--before',required=True);p.add_argument('--after',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.before,a.after,a.root)
